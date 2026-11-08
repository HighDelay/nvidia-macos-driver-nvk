/* SPDX-License-Identifier: MIT */
#ifndef ZINK_IOSURFACE_TEST_HOOKS_H
#define ZINK_IOSURFACE_TEST_HOOKS_H
#include <vulkan/vulkan.h>
#include <IOSurface/IOSurface.h>
#include <pthread.h>
#include <stdatomic.h>
#include <time.h>

static pthread_mutex_t wait_mutex=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t wait_condition=PTHREAD_COND_INITIALIZER;
static int wait_entered,wait_released;
static _Thread_local int wait_mode,write_lock_depth;
static _Atomic(PFN_vkGetDeviceProcAddr) test_gdpa;
static _Atomic(PFN_vkWaitSemaphores) test_wait;
static _Atomic uint32_t watched_surface;
static _Atomic unsigned write_locks,write_unlocks;

static struct timespec test_deadline(unsigned milliseconds)
{
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME,&ts);
    ts.tv_sec+=milliseconds/1000;
    ts.tv_nsec+=(long)(milliseconds%1000)*1000000;
    if(ts.tv_nsec>=1000000000) { ++ts.tv_sec;ts.tv_nsec-=1000000000; }
    return ts;
}
__attribute__((visibility("default"))) void zink_test_arm_wait(int mode)
{
    pthread_mutex_lock(&wait_mutex);
    wait_entered=wait_released=0;wait_mode=mode;
    pthread_mutex_unlock(&wait_mutex);
}
__attribute__((visibility("default"))) int zink_test_wait_entered(unsigned milliseconds)
{
    struct timespec ts=test_deadline(milliseconds);
    pthread_mutex_lock(&wait_mutex);
    while(!wait_entered)
        if(pthread_cond_timedwait(&wait_condition,&wait_mutex,&ts)) break;
    int result=wait_entered;
    pthread_mutex_unlock(&wait_mutex);
    return result;
}
__attribute__((visibility("default"))) void zink_test_release_wait(void)
{
    pthread_mutex_lock(&wait_mutex);
    wait_released=1;pthread_cond_broadcast(&wait_condition);
    pthread_mutex_unlock(&wait_mutex);
}
__attribute__((visibility("default"))) void zink_test_watch_surface(uint32_t surface)
{
    atomic_store(&write_locks,0);atomic_store(&write_unlocks,0);
    atomic_store(&watched_surface,surface);
}
__attribute__((visibility("default"))) unsigned zink_test_surface_locks(int unlock)
{
    return atomic_load(unlock?&write_unlocks:&write_locks);
}
static VkResult VKAPI_CALL delayed_wait(VkDevice device,const VkSemaphoreWaitInfo *info,uint64_t timeout)
{
    int mode=write_lock_depth?wait_mode:0;
    if(mode) {
        wait_mode=0;
        pthread_mutex_lock(&wait_mutex);
        wait_entered=1;pthread_cond_broadcast(&wait_condition);
        while(!wait_released) pthread_cond_wait(&wait_condition,&wait_mutex);
        pthread_mutex_unlock(&wait_mutex);
        if(mode==2) return VK_TIMEOUT;
    }
    return atomic_load(&test_wait)(device,info,timeout);
}
static PFN_vkVoidFunction VKAPI_CALL watched_gdpa(VkDevice device,const char *name)
{
    PFN_vkVoidFunction proc=atomic_load(&test_gdpa)(device,name);
    if(proc && (!strcmp(name,"vkWaitSemaphores") || !strcmp(name,"vkWaitSemaphoresKHR"))) {
        atomic_store(&test_wait,(PFN_vkWaitSemaphores)proc);
        return (PFN_vkVoidFunction)delayed_wait;
    }
    return proc;
}
static void *zink_test_wrap_symbol(const char *name,void *symbol,void *caller)
{
    Dl_info image={0};
    if(symbol && !strcmp(name,"vkGetDeviceProcAddr") && dladdr(caller,&image) &&
       image.dli_fname && strstr(image.dli_fname,"/libnvk_zink.dylib")) {
        atomic_store(&test_gdpa,(PFN_vkGetDeviceProcAddr)symbol);
        return (void *)watched_gdpa;
    }
    return symbol;
}
static IOReturn watched_lock(IOSurfaceRef surface,IOSurfaceLockOptions options,uint32_t *seed)
{
    IOReturn result=IOSurfaceLock(surface,options,seed);
    if(!result && !(options&kIOSurfaceLockReadOnly) && IOSurfaceGetID(surface)==atomic_load(&watched_surface)) {
        ++write_lock_depth;atomic_fetch_add(&write_locks,1);
    }
    return result;
}
static IOReturn watched_unlock(IOSurfaceRef surface,IOSurfaceLockOptions options,uint32_t *seed)
{
    IOReturn result=IOSurfaceUnlock(surface,options,seed);
    if(!result && !(options&kIOSurfaceLockReadOnly) && IOSurfaceGetID(surface)==atomic_load(&watched_surface)) {
        --write_lock_depth;atomic_fetch_add(&write_unlocks,1);
    }
    return result;
}
__attribute__((used)) static struct {const void *replacement,*original;} surface_hooks[]
__attribute__((section("__DATA,__interpose")))={
    {(const void *)watched_lock,(const void *)IOSurfaceLock},
    {(const void *)watched_unlock,(const void *)IOSurfaceUnlock},
};
#endif
