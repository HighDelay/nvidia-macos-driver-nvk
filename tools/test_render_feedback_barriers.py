#!/usr/bin/env python3
"""Exercise production render-feedback barriers and restart bookkeeping without a GPU."""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/rotation'))
from test_support import method

source = (ROOT / 'plugin/NVMTLObjects.m').read_text()
methods = '\n'.join(method(source, signature) for signature in (
    '- (void)nvmtlHzRestart:', '- (BOOL)nvmtlPublishRenderShadows',
    '- (BOOL)nvmtlPassAttachment:', '- (BOOL)nvmtlBindSamplerPipeline',
    '- (void)textureBarrier', '- (void)memoryBarrierWithScope:',
    '- (void)memoryBarrierWithResources:'))
begin = source.index('        _passWritten = NO;', source.index('- (void)setRenderPipelineState:'))
clear = source[begin:source.index('        _begun = YES;', begin)]

fixture = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <assert.h>
#include <stdint.h>
#define nvlog(...) ((void)0)
#include "nvmtl_multiview.h"
#define NVMTL_NCOL 8
#define NVMTL_LOAD_COLOUR(i) (1u << (i))
#define NVMTL_LOAD_DEPTH (1u << 8)
#define NVMTL_LOAD_STENCIL (1u << 9)
#define NVMTL_SET_VERTEX 0
#define NVMTL_SET_FRAGMENT 1
#define NVMTL_BARS_HAZARD_RESTART 0
#define NVMTL_BARS_FENCE_API 1
typedef struct { int shadow; } nvk_image;
typedef struct { nvk_image *col[8]; uint32_t load; } nvk_pass;
typedef struct { unsigned ncol, cfmts[8], has_depth; } Signature;
typedef struct { unsigned has_stencil, view_mask, fetch_copy; } Pipeline;
typedef struct { int hz_draw_due, hz_wm_dirty; } Commands;
@interface NVMTLTexture : NSObject { @public nvk_image *image; NVMTLTexture *parent; }
- (nvk_image *)nvi;
@end
@implementation NVMTLTexture
- (nvk_image *)nvi { return parent ? [parent nvi] : image; }
@end
@interface NVMTLResSet : NSMutableSet @end
@implementation NVMTLResSet @end
@interface NVMTLRenderPipelineState : NSObject { @public Pipeline _p; void *_descVert, *_descFrag; } @end
@implementation NVMTLRenderPipelineState @end
@interface NVMTLCommandBuffer : NSObject { @public Commands _c; NSMutableSet *_resources; } @end
@implementation NVMTLCommandBuffer @end
static unsigned ends, bindings, restarts;
static int hazard;
static int nvmtl_hz_draw(Commands *c) { c->hz_draw_due=c->hz_wm_dirty=0; int r=hazard;hazard=0;return r; }
static void nvmtl_vk_cmd_end_render(Commands *c) { (void)c;ends++; }
static void nvmtl_vk_cmd_barrier(Commands *c) { (void)c; }
static void nvmtl_vk_barrier_site(int site) { restarts+=site==NVMTL_BARS_HAZARD_RESTART; }
static int nvmtl_vk_pass_render_shadowed(const nvk_pass *p) { return p->col[0] && p->col[0]->shadow; }
static void nvmtl_vk_cmd_bind_pipeline(Commands *c, Pipeline *p) { (void)c;(void)p;bindings++; }
static void nvmtl_vk_cmd_bind_set(Commands *c, Pipeline *p) { (void)c;(void)p; }
static void nvmtl_bind_embedded(id cb, int set, void *d, void *b, void *o) {}
@interface NVMTLRenderCommandEncoder : NSObject {
@public BOOL _hzFull,_begun,_passWritten,_visActive,_visPending;
    Signature _passSig; nvk_pass _pass; uint32_t _load;
    NVMTLCommandBuffer *_cb; NVMTLRenderPipelineState *_ps; unsigned _ampCount; const char *_ampWhy;
    NSDictionary *_vertexSamplers,*_fragmentSamplers;
    void *_vbufAt,*_vbufOff,*_fbufAt,*_fbufOff;
}
- (BOOL)nvmtlFetchCopy:(Pipeline *)pipeline;
- (void)nvmtlCloseVisibility;
- (void)setRenderPipelineState:(NVMTLRenderPipelineState *)p;
- (NVMTLRenderPipelineState *)nvmtlSamplerPipeline;
@end
@implementation NVMTLRenderCommandEncoder
- (BOOL)nvmtlFetchCopy:(Pipeline *)pipeline { assert(!pipeline->fetch_copy); return YES; }
- (void)nvmtlCloseVisibility { _visActive=NO; }
- (NVMTLRenderPipelineState *)nvmtlSamplerPipeline { return _ps; }
- (void)setRenderPipelineState:(NVMTLRenderPipelineState *)p {
    _ps=p;
    if (!_begun) {
@@CLEAR@@
        _begun=YES;
    }
}
@@METHODS@@
@end
static NVMTLRenderCommandEncoder *encoder(nvk_image *image, BOOL clear) {
    NVMTLRenderCommandEncoder *e=[NVMTLRenderCommandEncoder new];
    e->_cb=[NVMTLCommandBuffer new];e->_passSig=(Signature){1,{1},0};
    e->_pass.col[0]=image;e->_pass.load=clear?0:1;
    [e setRenderPipelineState:[NVMTLRenderPipelineState new]];
    return e;
}
static void barrier(NVMTLRenderCommandEncoder *e, NVMTLTexture *t, unsigned kind) {
    if(kind==0)[e textureBarrier];
    else if(kind==1)[e memoryBarrierWithScope:MTLBarrierScopeRenderTargets afterStages:MTLRenderStageFragment beforeStages:MTLRenderStageFragment];
    else {id<MTLResource> r[]={(id)t};[e memoryBarrierWithResources:r count:1 afterStages:MTLRenderStageFragment beforeStages:MTLRenderStageFragment];}
}
int main(void) { @autoreleasepool {
    nvk_image shadow={1},direct={0},other={1};
    NVMTLTexture *target=[NVMTLTexture new];target->image=&shadow;
    NVMTLTexture *view=[NVMTLTexture new];view->parent=target;
    NVMTLTexture *unrelated=[NVMTLTexture new];unrelated->image=&other;
    for(unsigned kind=0;kind<3;kind++) {
        NVMTLRenderCommandEncoder *e=encoder(&shadow,NO);unsigned n=ends;
        barrier(e,view,kind);assert(ends==n); // An untouched Load needs no publication.
        assert([e nvmtlBindSamplerPipeline]);barrier(e,view,kind);assert(ends==n+1);
        barrier(e,view,kind);assert(ends==n+1); // Repeat barrier preserves contents without another restart.
        assert([e nvmtlBindSamplerPipeline]);barrier(e,unrelated,2);assert(ends==n+1);
        [e memoryBarrierWithScope:MTLBarrierScopeBuffers afterStages:MTLRenderStageFragment beforeStages:MTLRenderStageFragment];assert(ends==n+1);
        e->_cb->_c.hz_draw_due=1;hazard=1;
        assert([e nvmtlBindSamplerPipeline]);assert(ends==n+2);
        barrier(e,target,kind);assert(ends==n+3); // The draw after the hazard restart must still publish.
        e=encoder(&shadow,YES);n=ends;barrier(e,view,kind);assert(ends==n+1);
        barrier(e,view,kind);assert(ends==n+1); // Clear-only pass publishes once, then loads.
        e=encoder(&direct,YES);n=ends;barrier(e,target,kind);assert(ends==n); // Direct path unchanged.
        e=encoder(&shadow,NO);e->_ps=nil;assert(![e nvmtlBindSamplerPipeline]);assert(!e->_passWritten);
        e->_begun=NO;barrier(e,target,kind);assert(ends==n);
    }
    assert(ends==restarts && bindings>0);
    puts("PASS: all barrier forms, clear-only writes, hazard restart then draw, repeated barriers, texture views, unrelated resources and direct paths");
} }
'''

fixture = fixture.replace('@@CLEAR@@', clear).replace('@@METHODS@@', methods)
with tempfile.TemporaryDirectory(prefix='nvmtl-render-feedback-') as directory:
    tmp = Path(directory)
    (tmp / 'test.m').write_text(fixture)
    subprocess.run(['xcrun', 'clang', '-O1', '-g', '-fobjc-arc', '-fsanitize=address,undefined',
        '-I'+str(ROOT/'plugin'), str(tmp / 'test.m'), '-framework', 'Foundation', '-framework', 'Metal', '-o', str(tmp / 'test')], check=True)
    subprocess.run([str(tmp / 'test')], check=True)
