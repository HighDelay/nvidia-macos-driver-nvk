#!/usr/bin/env python3
"""Exercise the Metal ICD's actual page/memory fallback, including cleanup."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
text = (ROOT / 'plugin/nvmtl_vk.c').read_text()
start = text.index('static long nvmtl_sysconf(')
body = text[start:text.index('\n}\n', start) + 3]
source = r'''
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <mach/mach.h>
#include <sys/sysctl.h>
static long normal = -1;
static int failure = EPERM, sysctl_failure, host_failure, allocated, released;
static unsigned page = 4096;
static uint64_t bytes = 16ull << 30;
static long query(int name) { (void)name; errno = failure; return normal; }
static int memory(const char *key, void *out, size_t *size, void *input, size_t n) {
    (void)input; (void)n; assert(!strcmp(key,"hw.memsize"));
    if (sysctl_failure) { errno = EACCES; return -1; }
    *(uint64_t *)out = bytes; *size = sizeof(bytes); return 0;
}
static mach_port_t test_host(void) { allocated++; return 123; }
static kern_return_t info(mach_port_t p, host_flavor_t f, host_info_t out, mach_msg_type_number_t *count) {
    assert(p == 123 && f == HOST_BASIC_INFO && *count == HOST_BASIC_INFO_COUNT);
    ((host_basic_info_t)out)->max_mem = bytes; return host_failure ? KERN_FAILURE : KERN_SUCCESS;
}
static kern_return_t release(ipc_space_t task, mach_port_t p) {
    (void)task; assert(p == 123); released++; return KERN_SUCCESS;
}
#define sysconf query
#define sysctlbyname memory
#define mach_host_self test_host
#define host_info info
#define mach_port_deallocate release
#define vm_page_size page
PRODUCTION
int main(void) {
    errno=EDOM; assert(nvmtl_sysconf(_SC_PAGESIZE)==4096 && errno==EDOM && !allocated);
    errno=EDOM; assert(nvmtl_sysconf(_SC_PHYS_PAGES)==(long)(bytes/page) && errno==EDOM && !allocated);
    sysctl_failure=1; errno=EDOM;
    assert(nvmtl_sysconf(_SC_PHYS_PAGES)==(long)(bytes/page) && errno==EDOM && allocated==1 && released==1);
    host_failure=1; assert(nvmtl_sysconf(_SC_PHYS_PAGES)==-1 && errno==EPERM && allocated==released);
    sysctl_failure=host_failure=0; bytes=0;
    assert(nvmtl_sysconf(_SC_PHYS_PAGES)==-1 && errno==EPERM && allocated==released);
    page=0; assert(nvmtl_sysconf(_SC_PAGESIZE)==-1 && errno==EPERM); page=1; bytes=UINT64_MAX;
    assert(nvmtl_sysconf(_SC_PHYS_PAGES)==-1 && errno==EPERM);
    assert(nvmtl_sysconf(_SC_OPEN_MAX)==-1 && errno==EPERM);
    normal=512; failure=0; assert(nvmtl_sysconf(_SC_PAGESIZE)==512 && errno==0);
    assert(allocated==released);
    puts("PASS: Metal ICD page-size and physical-memory fallback, original errors, overflow and Mach port cleanup");
}
'''
with tempfile.TemporaryDirectory(prefix='nvmtl-memory-') as directory:
    folder = Path(directory)
    (folder / 'test.c').write_text(source.replace('PRODUCTION', body))
    subprocess.run(['xcrun', 'clang', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                    str(folder / 'test.c'), '-o', str(folder / 'test')], check=True)
    subprocess.run([str(folder / 'test')], check=True)
