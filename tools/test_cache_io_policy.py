#!/usr/bin/env python3
"""Exercise production shader-cache policy and dispatch with concurrent callers."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', type=Path, default=ROOT / 'plugin/NVMTLLibraryLoad.m')
args = parser.parse_args()
source = args.source.read_text()
start = source.index('static int nvmtl_cache_sync_io(void)')
end = source.index('// The touch only', start)
production = source[start:end]
fixture = r'''
#import <Foundation/Foundation.h>
#include <assert.h>
#include <libproc.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <unistd.h>

static const char *process_name;
static _Atomic unsigned name_queries;
static int test_proc_name(int pid, void *buffer, uint32_t size) {
    assert(pid == getpid());
    atomic_fetch_add(&name_queries, 1);
    // Widen first-call races without changing the process's real identity.
    usleep(10000);
    if (!strcmp(process_name, "<unavailable>")) return 0;
    return snprintf(buffer, size, "%s", process_name);
}
static void nvlog(const char *format, ...) { (void)format; }
#define proc_name test_proc_name
@@PRODUCTION@@
#undef proc_name

int main(int argc, char **argv) { @autoreleasepool {
    assert(argc == 3);
    process_name = argv[1];
    const int expected = atoi(argv[2]);
    _Atomic unsigned mismatches = 0;
    _Atomic unsigned *bad = &mismatches;
    dispatch_apply(64, dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^(size_t i) {
        (void)i;
        if (nvmtl_cache_sync_io() != expected) atomic_fetch_add(bad, 1);
    });
    assert(!atomic_load(&mismatches));
    assert(atomic_load(&name_queries) == (getenv("NVMTL_CACHE_SYNC_IO") ? 0u : 1u));

    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    __block BOOL ran = NO, same_thread = NO;
    pthread_t caller = pthread_self();
    if (!expected) dispatch_suspend(nvmtl_cache_io_queue());
    nvmtl_cache_io(^{
        same_thread = pthread_equal(caller, pthread_self());
        ran = YES;
        dispatch_semaphore_signal(done);
    });
    if (expected) {
        assert(ran && !gCacheIoQueue);
    } else {
        assert(!ran && gCacheIoQueue);
        dispatch_resume(gCacheIoQueue);
    }
    assert(!dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 2 * NSEC_PER_SEC)));
    assert(ran && same_thread == expected);
    nvmtl_cache_io_drain();
    printf("PASS process=%s setting=%s sync=%d concurrent_callers=64 name_queries=%u\n",
        process_name, getenv("NVMTL_CACHE_SYNC_IO") ?: "<unset>", expected,
        atomic_load(&name_queries));
    return 0;
} }
'''.replace('@@PRODUCTION@@', production)

cases = [
    ('prl_vm_app', None, 1),
    ('WindowServer', None, 0),
    ('Microsoft Edge Helper', None, 0),
    ('prl_vm_app_helper', None, 0),
    ('prefix_prl_vm_app', None, 0),
    ('<unavailable>', None, 0),
    ('prl_vm_app', '0', 0),
    ('prl_vm_app', '', 0),
    ('prl_vm_app', '1', 1),
    ('WindowServer', '1', 1),
    ('WindowServer', '0', 0),
    ('Microsoft Edge Helper', '', 0),
    ('WindowServer', 'true', 1),
    ('prl_vm_app', '0-test', 0),
]
with tempfile.TemporaryDirectory(prefix='nvmtl-cache-policy-') as directory:
    p = Path(directory)
    (p / 'fixture.m').write_text(fixture)
    subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-fblocks', '-O1',
                    '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                    '-fno-sanitize-recover=all', str(p / 'fixture.m'),
                    '-framework', 'Foundation', '-o', str(p / 'fixture')], check=True)
    for name, setting, expected in cases:
        env = dict(os.environ)
        env.pop('NVMTL_CACHE_SYNC_IO', None)
        if setting is not None:
            env['NVMTL_CACHE_SYNC_IO'] = setting
        subprocess.run([str(p / 'fixture'), name, str(expected)], env=env,
                       check=True, timeout=10)
print(f'PASS: {len(cases)} process/override cases, 64 concurrent policy callers per case, sync/async dispatch and drain')
