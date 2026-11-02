#!/usr/bin/env python3
"""Exercise production Metal sampler creation/destruction and VkSampler interning without a GPU.

Only Vulkan allocation and resource-ID assignment are doubles. Equal descriptors must create independent wrappers,
with caller-specific labels, pixel metadata and lazy ID fields, while sharing refcounted backend handles. Dropping
wrappers must release them immediately even inside an undrained pool, and destroy the handle only after its last owner.
"""
import os
import pathlib
import re
import subprocess
import tempfile

root = pathlib.Path(__file__).resolve().parents[1]
device = (root / 'plugin/NVMTLDevice.m').read_text()
objects = (root / 'plugin/NVMTLObjects.m').read_text()
header = (root / 'plugin/NVMTLObjects.h').read_text()
backend = (root / 'plugin/nvmtl_vk.c').read_text()


def method(source, signature):
    start = source.index(signature)
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


create = method(device, '- (id<MTLSamplerState>)newSamplerStateWithDescriptor:')
iface = re.search(r'@interface NVMTLSamplerState : NSObject .*?@end', header, re.S).group(0)
extension = re.search(r'@interface NVMTLSamplerState \(\) .*?@end', header, re.S).group(0)
start = objects.index('@implementation NVMTLSamplerState')
sampler = objects[start:objects.index('@end', start) + 4]
start = backend.index('#define NVMTL_SAMPLER_INTERN_CAP')
intern = backend[start:backend.index('static int nvmtl_table_ubo(', start)]
backend_methods = '\n'.join(method(backend, signature) for signature in [
    'int nvmtl_vk_sampler_create(', 'int nvmtl_vk_sampler_create_desc(', 'void nvmtl_vk_sampler_destroy('])

fixture = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <assert.h>
#include <pthread.h>
#define VK_NO_PROTOTYPES 1
#include <vulkan/vulkan.h>
#include "nvmtl_vk.h"
#include "nvmtl_sampler_state.h"
#define nvlog(...) ((void)0)
#define VKCK(call, label) do { if ((call) != VK_SUCCESS) return -1; } while (0)
static VkDevice g_dev;
static VkPhysicalDeviceLimits g_lim = { .maxSamplerAllocationCount = 4096, .maxSamplerAnisotropy = 16 };
static int g_sampler_anisotropy = 1, g_sampler_mirror_clamp = 1, fail_init, fail_create;
static unsigned created, destroyed, resid_calls;
static uintptr_t next_handle;
int nvmtl_vk_init(void) { return fail_init; }
static VkResult pvkCreateSampler(VkDevice dev, const VkSamplerCreateInfo *info,
                               const VkAllocationCallbacks *alloc, VkSampler *out) {
    if (fail_create) return VK_ERROR_OUT_OF_HOST_MEMORY;
    *out = (VkSampler)(++next_handle); ++created; return VK_SUCCESS;
}
static void pvkDestroySampler(VkDevice dev, VkSampler sampler, const VkAllocationCallbacks *alloc) {
    assert(sampler && destroyed < created); ++destroyed;
}
@@INTERN@@
@@BACKEND_METHODS@@
@@IFACE@@
@@EXTENSION@@
id gNVMTLMainDevice;
// This double assigns no heap slot and retains no wrapper; test only the wrapper's lazy per-object field.
uint64_t nvmtl_resid_for_sampler(id sampler) { return ++resid_calls; }
@@SAMPLER@@
@interface NVMTLDevice : NSObject
- (id<MTLSamplerState>)newSamplerStateWithDescriptor:(MTLSamplerDescriptor *)descriptor;
@end
@implementation NVMTLDevice
@@CREATE@@
@end

