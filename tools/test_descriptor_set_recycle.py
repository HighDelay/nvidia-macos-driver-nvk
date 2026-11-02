#!/usr/bin/env python3
"""Retired snapshot sets are kept per layout for reuse; every other set still goes back to its own pool."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/rotation'))
from test_support import method

source = (ROOT / 'plugin/nvmtl_vk.c').read_text()
header = (ROOT / 'plugin/nvmtl_vk.h').read_text()
assert 'typedef struct { void *pool, *set; unsigned kind; } nvk_setref;' in header
assert 'void *dcache[32]; void *dcachepools[32];' in header
# Snapshot sets carry their layout; framebuffer-fetch sets (another layout and pool family) are never listed.
assert 'c->sets[c->nsets++] = (nvk_setref){ pool, set, gl ? 2u : 1u };' in source
assert 'c->sets[c->nsets++] = (nvk_setref){ pool, set, 0 };' in source
# Retirement hands everything to the shared release path; nothing frees snapshot sets around it.
retire = method(source, 'static void nvmtl_cmd_retire(')
assert 'nvmtl_sets_put_locked(c->sets, c->nsets)' in retire and 'nvmtl_dcache_release_locked(c)' in retire
assert 'pvkFreeDescriptorSets' not in retire
# The allocator takes listed sets of its own layout first, and drains the other layout's list before a new pool.
snap = method(source, 'static VkDescriptorSet nvmtl_snapshot_set_md(nvk_cmdbuf *c, uint32_t which, uint32_t stex_hi, const uint64_t *mask, const uint64_t *tb,\n                                             VkDescriptorSet from, const uint64_t *copym, const VkDescriptorBufferInfo *uniform)\n{')
assert 'n = nvmtl_sets_take_locked(gl, n, got, gotpool);' in snap
assert 'if (r != VK_SUCCESS && nvmtl_sets_drain_locked(!gl)) continue;' in snap
assert snap.index('nvmtl_sets_take_locked') < snap.index('pvkAllocateDescriptorSets')
assert snap.index('nvmtl_sets_drain_locked') < snap.index('nvmtl_desc_pool_open() == 0')

helpers = '\n'.join(method(source, name) for name in (
    'static int nvmtl_set_recycle_on(', 'static void nvmtl_sets_put_locked(', 'static void nvmtl_dcache_release_locked(',
    'static unsigned nvmtl_sets_take_locked(', 'static unsigned nvmtl_sets_drain_locked('))
fixture = r'''
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
typedef struct VkDescriptorPool_T *VkDescriptorPool;
typedef struct VkDescriptorSet_T *VkDescriptorSet;
typedef struct VkDevice_T *VkDevice;
#define VK_NULL_HANDLE NULL
#define nvlog(...) ((void)0)
static struct { void *view; } g_dummyStex = { (void *)1 };
#define NVMTL_DESC_BATCH_MAX 32
typedef struct { void *pool, *set; unsigned kind; } nvk_setref;
typedef struct { void *dcache[32]; void *dcachepools[32]; unsigned ndcache, dcache_gl_uniform; } nvk_cmdbuf;
static VkDevice g_dev;
static nvk_setref *g_set_recycle[2];
static unsigned g_nset_recycle[2], g_capset_recycle[2];
static unsigned frees, freed; static uintptr_t last_pool; static unsigned maxrun;
static void pvkFreeDescriptorSets(VkDevice d, VkDescriptorPool p, unsigned n, const VkDescriptorSet *s) {
    (void)d; assert(n >= 1 && n <= 256);
    for (unsigned i = 0; i < n; i++) assert(((uintptr_t)s[i] >> 20) == (uintptr_t)p);   // each set to its own pool
    frees++; freed += n; last_pool = (uintptr_t)p; if (n > maxrun) maxrun = n;
}
static void *P(unsigned p) { return (void *)(uintptr_t)p; }
static void *S(unsigned p, unsigned i) { return (void *)(((uintptr_t)p << 20) | i); }
@@HELPERS@@
static void put_mixed(void) {
    nvk_setref a[6] = { { P(1), S(1, 1), 1 }, { P(1), S(1, 2), 0 }, { P(1), S(1, 3), 0 }, { P(2), S(2, 4), 2 },
                        { P(2), S(2, 5), 0 }, { P(3), S(3, 6), 1 } };
    nvmtl_sets_put_locked(a, 6);
}
int main(int argc, char **argv) {
    const char *mode = argc > 1 ? argv[1] : "on";
    // Pool pressure can try the other layout before it has ever owned a free list.
    // Draining that untouched layout must not do pointer arithmetic on NULL.
    assert(nvmtl_sets_drain_locked(0) == 0 && nvmtl_sets_drain_locked(1) == 0);
    if (!strcmp(mode, "off") || !strcmp(mode, "nodummy")) {
        // Switched off (any value but 0), or no storage dummy: every set goes back to its own pool.
        if (!strcmp(mode, "nodummy")) g_dummyStex.view = NULL;
        put_mixed();
        assert(g_nset_recycle[0] == 0 && g_nset_recycle[1] == 0 && freed == 6 && frees == 3);   // one run per pool
        printf("PASS: %s - every retired set freed to its own pool\n", mode);
        return 0;
    }
    put_mixed();
    assert(g_nset_recycle[0] == 2 && g_nset_recycle[1] == 1);
    assert(g_set_recycle[0][0].set == S(1, 1) && g_set_recycle[0][1].set == S(3, 6) && g_set_recycle[1][0].set == S(2, 4));
    assert(g_set_recycle[0][1].pool == P(3) && freed == 3 && frees == 2);   // two pool runs of kind-0 sets

    nvk_cmdbuf c = { .ndcache = 3, .dcache_gl_uniform = 1 };
    for (unsigned k = 0; k < 3; k++) { c.dcache[k] = S(4 + k, 10 + k); c.dcachepools[k] = P(4 + k); }
    nvmtl_dcache_release_locked(&c);
    assert(c.ndcache == 0 && g_nset_recycle[1] == 4 && g_set_recycle[1][3].pool == P(6) && g_set_recycle[1][3].kind == 2);

    // Taking returns the newest sets first, each with its own pool, and clamps to what is listed.
    VkDescriptorSet got[32]; VkDescriptorPool gotpool[32];
    assert(nvmtl_sets_take_locked(1, 32, got, gotpool) == 4 && g_nset_recycle[1] == 0);
    assert(got[0] == (VkDescriptorSet)S(6, 12) && gotpool[0] == (VkDescriptorPool)P(6) && got[3] == (VkDescriptorSet)S(2, 4) && gotpool[3] == (VkDescriptorPool)P(2));
    assert(nvmtl_sets_take_locked(1, 32, got, gotpool) == 0);
    assert(nvmtl_sets_take_locked(0, 1, got, gotpool) == 1 && got[0] == (VkDescriptorSet)S(3, 6) && g_nset_recycle[0] == 1);
    nvk_setref back = { P(3), S(3, 6), 1 }; nvmtl_sets_put_locked(&back, 1);

    static nvk_setref many[5000];
    for (unsigned i = 0; i < 5000; i++) many[i] = (nvk_setref){ P(7), S(7, i), 1 };
    nvmtl_sets_put_locked(many, 5000);
    assert(g_nset_recycle[0] == 5002 && g_capset_recycle[0] >= 5002 && g_set_recycle[0][5001].set == S(7, 4999));

    // Kind-0 sets of one pool are freed in runs of at most 256.
    unsigned before = frees; freed = 0;
    for (unsigned i = 0; i < 600; i++) many[i] = (nvk_setref){ P(8), S(8, i), 0 };
    nvmtl_sets_put_locked(many, 600);
    assert(freed == 600 && frees - before == 3 && maxrun == 256 && last_pool == 8);

    // Draining frees at most 1024 per call, newest first, to their own pools, and stops when the list is empty.
    freed = 0; unsigned passes = 0, drained;
    while ((drained = nvmtl_sets_drain_locked(0))) { assert(drained <= 1024); passes++; }
    assert(passes == 5 && freed == 5002 && g_nset_recycle[0] == 0 && nvmtl_sets_drain_locked(0) == 0);
    assert(nvmtl_sets_drain_locked(1) == 0);
    puts("PASS: snapshot sets kept per layout with their pools; take clamps and keeps pools; bounded drain; other sets freed in bounded runs; list grows past 4096");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='nvmtl-set-recycle-') as directory:
    directory = Path(directory)
    (directory / 'test.c').write_text(fixture.replace('@@HELPERS@@', helpers))
    subprocess.run(['xcrun', 'clang', '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                    str(directory / 'test.c'), '-o', str(directory / 'test')], check=True)
    clean = {k: v for k, v in os.environ.items() if k != 'NVMTL_NO_SET_RECYCLE'}
    subprocess.run([str(directory / 'test')], check=True, timeout=30, env=clean)
    subprocess.run([str(directory / 'test'), 'off'], check=True, timeout=30, env={**clean, 'NVMTL_NO_SET_RECYCLE': 'yes'})
    subprocess.run([str(directory / 'test'), 'nodummy'], check=True, timeout=30, env={**clean, 'NVMTL_NO_SET_RECYCLE': '0'})
