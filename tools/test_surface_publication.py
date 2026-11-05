#!/usr/bin/env python3
"""Force CPU import to overlap GPU surface publication in production methods."""
import pathlib
import subprocess
import tempfile

root = pathlib.Path(__file__).resolve().parents[1]
source = (root / 'plugin/NVMTLObjects.m').read_text()
start = source.index('static id nvmtl_surface_sync(')
end = source.index('- (NSUInteger)width ', start)
methods = source[start:end]
fixture = r'''
#import <Foundation/Foundation.h>
#include <assert.h>
#include <dispatch/dispatch.h>
#include <stdint.h>
#include <stdatomic.h>
#include <string.h>
#include <time.h>
#import <objc/runtime.h>
typedef struct __IOSurface { unsigned seed; unsigned char cpu[4]; } Surface;
typedef int IOReturn;
enum { kIOSurfaceLockReadOnly = 1 };
typedef struct { unsigned w,h,bpp; unsigned char gpu[4]; void *img; } Image;
typedef struct { BOOL imported; } HostPages;
static BOOL hostPagesAvailable;
static size_t nvmtl_vk_host_import_align(void) { return 1; }
static dispatch_semaphore_t entered, release_read;
static unsigned uploads, gpu_seed, vram_generation=1, gpu_copies; static int copy_failure;
static unsigned long long g_surfin_skip, g_surfin_copy;
static uint32_t IOSurfaceGetSeed(IOSurfaceRef s) { return s->seed; }
static unsigned IOSurfaceGetID(IOSurfaceRef s) { return 1; }
static IOReturn IOSurfaceLock(IOSurfaceRef s, unsigned opts, uint32_t *seed) {
    if (!opts) s->seed++;
    if (seed) *seed=s->seed;
    return 0;
}
static IOReturn IOSurfaceUnlock(IOSurfaceRef s, unsigned opts, uint32_t *seed) {
    if (seed) *seed=s->seed;
    return 0;
}
static void *IOSurfaceGetBaseAddressOfPlane(IOSurfaceRef s, uint32_t p) { return s->cpu; }
static size_t IOSurfaceGetBytesPerRowOfPlane(IOSurfaceRef s, uint32_t p) { return 4; }
static void *IOSurfaceGetBaseAddress(IOSurfaceRef s) { return s->cpu; }
static size_t IOSurfaceGetAllocSize(IOSurfaceRef s) { return sizeof s->cpu; }
static uint32_t nvmtl_vram_gen(IOSurfaceRef s, NSUInteger p) { return vram_generation; }
static int nvmtl_vram_tag_current(IOSurfaceRef s, NSUInteger p, unsigned seed) { return gpu_seed == seed; }
static void nvmtl_vram_tag_set(IOSurfaceRef s, NSUInteger p, unsigned seed) { gpu_seed=seed; }
static unsigned nvmtl_vram_tag_bump(IOSurfaceRef s, NSUInteger p, unsigned seed) { gpu_seed=seed; return ++vram_generation; }
// the kernel's VRAM mark: dirtyAnswer for the blocking one (1 = refused, the copy path), tryAnswer for the try that never
// waits for the accelerator lock (1 = the lock was taken, -1 = refused); each call counted
static int dirtyAnswer=1; static _Atomic int tryAnswer=-1;
static _Atomic unsigned blockingDirties, tries;
static int nvmtl_vk_surface_dirty(unsigned s, unsigned p) { blockingDirties++; return dirtyAnswer; }
static int nvmtl_vk_surface_dirty_try(unsigned s, unsigned p) { tries++; return tryAnswer; }
static int nvmtl_noseed(void) { return 0; }
static void nvmtl_frame_dump(const char *tag, void *base, size_t row, unsigned w, unsigned h) {}
#define nvlog(...) ((void)0)
// helpers the capture path calls (NVMTLEventSync.m); WindowServer kernel marks are off here
static BOOL nvmtl_ws_kernel_waits(void){return NO;}
static void nvmtl_ws_mark(uint32_t sid,BOOL pending){(void)sid;(void)pending;}
static void nvtrace(const char *f,...){(void)f;}
static int nvmtl_vk_copy_image(void *q, Image *src, unsigned sx, unsigned sy, Image *dst, unsigned dx, unsigned dy, unsigned w, unsigned h) {
    gpu_copies++; if(copy_failure)return -1;
    memcpy(dst->gpu,src->gpu,4); return 0;
}
static int nvmtl_vk_image_write(void *q, Image *i, void *base, size_t row) {
    memcpy(i->gpu,base,4); uploads++; return 0;
}
static int nvmtl_vk_image_read(void *q, Image *i, void *base, size_t row) {
    dispatch_semaphore_signal(entered);
    assert(!dispatch_semaphore_wait(release_read,dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_SEC)));
    memcpy(base,i->gpu,4); return 0;
}
// Exercise publication ordering with both imported-page success and CPU fallback.
// The GPU transfers themselves are mocked; production surface methods own the locking.
static int nvmtl_vk_image_write_host(void *q, Image *img, void *base, size_t row, size_t bytes, HostPages *cache) {
    if (!hostPagesAvailable) return -1;
    cache->imported=YES; return nvmtl_vk_image_write(q,img,base,row);
}
static int nvmtl_vk_image_read_host(void *q, Image *img, void *base, size_t row, size_t bytes, HostPages *cache) {
    if (!hostPagesAvailable) return -1;
    cache->imported=YES; return nvmtl_vk_image_read(q,img,base,row);
}
@interface NVMTLTexture : NSObject {
@public NVMTLTexture *_parent; IOSurfaceRef _surf; BOOL _vramOn, _surfSynced;
    HostPages _hostPages;
    uint32_t _surfSeed, _surfGen; NSUInteger _plane; Image _i, _surfaceLinear; void *_q;
}
- (BOOL)nvmtlSurfaceCopyable;
- (void)nvmtlSurfaceIn;
- (BOOL)nvmtlSurfaceOut;
- (BOOL)nvmtlSurfaceOutDirtied:(BOOL)dirtied;
@end
@implementation NVMTLTexture
- (BOOL)nvmtlSurfaceCopyable { return YES; }
'''
checks = r'''
@end
int main(int argc, char **argv) { @autoreleasepool {
    const BOOL shared=argc>1 && !strcmp(argv[1],"shared");
    hostPagesAvailable=argc>2;
    Surface s={.seed=1};
    NVMTLTexture *t=[NVMTLTexture new]; t->_surf=&s; t->_i=(Image){.w=1,.h=1,.bpp=4,.gpu={0,128,255,255}};
    NVMTLTexture *alias=[NVMTLTexture new];
    if (shared) { alias->_surf=&s; alias->_vramOn=YES; t->_vramOn=YES; }
    else alias->_parent=t;
    entered=dispatch_semaphore_create(0); release_read=dispatch_semaphore_create(0);
    dispatch_group_t group=dispatch_group_create();
    dispatch_queue_t queue=dispatch_get_global_queue(QOS_CLASS_USER_INITIATED,0);
    dispatch_group_async(group,queue,^{ @autoreleasepool { assert([t nvmtlSurfaceOut]); } });
    assert(!dispatch_semaphore_wait(entered,dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_SEC)));
    dispatch_semaphore_t importing=dispatch_semaphore_create(0);
    dispatch_group_async(group,queue,^{ @autoreleasepool {
        dispatch_semaphore_signal(importing); [alias nvmtlSurfaceIn];
    } });
    assert(!dispatch_semaphore_wait(importing,dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_SEC)));
    // Give an unsynchronized importer time to read the still-old CPU pixels.
    [NSThread sleepForTimeInterval:0.1];
    dispatch_semaphore_signal(release_read);
    assert(!dispatch_group_wait(group,dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_SEC)));
    const unsigned char orange[4]={0,128,255,255};
    assert(!memcmp(t->_i.gpu,orange,4)); assert(!memcmp(s.cpu,orange,4)); assert(uploads==0);
    // A later CPU edit must still be imported once publication has finished.
    s.seed++; memset(s.cpu,71,4); [alias nvmtlSurfaceIn];
    assert(uploads==1); assert(!memcmp((shared?alias:t)->_i.gpu,s.cpu,4));
    assert(t->_hostPages.imported==hostPagesAvailable);
    // Publication after the commit's VRAM mark (WindowServer, an NVAccel whose page-off waits) runs on the thread that
    // releases the pending marks, so it never waits for the accelerator lock: busy, the commit's mark stands and nothing
    // is copied (a copy would block in the read stub and fail); free, the surface is marked again.
    Surface ws={.seed=9}; NVMTLTexture *w=[NVMTLTexture new]; w->_surf=&ws; w->_vramOn=YES; w->_i=(Image){.w=1,.h=1,.bpp=4};
    blockingDirties=0; tries=0; tryAnswer=-1;
    assert([w nvmtlSurfaceOutDirtied:YES] && tries==1 && !blockingDirties && w->_surfSynced && w->_surfSeed==9);
    tryAnswer=0;
    assert([w nvmtlSurfaceOutDirtied:YES] && tries==2 && !blockingDirties);
    // the lock was taken: this thread goes on, and the re-mark is retried off it, again only as tries (a mark that
    // waited for the lock could land after a newer CPU write), until one takes; a page-off that slipped in between the
    // commit's marks must not leave its copy current
    tryAnswer=1;
    assert([w nvmtlSurfaceOutDirtied:YES] && tries>=3);
    for (int i=0; i<200 && tries<6; i++) [NSThread sleepForTimeInterval:0.002];
    assert(tries>=6 && !blockingDirties);
    const unsigned settledTries=tries; tryAnswer=0;
    for (int i=0; i<200 && tries==settledTries; i++) [NSThread sleepForTimeInterval:0.002];
    [NSThread sleepForTimeInterval:0.02];
    const unsigned after=tries; [NSThread sleepForTimeInterval:0.02];
    assert(after>settledTries && tries==after && !blockingDirties);   // it took, and the retries stopped
    tryAnswer=-1; tries=2;
    // any other publication asks with the blocking mark, as before
    dirtyAnswer=0;
    assert([w nvmtlSurfaceOut] && tries==2 && blockingDirties==1);
    dirtyAnswer=1;
    // Tiled Managed publication must copy into the family image before
    // marking it current. A later GPU generation reloads it even at the same seed.
    Surface managed={.seed=17}; NVMTLTexture *m=[NVMTLTexture new]; m->_surf=&managed; m->_vramOn=YES;
    m->_i=(Image){.w=1,.h=1,.bpp=4,.gpu={3,17,41,255}};
    m->_surfaceLinear=(Image){.w=1,.h=1,.bpp=4,.img=(void*)1};
    dirtyAnswer=0;gpu_copies=0;
    assert([m nvmtlSurfaceOut] && gpu_copies==1 && !memcmp(m->_i.gpu,m->_surfaceLinear.gpu,4));
    memset(m->_surfaceLinear.gpu,82,4);vram_generation++;
    [m nvmtlSurfaceIn];
    assert(gpu_copies==2 && m->_i.gpu[0]==82 && m->_surfGen==vram_generation);
    const unsigned previousUploads=uploads;
    managed.seed++;memset(managed.cpu,93,4);[m nvmtlSurfaceIn];
    assert(uploads==previousUploads+1 && m->_i.gpu[0]==93 && gpu_seed!=managed.seed);
    // Failed copies cannot publish or bless an uninitialized tiled image.
    copy_failure=1;const unsigned marksBefore=blockingDirties;
    assert(![m nvmtlSurfaceOut] && blockingDirties==marksBefore);
    m->_surfSynced=NO;gpu_seed=managed.seed;[m nvmtlSurfaceIn];assert(!m->_surfSynced);
    copy_failure=0;[m nvmtlSurfaceIn];assert(m->_surfSynced && m->_i.gpu[0]==82);
    puts("PASS: overlapping publication preserves GPU pixels; later CPU edits import; a publication after the commit's "
         "VRAM mark never waits for the accelerator lock and retries a busy re-mark off its thread");
} return 0; }
'''
with tempfile.TemporaryDirectory() as directory:
    path = pathlib.Path(directory)
    (path / 'test.m').write_text(fixture + methods + checks)
    subprocess.run(['clang', '-fobjc-arc', '-framework', 'Foundation',
                    str(path / 'test.m'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
    subprocess.run([str(path / 'test'), 'shared'], check=True)
    subprocess.run([str(path / 'test'), 'root', 'host-pages'], check=True)
    subprocess.run([str(path / 'test'), 'shared', 'host-pages'], check=True)
