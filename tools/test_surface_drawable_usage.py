#!/usr/bin/env python3
"""Exercise production drawable policy and import fallback/cleanup without installing a driver."""
from pathlib import Path
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'tools/rotation'))
from test_support import method
source=(root/'plugin/NVMTLDevice.m').read_text()
policy=method(source,'static BOOL nvmtl_surface_widen_usage(')+'\n'+method(source,'static NSUInteger nvmtl_surface_usage(')
image=method(source,'static int nvmtl_surface_image_create(')
fixture=r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
typedef unsigned BOOL;
typedef unsigned long NSUInteger;
#define YES 1
#define MTLTextureUsageShaderRead 1
#define MTLTextureUsageShaderWrite 2
#define MTLTextureUsageRenderTarget 4
static const char *program;
#define getprogname() program
@@POLICY@@
typedef struct { int storage; } nvk_image;
typedef struct { int allocated; } nvk_buffer;
typedef struct { uint64_t _vramBorn; nvk_buffer _vramBuf; BOOL _vramOn; uint32_t _surfSeed; BOOL _surfSynced; nvk_image _i; nvk_buffer _hostPages; } NVMTLTexture;
typedef void *IOSurfaceRef;
static unsigned g_surf_vram,g_surf_private;
static unsigned image_drops,buffer_drops,private_requests,alias_requests;
static int drop_storage,alias_failure;
#define nvlog(...) ((void)0)
static unsigned IOSurfaceGetPlaneCount(IOSurfaceRef s) { return 0; }
static unsigned IOSurfaceGetBytesPerRow(IOSurfaceRef s) { return 2048; }
static unsigned IOSurfaceGetHeight(IOSurfaceRef s) { return 64; }
static unsigned IOSurfaceGetID(IOSurfaceRef s) { return 1; }
static unsigned IOSurfaceGetSeed(IOSurfaceRef s) { return 7; }
static int nvmtl_vk_surface_share_on(void) { return 1; }
static int nvmtl_vk_format_is_storage(uint32_t f) { return 1; }
static int nvmtl_vram_tag_current(IOSurfaceRef s, unsigned p, uint32_t seed) { return 0; }
static int nvmtl_vram_keep_get(int host,IOSurfaceRef s,NSUInteger p,size_t n,nvk_buffer *b,uint64_t *born) { b->allocated=!host;if(born)*born=10;return host?-1:0; }
static int nvmtl_vk_image_create_buffer_alias(uint32_t w,uint32_t h,uint32_t f,uint32_t bpp,int a8,int storage,int render,nvk_buffer *b,size_t offset,size_t row,nvk_image *out) { alias_requests++;out->storage=storage&&!drop_storage;return alias_failure; }
static void nvmtl_vk_image_destroy(nvk_image *i) { image_drops++;memset(i,0,sizeof *i); }
static void nvmtl_vk_buffer_destroy(nvk_buffer *b) { assert(b->allocated);buffer_drops++;b->allocated=0; }
static int nvmtl_vk_image_create_typed(uint32_t w,uint32_t h,uint32_t f,uint32_t bpp,int a8,unsigned mips,int storage,unsigned type,unsigned layers,nvk_image *out) { private_requests++;out->storage=storage;return 0; }
@@IMAGE@@
static void reset(void) { image_drops=buffer_drops=private_requests=alias_requests=0;drop_storage=alias_failure=0; }
int main(int argc,char **argv) {
 assert(argc==2);program=argv[1];int ws=!strcmp(program,"WindowServer");
 assert(nvmtl_surface_usage(1,0,0)==(ws?1:5));
 assert(nvmtl_surface_usage(1,0,1)==(ws?1:7));
 assert(nvmtl_surface_usage(1,1,1)==1);
 assert(nvmtl_surface_usage(3,0,0)==(ws?3:7));
 assert(nvmtl_surface_usage(3,1,1)==3);
 NVMTLTexture t={0};reset();
 assert(!nvmtl_surface_image_create(&t,(void*)1,0,512,64,44,4,0,1));
 assert(t._vramOn && t._i.storage && alias_requests==1 && !private_requests && !buffer_drops && !image_drops);
 memset(&t,0,sizeof t);reset();drop_storage=1;
 assert(!nvmtl_surface_image_create(&t,(void*)1,0,512,64,44,4,0,1));
 assert(!t._vramOn && t._i.storage && private_requests==1 && buffer_drops==1 && image_drops==1);
 memset(&t,0,sizeof t);reset();drop_storage=1;
 assert(!nvmtl_surface_image_create(&t,(void*)1,0,512,64,44,4,0,0));
 assert(t._vramOn && !t._i.storage && !private_requests && !image_drops && !buffer_drops);
 memset(&t,0,sizeof t);reset();alias_failure=1;
 assert(!nvmtl_surface_image_create(&t,(void*)1,0,512,64,44,4,0,1));
 assert(!t._vramOn && t._i.storage && private_requests==1 && buffer_drops==1 && !image_drops);
 puts("PASS: app/WindowServer and framebuffer-only policy, real storage request, linear-storage fallback and exact cleanup");
}
'''.replace('@@POLICY@@',policy).replace('@@IMAGE@@',image)
with tempfile.TemporaryDirectory() as d:
 p=Path(d);(p/'test.c').write_text(fixture)
 subprocess.run(['clang','-std=c11','-O1','-fsanitize=address,undefined',str(p/'test.c'),'-o',str(p/'test')],check=True)
 for process in ('Photo Booth','WindowServer'):
  subprocess.run([str(p/'test'),process],check=True)
