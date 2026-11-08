#!/usr/bin/env python3
"""Sanitize production IOSurface binding snapshots and per-surface lock lifetimes."""
import argparse
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PRELUDE = r'''
#include <assert.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#define p_atomic_inc(p) ((void)__atomic_add_fetch(p,1,__ATOMIC_SEQ_CST))
#define p_atomic_dec(p) ((void)__atomic_sub_fetch(p,1,__ATOMIC_SEQ_CST))
#define p_atomic_dec_zero(p) (__atomic_sub_fetch(p,1,__ATOMIC_SEQ_CST)==0)
#define p_atomic_set(p,v) __atomic_store_n(p,v,__ATOMIC_SEQ_CST)
#define p_atomic_read(p) __atomic_load_n(p,__ATOMIC_SEQ_CST)
#define MAX2(a,b) ((a)>(b)?(a):(b))
struct surface { unsigned id;int refs; };
typedef struct surface *IOSurfaceRef;
struct gl_texture_object { int refs; };
struct pipe_resource { int refs;unsigned nvk_iosurface_tracked; };
struct pipe_fence_handle { int unused; };
static _Atomic unsigned created,freed;
static _Atomic int fail_calloc;
static void *test_calloc(size_t n,size_t size) { if(atomic_exchange(&fail_calloc,0))return NULL;return calloc(n,size); }
#define calloc test_calloc
#define zink_resource(p) (p)
static unsigned IOSurfaceGetID(IOSurfaceRef s) { return s->id; }
static void *CFRetain(IOSurfaceRef s) { p_atomic_inc(&s->refs);return s; }
static void CFRelease(IOSurfaceRef s) { assert(p_atomic_read(&s->refs)>1);p_atomic_dec(&s->refs); }
static void _mesa_reference_texobj(struct gl_texture_object **dst,struct gl_texture_object *src) {
    assert(!src);if(*dst && p_atomic_dec_zero(&(*dst)->refs))free(*dst);*dst=src;
}
static void pipe_resource_reference(struct pipe_resource **dst,struct pipe_resource *src) {
    assert(!src);if(*dst && p_atomic_dec_zero(&(*dst)->refs)){free(*dst);atomic_fetch_add(&freed,1);}*dst=src;
}
'''
CHECKS = r'''
static struct nvk_iosurface_group group;
static struct surface surfaces[7];
static _Atomic unsigned producers_done;
static struct surface_binding *add(unsigned index) {
    struct surface_binding *b=calloc(1,sizeof(*b));assert(b);
    b->storage=retain_storage(&group,&surfaces[index%7]);assert(b->storage);
    b->surface=b->storage->surface;b->refs=1;
    b->image=calloc(1,sizeof(*b->image));b->texture=calloc(1,sizeof(*b->texture));assert(b->image && b->texture);
    b->image->refs=1;b->image->nvk_iosurface_tracked=1;b->texture->refs=1;
    atomic_fetch_add(&created,1);
    pthread_mutex_lock(&group.mutex);b->next=group.bindings;group.bindings=b;p_atomic_inc(&group.count);pthread_mutex_unlock(&group.mutex);
    return b;
}
static void *produce(void *arg) {
    unsigned offset=(unsigned)(uintptr_t)arg;
    for(unsigned i=0;i<4000;++i)add(i+offset);
    atomic_fetch_add(&producers_done,1);return NULL;
}
static void *retire(void *arg) {
    bool erase=(uintptr_t)arg!=0;
    do {
        unsigned count;struct surface_binding **items=snapshot(&group,&count);assert(items);
        for(unsigned i=0;i<count;++i) {
            struct surface_binding *b=items[i];pthread_mutex_lock(&b->storage->mutex);
            assert(b->image && b->texture && p_atomic_read(&b->image->nvk_iosurface_tracked)==1);
            assert(p_atomic_read(&b->image->refs)==1 && p_atomic_read(&b->texture->refs)==1);
            if(erase)unlink_binding(&group,b);
            pthread_mutex_unlock(&b->storage->mutex);
        }
        release_snapshot(&group,items,count);
    } while(atomic_load(&producers_done)!=2 || p_atomic_read(&group.count));
    return NULL;
}
int main(void) {
    assert(!pthread_mutex_init(&group.mutex,NULL));
    for(unsigned i=0;i<7;++i)surfaces[i]=(struct surface){i+1,1};
    struct surface_binding *b=add(0);unsigned an,bn;
    struct surface_binding **a=snapshot(&group,&an),**other=snapshot(&group,&bn);assert(a && other && an==1 && bn==1);
    unlink_binding(&group,b);assert(!group.count && group.surfaces && b->refs==2);
    release_snapshot(&group,a,an);assert(b->refs==1 && b->image->nvk_iosurface_tracked);
    release_snapshot(&group,other,bn);assert(!group.surfaces && atomic_load(&freed)==1);
    b=add(0);atomic_store(&fail_calloc,1);a=snapshot(&group,&an);assert(!a && !an && group.count==1 && b->refs==1);
    unlink_binding(&group,b);assert(!group.surfaces);
    for(unsigned round=0;round<8;++round) {
        atomic_store(&producers_done,0);pthread_t threads[7];
        assert(!pthread_create(&threads[0],NULL,produce,(void *)0));assert(!pthread_create(&threads[1],NULL,produce,(void *)3));
        for(unsigned i=2;i<7;++i)assert(!pthread_create(&threads[i],NULL,retire,(void *)(uintptr_t)(i!=2)));
        for(unsigned i=0;i<7;++i)assert(!pthread_join(threads[i],NULL));
        assert(!group.bindings && !group.surfaces && !group.count);
        for(unsigned i=0;i<7;++i)assert(surfaces[i].refs==1);
    }
    b=add(0);a=snapshot(&group,&an);assert(a && an==1);
    unlink_binding(&group,b);assert(b->refs==1 && !group.bindings);
    b->locked=true;quarantine_binding(&group,b);quarantine_binding(&group,b);
    assert(b->refs==2 && group.quarantine==b && !b->quarantine_next);
    release_snapshot(&group,a,an);
    assert(b->refs==1 && b->image->nvk_iosurface_tracked && surfaces[0].refs==2);
    b->locked=false;group.quarantine=NULL;release_binding(&group,b);
    assert(!group.surfaces && surfaces[0].refs==1);
    assert(atomic_load(&created)==64003 && atomic_load(&freed)==64003);pthread_mutex_destroy(&group.mutex);
    printf("PASS: 64003 binding lifetimes, concurrent snapshots/retirement, shared surface locks, allocation failure and retired-snapshot quarantine\n");
}
'''


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source = (ROOT/'zink/nvk_iosurface.c').read_text()
    types = source[source.index('struct surface_storage {'):source.index('int nvk_iosurface_group_init(')]
    methods = source[source.index('static struct surface_storage *retain_storage('):source.index('static bool obsolete(')]
    fixture = out/'lifetime.c'
    fixture.write_text(PRELUDE+types+methods+CHECKS)
    for sanitizer in ('address,undefined', 'thread'):
        binary = out/('lifetime-'+sanitizer.replace(',', '-'))
        subprocess.run(['clang', '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize='+sanitizer, '-fno-omit-frame-pointer', str(fixture), '-o', str(binary)], check=True)
        result = subprocess.run([str(binary)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
        (out/(binary.name+'.log')).write_text(result.stdout)
        print(sanitizer, result.returncode, result.stdout, flush=True)
        if result.returncode:
            raise SystemExit(1)
    negative = out/'lifetime-without-quarantine-retain.c'
    negative.write_text((PRELUDE+types+methods+CHECKS).replace('p_atomic_inc(&b->refs); b->quarantined=true;', 'b->quarantined=true;'))
    binary = out/'lifetime-without-quarantine-retain'
    subprocess.run(['clang', '-std=c11', '-O1', '-g', '-fsanitize=address,undefined',
                    '-fno-omit-frame-pointer', str(negative), '-o', str(binary)], check=True)
    result = subprocess.run([str(binary)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
    (out/'negative-control.log').write_text(result.stdout)
    if not result.returncode or 'b->refs==2' not in result.stdout:
        raise RuntimeError('Missing quarantine reference was not rejected by its regression')
    print('PASS: missing-quarantine-retain negative control rejected', flush=True)


if __name__ == '__main__':
    main()
