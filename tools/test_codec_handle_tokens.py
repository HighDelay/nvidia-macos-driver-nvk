#!/usr/bin/env python3
"""Exercise real codec tokens under sandbox denials, crowded fd tables and threads."""
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'plugin/NVMTLDevice.m').read_text()
def function(name):
    start=SOURCE.index('static int '+name+'(')
    return SOURCE[start:SOURCE.index('\n}\n',start)+3]
HARNESS=r'''
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <unistd.h>
static int denial, dup_failure, limit_failure, small_limit, mode_seen;
static int test_open(const char *path,int flags,...) {
    int mode=0;if(flags&O_CREAT){va_list a;va_start(a,flags);mode=va_arg(a,int);va_end(a);mode_seen=mode;}
    if(denial&&path&&!strcmp(path,"/dev/null")){errno=denial;return -1;}
    return open(path,flags,mode);
}
static int test_fcntl(int fd,int cmd,...) {
    va_list a;va_start(a,cmd);int value=va_arg(a,int);va_end(a);
    if(dup_failure&&(cmd==F_DUPFD||cmd==F_DUPFD_CLOEXEC)){errno=EMFILE;return -1;}
    return fcntl(fd,cmd,value);
}
static int test_limit(int name,struct rlimit *out) {
    if(limit_failure){errno=EACCES;return -1;}
    int rc=getrlimit(name,out);if(!rc&&small_limit)out->rlim_cur=64;return rc;
}
#define open test_open
#define fcntl test_fcntl
#define getrlimit test_limit
#include "nvmtl_fd_token.h"
PRODUCTION
#undef open
#undef fcntl
#undef getrlimit
static unsigned descriptors(void){unsigned n=0;for(int fd=0;fd<256;fd++)n+=fcntl(fd,F_GETFD)>=0;return n;}
static void *worker(void *unused){(void)unused;
    for(unsigned i=0;i<48;i++){int fd=nvmtl_codec_open("/dev/null",O_RDONLY|O_CLOEXEC);assert(fd>=128&&fd<256);assert(fcntl(fd,F_GETFD)&FD_CLOEXEC);char c;assert(!read(fd,&c,1));close(fd);}return NULL;
}
int main(int argc,char **argv){
    assert(argc==2);struct rlimit original,limit;assert(!getrlimit(RLIMIT_NOFILE,&original));limit=original;limit.rlim_cur=256;assert(original.rlim_max>=256&&!setrlimit(RLIMIT_NOFILE,&limit));
    unsigned before=descriptors();int owner=open("/dev/null",O_RDONLY);assert(owner>=0);
    int busy[93];for(unsigned i=0;i<93;i++){busy[i]=fcntl(owner,F_DUPFD,128);assert(busy[i]==128+(int)i);}
    // F_DUPFD skips 93 occupied slots; every result must advance the cursor.
    int previous=-1;
    for(unsigned i=0;i<8;i++){
        errno=EDOM;int fd=nvmtl_codec_open("/dev/null",O_RDWR|O_CLOEXEC);assert(fd>=221&&fd<256&&fd!=previous&&errno==EDOM);previous=fd;assert(fcntl(fd,F_GETFD)&FD_CLOEXEC);close(fd);
    }
    for(unsigned i=0;i<93;i++)close(busy[i]);close(owner);
    for(unsigned i=0;i<2;i++){
        denial=i?EACCES:EPERM;
        int fd=nvmtl_codec_open("/dev/null",O_RDWR|O_CLOEXEC);assert(fd>=128&&fd<256);char c;assert(!read(fd,&c,1));assert(write(fd,"x",1)==-1&&errno==EBADF);close(fd);
    }
    denial=ENOENT;assert(nvmtl_codec_open("/dev/null",O_RDONLY)==-1&&errno==ENOENT);
    denial=EPERM;assert(nvmtl_codec_open("/dev/null",O_WRONLY)==-1&&errno==EPERM);assert(nvmtl_codec_open("/dev/null",O_RDONLY|O_NONBLOCK)==-1&&errno==EPERM);
    denial=0;int fd=nvmtl_codec_open("/dev/null",O_WRONLY);assert(fd>=0&&fd<128);close(fd);
    fd=nvmtl_codec_open(argv[1],O_CREAT|O_RDWR,0640);assert(fd>=0&&fd<128&&mode_seen==0640);close(fd);unlink(argv[1]);
    dup_failure=1;errno=EDOM;fd=nvmtl_codec_open("/dev/null",O_RDONLY);assert(fd>=0&&fd<128&&errno==EDOM);close(fd);dup_failure=0;
    for(unsigned i=0;i<2;i++){
        limit_failure=!i;small_limit=i;errno=EDOM;fd=nvmtl_codec_open("/dev/null",O_RDONLY);assert(fd>=0&&fd<128&&errno==EDOM);close(fd);
    }limit_failure=small_limit=0;
    pthread_t threads[8];for(unsigned i=0;i<8;i++)assert(!pthread_create(&threads[i],NULL,worker,NULL));for(unsigned i=0;i<8;i++)assert(!pthread_join(threads[i],NULL));
    assert(descriptors()==before);assert(!setrlimit(RLIMIT_NOFILE,&original));
    puts("PASS: crowded table rotation, permissions, modes, errno, low limits, exhaustion, 384 threaded tokens and no leaks");
}
'''
with tempfile.TemporaryDirectory(prefix='nvmtl-codec-tokens-') as directory:
    root=Path(directory);source=root/'test.c';binary=root/'test'
    source.write_text(HARNESS.replace('PRODUCTION',function('nvmtl_codec_token_fd')+function('nvmtl_codec_open')))
    subprocess.run(['xcrun','clang','-O1','-g','-fsanitize=address,undefined','-I'+str(ROOT/'plugin'),str(source),'-o',str(binary)],check=True)
    subprocess.run([str(binary),str(root/'created')],check=True)
