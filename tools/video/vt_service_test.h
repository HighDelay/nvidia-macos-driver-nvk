/* SPDX-License-Identifier: MIT
 * Isolated codec-service bootstrap tests. Does not install or invoke a registrar.
 */
#include <dlfcn.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void vt_service_isolation(void) {
    unsigned drivers = 0, renderers = 0;
    for (uint32_t i = 0; i < _dyld_image_count(); ++i) {
        const char *path = _dyld_get_image_name(i), *name = strrchr(path, '/');
        name = name ? name + 1 : path;
        drivers += !strcmp(name, "NVMTLDriver");
        renderers += !strcmp(name, "libvulkan_nouveau.dylib") || !strcmp(name, "libnvk_renderer.dylib");
    }
    require(drivers == 1 && renderers == 0, "one plugin image and no Vulkan render-device initialization");
}

static void *vt_service_test(const char *argument, const char *role) {
    char prefix[48]; snprintf(prefix, sizeof prefix, "%s-service:", role);
    if (strncmp(argument, prefix, strlen(prefix))) return NULL;
    const char *plugin = argument + strlen(prefix);
    require(plugin[0] == '/', "service test needs an absolute plugin path");
    setprogname(!strcmp(role, "decoder") ? "VTDecoderXPCService" : "VTEncoderXPCService");
    require(dlopen(plugin, RTLD_NOW | RTLD_LOCAL) != NULL, "load isolated plugin");
    char addon[4096]; require(strlen(plugin) < sizeof addon - 32, "bounded plugin path");
    strcpy(addon, plugin); char *slash = strrchr(addon, '/'); strcpy(slash + 1, "libnvencvt.dylib");
    void *library = NULL;
    for (unsigned attempt = 0; attempt < 500 && !library; ++attempt) {
        library = dlopen(addon, RTLD_LAZY | RTLD_LOCAL | RTLD_NOLOAD);
        if (!library) usleep(10000);
    }
    require(library != NULL, "service constructor loads the matching addon without a Metal device");
    usleep(100000); // The library mapping precedes its short registration call.
    vt_service_isolation();
    printf("bootstrap=%s driver_images=1 renderer_images=0\n", role);
    return library;
}
