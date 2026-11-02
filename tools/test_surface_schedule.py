#!/usr/bin/env python3
"""Exercise the production IOSurface handoff policy with accepted/refused fences."""
import os
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'plugin/NVMTLEventSync.m').read_text()
start = source.index('static BOOL nvmtl_surface_schedule_after_publish(')
policy = source[start:source.index('- (void)nvmtlDispatchHandlers:', start)]
fixture = r'''
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#include <assert.h>
typedef struct { unsigned sid, planes; } Surface;
static unsigned IOSurfaceGetID(Surface *s) { return s->sid; }
static unsigned IOSurfaceGetPlaneCount(Surface *s) { return s->planes; }
@interface NVMTLTexture : NSObject { @public
    NVMTLTexture *_parent; Surface *_surf; BOOL _vramOn;
} @end
@implementation NVMTLTexture @end
@interface NVMTLSubmissionPart : NSObject { @public NSArray *surfaces; } @end
@implementation NVMTLSubmissionPart @end
@interface NVMTLCommandBuffer : NSObject { @public NSArray *_parts; NVMTLTexture *_present; } @end
@implementation NVMTLCommandBuffer @end
@interface NVMTLSurfaceHolds : NSObject { @public NSSet *ids, *dirtied; BOOL early; } @end
@implementation NVMTLSurfaceHolds @end
static const void *const nvmtl_ws_marks_key = &nvmtl_ws_marks_key;
'''
checks = r'''
static NVMTLTexture *texture(Surface *surface, BOOL shared) {
    NVMTLTexture *t=[NVMTLTexture new]; t->_surf=surface; t->_vramOn=shared; return t;
}
static NVMTLCommandBuffer *command(NSArray *textures) {
    NVMTLSubmissionPart *p=[NVMTLSubmissionPart new]; p->surfaces=textures;
    NVMTLCommandBuffer *c=[NVMTLCommandBuffer new]; c->_parts=@[p]; return c;
}
static void check(NVMTLCommandBuffer *c, BOOL delayed) {
    assert(nvmtl_surface_schedule_after_publish(c)==delayed);
}
int main(int argc, char **argv) { @autoreleasepool {
    Surface one={11,0}, two={12,2};
    NVMTLTexture *shared=texture(&one,YES), *private=texture(&one,NO), *nv12=texture(&two,NO);
    NVMTLCommandBuffer *c=command(@[shared]);
    int override=argc>1?atoi(argv[1]):-1;
    if (override>=0) {
        check(c,override==1);
        check(command(@[private]),override==1||override==2);
        check(command(@[nv12]),override!=0);
        c->_present=shared; check(c,NO);
        puts("PASS: explicit surface scheduling diagnostic override"); return 0;
    }
    check(command(@[]),NO); // ordinary offscreen work stays asynchronous
    check(command(@[[NVMTLTexture new]]),NO);
    check(c,YES); // shared allocation alone supplies no cross-process fence
    check(command(@[private]),YES);
    check(command(@[nv12]),YES);
    NVMTLSurfaceHolds *holds=[NVMTLSurfaceHolds new];
    holds->early=YES; holds->ids=[NSSet setWithObject:@11]; holds->dirtied=[NSSet setWithObject:shared];
    objc_setAssociatedObject(c,nvmtl_ws_marks_key,holds,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    check(c,NO); // accepted kernel mark AND early dirty publication
    holds->early=NO; check(c,YES); holds->early=YES;
    holds->ids=[NSSet set]; check(c,YES); holds->ids=[NSSet setWithObject:@11];
    holds->dirtied=[NSSet set]; check(c,YES); // refused early dirty mark must not expose old CPU pixels
    holds->dirtied=[NSSet setWithObject:shared];
    NVMTLSubmissionPart *extra=[NVMTLSubmissionPart new]; extra->surfaces=@[nv12];
    c->_parts=[c->_parts arrayByAddingObject:extra]; check(c,YES); // one unfenced output holds the handoff
    c=command(@[shared]); c->_present=shared; check(c,NO);
    NVMTLTexture *view=[NVMTLTexture new]; view->_parent=shared;
    c=command(@[view]); c->_present=shared; check(c,NO); // views of a presented drawable use its completion path
    c=command(@[private]);
    objc_setAssociatedObject(c,nvmtl_ws_marks_key,holds,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    check(c,YES); // pending marks cannot publish a private image's later copy-out
    puts("PASS: unfenced surface handoff, protected async path, refused marks, mixed outputs, drawable views");
} }
'''
with tempfile.TemporaryDirectory() as name:
    path = Path(name)
    (path / 'test.m').write_text(fixture + policy + checks)
    subprocess.run(['clang', '-fobjc-arc', str(path / 'test.m'), '-framework', 'Foundation',
                    '-o', str(path / 'test')], check=True)
    env = dict(os.environ)
    env.pop('NVMTL_SCHEDULE_AFTER_SURFACE_WRITES', None)
    subprocess.run([str(path / 'test')], env=env, check=True)
    for value in range(4):
        env['NVMTL_SCHEDULE_AFTER_SURFACE_WRITES'] = str(value)
        subprocess.run([str(path / 'test'), str(value)], env=env, check=True)
    env['NVMTL_SCHEDULE_AFTER_SURFACE_WRITES'] = 'invalid'
    subprocess.run([str(path / 'test')], env=env, check=True)
