/* SPDX-License-Identifier: MIT
 * Private Metal probe lookup. Never install this shim or use it with normal apps.
 */
#include <dlfcn.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
extern int gpu_bundle_find_trusted(const char *,char *,size_t);
static int lookup(const char *name,char *output,size_t bytes) {
    const char *bundle=getenv("NM_TEST_METAL_BUNDLE");
    if (bundle && (!strcmp(name,"NVMTLDriver") || !strcmp(name,"NVMTLDriver.bundle"))) {
        char path[PATH_MAX];
        if (!realpath(bundle,path) || strlen(path)+1>bytes)return -1;
        memcpy(output,path,strlen(path)+1);
        return 0;
    }
    return gpu_bundle_find_trusted(name,output,bytes);
}
static void *open_image(const char *path,int flags) {
    const char *bundle=getenv("NM_TEST_METAL_BUNDLE");
    if (bundle && path && !strcmp(path,"/Library/GPUBundles/NVMTLDriver.bundle/Contents/MacOS/NVMTLDriver")) {
        char binary[PATH_MAX];
        int n=snprintf(binary,sizeof binary,"%s/Contents/MacOS/NVMTLDriver",bundle);
        if(n<0 || (size_t)n>=sizeof binary)return NULL;
        return dlopen(binary,flags);
    }
    return dlopen(path,flags);
}
#define INTERPOSE(replacement, original) \
    __attribute__((used)) static struct {const void *replacement; const void *original;} \
    pair_##original __attribute__((section("__DATA,__interpose"))) = {(const void *)&replacement,(const void *)&original}
INTERPOSE(lookup,gpu_bundle_find_trusted);
INTERPOSE(open_image,dlopen);
