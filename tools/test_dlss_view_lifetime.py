#!/usr/bin/env python3
"""Exercise production DLSS image-view reuse and cleanup beyond its initial cache size."""
import argparse
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--source',type=Path,default=ROOT)
args=p.parse_args()
s=(args.source/'plugin/nvmtl_vk.c').read_text()
a=s.index('#define DLSS_VIEW_CACHE'); b=s.index('static int dlss_image(',a)
production=s[a:b]
fixture=r'''
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <pthread.h>
#include <vulkan/vulkan.h>
typedef struct { void *img; uint32_t fmt,w,h,usage; } nvk_image;
static VkDevice g_dev;
static unsigned created,destroyed;
static int fail_view,fail_alloc;
static void *controlled_realloc(void *p,size_t n) { return fail_alloc ? NULL : realloc(p,n); }
static VkResult pvkCreateImageView(VkDevice d,const VkImageViewCreateInfo *i,const VkAllocationCallbacks *a,VkImageView *v) {
    (void)d;(void)a;
    assert(i->image && i->viewType==VK_IMAGE_VIEW_TYPE_2D && !i->pNext);
    assert(i->subresourceRange.levelCount==1 && i->subresourceRange.layerCount==1);
    if(fail_view) return VK_ERROR_OUT_OF_DEVICE_MEMORY;
    *v=(VkImageView)(uintptr_t)(++created+65536); return VK_SUCCESS;
}
static void pvkDestroyImageView(VkDevice d,VkImageView v,const VkAllocationCallbacks *a) {
    (void)d;(void)a; assert(v);destroyed++;
}
#define nvlog(...) ((void)0)
#define realloc controlled_realloc
@@PRODUCTION@@
#undef realloc
int main(void) {
    nvk_image images[192]; VkImageView views[192][2];
    for(unsigned i=0;i<192;i++) {
        images[i]=(nvk_image){.img=(void *)(uintptr_t)(65536+i),.fmt=VK_FORMAT_R8G8B8A8_UNORM,.w=16,.h=16};
        for(int rw=0;rw<2;rw++) {
            views[i][rw]=dlss_view(&images[i],VK_IMAGE_ASPECT_COLOR_BIT,rw);
            assert(views[i][rw]);
        }
    }
    for(unsigned i=0;i<192;i++) for(int rw=0;rw<2;rw++)
        assert(dlss_view(&images[i],VK_IMAGE_ASPECT_COLOR_BIT,rw)==views[i][rw]);
    assert(created==384);
    for(unsigned i=0;i<192;i++) nvmtl_dlss_views_drop((VkImage)images[i].img);
    assert(destroyed==created);
    nvmtl_dlss_views_drop((VkImage)images[0].img);assert(destroyed==created);
    fail_view=1; assert(!dlss_view(&images[0],VK_IMAGE_ASPECT_COLOR_BIT,0));
    fail_view=0; assert(dlss_view(&images[0],VK_IMAGE_ASPECT_COLOR_BIT,0));
    nvmtl_dlss_views_drop((VkImage)images[0].img);assert(destroyed==created);
#ifdef DLSS_VIEW_CACHE_MAX
    for(unsigned i=0;i<DLSS_VIEW_CACHE_MAX;i++) {
        nvk_image image={.img=(void *)(uintptr_t)(131072+i),.fmt=VK_FORMAT_R8G8B8A8_UNORM};
        assert(dlss_view(&image,VK_IMAGE_ASPECT_COLOR_BIT,0));
    }
    unsigned before=created;
    nvk_image extra={.img=(void *)0x80000,.fmt=VK_FORMAT_R8G8B8A8_UNORM};
    assert(!dlss_view(&extra,VK_IMAGE_ASPECT_COLOR_BIT,0));assert(created==before);
    for(unsigned i=0;i<DLSS_VIEW_CACHE_MAX;i++) nvmtl_dlss_views_drop((VkImage)(uintptr_t)(131072+i));
    assert(destroyed==created);
    free(g_dlss_vc);g_dlss_vc=NULL;g_dlss_vc_cap=0;
    fail_alloc=1;assert(!dlss_view(&extra,VK_IMAGE_ASPECT_COLOR_BIT,0));assert(created==before);
    fail_alloc=0;assert(dlss_view(&extra,VK_IMAGE_ASPECT_COLOR_BIT,0));
    nvmtl_dlss_views_drop((VkImage)extra.img);assert(destroyed==created);
    free(g_dlss_vc);
#endif
    puts("PASS: DLSS view reuse, aspect/access identity, complete destruction, bounded growth and allocation failures");
}
'''.replace('@@PRODUCTION@@',production)
with tempfile.TemporaryDirectory(prefix='dlss-views-') as temp:
    source=Path(temp)/'probe.c'; binary=Path(temp)/'probe'
    source.write_text(fixture)
    subprocess.run(['xcrun','clang','-O1','-g','-I'+str(Path.home()/'Vulkan-Headers/include'),
                    '-fsanitize=address,undefined','-fno-sanitize-recover=all',str(source),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True,timeout=30)