__attribute__((noinline)) static void exercise(void) {
    NVMTLDevice *device = [NVMTLDevice new], *other = [NVMTLDevice new];
    __weak NVMTLDevice *weakDevice = device;
    __weak NVMTLDevice *weakOther = other;
    MTLSamplerDescriptor *sd = [MTLSamplerDescriptor new];
    sd.mipFilter = MTLSamplerMipFilterNearest; sd.lodMaxClamp = 1000;
    NSMutableString *label = [NSMutableString stringWithString:@"first"];
    sd.label = label;
    __attribute__((objc_precise_lifetime)) NVMTLSamplerState *a = (id)[device newSamplerStateWithDescriptor:sd];
    assert(a && [a.label isEqual:@"first"] && !a->_pixelState && !a->_resid);
    [label appendString:@" changed"]; sd.label = @"second";
    __attribute__((objc_precise_lifetime)) NVMTLSamplerState *b = (id)[device newSamplerStateWithDescriptor:sd];
    assert(b && a != b && a->_s.s == b->_s.s && created == 1 && !destroyed);
    assert([a.label isEqual:@"first"] && [b.label isEqual:@"second"]);
    MTLResourceID rid = a.gpuResourceID;
    assert(rid._impl && a.gpuResourceID._impl == rid._impl && resid_calls == 1 && !b->_resid);
    // Repeated equal requests keep only the anchor's backend allocation, with no retained wrapper cache.
    for (int k = 0; k < 10000; k++) {
        NVMTLSamplerState *temporary = (id)[device newSamplerStateWithDescriptor:sd];
        __weak NVMTLSamplerState *weak = temporary;
        assert(temporary != a && temporary != b && temporary->_s.s == a->_s.s && !temporary->_resid);
        temporary = nil; assert(!weak && created == 1 && !destroyed);
    }
    __attribute__((objc_precise_lifetime)) NVMTLSamplerState *cross = (id)[other newSamplerStateWithDescriptor:sd];
    assert(cross != a && cross != b && cross->_s.s == a->_s.s && created == 1);
    __weak NVMTLSamplerState *weakA = a;
    __weak NVMTLSamplerState *weakB = b;
    __weak NVMTLSamplerState *weakCross = cross;
    a = nil; assert(!weakA && !destroyed && b->_s.s);
    b = nil; assert(!weakB && !destroyed && cross->_s.s);
    cross = nil; assert(!weakCross && destroyed == 1 && !g_sampler_intern_live);

    // Pixel samplers can share a generic backend handle while their translation metadata differs.
    sd.normalizedCoordinates = NO; sd.label = @"pixel-a"; sd.lodMinClamp = 3;
    __attribute__((objc_precise_lifetime)) NVMTLSamplerState *p = (id)[device newSamplerStateWithDescriptor:sd];
    sd.label = @"pixel-b"; sd.lodMinClamp = 7;
    __attribute__((objc_precise_lifetime)) NVMTLSamplerState *q = (id)[device newSamplerStateWithDescriptor:sd];
    assert(p && q && p != q && p->_s.s == q->_s.s && created == 2);
    assert([p->_pixelState[@"lod_min_clamp"] intValue] == 3 && [q->_pixelState[@"lod_min_clamp"] intValue] == 7);
    assert([p.label isEqual:@"pixel-a"] && [q.label isEqual:@"pixel-b"]);
    assert([p->_pixelState[@"coordinates"] isEqual:@"Pixel"]);
    __weak NVMTLSamplerState *weakP = p;
    __weak NVMTLSamplerState *weakQ = q;
    p = nil; assert(!weakP && destroyed == 1 && q->_s.s);
    q = nil; assert(!weakQ && destroyed == 2 && !g_sampler_intern_live);

    unsigned before = created;
    fail_init = 1; assert(![device newSamplerStateWithDescriptor:sd]); fail_init = 0;
    fail_create = 1; assert(![device newSamplerStateWithDescriptor:sd]); fail_create = 0;
    assert(created == before && destroyed == before && !g_sampler_intern_live);
    sd.normalizedCoordinates = YES;
    // No backend allocation survives a completed create/release pair in this undrained pool.
    for (int k = 0; k < 1000; k++) {
        NVMTLSamplerState *temporary = (id)[device newSamplerStateWithDescriptor:sd];
        __weak NVMTLSamplerState *weak = temporary; assert(temporary);
        temporary = nil; assert(!weak && created == destroyed && !g_sampler_intern_live);
    }
    device = nil; other = nil; assert(!weakDevice && !weakOther);
}
int main(void) {
    @autoreleasepool { exercise(); assert(created == destroyed && !g_sampler_intern_live); }
    puts("PASS: independent labels/pixel metadata/lazy ID fields; 10000 shared-handle requests; immediate wrapper release; last-owner backend destruction; failures; device lifetime");
}
'''
for key, value in {'INTERN': intern, 'BACKEND_METHODS': backend_methods, 'IFACE': iface, 'EXTENSION': extension,
                   'SAMPLER': sampler, 'CREATE': create}.items():
    fixture = fixture.replace(f'@@{key}@@', value)

with tempfile.TemporaryDirectory(prefix='nvmtl-sampler-wrapper-') as tmp:
    path = pathlib.Path(tmp)
    (path / 'test.m').write_text(fixture)
    subprocess.run(['xcrun', 'clang', '-O2', '-fobjc-arc', '-g', '-fsanitize=address,undefined',
                    '-fno-sanitize-recover=all', str(path / 'test.m'), '-I' + str(root / 'plugin'),
                    '-I' + str(pathlib.Path.home() / 'Vulkan-Headers/include'),
                    '-framework', 'Foundation', '-framework', 'Metal', '-o', str(path / 'test')], check=True)
    for opt_out in (False, True):
        env = os.environ.copy()
        env.pop('NVMTL_NO_SAMPLER_CONTRACT', None)
        if opt_out:
            env['NVMTL_NO_SAMPLER_CONTRACT'] = '1'
        subprocess.run([str(path / 'test')], env=env, check=True, timeout=30)
