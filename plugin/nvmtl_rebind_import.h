/* SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 */
#ifndef NVMTL_REBIND_IMPORT_H
#define NVMTL_REBIND_IMPORT_H
#include <dlfcn.h>
#include <mach-o/loader.h>
#include <mach-o/nlist.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <pthread.h>
#include <stdint.h>
#include <string.h>

static int nvmtl_linkedit_range(const struct segment_command_64 *le, uint64_t off, uint64_t bytes)
{
    return off >= le->fileoff && off - le->fileoff <= le->filesize
        && bytes <= le->filesize - (off - le->fileoff);
}

/* Rebind one import only in the loaded image containing inside. Its owner must
 * keep that image loaded. Preserve each page's actual protection, including
 * writable __DATA_CONST pages, and release every VM-region object port. */
static int nvmtl_rebind_import(const void *inside, const char *symbol, void *replacement)
{
    Dl_info di;
    if (!inside || !symbol || !replacement || !dladdr(inside, &di) || !di.dli_fbase) return -1;
    const struct mach_header_64 *mh = (const struct mach_header_64 *)di.dli_fbase;
    if (mh->magic != MH_MAGIC_64 || mh->ncmds > mh->sizeofcmds / sizeof(struct load_command)) return -1;
    const struct segment_command_64 *text = NULL, *linkedit = NULL;
    const struct symtab_command *st = NULL;
    const struct dysymtab_command *dst = NULL;
    const uint8_t *lc = (const uint8_t *)(mh + 1);
    size_t left = mh->sizeofcmds;
    for (uint32_t i = 0; i < mh->ncmds; i++) {
        if (left < sizeof(struct load_command)) return -1;
        const struct load_command *c = (const struct load_command *)lc;
        if (c->cmdsize < sizeof *c || c->cmdsize > left) return -1;
        if (c->cmd == LC_SEGMENT_64) {
            if (c->cmdsize < sizeof(struct segment_command_64)) return -1;
            const struct segment_command_64 *sg = (const struct segment_command_64 *)c;
            if (sg->nsects > (c->cmdsize - sizeof *sg) / sizeof(struct section_64)) return -1;
            if (!strncmp(sg->segname, SEG_TEXT, sizeof sg->segname)) text = sg;
            else if (!strncmp(sg->segname, SEG_LINKEDIT, sizeof sg->segname)) linkedit = sg;
        } else if (c->cmd == LC_SYMTAB) {
            if (c->cmdsize < sizeof(struct symtab_command)) return -1;
            st = (const struct symtab_command *)c;
        } else if (c->cmd == LC_DYSYMTAB) {
            if (c->cmdsize < sizeof(struct dysymtab_command)) return -1;
            dst = (const struct dysymtab_command *)c;
        }
        lc += c->cmdsize; left -= c->cmdsize;
    }
    if (!text || !linkedit || !st || !dst || !dst->nindirectsyms
        || !nvmtl_linkedit_range(linkedit, st->symoff, (uint64_t)st->nsyms * sizeof(struct nlist_64))
        || !nvmtl_linkedit_range(linkedit, st->stroff, st->strsize)
        || !nvmtl_linkedit_range(linkedit, dst->indirectsymoff, (uint64_t)dst->nindirectsyms * sizeof(uint32_t))) return -1;
    const uintptr_t slide = (uintptr_t)mh - text->vmaddr;
    const uintptr_t lebase = slide + linkedit->vmaddr - linkedit->fileoff;
    const struct nlist_64 *syms = (const struct nlist_64 *)(lebase + st->symoff);
    const char *strings = (const char *)(lebase + st->stroff);
    const uint32_t *indirect = (const uint32_t *)(lebase + dst->indirectsymoff);
    if (!vm_page_size || (vm_page_size & (vm_page_size - 1))) return -1;
    static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
    pthread_mutex_lock(&lock);
    int rebound = 0;
    lc = (const uint8_t *)(mh + 1);
    for (uint32_t i = 0; i < mh->ncmds; i++, lc += ((const struct load_command *)lc)->cmdsize) {
        const struct load_command *c = (const struct load_command *)lc;
        if (c->cmd != LC_SEGMENT_64) continue;
        const struct segment_command_64 *sg = (const struct segment_command_64 *)c;
        if (strncmp(sg->segname, "__DATA", 6)) continue;
        const struct section_64 *sec = (const struct section_64 *)(sg + 1);
        for (uint32_t k = 0; k < sg->nsects; k++, sec++) {
            const uint32_t type = sec->flags & SECTION_TYPE;
            if (type != S_LAZY_SYMBOL_POINTERS && type != S_NON_LAZY_SYMBOL_POINTERS) continue;
            if (sec->addr < sg->vmaddr || sec->addr - sg->vmaddr > sg->vmsize
                || sec->size > sg->vmsize - (sec->addr - sg->vmaddr)) continue;
            void **slots = (void **)(slide + sec->addr);
            for (uint64_t e = 0; e < sec->size / sizeof(void *); e++) {
                if ((uint64_t)sec->reserved1 + e >= dst->nindirectsyms) break;
                const uint32_t si = indirect[sec->reserved1 + e];
                if ((si & (INDIRECT_SYMBOL_ABS | INDIRECT_SYMBOL_LOCAL)) || si >= st->nsyms) continue;
                const uint32_t strx = syms[si].n_un.n_strx;
                if (strx >= st->strsize || !memchr(strings + strx, 0, st->strsize - strx)
                    || strcmp(strings + strx, symbol)) continue;
                const mach_vm_address_t address = (mach_vm_address_t)(uintptr_t)&slots[e];
                mach_vm_address_t region = address;
                mach_vm_size_t bytes = 0;
                vm_region_basic_info_data_64_t info;
                mach_msg_type_number_t count = VM_REGION_BASIC_INFO_COUNT_64;
                mach_port_t object = MACH_PORT_NULL;
                const kern_return_t rc = mach_vm_region(mach_task_self(), &region, &bytes, VM_REGION_BASIC_INFO_64,
                                                       (vm_region_info_t)&info, &count, &object);
                if (object != MACH_PORT_NULL) mach_port_deallocate(mach_task_self(), object);
                if (rc != KERN_SUCCESS || region > address || bytes < sizeof(void *)
                    || address - region > bytes - sizeof(void *) || !(info.protection & VM_PROT_READ)) continue;
                if (__atomic_load_n(&slots[e], __ATOMIC_ACQUIRE) == replacement) { rebound++; continue; }
                const int writable = (info.protection & VM_PROT_WRITE) != 0;
                const mach_vm_address_t page = address & ~(mach_vm_address_t)(vm_page_size - 1);
                if (!writable && mach_vm_protect(mach_task_self(), page, vm_page_size, FALSE,
                                                info.protection | VM_PROT_WRITE | VM_PROT_COPY) != KERN_SUCCESS) continue;
                __atomic_store_n(&slots[e], replacement, __ATOMIC_RELEASE);
                if (!writable && mach_vm_protect(mach_task_self(), page, vm_page_size, FALSE, info.protection) != KERN_SUCCESS) {
                    pthread_mutex_unlock(&lock); return -1;
                }
                rebound++;
            }
        }
    }
    pthread_mutex_unlock(&lock);
    return rebound;
}
#endif
