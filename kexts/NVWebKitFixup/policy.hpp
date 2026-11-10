// SPDX-License-Identifier: BSD-3-Clause
#pragma once
#include <string.h>

namespace NVWebKitPolicy {
enum class Resource { None, Runtime, NullDevice };

inline bool client(const char *identity, bool platform, bool valid) {
    return platform && valid && identity && !strcmp(identity, "com.apple.WebKit.GPU");
}

inline Resource resource(const char *path) {
    if (!path) return Resource::None;
    if (!strcmp(path, "/dev/null")) return Resource::NullDevice;
    // vn_getpath can expose the Data volume instead of its firmlink.
    constexpr const char data[] = "/System/Volumes/Data/";
    if (!strncmp(path, data, sizeof(data) - 1)) path += sizeof(data) - 2;
    const char *images[] = {
        "/Library/GPUBundles/NVMTLDriver.bundle/Contents/MacOS/NVMTLDriver",
        "/Library/GPUBundles/NVMTLDriver.bundle/Contents/MacOS/libnvmtl_translate.dylib",
        "/Library/GPUBundles/NVMTLDriver.bundle/Contents/MacOS/air-runtime/libLLVM.dylib",
        "/Library/GPUBundles/NVMTLDriver.bundle/Contents/MacOS/air-runtime/libzstd.1.dylib",
        "/Library/GPUBundles/nvmtl/libvulkan_nouveau.dylib",
        "/Library/GPUBundles/nvmtl/libnvmtl_translate.dylib"
    };
    for (const char *image : images) if (!strcmp(path, image)) return Resource::Runtime;
    return Resource::None;
}

inline bool read(int original, int mode, Resource target) {
    return original == 1 && mode == 1 && target == Resource::NullDevice; // EPERM, FREAD only.
}

inline bool map(int original, int prot, int flags, Resource target) {
    // X/RX, MAP_PRIVATE, never MAP_SHARED, MAP_ANON or MAP_JIT.
    return original == 1 && (prot == 4 || prot == 5) && (flags & 3) == 2 &&
        !(flags & (0x1000 | 0x800)) && target == Resource::Runtime;
}
}
