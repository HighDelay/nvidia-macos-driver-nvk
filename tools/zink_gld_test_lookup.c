/* SPDX-License-Identifier: MIT
 * Process-local GLD test shim. It does not register or trust a system bundle.
 * Link with the tested runtime as LC_RPATH: interposed dlopen changes its caller.
 */
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>
#include <sys/sysctl.h>
#include <errno.h>
#include <stdint.h>
#include <CoreFoundation/CoreFoundation.h>
extern int gpu_bundle_find_trusted(const char *, char *, size_t);
static int lookup(const char *name, char *output, size_t bytes)
{
    /* Explicit-attachment probes still need an Apple carrier when the host
     * already selects our automatic GLD. Keep this override process-local. */
    if (getenv("ZINK_TEST_APPLE_GLD") && !strcmp(name,"NVKZinkGLDriver"))
        return gpu_bundle_find_trusted("AppleMetalOpenGLRenderer",output,bytes);
    const char *bundle = getenv("ZINK_TEST_GLD_BUNDLE");
    if (bundle && (!strcmp(name,"AppleMetalOpenGLRenderer") || !strcmp(name,"NVKZinkGLDriver"))) {
        char path[PATH_MAX];
        if (!realpath(bundle,path) || strlen(path)+1 > bytes) return -1;
        memcpy(output,path,strlen(path)+1);
        fprintf(stderr,"Process-local GLD test lookup: %s\n",path);
        return 0;
    }
    return gpu_bundle_find_trusted(name,output,bytes);
}
static void *open_bundle(const char *path, int mode)
{
    const char *suffix = "/Contents/MacOS/NVKZinkGLDriver";
    size_t length = strlen(path), tail = strlen(suffix);
    if (getenv("ZINK_TEST_APPLE_GLD") && length >= tail && !strcmp(path+length-tail,suffix)) {
        char apple[PATH_MAX], executable[PATH_MAX];
        if (gpu_bundle_find_trusted("AppleMetalOpenGLRenderer",apple,sizeof(apple))) return NULL;
        int size = snprintf(executable,sizeof(executable),"%s/Contents/MacOS/AppleMetalOpenGLRenderer",apple);
        if (size<0 || (size_t)size>=sizeof(executable)) return NULL;
        return dlopen(executable,mode);
    }
    const char *bundle = getenv("ZINK_TEST_GLD_BUNDLE");
    char original[PATH_MAX], replacement[PATH_MAX];
    if (bundle) {
        snprintf(original,sizeof(original),"%s/Contents/MacOS/AppleMetalOpenGLRenderer",bundle);
        snprintf(replacement,sizeof(replacement),"%s/Contents/MacOS/NVKZinkGLDriver",bundle);
        if (!strcmp(path,original)) return dlopen(replacement,mode);
    }
    return dlopen(path,mode);
}

/* Simulate a later OS build in this probe process while retaining the actual
 * Apple CGL binaries/layout. This checks the version policy, not a new OS ABI. */
static int os_version(const char *name, void *value, size_t *size, void *new_value, size_t new_size)
{
    const char *build = getenv("ZINK_TEST_OS_BUILD");
    if (build && !strcmp(name,"kern.osversion") && !new_value) {
        if (!size) { errno = EFAULT; return -1; }
        size_t needed = strlen(build)+1, capacity = *size;
        *size = needed;
        if (value) {
            if (capacity<needed) { errno = ENOMEM; return -1; }
            memcpy(value,build,needed);
        }
        return 0;
    }
    return sysctlbyname(name,value,size,new_value,new_size);
}
__attribute__((used)) static struct { const void *replacement, *original; } hook
__attribute__((section("__DATA,__interpose"))) = { (const void *)lookup, (const void *)gpu_bundle_find_trusted };

__attribute__((used)) static struct { const void *replacement, *original; } open_hook
__attribute__((section("__DATA,__interpose"))) = { (const void *)open_bundle, (const void *)dlopen };

__attribute__((used)) static struct { const void *replacement, *original; } version_hook
__attribute__((section("__DATA,__interpose"))) = { (const void *)os_version, (const void *)sysctlbyname };

/* Model absent/explicit OBS preferences without writing the user's defaults. */
static Boolean obs_preference(CFStringRef key, CFStringRef domain, Boolean *valid)
{
    const char *value = getenv("ZINK_TEST_OBS_PREFERENCE");
    if (value && CFEqual(key,CFSTR("NVKZinkCaptureCompatibility")) &&
        CFEqual(domain,CFSTR("com.obsproject.obs-studio"))) {
        if (valid) *valid = strcmp(value,"absent") != 0;
        return !strcmp(value,"on");
    }
    return CFPreferencesGetAppBooleanValue(key,domain,valid);
}
__attribute__((used)) static struct { const void *replacement, *original; } preference_hook
__attribute__((section("__DATA,__interpose"))) = { (const void *)obs_preference, (const void *)CFPreferencesGetAppBooleanValue };

/* Refuse only this process's native Zink presentation imports, before GPU
 * submission. Keep the real kernel checks and standard host import active. */
struct test_surface_import { uint32_t version, surface, plane, pad; uint64_t bytes; };
typedef void (*test_stage_fn)(const struct test_surface_import *);
static _Thread_local test_stage_fn real_stage;
static void refuse_family_vram(const struct test_surface_import *record)
{
    if (!record) { real_stage(NULL); return; }
    struct test_surface_import refused = *record;
    refused.surface = 0;
    real_stage(&refused);
}
static void *find_symbol(void *handle, const char *name)
{
    void *symbol = dlsym(handle,name);
    if (symbol && getenv("ZINK_TEST_NO_FAMILY_VRAM") && !strcmp(name,"nvmtl_nvk_stage_import")) {
        Dl_info caller = {0};
        if (dladdr(__builtin_return_address(0),&caller) && caller.dli_fname &&
            strstr(caller.dli_fname,"/libnvk_zink.dylib")) {
            real_stage = (test_stage_fn)symbol;
            return (void *)refuse_family_vram;
        }
    }
    return symbol;
}
__attribute__((used)) static struct { const void *replacement, *original; } symbol_hook
__attribute__((section("__DATA,__interpose"))) = { (const void *)find_symbol, (const void *)dlsym };
