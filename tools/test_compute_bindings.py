#!/usr/bin/env python3
"""Exercise production compute binding caches and dispatch paths with a checking Vulkan backend."""
import pathlib
import subprocess
import tempfile

root = pathlib.Path(__file__).resolve().parents[1]
source = (root / 'plugin/nvmtl_vk.c').read_text()
def section(start, end):
    i = source.index(start)
    return source[i:source.index(end, i)]

helpers = section('static VkPipeline nvmtl_compute_variant_cached(', '\nint nvmtl_vk_cmd_dispatch_sass(')
# Extract complete functions independently, without depending on the next unrelated declaration.
def function(start):
    i = source.index(start)
    brace = source.index('{', i)
    depth = 1
    j = brace + 1
    while depth:
        depth += (source[j] == '{') - (source[j] == '}')
        j += 1
    return source[i:j]
dispatch = '\n'.join(function(name) for name in (
    'void nvmtl_vk_cmd_dispatch_threads(', 'void nvmtl_vk_cmd_dispatch_indirect_tg(',
    'void nvmtl_vk_cmd_dispatch(', 'void nvmtl_vk_cmd_dispatch_indirect(',
    'int nvmtl_vk_cmd_sum_occlusion('))
assert source.count('pvkCmdBindPipeline(c->cb, VK_PIPELINE_BIND_POINT_COMPUTE,') == 1
assert source.count('pvkCmdBindDescriptorSets(c->cb, VK_PIPELINE_BIND_POINT_COMPUTE,') == 1
sass = function('int nvmtl_vk_cmd_dispatch_sass(')
assert sass.index('nvmtl_compute_bindings_invalidate(c)') < sass.index('int rc = hook(')
assert 'memset(out, 0, sizeof *out)' in function('int nvmtl_vk_cmd_begin(')
fixture = r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <stdatomic.h>
#include <mach/mach.h>
#include <vulkan/vulkan.h>
#include "nvmtl_vk.h"
#define NVMTL_DISPATCH_PC_BYTES 48
#define nvlog(...) ((void)0)
static VkPhysicalDeviceLimits g_lim = {.maxComputeWorkGroupCount={65535,65535,65535}};
static VkDescriptorSet g_heapSet=(VkDescriptorSet)30;
static VkPipeline g_visibility_sum_pipeline=(VkPipeline)90;
static VkPipelineLayout g_visibility_sum_layout=(VkPipelineLayout)91;
static unsigned pipeline_calls, set_calls, dispatch_calls, push_calls, barrier_calls;
static unsigned ubo=1, fail_snapshot;
static unsigned variant_calls, fail_variant;
static VkPipeline bound_pipeline;
static VkDescriptorSet snapshots[2]={(VkDescriptorSet)10,(VkDescriptorSet)20};
static uint32_t snapshot_offsets[2]={0,256};
static VkDescriptorSet nvmtl_snapshot_set_c(nvk_cmdbuf *c,unsigned i,nvk_pipeline *p) {
    c->dyn_out[i]=snapshot_offsets[i]; return fail_snapshot ? VK_NULL_HANDLE : snapshots[i];
}
static VkDescriptorSet nvmtl_snapshot_set_t(nvk_cmdbuf *c,unsigned i,unsigned high,const uint64_t *mask) {
    return nvmtl_snapshot_set_c(c,i,NULL);
}
static int nvmtl_table_ubo(void) { return ubo; }
static nvk_pipeline *nvmtl_align_guard_pick(nvk_cmdbuf *c,nvk_pipeline *p) { return p; }
static VkPipeline nvmtl_compute_variant(nvk_pipeline *p,const uint32_t local[3]) {
    variant_calls++; if (fail_variant) return VK_NULL_HANDLE;
    return (VkPipeline)((uintptr_t)p->pipe + (local[0]==1 ? 1 : 0));
}
static int nvmtl_visibility_sum_init(void) { return 0; }
uint64_t nvmtl_vk_buffer_address(nvk_buffer *b) { return (uintptr_t)b->buf; }
void nvmtl_vk_cmd_barrier(nvk_cmdbuf *c) { barrier_calls++; }
static void pvkCmdBindPipeline(VkCommandBuffer cb,VkPipelineBindPoint point,VkPipeline p) {
    assert(point==VK_PIPELINE_BIND_POINT_COMPUTE); pipeline_calls++; bound_pipeline=p;
}
static void pvkCmdBindDescriptorSets(VkCommandBuffer cb,VkPipelineBindPoint point,VkPipelineLayout layout,
        unsigned first,unsigned count,const VkDescriptorSet *sets,unsigned ndyn,const unsigned *offsets) {
    assert(point==VK_PIPELINE_BIND_POINT_COMPUTE && first==0 && count>=2 && count<=3 && ndyn<=2);
    assert(sets[0] && sets[1]); set_calls++;
}
static void pvkCmdPushConstants(VkCommandBuffer cb,VkPipelineLayout layout,VkShaderStageFlags stage,
        unsigned offset,unsigned bytes,const void *data) { push_calls++; }
