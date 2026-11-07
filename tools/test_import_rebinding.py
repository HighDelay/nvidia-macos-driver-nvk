#!/usr/bin/env python3
"""Rebind real Mach-O imports without altering another image or page protection."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = r'''
#include <fcntl.h>
int imported_open(const char *path) { return open(path,O_RDONLY|O_CLOEXEC); }
'''
HARNESS = r'''
#include <assert.h>
#include <fcntl.h>
#include <stdio.h>
#include <unistd.h>
#include "nvmtl_rebind_import.h"
static int replacement(const char *path,int flags,...) { (void)path;(void)flags;return -12345; }
static vm_prot_t protection(void *address) {
    mach_vm_address_t region=(uintptr_t)address;mach_vm_size_t bytes=0;
    vm_region_basic_info_data_64_t info;mach_msg_type_number_t n=VM_REGION_BASIC_INFO_COUNT_64;
    mach_port_t object=MACH_PORT_NULL;
    assert(mach_vm_region(mach_task_self(),&region,&bytes,VM_REGION_BASIC_INFO_64,(vm_region_info_t)&info,&n,&object)==KERN_SUCCESS);
    if(object)mach_port_deallocate(mach_task_self(),object);
    return info.protection;
}
static void *import_slot(const void *inside) {
    Dl_info di;assert(dladdr(inside,&di));
    const struct mach_header_64 *mh=(const struct mach_header_64 *)di.dli_fbase;
    const uint8_t *lc=(const uint8_t *)(mh+1);uintptr_t slide=0;
    for(uint32_t i=0;i<mh->ncmds;i++,lc+=((const struct load_command *)lc)->cmdsize){
        const struct load_command *c=(const struct load_command *)lc;
        if(c->cmd==LC_SEGMENT_64){const struct segment_command_64 *sg=(const struct segment_command_64 *)c;if(!strcmp(sg->segname,SEG_TEXT))slide=(uintptr_t)mh-sg->vmaddr;}
    }
    lc=(const uint8_t *)(mh+1);
    for(uint32_t i=0;i<mh->ncmds;i++,lc+=((const struct load_command *)lc)->cmdsize){
        const struct load_command *c=(const struct load_command *)lc;if(c->cmd!=LC_SEGMENT_64)continue;
        const struct segment_command_64 *sg=(const struct segment_command_64 *)c;
        const struct section_64 *sec=(const struct section_64 *)(sg+1);
        for(uint32_t k=0;k<sg->nsects;k++,sec++){
            uint32_t type=sec->flags&SECTION_TYPE;
            if(type==S_LAZY_SYMBOL_POINTERS||type==S_NON_LAZY_SYMBOL_POINTERS){
                void **slots=(void **)(slide+sec->addr);
                for(uint64_t e=0;e<sec->size/sizeof(void *);e++)if(slots[e]==(void *)replacement)return &slots[e];
            }
        }
    }return NULL;
}
static uint64_t send_refs(void) {
    mach_port_name_array_t names;mach_port_type_array_t types;
    mach_msg_type_number_t nn,nt;uint64_t total=0;
    assert(mach_port_names(mach_task_self(),&names,&nn,&types,&nt)==KERN_SUCCESS&&nn==nt);
    for(unsigned i=0;i<nn;i++)if(types[i]&MACH_PORT_TYPE_SEND){mach_port_urefs_t refs=0;if(mach_port_get_refs(mach_task_self(),names[i],MACH_PORT_RIGHT_SEND,&refs)==KERN_SUCCESS)total+=refs;}
    vm_deallocate(mach_task_self(),(vm_address_t)names,nn*sizeof *names);
    vm_deallocate(mach_task_self(),(vm_address_t)types,nt*sizeof *types);
    return total;
}
int main(int argc,char **argv) {
    assert(argc==3);void *a=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL),*b=dlopen(argv[2],RTLD_NOW|RTLD_LOCAL);assert(a&&b);
    int(*first)(const char *)=(int(*)(const char *))dlsym(a,"imported_open");
    int(*second)(const char *)=(int(*)(const char *))dlsym(b,"imported_open");assert(first&&second);
    int fd=first("/dev/null");assert(fd>=0);close(fd);
    assert(nvmtl_rebind_import((void *)first,"_open",(void *)replacement)>0);
    void *slot=import_slot((void *)first);assert(slot);vm_prot_t original=protection(slot);
    assert(first("/dev/null")==-12345);fd=second("/dev/null");assert(fd>=0);close(fd);
    uint64_t refs=send_refs();
    for(unsigned i=0;i<512;i++)assert(nvmtl_rebind_import((void *)first,"_open",(void *)replacement)>0);
    assert(send_refs()==refs);assert(protection(slot)==original);
    assert(!nvmtl_rebind_import((void *)first,"_not_a_real_import",(void *)replacement));
    assert(nvmtl_rebind_import(NULL,"_open",(void *)replacement)==-1);
    assert(nvmtl_rebind_import((void *)first,NULL,(void *)replacement)==-1);
    assert(nvmtl_rebind_import((void *)first,"_open",NULL)==-1);
    void *real=dlsym(RTLD_DEFAULT,"open");assert(real);
    assert(nvmtl_rebind_import((void *)first,"_open",real)>0&&protection(slot)==original);
    fd=first("/dev/null");assert(fd>=0);close(fd);
    // Exercise initially writable __DATA_CONST too; never forcibly downgrade it.
    mach_vm_address_t page=(uintptr_t)slot&~(mach_vm_address_t)(vm_page_size-1);
    assert(mach_vm_protect(mach_task_self(),page,vm_page_size,FALSE,original|VM_PROT_WRITE|VM_PROT_COPY)==KERN_SUCCESS);
    vm_prot_t writable=protection(slot);
    assert(nvmtl_rebind_import((void *)first,"_open",(void *)replacement)>0);
    assert(protection(slot)==writable&&first("/dev/null")==-12345);
    assert(nvmtl_rebind_import((void *)first,"_open",real)>0);
    assert(mach_vm_protect(mach_task_self(),page,vm_page_size,FALSE,original)==KERN_SUCCESS);
    dlclose(b);dlclose(a);
    puts("PASS: image isolation, reversible imports, page protections, 512 repeated binds without port leaks");
}
'''
with tempfile.TemporaryDirectory(prefix='nvmtl-rebind-') as directory:
    root = Path(directory)
    source = root/'library.c'; source.write_text(LIBRARY)
    for name in ['first','second']:
        subprocess.run(['xcrun','clang','-dynamiclib',str(source),'-o',str(root/(name+'.dylib'))],check=True)
    for language in ['c','c++']:
        source=root/('test.c' if language=='c' else 'test.cpp');source.write_text(HARNESS)
        binary=root/('test-'+language)
        subprocess.run(['xcrun','clang++' if language=='c++' else 'clang','-O1','-g',
                        '-fsanitize=address,undefined','-I'+str(ROOT/'plugin'),str(source),'-o',str(binary)],check=True)
        subprocess.run([str(binary),str(root/'first.dylib'),str(root/'second.dylib')],check=True)
