#!/usr/bin/env python3
"""Exercise production page-query fallback and its original-error semantics."""
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
source=(ROOT/'nvk/native-macos/bridge.mm').read_text()
start=source.index('long sandbox_sysconf(')
body=source[start:source.index('\n}\n',start)+3]
harness=r'''
#include <cassert>
#include <cerrno>
#include <climits>
#include <cstdint>
#include <cstdio>
#include <unistd.h>
#include <mach/mach.h>
#include <sys/sysctl.h>
static long result=-1;
static int error=EPERM,sysctl_error,host_error,hosts,freed;
static uint64_t total=16ull<<30;
static unsigned page=4096;
static long fake_sysconf(int name){(void)name;errno=error;return result;}
static int fake_sysctl(const char *name,void *out,size_t *length,void *,size_t){
    assert(!strcmp(name,"hw.memsize"));if(sysctl_error){errno=EACCES;return -1;}
    *(uint64_t *)out=total;*length=sizeof total;return 0;
}
static mach_port_t fake_host(void){hosts++;return 77;}
static kern_return_t fake_info(mach_port_t host,host_flavor_t flavor,host_info_t value,mach_msg_type_number_t *count){
    assert(host==77&&flavor==HOST_BASIC_INFO&&*count==HOST_BASIC_INFO_COUNT);
    ((host_basic_info_t)value)->max_mem=total;return host_error?KERN_FAILURE:KERN_SUCCESS;
}
static kern_return_t fake_deallocate(ipc_space_t,mach_port_t host){assert(host==77);freed++;return KERN_SUCCESS;}
#define sysconf fake_sysconf
#define sysctlbyname fake_sysctl
#define mach_host_self fake_host
#define host_info fake_info
#define mach_port_deallocate fake_deallocate
#define vm_page_size page
PRODUCTION
int main(){
    errno=EDOM;assert(sandbox_sysconf(_SC_PAGESIZE)==4096&&errno==EDOM&&!hosts);
    errno=EDOM;assert(sandbox_sysconf(_SC_PHYS_PAGES)==(long)(total/4096)&&errno==EDOM&&!hosts);
    sysctl_error=1;errno=EDOM;assert(sandbox_sysconf(_SC_PHYS_PAGES)==(long)(total/4096)&&errno==EDOM&&hosts==1&&freed==1);
    host_error=1;errno=EDOM;assert(sandbox_sysconf(_SC_PHYS_PAGES)==-1&&errno==EPERM&&hosts==2&&freed==2);
    sysctl_error=host_error=0;total=0;errno=EDOM;assert(sandbox_sysconf(_SC_PHYS_PAGES)==-1&&errno==EPERM&&hosts==3&&freed==3);
    total=1;errno=EDOM;assert(sandbox_sysconf(_SC_PHYS_PAGES)==-1&&errno==EPERM);
    page=0;errno=EDOM;assert(sandbox_sysconf(_SC_PAGESIZE)==-1&&errno==EPERM);page=4096;
    errno=EDOM;assert(sandbox_sysconf(_SC_OPEN_MAX)==-1&&errno==EPERM);
    result=512;error=0;assert(sandbox_sysconf(_SC_PAGESIZE)==512&&errno==0);
    puts("PASS: page size, byte-to-page conversion, host fallback, port cleanup and original failures");
}
'''
harness=harness.replace('#include <cstdio>','#include <cstdio>\n#include <cstring>')
with tempfile.TemporaryDirectory(prefix='nvmtl-memory-query-') as directory:
    root=Path(directory);src=root/'test.cpp';binary=root/'test';src.write_text(harness.replace('PRODUCTION',body))
    subprocess.run(['xcrun','clang++','-std=c++17','-O1','-g','-fsanitize=address,undefined',str(src),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
