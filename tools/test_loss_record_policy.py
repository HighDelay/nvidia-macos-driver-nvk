#!/usr/bin/env python3
"""Run production allocation/image recorders with diagnostics on and off.

Ordinary release processes must not retain the whole allocation history or
create extra Vulkan buffers to discover addresses. The opt-in must still record
real allocations and image views. No driver installation or GPU is involved.
"""
import os
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/rotation'))
from test_support import method

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', type=Path, default=ROOT/'plugin/nvmtl_vk.c')
args=parser.parse_args()
source=args.source.read_text()
production = '\n'.join(method(source, signature) for signature in (
    'static int nvmtl_fx_records_on(void)\n{',
    'static VkResult nvmtl_mem_alloc_rec(',
    'static void nvmtl_mem_free_rec(',
    'static VkResult nvmtl_mem_bind_image_rec(',
    'static void nvmtl_commit_image(',
    'static void nvmtl_vw_note_died(const void *view) {',
))
fixture = r'''
#include <assert.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <vulkan/vulkan.h>
#include "nvmtl_vk.h"
#include "nvmtl_mem.h"
#include "nvmtl_slots.h"
#define nvlog(...) ((void)0)
static int nvmtl_dwit_on(void) { return 0; }
static nvmtl_mm_state g_mm; static nvmtl_mb_state g_mb;
static nvmtl_im_state g_im; static nvmtl_vw_state g_vw;
static pthread_mutex_t g_mm_lock=PTHREAD_MUTEX_INITIALIZER,g_sl_lock=PTHREAD_MUTEX_INITIALIZER;
static uint64_t g_cmd_submitted=1,g_alloc_bytes,g_sys_bytes,g_sys_align,g_vram_bytes,g_img_align,g_img_made;
static unsigned allocated,freed,bound,probes,censuses;
static VkResult pvkAllocateMemory(VkDevice d,const VkMemoryAllocateInfo *i,const VkAllocationCallbacks *a,VkDeviceMemory *out) {
    *out=(VkDeviceMemory)(uintptr_t)(0x10000+(++allocated)*16);return VK_SUCCESS;
}
static void pvkFreeMemory(VkDevice d,VkDeviceMemory m,const VkAllocationCallbacks *a) { freed++; }
static VkResult pvkBindImageMemory(VkDevice d,VkImage i,VkDeviceMemory m,VkDeviceSize o) { bound++;return VK_SUCCESS; }
static uint64_t nvmtl_mem_base_va(VkDeviceMemory m,VkDeviceSize n) { probes++;return 0x100000000ull+(uintptr_t)m; }
static unsigned nvmtl_vk_view_type_of(nvk_image *i) { return VK_IMAGE_VIEW_TYPE_2D; }
static void nvmtl_vw_tic_note(const void *v) {}
static void nvmtl_vk_image_census(void) { censuses++; }
@@PRODUCTION@@
int main(void) {
    enum { N=4096 };
    const int expected=nvmtl_fx_records_on();
    for(unsigned k=0;k<N;k++) {
        VkMemoryAllocateInfo info={.sType=VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,.allocationSize=4096,.memoryTypeIndex=1};
        VkDeviceMemory memory;
        assert(nvmtl_mem_alloc_rec(NULL,&info,NULL,&memory,"test")==VK_SUCCESS);
        VkImage handle=(VkImage)(uintptr_t)(0x20000+k*16);
        assert(nvmtl_mem_bind_image_rec(NULL,handle,memory,0)==VK_SUCCESS);
        nvk_image image={.img=(void *)handle,.mem=(void *)memory,.view=(void *)(uintptr_t)(0x30000+k*16),
            .alloc=4096,.w=16,.h=16,.fmt=37,.layers=1,.mips=1,.samples=1};
        nvmtl_commit_image(&image);
        nvmtl_vw_note_died(image.view);
        nvmtl_mem_free_rec(NULL,memory,NULL);
    }
    assert(allocated==N && freed==N && bound==N && censuses==N && g_img_made==N && g_alloc_bytes==(uint64_t)N*4096);
    if(expected) {
        assert(probes==N && g_mm.n==N && g_mm.live==0 && g_im.n==N && g_vw.n==N);
        assert(g_vw.e[0].died && g_vw.e[N-1].died);
    } else {
        assert(!probes && !g_mm.n && !g_mm.e && !g_mm.cap && !g_mb.n);
        assert(!g_im.n && !g_im.e && !g_im.cap && !g_vw.n && !g_vw.e && !g_vw.cap);
    }
    free(g_mm.e);free(g_im.e);free(g_vw.e);
    printf("PASS: %u allocations/images, diagnostics=%d, address probes=%u, ordinary GPU operations preserved\n",N,expected,probes);
}
'''
with tempfile.TemporaryDirectory(prefix='nvmtl-record-policy-') as directory:
    path=Path(directory);(path/'test.c').write_text(fixture.replace('@@PRODUCTION@@',production))
    for release in (0,1):
        exe=path/f'test-{release}'
        subprocess.run(['xcrun','clang','-O2','-fsanitize=address,undefined','-fno-sanitize-recover=all',f'-DNVMTL_RELEASE={release}',
            '-I'+str(ROOT/'plugin'),'-I'+str(Path.home()/'Vulkan-Headers/include'),str(path/'test.c'),'-o',str(exe)],check=True)
        for value in (None,'0','1'):
            env=os.environ.copy();env.pop('NVMTL_LOSS_RECORDS',None)
            if value is not None: env['NVMTL_LOSS_RECORDS']=value
            subprocess.run([str(exe)],env=env,check=True)
