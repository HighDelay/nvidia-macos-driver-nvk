#!/usr/bin/env python3
"""A later recorded witness is not completion proof for an earlier submission.

Compile the production verdict with a deterministic witness slot. A thread can
allocate s, pause before the submit lock, then submit after s + WIT_SLOTS; nested
pre-submit hooks can also invert sequence and queue order. Accept only the exact
witness, while preserving blind/lost/backend-disabled handling.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
source = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'plugin/nvmtl_vk.c'
src = source.read_text()
start = src.index('static int nvmtl_keep_verdict(uint32_t s)')
end = src.index('\n}\n', start) + 3
fixture = r'''
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <time.h>
#include <sched.h>
#include <unistd.h>
#define NVMTL_WIT_SLOTS 4096u
#define NVMTL_WSEQ_BLIND 0xFFFFFFFFu
#define NVMTL_WSEQ_LOST 0xFFFFFFFEu
#define nvlog(...) ((void)0)
static int g_state = 1, g_wit_state = 1, keep = 1;
static uint32_t slot;
static uint64_t g_wit_ok, g_wit_waited, g_wit_leak, g_wit_lapped;
static int nvmtl_keep_on(void) { return keep; }
static uint32_t nvmtl_wit_read(uint32_t s) { return slot; }
''' + src[start:end] + r'''
int main(void) {
    setenv("NVMTL_WIT_WAIT_MS", "0", 1);
    const uint32_t seq = 7;
    slot = seq + NVMTL_WIT_SLOTS;
    assert(nvmtl_keep_verdict(seq) == -1);
    slot = seq + 2 * NVMTL_WIT_SLOTS;
    assert(nvmtl_keep_verdict(seq) == -1);
    slot = seq;
    assert(nvmtl_keep_verdict(seq) == 1);
    slot = 0;
    assert(nvmtl_keep_verdict(seq) == -1);
    assert(nvmtl_keep_verdict(0) == 1);
    assert(nvmtl_keep_verdict(NVMTL_WSEQ_BLIND) == 0);
    assert(nvmtl_keep_verdict(NVMTL_WSEQ_LOST) == -1);
    slot = seq; g_state = -1;
    assert(nvmtl_keep_verdict(seq) == -1);
    g_state = 1; g_wit_state = -1;
    assert(nvmtl_keep_verdict(seq) == -1);
    g_wit_state = 1; keep = 0;
    assert(nvmtl_keep_verdict(seq) == 0);
    puts("PASS: 10 witness completion/order cases");
}
'''
with tempfile.TemporaryDirectory(prefix='nvmtl-witness-order-') as directory:
    path = Path(directory)
    (path / 'test.c').write_text(fixture)
    subprocess.run(['xcrun', 'clang', '-O1', '-g', '-fsanitize=address,undefined',
                    str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    sys.exit(subprocess.run([str(path / 'test')], timeout=10).returncode)
