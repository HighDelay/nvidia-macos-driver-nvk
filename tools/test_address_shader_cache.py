#!/usr/bin/env python3
"""Exercise the production lowering cache's ownership, LRU, flags and concurrency."""
import argparse
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', type=Path, default=ROOT / 'plugin/nvmtl_vk.c')
parser.add_argument('--measure', action='store_true', help='measure the production byte budget in an isolated process')
parser.add_argument('--benchmark', action='store_true', help='time warm lookups with a shader working set below the production byte budget')
args = parser.parse_args()
text = args.source.read_text()
start = text.find('#ifndef NVMTL_ADDRESS_CACHE_BYTES')
if start < 0:
    start = text.index('static NSData *nvmtl_address_shader_g(const void *data, size_t len, uint32_t flags, uint32_t *info) {')
end = text.index('static _Atomic unsigned long long g_g1_descriptors;', start)
production = text[start:end]
fixture = r'''
#import <Foundation/Foundation.h>
#include <CommonCrypto/CommonDigest.h>
#include <assert.h>
#include <libproc.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include <sys/resource.h>
#include <time.h>
#include <unistd.h>
static _Atomic unsigned calls;
static _Atomic bool slow;
static NSData *nvmtl_address_shader_g_uncached(const void *data,size_t len,uint32_t flags,uint32_t *info) {
    atomic_fetch_add(&calls,1);
    if(atomic_load(&slow))usleep(10000);
    uint32_t ident=0; if(len>=4)memcpy(&ident,data,4);
    info[0]=ident; info[1]=flags; info[2]=(uint32_t)len; info[3]=ident^flags;
    if(ident==0xdead)return nil;
    NSMutableData *d=[NSMutableData dataWithBytes:data length:len];
    if(len)((uint8_t*)d.mutableBytes)[len-1]^=(uint8_t)flags;
    return d;
}
@@PRODUCTION@@
static NSMutableData *input(uint32_t ident,NSUInteger length) {
    NSMutableData *d=[NSMutableData dataWithLength:length];
    memset(d.mutableBytes,ident&255,length);if(length>=4)memcpy(d.mutableBytes,&ident,4);return d;
}
static NSData *lookup(uint32_t ident,NSUInteger length) {
    NSMutableData *d=input(ident,length);return nvmtl_address_shader_g(d.bytes,d.length,0,NULL);
}
int main(int argc,char **argv) { @autoreleasepool {
    assert(argc==2);NSString *mode=@(argv[1]);uint32_t a[4]={0},b[4]={0};
    if([mode isEqual:@"basic"]) {
        NSData *src=input(1,768),*x=nvmtl_address_shader_g(src.bytes,src.length,0,a);
        NSData *y=nvmtl_address_shader_g(src.bytes,src.length,0,b);
        assert([x isEqualToData:y]&&!memcmp(a,b,sizeof a)&&atomic_load(&calls)==1);
    } else if([mode isEqual:@"flags"]) {
        NSData *src=input(1,768);NSData *x=nvmtl_address_shader_g(src.bytes,src.length,1,a);
        NSData *y=nvmtl_address_shader_g(src.bytes,src.length,2,b);assert(![x isEqualToData:y]&&a[1]==1&&b[1]==2);
        uint32_t got[4];assert([x isEqualToData:nvmtl_address_shader_g(src.bytes,src.length,1,got)]&&!memcmp(a,got,sizeof a));assert(atomic_load(&calls)==2);
    } else if([mode isEqual:@"mutable-input"]) {
        NSMutableData *src=input(1,768);NSData*x=nvmtl_address_shader_g(src.bytes,src.length,0,a);
        ((uint8_t*)src.mutableBytes)[100]^=1;NSData*y=nvmtl_address_shader_g(src.bytes,src.length,0,b);
        assert(![x isEqualToData:y]&&atomic_load(&calls)==2);
    } else if([mode isEqual:@"negative"]) {
        NSData *src=input(0xdead,768);assert(!nvmtl_address_shader_g(src.bytes,src.length,3,a));
        assert(!nvmtl_address_shader_g(src.bytes,src.length,3,b));assert(!memcmp(a,b,sizeof a)&&a[0]==0xdead&&a[1]==3&&atomic_load(&calls)==1);
    } else if([mode isEqual:@"oversized"]) {
        __weak NSData *weak=nil;
        @autoreleasepool{NSData*d=lookup(1,8192);assert(d.length==8192);weak=d;}
        assert(!weak);assert(lookup(1,8192).length==8192&&atomic_load(&calls)==2);
    } else if([mode isEqual:@"lru"]) {
        NSData *first=nil;
        @autoreleasepool{first=lookup(1,768);lookup(2,768);lookup(3,768);}
        @autoreleasepool{lookup(1,768);lookup(4,768);}
        assert(atomic_load(&calls)==4);
        @autoreleasepool{lookup(1,768);}assert(atomic_load(&calls)==4);
        @autoreleasepool{lookup(2,768);}assert(atomic_load(&calls)==5);
        // A caller may retain a result after it has been evicted from the cache.
        @autoreleasepool{lookup(5,768);lookup(6,768);lookup(7,768);}
        assert([first isEqualToData:input(1,768)]);
    } else if([mode isEqual:@"concurrent"]) {
        NSData *src=input(1,768);_Atomic unsigned errors=0;_Atomic unsigned *bad=&errors;
        atomic_store(&slow,true);
        dispatch_apply(64,dispatch_get_global_queue(QOS_CLASS_USER_INITIATED,0),^(size_t i){@autoreleasepool{
            (void)i;
            uint32_t got[4];NSData*d=nvmtl_address_shader_g(src.bytes,src.length,0,got);
            if(![d isEqualToData:src]||got[0]!=1||got[1]!=0||got[2]!=768)atomic_fetch_add(bad,1);
        }});assert(!atomic_load(&errors));unsigned before=atomic_load(&calls);assert(before>1);
        atomic_store(&slow,false);
        @autoreleasepool{lookup(1,768);lookup(2,768);lookup(3,768);}
        assert(atomic_load(&calls)==before+2);@autoreleasepool{lookup(1,768);}assert(atomic_load(&calls)==before+2);
    } else if([mode isEqual:@"invalid"]) {
        memset(a,255,sizeof a);assert(!nvmtl_address_shader_g(NULL,1,0,a));assert(!a[0]&&!a[1]&&!a[2]&&!a[3]&&!atomic_load(&calls));
    } else if([mode isEqual:@"measure"]) {
        for(unsigned i=1;i<=128;i++){@autoreleasepool{NSData*d=lookup(i,1u<<20);assert(d.length==(1u<<20));}}
        struct rusage_info_v4 usage={0};assert(!proc_pid_rusage(getpid(),RUSAGE_INFO_V4,(rusage_info_t*)&usage));
        printf("{\"modules\":128,\"module_bytes\":1048576,\"footprint_bytes\":%llu,\"resident_bytes\":%llu,\"lowering_calls\":%u}\n",usage.ri_phys_footprint,usage.ri_resident_size,atomic_load(&calls));return 0;
    } else if([mode isEqual:@"hot-cache"]) {
        NSMutableArray *sources=[NSMutableArray new];
        for(unsigned i=1;i<=32;i++){NSMutableData *src=input(i,685*1024);[sources addObject:src];@autoreleasepool{assert(nvmtl_address_shader_g(src.bytes,src.length,0,NULL));}}
        const unsigned rounds=512;uint64_t start=clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW);
        for(unsigned i=0;i<rounds;i++){@autoreleasepool{NSData *src=sources[i%32];NSData *d=nvmtl_address_shader_g(src.bytes,src.length,0,NULL);assert(d.length==src.length);}}
        uint64_t elapsed=clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW)-start;assert(atomic_load(&calls)==32);
        printf("{\"modules\":32,\"module_bytes\":701440,\"warm_lookups\":%u,\"nanoseconds\":%llu,\"us_per_lookup\":%.3f,\"lowering_calls\":%u}\n",rounds,elapsed,(double)elapsed/1000/rounds,atomic_load(&calls));return 0;
    } else return 2;
    printf("PASS %s calls=%u\n",argv[1],atomic_load(&calls));return 0;
} }
'''.replace('@@PRODUCTION@@', production)
with tempfile.TemporaryDirectory(prefix='nvmtl-address-cache-') as directory:
    p = Path(directory)
    (p / 'fixture.m').write_text(fixture)
    flags = [] if args.measure or args.benchmark else ['-DNVMTL_ADDRESS_CACHE_BYTES=6144', '-fsanitize=address,undefined', '-fno-sanitize-recover=all']
    subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-O1', '-Wall', '-Wextra', '-Werror',
                    '-Wno-deprecated-declarations', *flags, str(p / 'fixture.m'),
                    '-framework', 'Foundation', '-o', str(p / 'fixture')], check=True)
    cases = ['measure'] if args.measure else ['hot-cache'] if args.benchmark else ['basic', 'flags', 'mutable-input', 'negative', 'oversized', 'lru', 'concurrent', 'invalid']
    for case in cases:
        subprocess.run([str(p / 'fixture'), case], check=True, timeout=45)