static void pvkCmdDispatch(VkCommandBuffer cb,unsigned x,unsigned y,unsigned z) {
    assert(bound_pipeline && x && y && z); dispatch_calls++;
}
static void pvkCmdDispatchIndirect(VkCommandBuffer cb,VkBuffer b,VkDeviceSize off) {
    assert(bound_pipeline && b); dispatch_calls++;
}
''' + helpers + '\n' + dispatch + r'''
int main(void) {
    nvk_cmdbuf c={0}; nvk_pipeline p={.pipe=(void *)50,.layout=(void *)40};
    nvk_buffer buffer={.buf=(void *)4096,.size=4096};
    const uint32_t threads[3]={64,1,1}, tg[3]={32,1,1}, tail[3]={65,1,1};
    for(unsigned i=0;i<10000;i++) nvmtl_vk_cmd_dispatch_threads(&c,&p,threads,tg);
    assert(pipeline_calls==1 && set_calls==1 && dispatch_calls==10000 && push_calls==10000);
    assert(variant_calls==1);
    puts("PASS: 10000 identical dispatches issue 1 pipeline bind and 1 descriptor bind; all dispatches and push constants retained");
    // All key inputs must force a rebind; unused dynamic-offset values must not.
    for(unsigned i=0;i<2;i++) {
        snapshot_offsets[i]+=256;
        unsigned n=set_calls; nvmtl_vk_cmd_dispatch_threads(&c,&p,threads,tg); assert(set_calls==n+1);
        snapshots[i]=(VkDescriptorSet)((uintptr_t)snapshots[i]+1);
        n=set_calls; nvmtl_vk_cmd_dispatch_threads(&c,&p,threads,tg); assert(set_calls==n+1);
    }
    unsigned n=set_calls;
    g_heapSet=(VkDescriptorSet)31; nvmtl_vk_cmd_dispatch_threads(&c,&p,threads,tg); assert(set_calls==++n);
    g_heapSet=VK_NULL_HANDLE; nvmtl_vk_cmd_dispatch_threads(&c,&p,threads,tg); assert(set_calls==++n);
    ubo=0; nvmtl_vk_cmd_dispatch_threads(&c,&p,threads,tg); assert(set_calls==++n);
    snapshot_offsets[0]+=256; nvmtl_vk_cmd_dispatch_threads(&c,&p,threads,tg); assert(set_calls==n);
    ubo=1; nvmtl_vk_cmd_dispatch_threads(&c,&p,threads,tg); assert(set_calls==++n);
    p.layout=(void *)41; nvmtl_vk_cmd_dispatch_threads(&c,&p,threads,tg); assert(set_calls==++n);
    unsigned pipelines=pipeline_calls;
    // Tail variant must be restored to full variant on the next dispatch.
    nvmtl_vk_cmd_dispatch_threads(&c,&p,tail,tg); assert(bound_pipeline==(VkPipeline)51);
    nvmtl_vk_cmd_dispatch_threads(&c,&p,threads,tg); assert(bound_pipeline==(VkPipeline)50);
    assert(pipeline_calls==pipelines+2 && set_calls==n);
    nvmtl_vk_cmd_dispatch_indirect_tg(&c,&p,&buffer,0,tg); assert(pipeline_calls==pipelines+2 && set_calls==n);
    nvmtl_vk_cmd_dispatch(&c,&p,1,1,1); assert(pipeline_calls==pipelines+2 && set_calls==n);
    nvmtl_vk_cmd_dispatch_indirect(&c,&p,&buffer,0); assert(pipeline_calls==pipelines+2);
    assert(nvmtl_vk_cmd_sum_occlusion(&c,&buffer,0,2,&buffer,16)==0);
    assert(bound_pipeline==g_visibility_sum_pipeline && barrier_calls==2);
    nvmtl_vk_cmd_dispatch_threads(&c,&p,threads,tg); assert(bound_pipeline==(VkPipeline)50 && pipeline_calls==pipelines+4);
    nvmtl_compute_bindings_invalidate(&c); // SASS entry, including failed hooks
    nvmtl_vk_cmd_dispatch_threads(&c,&p,threads,tg); assert(pipeline_calls==pipelines+5 && set_calls==++n);
    unsigned draws=dispatch_calls;
    fail_snapshot=1; nvmtl_vk_cmd_dispatch_threads(&c,&p,threads,tg);
    assert(c.nobind && dispatch_calls==draws);
    fail_snapshot=0; nvmtl_vk_cmd_dispatch_threads(&c,&p,threads,tg); assert(!c.nobind && dispatch_calls==draws+1);
    memset(&c,0,sizeof c);
    nvmtl_vk_cmd_dispatch_threads(&c,&p,threads,tg); assert(pipeline_calls==pipelines+6 && set_calls==++n);
    nvk_cmdbuf other={0};
    nvmtl_vk_cmd_dispatch_threads(&other,&p,threads,tg); assert(pipeline_calls==pipelines+7 && set_calls==++n);
    puts("PASS: layouts, descriptors, heap, offsets/counts, tail variants, indirect/raw dispatch, internal sums, SASS invalidation, failure and recording isolation");
    nvk_cmdbuf variants={0};
    unsigned calls=variant_calls;
    nvk_pipeline owners[9]={0};
    for(unsigned i=0;i<9;i++) {
        owners[i].pipe=(void *)(uintptr_t)(100+i);
        assert(nvmtl_compute_variant_cached(&variants,&owners[i],tg)==(VkPipeline)owners[i].pipe);
    }
    assert(variant_calls==calls+9);
    for(unsigned i=1;i<9;i++) nvmtl_compute_variant_cached(&variants,&owners[i],tg);
    assert(variant_calls==calls+9); // all eight recent entries remain cached
    nvmtl_compute_variant_cached(&variants,&owners[0],tg); assert(variant_calls==calls+10);
    uint32_t different[3]={32,2,1};
    fail_variant=1;
    assert(!nvmtl_compute_variant_cached(&variants,&owners[0],different));
    assert(!nvmtl_compute_variant_cached(&variants,&owners[0],different));
    assert(variant_calls==calls+12);
    fail_variant=0;
    assert(nvmtl_compute_variant_cached(&variants,&owners[0],different));
    nvmtl_compute_variant_cached(&variants,&owners[0],different); assert(variant_calls==calls+13);
    different[2]=2;
    nvmtl_compute_variant_cached(&variants,&owners[0],different); assert(variant_calls==calls+14);
    puts("PASS: repeated variant lookup avoids global mutex/search; bounded eviction, owner/size keys and failed-build retry");
}
'''
with tempfile.TemporaryDirectory(prefix='nvmtl-compute-bindings-') as tmp:
    path = pathlib.Path(tmp)
    (path / 'test.c').write_text(fixture)
    subprocess.run(['xcrun', 'clang', '-O2', '-fsanitize=address,undefined', str(path / 'test.c'),
                    '-I'+str(root/'plugin'), '-I'+str(pathlib.Path.home()/'Vulkan-Headers/include'),
                    '-o', str(path/'test')], check=True)
    subprocess.run([str(path/'test')], check=True)
