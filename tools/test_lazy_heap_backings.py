#!/usr/bin/env python3
"""Exercise the production lazy-heap allocator across metadata growth and concurrent binds."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--source', type=Path, default=ROOT)
args = p.parse_args()
objects = (args.source / 'plugin/NVMTLObjects.m').read_text()
device = (args.source / 'plugin/NVMTLObjects.h').read_text()
a = objects.index('static nvk_heap *nvmtl_heap_mem_for(')
b = objects.index('\nvoid nvmtl_heap_lazy_census(', a)
allocator = objects[a:b]
fields = re.search(r'@interface NVMTLHeap \(\) \{ @public size_t _lzc;.*?\} @end', device, re.S).group(0)
fields = fields[fields.index('@public'):fields.rindex('}')]
stable = 'nvk_heap **_lzm' in fields
fixture = r'''
#import <Foundation/Foundation.h>
#include <assert.h>
#include <os/lock.h>
#include <stdatomic.h>
#include <dispatch/dispatch.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
typedef struct { void *mem; size_t size; } nvk_heap;
enum { MTLStorageModeShared = 0 };
@interface NVMTLHeap : NSObject { @public size_t _size; int _storage; nvk_heap _hm;
@@FIELDS@@
} @end
@implementation NVMTLHeap @end
static _Atomic uint64_t gHLazyBacked, gHLazyMixed;
static int create_fail;
static int nvmtl_vk_heap_create(size_t size, int shared, nvk_heap *out) {
    (void)shared; if (create_fail) return -1;
    *out=(nvk_heap){ .mem=(void *)(uintptr_t)(size+65536), .size=size }; return 0;
}
static void nvmtl_vk_heap_zero(nvk_heap *m, const char *why) { (void)m; (void)why; }
#define nvlog(...) ((void)0)
@@ALLOCATOR@@
int main(void) { @autoreleasepool {
    NVMTLHeap *h=[NVMTLHeap new]; h->_size=128*64; h->_lzc=64; h->_lzslots=128;
    h->_lzo=calloc(h->_lzslots,sizeof *h->_lzo); h->_lzl=OS_UNFAIR_LOCK_INIT;
    NSUInteger rel=999; nvk_heap *first=nvmtl_heap_mem_for(h,0,16,&rel);
    assert(first && first->size==64 && rel==0); void *memory=first->mem;
    for (unsigned i=1;i<64;i++) assert(nvmtl_heap_mem_for(h,i*64,16,&rel));
    // A binder retains this pointer after releasing the allocator lock. Growing
    // the metadata must leave the backing object and its memory handle valid.
    assert(first->mem==memory && first->size==64);
    assert(nvmtl_heap_mem_for(h,8,8,&rel)==first && rel==8);
    dispatch_apply(32,dispatch_get_global_queue(QOS_CLASS_DEFAULT,0),^(size_t i) {
        NSUInteger offset=0; nvk_heap *m=nvmtl_heap_mem_for(h,(64+i)*64,16,&offset);
        assert(m && m->size==64 && offset==0);
    });
    assert(first->mem==memory && h->_lzn==96);
    assert(!nvmtl_heap_mem_for(h,0,0,&rel));
    assert(!nvmtl_heap_mem_for(h,NSUIntegerMax-8,16,&rel));
    assert(!nvmtl_heap_mem_for(h,h->_size,1,&rel));
    assert(!nvmtl_heap_mem_for(h,32,64,&rel)); // differently owned slots
    create_fail=1; assert(!nvmtl_heap_mem_for(h,96*64,8,&rel));
    assert(!h->_lzo[96] && h->_lzn==96);
    create_fail=0; assert(nvmtl_heap_mem_for(h,96*64,8,&rel));
    assert(h->_lzn==97 && h->_lzo[96]);
    @@FREE_BACKINGS@@
    free(h->_lzm); free(h->_lzb); free(h->_lzo);
    puts("PASS: stable heap backings across growth, concurrent binds, subranges, overflow, mixed slots and allocation retry");
} }
'''.replace('@@FIELDS@@', fields).replace('@@ALLOCATOR@@', allocator)
fixture=fixture.replace('@@FREE_BACKINGS@@', 'for (uint32_t k=0;k<h->_lzn;k++) free(h->_lzm[k]);' if stable else '')
with tempfile.TemporaryDirectory(prefix='lazy-heap-') as temp:
    source=Path(temp)/'probe.m'; binary=Path(temp)/'probe'
    source.write_text(fixture)
    subprocess.run(['xcrun','clang','-fobjc-arc','-O1','-g','-fsanitize=address,undefined',
                    '-fno-sanitize-recover=all','-framework','Foundation',str(source),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True,timeout=30)
