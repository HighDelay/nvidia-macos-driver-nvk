#!/usr/bin/env python3
"""Run the actual RM lock adapter with per-thread interrupt-state stand-ins."""
from pathlib import Path
import argparse
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source',type=Path,default=ROOT/'kexts/NVRM/os-xnu.cpp')
args=parser.parse_args()
text=args.source.read_text();start=text.index('struct nv_xnu_spin {')
production=text[start:text.index('void     *NV_API_CALL os_alloc_rwlock',start)]
fixture=r'''
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <mutex>
#include <thread>
#include <vector>
#define NV_XNU_TRC ((void)0)
#define NV_API_CALL
using NvU64=uint64_t;using NV_STATUS=int;using IOInterruptState=uintptr_t;
constexpr int NV_OK=0,NV_ERR_NO_MEMORY=1;
struct IOSimpleLock {std::mutex mutex;};
static thread_local uintptr_t interruptState;
static void *kern_os_malloc(size_t n){return malloc(n);}
static void kern_os_free(void *p){free(p);}
static IOSimpleLock *IOSimpleLockAlloc(){return new IOSimpleLock;}
static void IOSimpleLockFree(IOSimpleLock *p){delete p;}
static IOInterruptState IOSimpleLockLockDisableInterrupt(IOSimpleLock *p){
 auto old=interruptState;interruptState=0;p->mutex.lock();return old;
}
static void IOSimpleLockUnlockEnableInterrupt(IOSimpleLock *p,IOInterruptState old){p->mutex.unlock();interruptState=old;}
@@PRODUCTION@@
int main(){
 void *lock=nullptr;assert(os_alloc_spinlock(&lock)==NV_OK && lock);
 std::vector<std::thread> threads;
 for(uintptr_t t=0;t<64;t++)threads.emplace_back([=]{
  void *nested=nullptr;assert(os_alloc_spinlock(&nested)==NV_OK);
  for(unsigned i=0;i<1000;i++){
   const uintptr_t before=0x202+(t<<12)+(uintptr_t(i)<<20);interruptState=before;
   auto old=os_acquire_spinlock(lock);assert(old==before && interruptState==0);
   auto inner=os_acquire_spinlock(nested);assert(inner==0 && interruptState==0);
   os_release_spinlock(nested,inner);assert(interruptState==0);
   os_release_spinlock(lock,old);assert(interruptState==before);
  }
  os_free_spinlock(nested);
 });
 for(auto &t:threads)t.join();os_free_spinlock(lock);os_free_spinlock(nullptr);
 puts("PASS: 64000 concurrent outer/inner lock pairs preserve the caller's interrupt state");
}
'''
with tempfile.TemporaryDirectory(prefix='nvrm-spin-state-') as directory:
 p=Path(directory);(p/'test.cpp').write_text(fixture.replace('@@PRODUCTION@@',production))
 subprocess.run(['xcrun','clang++','-std=c++17','-O2','-fsanitize=address,undefined','-fno-sanitize-recover=all',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
