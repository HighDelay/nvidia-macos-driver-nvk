#!/usr/bin/env python3
"""Test the production metadata fast path together with real sampler restoration.

Only argument-buffer field decoding (unchanged by this optimization) is stubbed.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/rotation'))
from test_support import method

embedded = (ROOT / 'plugin/NVMTLEmbedded.m').read_text()
backend = (ROOT / 'plugin/nvmtl_vk.c').read_text()
bind = method(embedded, 'void nvmtl_bind_embedded(')
bind = bind[:bind.index('    extern int nvmtl_vk_emb_same')] + '    slow_path++;\n}\n'
dictionary = method(embedded, 'void nvmtl_bind_embedded_dict(')
restore = '\n'.join(method(backend, name) for name in (
    'static int nvmtl_sampler_restore_on(', 'int nvmtl_vk_bind_parked_sampler(',
    'void nvmtl_vk_parked_samplers_keep('))
assert 'uint8_t _bindingMetadataEmpty;' in (ROOT / 'plugin/NVMTLObjects.h').read_text()

fixture = r'''
#import <Foundation/Foundation.h>
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
enum { NVMTL_NSAMP=32, NVMTL_NBUF=32, NVMTL_NTEX=128, NVMTL_NBUFAT=32,
       NVMTL_SLOT_NONE=0, NVMTL_SLOT_SAMP=3 };
#define nvlog(...) ((void)0)
typedef struct { uint8_t kind; void *h; } nvmtl_slot;
typedef struct {
    nvmtl_slot s[2][192]; uint32_t park_mask[2];
    uint8_t park_kind[2][32],park_saved_kind[2][32];
    void *park_h[2][32],*park_saved[2][32];
} nvmtl_bindtable;
typedef struct { void *table; } nvk_cmdbuf;
typedef struct { void *s; } nvk_sampler;
static int nvmtl_bind_slot(nvk_cmdbuf *c,uint32_t set,uint32_t slot,uint8_t kind,void *h,size_t off) {
    ((nvmtl_bindtable *)c->table)->s[set][slot]=(nvmtl_slot){kind,h};return 0;
}
static int nvmtl_vk_bind_sampler(nvk_cmdbuf *c,uint32_t set,uint32_t index,nvk_sampler *s) {
    return nvmtl_bind_slot(c,set,160+index,s?NVMTL_SLOT_SAMP:NVMTL_SLOT_NONE,s?s->s:NULL,0);
}
@@RESTORE@@
@interface NVMTLFunction : NSObject { @public uint8_t _bindingMetadataEmpty;
    NSArray *statics, *plans; unsigned static_reads, plan_reads; } @end
@implementation NVMTLFunction @end
@interface NVMTLCommandBuffer : NSObject { @public nvk_cmdbuf _c; } @end
@implementation NVMTLCommandBuffer @end
@interface NVMTLBuffer : NSObject @end
@implementation NVMTLBuffer @end
@interface NVMTLSamplerState : NSObject { @public nvk_sampler _s; } @end
@implementation NVMTLSamplerState @end
@interface NVMTLEmbeddedPlan : NSObject { @public BOOL wantSampler; uint32_t slot; } @end
@implementation NVMTLEmbeddedPlan @end
static BOOL static_enabled=YES;
static unsigned slow_path, retained;
static int nvmtl_static_samplers_on(void) { return static_enabled; }
static NSArray *nvmtl_static_samplers(NVMTLFunction *fn) {
    __atomic_add_fetch(&fn->static_reads,1,__ATOMIC_RELAXED); return fn->statics;
}
static NSArray *nvmtl_embedded_plan(NVMTLFunction *fn) {
    __atomic_add_fetch(&fn->plan_reads,1,__ATOMIC_RELAXED); return fn->plans;
}
static void nvmtl_retain_resource(NVMTLCommandBuffer *cb,id resource) { retained++; }
@@BIND@@
@@DICTIONARY@@
static NVMTLFunction *function(NSArray *statics,NSArray *plans) {
    NVMTLFunction *fn=[NVMTLFunction new]; fn->statics=statics;fn->plans=plans;return fn;
}
int main(void) { @autoreleasepool {
    NVMTLCommandBuffer *cb=[NVMTLCommandBuffer new]; nvmtl_bindtable t={0};cb->_c.table=&t;
    NVMTLFunction *empty=function(@[],@[]), *other=function(@[],@[]);
    nvk_sampler app={(void *)1},shader={(void *)2},replacement={(void *)3};
    nvmtl_bind_embedded(nil,0,empty,NULL,NULL);nvmtl_bind_embedded(cb,0,nil,NULL,NULL);
    assert(!empty->static_reads && !empty->plan_reads);
    nvmtl_vk_bind_sampler(&cb->_c,0,7,&app);nvmtl_vk_bind_parked_sampler(&cb->_c,0,7,&shader);
    nvmtl_bind_embedded(cb,0,empty,NULL,NULL);
    assert(t.s[0][167].h==app.s && !t.park_mask[0] && empty->_bindingMetadataEmpty);
    for(unsigned i=0;i<10000;i++) nvmtl_bind_embedded(cb,0,empty,NULL,NULL);
    assert(empty->static_reads==1 && empty->plan_reads==1);
    // A warm empty shader must still undo a different shader's parked sampler.
    nvmtl_vk_bind_sampler(&cb->_c,1,31,&app);nvmtl_vk_bind_parked_sampler(&cb->_c,1,31,&shader);
    nvmtl_bind_embedded(cb,1,empty,NULL,NULL);assert(t.s[1][191].h==app.s && !t.park_mask[1]);
    nvmtl_vk_bind_parked_sampler(&cb->_c,0,7,&shader);nvmtl_vk_bind_sampler(&cb->_c,0,7,&replacement);
    nvmtl_bind_embedded(cb,0,empty,NULL,NULL);assert(t.s[0][167].h==replacement.s);
    nvmtl_bind_embedded(cb,0,other,NULL,NULL);assert(other->plan_reads==1);
    // Functions that own a static or argument-buffer sampler never take the empty path.
    NVMTLSamplerState *ss=[NVMTLSamplerState new];ss->_s=shader;
    NVMTLFunction *fixed=function(@[@[@7,ss]],@[]);
    for(unsigned i=0;i<2;i++) {
        nvmtl_bind_embedded(cb,0,fixed,NULL,NULL);assert(t.s[0][167].h==shader.s);
        nvmtl_bind_embedded(cb,0,empty,NULL,NULL);assert(t.s[0][167].h==replacement.s);
    }
    assert(!fixed->_bindingMetadataEmpty && fixed->plan_reads==2 && retained==2);
    NVMTLEmbeddedPlan *plan=[NVMTLEmbeddedPlan new];plan->wantSampler=YES;plan->slot=7;
    NVMTLFunction *argument=function(@[],@[plan]);
    nvmtl_vk_bind_parked_sampler(&cb->_c,0,7,&shader);
    nvmtl_bind_embedded(cb,0,argument,NULL,NULL);
    assert(t.s[0][167].h==shader.s && slow_path==1 && !argument->_bindingMetadataEmpty);
    plan->wantSampler=NO;
    nvmtl_bind_embedded(cb,0,argument,NULL,NULL);
    assert(t.s[0][167].h==replacement.s && slow_path==2);
    // The compute dictionary entry point skips its extra lookup once empty is known.
    for(unsigned i=0;i<1000;i++) nvmtl_bind_embedded_dict(cb,0,empty,@{});
    assert(empty->static_reads==1 && empty->plan_reads==1);
    NVMTLFunction *compute=function(@[],@[]);
    nvmtl_bind_embedded_dict(cb,0,compute,@{});unsigned reads=compute->plan_reads;
    nvmtl_bind_embedded_dict(cb,0,compute,@{});assert(compute->plan_reads==reads);
    // Concurrent cold/warm readers share only the monotonic atomic empty flag.
    NVMTLFunction *shared=function(@[],@[]);
    dispatch_apply(128,dispatch_get_global_queue(QOS_CLASS_USER_INITIATED,0),^(size_t index) { @autoreleasepool {
        NVMTLCommandBuffer *local=[NVMTLCommandBuffer new];nvmtl_bindtable local_table={0};local->_c.table=&local_table;
        for(unsigned i=0;i<100;i++) nvmtl_bind_embedded(local,index&1,shared,NULL,NULL);
    }});
    assert(shared->_bindingMetadataEmpty && shared->plan_reads>0);
    reads=shared->plan_reads;nvmtl_bind_embedded(cb,0,shared,NULL,NULL);assert(shared->plan_reads==reads);
    // Static-sampler opt-out still omits only static metadata; embedded work continues.
    static_enabled=NO;NVMTLFunction *disabled=function(@[@[@7,ss]],@[]);
    nvmtl_bind_embedded(cb,0,disabled,NULL,NULL);assert(disabled->_bindingMetadataEmpty && !disabled->static_reads);
    puts("PASS: cold/warm empty metadata, 10000 lookup elisions, sampler restoration after shader/app changes, independent stages/functions, nonempty static/argument plans, compute dictionaries and concurrent readers");
} }
'''
with tempfile.TemporaryDirectory(prefix='nvmtl-binding-metadata-') as directory:
    directory = Path(directory)
    source = directory / 'test.m'
    source.write_text(fixture.replace('@@RESTORE@@', restore).replace('@@BIND@@', bind).replace('@@DICTIONARY@@', dictionary))
    subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-O2', '-g', '-fsanitize=address,undefined',
                    '-fno-sanitize-recover=all', str(source), '-framework', 'Foundation',
                    '-o', str(directory / 'test')], check=True)
    subprocess.run([str(directory / 'test')], check=True)
