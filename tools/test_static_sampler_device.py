#!/usr/bin/env python3
"""Static samplers must be made on the supplied shader device, never the system default."""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/rotation'))
from test_support import method

source = (ROOT / 'plugin/NVMTLEmbedded.m').read_text()
factory = '\n'.join(method(source, name) for name in (
    'static NSUInteger nvmtl_ss_enum(', 'static id<MTLSamplerState> nvmtl_static_sampler_from('))
assert 'nvmtl_static_sampler_from(st, fn.device)' in source
fixture = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <assert.h>
#define nvlog(...) ((void)0)
static unsigned default_queries;
id<MTLDevice> MTLCreateSystemDefaultDevice(void) { default_queries++;return nil; }
@interface Owner : NSObject { @public unsigned calls; MTLSamplerDescriptor *last; id result; }
- (id<MTLSamplerState>)newSamplerStateWithDescriptor:(MTLSamplerDescriptor *)d;
@end
@implementation Owner
- (id<MTLSamplerState>)newSamplerStateWithDescriptor:(MTLSamplerDescriptor *)d {
    calls++;last=[d copy];return result;
}
@end
@@FACTORY@@
int main(void) { @autoreleasepool {
    Owner *a=[Owner new],*b=[Owner new];a->result=[NSObject new];b->result=[NSObject new];
    id sa=nvmtl_static_sampler_from(@{},(id)a), sb=nvmtl_static_sampler_from(@{},(id)b);
    assert(sa==a->result && sb==b->result && sa!=sb && a->calls==1 && b->calls==1);
    assert(a->last.minFilter==MTLSamplerMinMagFilterNearest && a->last.normalizedCoordinates);
    sa=nvmtl_static_sampler_from(@{@"min_filter":@"Linear",@"coordinates":@"Pixel"},(id)a);
    assert(sa==a->result && a->calls==2 && a->last.minFilter==MTLSamplerMinMagFilterLinear && !a->last.normalizedCoordinates);
    assert(!nvmtl_static_sampler_from(@{@"mag_filter":@"Invalid"},(id)a) && a->calls==2);
    a->result=nil;assert(!nvmtl_static_sampler_from(@{},(id)a) && a->calls==3);
    assert(!nvmtl_static_sampler_from(@{},nil) && !default_queries);
    puts("PASS: distinct shader devices own their samplers; descriptors, invalid metadata and allocation failure preserved; no system-default lookup");
} }
'''
with tempfile.TemporaryDirectory(prefix='nvmtl-static-sampler-device-') as directory:
    directory = Path(directory)
    (directory / 'test.m').write_text(fixture.replace('@@FACTORY@@', factory))
    subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-O2', '-fsanitize=address,undefined',
                    '-fno-sanitize-recover=all', str(directory / 'test.m'), '-framework', 'Foundation',
                    '-framework', 'Metal', '-o', str(directory / 'test')], check=True)
    subprocess.run([str(directory / 'test')], check=True)
