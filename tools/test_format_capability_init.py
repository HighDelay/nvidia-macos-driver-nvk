#!/usr/bin/env python3
"""Exercise simultaneous first format queries and a delayed instance resolver.

Compile the production instance-dispatch loader, initialization guard and storage
query. The negative control uses the previous unsynchronized function cache.
No GPU or Vulkan SDK is needed.
"""
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'plugin/nvmtl_vk.c').read_text()


def function(signature):
    start = SOURCE.index(signature + '\n{')
    return SOURCE[start:SOURCE.index('\n}\n', start) + 3]


macro = SOURCE[SOURCE.index('#define INSTANCE_FNS('):SOURCE.index('#define DEVICE_FNS(')]
names = re.findall(r'X\((\w+)\)', macro)
declarations = '\n'.join('typedef void (*PFN_' + name + ')(void);' for name in names
                         if name != 'vkGetPhysicalDeviceFormatProperties')
loader = next(line for line in SOURCE.splitlines() if line.startswith('#define LOAD_I('))
start = SOURCE.index('static pthread_once_t g_vk_once =')
guard = SOURCE[start:SOURCE.index('const char *nvmtl_vk_device_name(', start)]
query = function('int nvmtl_vk_format_is_storage(uint32_t vkfmt)')
negative = r'''
int nvmtl_vk_format_is_storage(uint32_t vkfmt) {
    static PFN_vkGetPhysicalDeviceFormatProperties fp;
    static int tried;
    if (nvmtl_vk_init()) return 0;
    if (!tried) {
        tried = 1;
        fp = (PFN_vkGetPhysicalDeviceFormatProperties)pvkGetInstanceProcAddr(g_inst, "vkGetPhysicalDeviceFormatProperties");
    }
    if (!fp) return 0;
    VkFormatProperties properties = {0}; fp(g_pdev, vkfmt, &properties);
    return (properties.optimalTilingFeatures & VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT) != 0;
}
'''
fixture = r'''
#include <assert.h>
#include <pthread.h>
#include <stdint.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
typedef void *VkInstance, *VkPhysicalDevice;
typedef uint32_t VkFormat;
typedef struct { uint32_t linearTilingFeatures, optimalTilingFeatures, bufferFeatures; } VkFormatProperties;
#define VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT 2u
typedef void (*PFN_vkVoidFunction)(void);
typedef void (*PFN_vkGetPhysicalDeviceFormatProperties)(VkPhysicalDevice, VkFormat, VkFormatProperties *);
DECLARATIONS
static VkInstance g_inst = (void *)1;
static VkPhysicalDevice g_pdev = (void *)2;
static _Atomic int g_state;
static _Atomic unsigned resolutions, ready, start_queries, incorrect;
static int missing;
static void properties(VkPhysicalDevice device, VkFormat format, VkFormatProperties *out) {
    assert(device == g_pdev); memset(out, 0, sizeof *out);
    out->optimalTilingFeatures = format == 64 ? VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT : 0;
}
static void placeholder(void) {}
static PFN_vkVoidFunction pvkGetInstanceProcAddr(VkInstance instance, const char *name) {
    assert(instance == g_inst);
    if (!strcmp(name, "vkGetPhysicalDeviceFormatProperties")) {
        atomic_fetch_add(&resolutions, 1);
        usleep(150000); // Other queries overlap function-pointer initialization.
        return missing ? NULL : (PFN_vkVoidFunction)properties;
    }
    return placeholder;
}
#define nvlog(...) ((void)0)
MACRO
#define DECL(f) static PFN_##f p##f;
INSTANCE_FNS(DECL)
LOADER
static int nvmtl_vk_initialize(void) { INSTANCE_FNS(LOAD_I) g_state = 1; return 0; }
GUARD
QUERY
static void *worker(void *unused) {
    (void)unused; atomic_fetch_add(&ready, 1);
    while (!atomic_load_explicit(&start_queries, memory_order_acquire)) usleep(100);
    if (nvmtl_vk_format_is_storage(64) != !missing) atomic_fetch_add(&incorrect, 1);
    return NULL;
}
int main(int argc, char **argv) {
    missing = argc > 1 && atoi(argv[1]);
    enum { Threads = 24 }; pthread_t threads[Threads];
    for (unsigned i=0; i<Threads; i++) assert(!pthread_create(&threads[i], NULL, worker, NULL));
    while (atomic_load(&ready) != Threads) usleep(100);
    atomic_store_explicit(&start_queries, 1, memory_order_release);
    for (unsigned i=0; i<Threads; i++) assert(!pthread_join(threads[i], NULL));
    if (NEGATIVE_CONTROL) assert(!missing && atomic_load(&incorrect) > 0);
    else {
        assert(!atomic_load(&incorrect));
        assert(atomic_load(&resolutions) == 1);
    }
    assert(nvmtl_vk_format_is_storage(58) == 0); // An unsupported format stays unsupported.
    printf("PASS: %s missing=%d incorrect=%u queries=%u\n", NEGATIVE_CONTROL ? "negative control reproduces race" : "concurrent first use", missing, atomic_load(&incorrect), atomic_load(&resolutions));
}
'''
fixture = fixture.replace('DECLARATIONS', declarations).replace('MACRO', macro).replace('LOADER', loader).replace('GUARD', guard)
with tempfile.TemporaryDirectory(prefix='nvmtl-format-init-') as directory:
    for label, method, control in [('current', query, 0), ('negative', negative, 1)]:
        source, binary = Path(directory) / (label + '.c'), Path(directory) / label
        source.write_text(fixture.replace('QUERY', method).replace('NEGATIVE_CONTROL', str(control)))
        subprocess.run(['xcrun', 'clang', '-O1', '-g', '-fsanitize=address,undefined',
                        '-fno-omit-frame-pointer', '-pthread', str(source), '-o', str(binary)], check=True)
        subprocess.run([str(binary), '0'], check=True, timeout=10)
        if not control:
            subprocess.run([str(binary), '1'], check=True, timeout=10)
