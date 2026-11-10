#!/usr/bin/env python3
"""Exercise the actual function-constant warm-up queue and foreground takeover."""
from pathlib import Path
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[1]
src=(ROOT/'plugin/NVMTLLibraryLoad.m').read_text()
a=src.index('/* Function-constant warm-up.')
production=src[a:src.index('\nNSData *nvmtl_translate_with_fc_cached(',a)]

FIXTURE = r'''
#import <Foundation/Foundation.h>
#include <CommonCrypto/CommonDigest.h>
#include <os/lock.h>
#include <fcntl.h>
#include <unistd.h>
#include <assert.h>
#include <stdatomic.h>
#include <time.h>
#include <pthread.h>
#define nvlog(...) ((void)0)
@interface NVMTLAirDigest : NSObject { @public CC_SHA256_CTX _ctx; } @end
@implementation NVMTLAirDigest @end
static NSString *cacheDir;
static NSMutableArray<NSString *> *pathOrder;
static NSMutableDictionary<NSString *, NSNumber *> *pathIds;
static NSMutableSet<NSNumber *> *startedIds;
static dispatch_semaphore_t entered, releaseCompiler;
static _Atomic unsigned long gFcStored;
static NSString *nvmtl_spvcache_dir(void) { return cacheDir; }
static void nvmtl_cache_io(dispatch_block_t block) { block(); }
static NSString *nvmtl_spvcache_path_air(NVMTLAirDigest *ad, NSData *key, NSString *flavour) {
    (void)ad; (void)flavour;
    NSDictionary *cfg = [NSJSONSerialization JSONObjectWithData:key options:0 error:NULL];
    NSString *ident = [cfg[@"constants"] allKeys][0];
    NSString *path = [cacheDir stringByAppendingPathComponent:[ident stringByAppendingString:@".spv"]];
    [pathOrder addObject:path]; pathIds[path] = @(ident.integerValue);
    return path;
}
static NSData *nvmtl_translate_with_fc(NSString *ll, NSString *stage, const uint32_t *idx,
        const uint32_t *sizes, const uint8_t *const *pay, size_t n, NSString **err) {
    (void)ll; (void)stage; (void)sizes; (void)pay; (void)n; (void)err;
    @synchronized(startedIds) { [startedIds addObject:@(idx[0])]; }
    dispatch_semaphore_signal(entered);
    dispatch_semaphore_wait(releaseCompiler, DISPATCH_TIME_FOREVER);
    usleep(10000);   // a cheap, deterministic compiler; not a GPU benchmark
    uint32_t spv[5] = { 0x07230203, 0, 0, 0, 0 };
    return [NSData dataWithBytes:spv length:sizeof spv];
}
static BOOL nvmtl_is_spirv(NSData *d) { return d.length == 20; }
static int nvmtl_cache_write(NSString *path, NSData *d) { return [d writeToFile:path atomically:YES] ? 0 : -1; }
static void *releaseLater(void *unused) {
    (void)unused; usleep(300000);
    for (unsigned i = 0; i < 66; i++) dispatch_semaphore_signal(releaseCompiler);
    return NULL;
}
'''
CHECKS = r'''
int main(int argc, const char **argv) { @autoreleasepool {
    assert(argc==2); unsetenv("NVMTL_FCWARM_OFF"); cacheDir=@(argv[1]);
    pathOrder=[NSMutableArray array]; pathIds=[NSMutableDictionary dictionary]; startedIds=[NSMutableSet set];
    entered=dispatch_semaphore_create(0); releaseCompiler=dispatch_semaphore_create(0);
    NVMTLAirDigest *ad=[NVMTLAirDigest new]; CC_SHA256_Init(&ad->_ctx); CC_SHA256_Update(&ad->_ctx,"AIR",3);
    NSString *hex=nvmtl_fcwarm_air_hex(ad); NSMutableString *records=[NSMutableString string];
    for(unsigned i=0;i<67;i++) {
        NSData *key=[NSJSONSerialization dataWithJSONObject:@{@"stage":@"fragment",@"entry":@"fixed_frag",@"constants":@{[@(i) stringValue]:@[@1]}} options:NSJSONWritingSortedKeys error:NULL];
        [records appendFormat:@"%@ %@\n",hex,[key base64EncodedStringWithOptions:0]];
    }
    assert([records writeToFile:nvmtl_fcwarm_records_path() atomically:YES encoding:NSUTF8StringEncoding error:NULL]);
    nvmtl_fcwarm_kick(@"stub AIR",ad,[cacheDir stringByAppendingPathComponent:@"0.spv"]);
    for(unsigned i=0;i<2;i++) assert(!dispatch_semaphore_wait(entered,dispatch_time(DISPATCH_TIME_NOW,3*NSEC_PER_SEC)));
    os_unfair_lock_lock(&gFcWarmLock);
    assert(gFcWarmWorkers==2 && gFcWarmPending.count>0 && gFcWarmPending.count<=NVMTL_FCWARM_MAX_QUEUED);
    assert(gFcWarmInFlight.count<=NVMTL_FCWARM_MAX_QUEUED+NVMTL_FCWARM_WORKERS);
    NVMTLFcWarmJob *queued=gFcWarmPending.lastObject, *running=nil;
    for(NVMTLFcWarmJob *j in gFcWarmInFlight.allValues) if(j->started) {running=j;break;}
    assert(queued && running); os_unfair_lock_unlock(&gFcWarmLock);
    uint64_t start=clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW);
    assert(!nvmtl_fcwarm_join(queued->path));
    assert((clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW)-start)/1e6<100);
    os_unfair_lock_lock(&gFcWarmLock);
    assert(!gFcWarmInFlight[queued->path] && ![gFcWarmPending containsObject:queued]);
    os_unfair_lock_unlock(&gFcWarmLock);
    start=clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW);
    assert(!nvmtl_fcwarm_join(running->path));
    double waited=(clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW)-start)/1e6;
    assert(waited<100); // still returns while BOTH compiler slots remain blocked
    for(unsigned i=0;i<NVMTL_FCWARM_MAX_QUEUED+NVMTL_FCWARM_WORKERS;i++) dispatch_semaphore_signal(releaseCompiler);
    const uint64_t deadline=clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW)+3*NSEC_PER_SEC;
    for(;;) {
        os_unfair_lock_lock(&gFcWarmLock); BOOL done=!gFcWarmInFlight.count && !gFcWarmWorkers; os_unfair_lock_unlock(&gFcWarmLock);
        if(done)break; assert(clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW)<deadline); usleep(1000);
    }
    @synchronized(startedIds) { assert(![startedIds containsObject:pathIds[queued->path]]); }
    assert(![[NSFileManager defaultManager] fileExistsAtPath:queued->path]);
    // The withdrawn variant is now owned by the foreground path, with no stale queued compilation.
    const uint32_t *idx=queued->packed.bytes,*sizes=idx+queued->n;
    const uint8_t *const *payloads=(const uint8_t *const *)(sizes+queued->n);
    assert(nvmtl_translate_with_fc(queued->ll,queued->stage,idx,sizes,payloads,queued->n,NULL));
    printf("PASS: bounded admission, queued foreground takeover, %.2f ms running-job wait, complete drain and withdrawn ownership\n",waited);
} }
'''

with tempfile.TemporaryDirectory(prefix='fc-warm-priority-') as name:
    tmp=Path(name); (tmp/'test.m').write_text(FIXTURE+production+CHECKS)
    subprocess.run(['clang','-fobjc-arc','-O1','-Wno-deprecated-declarations','-fsanitize=address,undefined','-fno-sanitize-recover=all',str(tmp/'test.m'),'-framework','Foundation','-o',str(tmp/'test')],check=True)
    cache=tmp/'cache';cache.mkdir()
    subprocess.run([str(tmp/'test'),str(cache)],check=True,timeout=15)
