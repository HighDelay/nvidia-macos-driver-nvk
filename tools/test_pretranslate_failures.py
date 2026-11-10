#!/usr/bin/env python3
"""Fault-inject the production shader-flight and worker-pool implementation."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <errno.h>
#include <stdio.h>
static int failAllocation, failKey, failWorker;
static void *checked_calloc(size_t n, size_t size) { return failAllocation ? NULL : calloc(n, size); }
static char *checked_strdup(const char *key) { return failKey ? NULL : strdup(key); }
static int checked_create(pthread_t *t, const pthread_attr_t *a, void *(*fn)(void *), void *arg) {
    return failWorker ? EAGAIN : pthread_create(t, a, fn, arg);
}
#define calloc checked_calloc
#define strdup checked_strdup
#define pthread_create checked_create
#include "nvmtl_pretranslate.h"
#undef calloc
#undef strdup
#undef pthread_create
static pthread_mutex_t gate = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t job_ready = PTHREAD_COND_INITIALIZER;
static int entered, released, completed;
static void job(void *arg) {
    (void)arg;
    pthread_mutex_lock(&gate); entered++; pthread_cond_broadcast(&job_ready);
    while (!released) pthread_cond_wait(&job_ready, &gate);
    completed++; pthread_mutex_unlock(&gate);
}
int main(int argc, char **argv) {
    assert(argc == 2);
    nvmtl_sf sf; nvmtl_sf_init(&sf, NULL, NULL);
    void *result = (void *)1;
    if (!strcmp(argv[1], "calloc") || !strcmp(argv[1], "key")) {
        failAllocation = !strcmp(argv[1], "calloc"); failKey = !strcmp(argv[1], "key");
        assert(nvmtl_sf_begin(&sf, "shader", 0, &result) == -1 && !result && !sf.head);
        failAllocation = failKey = 0;
        assert(nvmtl_sf_begin(&sf, "shader", 0, &result) == 1);
        nvmtl_sf_publish(&sf, "shader", (void *)42);
        assert(nvmtl_sf_begin(&sf, "shader", 1, &result) == 0 && result == (void *)42);
        nvmtl_sf_retire(&sf, "shader"); assert(!sf.head && !sf.entries);
    } else if (!strcmp(argv[1], "capacity")) {
        char key[32];
        for (unsigned i=0; i<NVMTL_SF_MAX_ENTRIES; i++) {
            snprintf(key, sizeof key, "shader%u", i); assert(nvmtl_sf_begin(&sf,key,0,NULL)==1);
        }
        assert(nvmtl_sf_begin(&sf,"overflow",0,&result)==-1 && !result);
        // Existing owners remain usable at capacity; a fallback owns no entry.
        nvmtl_sf_publish(&sf,"shader0",(void *)42);
        assert(nvmtl_sf_begin(&sf,"shader0",1,&result)==0 && result==(void *)42);
        for (unsigned i=0; i<NVMTL_SF_MAX_ENTRIES; i++) {
            snprintf(key, sizeof key, "shader%u", i); nvmtl_sf_retire(&sf,key);
        }
        assert(!sf.head && !sf.entries);
        assert(nvmtl_sf_begin(&sf,NULL,1,&result)==-1 && !result);
    } else {
        static nvmtl_pool pool; nvmtl_pool_init(&pool,1,0);
        failWorker = 1;
        assert(nvmtl_pool_submit(&pool,job,NULL)==-1);
        assert(!pool.head && !pool.tail && !pool.queued && !pool.started);
        // A rejected submission leaves no orphaned work for drain to wait for.
        nvmtl_pool_drain(&pool); failWorker = 0;
        assert(nvmtl_pool_submit(&pool,job,NULL)==0);
        pthread_mutex_lock(&gate);
        while (!entered) pthread_cond_wait(&job_ready,&gate);
        pthread_mutex_unlock(&gate);
        for (int i=0;i<NVMTL_POOL_MAX_QUEUED;i++) assert(nvmtl_pool_submit(&pool,job,NULL)==0);
        assert(nvmtl_pool_submit(&pool,job,NULL)==-1 && pool.queued==NVMTL_POOL_MAX_QUEUED);
        pthread_mutex_lock(&gate); released=1; pthread_cond_broadcast(&job_ready); pthread_mutex_unlock(&gate);
        nvmtl_pool_drain(&pool);
        assert(completed==NVMTL_POOL_MAX_QUEUED+1 && !pool.queued && !pool.running);
    }
    puts("PASS");
}
'''

with tempfile.TemporaryDirectory(prefix='pretranslate-failures-') as name:
    tmp = Path(name)
    (tmp/'test.c').write_text(HARNESS)
    subprocess.run(['clang','-std=gnu11','-O1','-g','-pthread',
                    '-fsanitize=address,undefined','-fno-sanitize-recover=all',
                    '-I'+str(ROOT/'plugin'),str(tmp/'test.c'),'-o',str(tmp/'test')],check=True)
    for mode in ['calloc','key','capacity','worker']:
        subprocess.run([str(tmp/'test'),mode],check=True,timeout=15)
print('PASS: allocation/key refusal, bounded flight/queue ownership, failed worker recovery and drain')
