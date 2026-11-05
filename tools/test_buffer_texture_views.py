#!/usr/bin/env python3
"""Check buffer-texture view lists and safe fallback with mocked device queries.

Compiles production format tables, list selection and view guards with Vulkan
types. This exercises capture's BGRA/RGBA alias without opening a GPU.
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'plugin/nvmtl_vk.c').read_text()
TABLES = SOURCE[SOURCE.index('static const VkFormat nvmtl_vk_view1[]'):SOURCE.index('static int nvmtl_vk_3d_slice_ok(')]
GUARD = SOURCE[SOURCE.index('static int nvmtl_vk_view_guard('):SOURCE.index('static uint32_t nvmtl_spirv_stex_hi(')]
ALIAS = SOURCE.index('    uint32_t vmask = 0;', SOURCE.index('static int nvmtl_vk_image_create_buffer_alias_lifetime_body('))
SELECTION = SOURCE[ALIAS:SOURCE.index('    VKCK(nvmtl_opaque_CreateImage(', ALIAS)]
FEATURES = SOURCE[SOURCE.index('static _Atomic uint64_t g_alias_feature_cache'):SOURCE.index('static int nvmtl_vk_image_create_buffer_alias_lifetime_body(')]

HARNESS = r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdatomic.h>
#include <mach/mach.h>
#include <vulkan/vulkan.h>
#include "nvmtl_vk.h"
#define nvlog(...) ((void)0)
int nvmtl_vk_format_block(uint32_t f, uint32_t *w, uint32_t *h, uint32_t *b) { return -1; }
int nvmtl_vk_format_is_storage(uint32_t f) { return 1; }
static int nvmtl_fbfetch_on(void) { return 0; }
TABLES
GUARD
static int g_linmod, query_missing, query_refused, queries, only_self, no_self;
static int no_format_properties, linear_no_render, modifier_no_render, missing_modifier, modifier_planes=1, property_queries;
static VkFormat self_format, filtered_format;
static VkFormatFeatureFlags remove_features;
static VkPhysicalDevice g_pdev;
static VkInstance g_inst;
static const VkFormatFeatureFlags features = VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT |
    VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT | VK_FORMAT_FEATURE_COLOR_ATTACHMENT_BIT |
    VK_FORMAT_FEATURE_TRANSFER_SRC_BIT | VK_FORMAT_FEATURE_TRANSFER_DST_BIT;
static VkFormatFeatureFlags format_features(VkFormat format)
{
    VkFormatFeatureFlags result=features;
    if ((only_self && format != self_format) || (no_self && format == self_format))
        result=0;
    if (format == filtered_format) result &= ~remove_features;
    return result;
}
static void properties(VkPhysicalDevice device, VkFormat format, VkFormatProperties *out)
{
    memset(out, 0, sizeof *out);
    out->linearTilingFeatures=format_features(format);
    if(linear_no_render) out->linearTilingFeatures &= ~VK_FORMAT_FEATURE_COLOR_ATTACHMENT_BIT;
}
static void properties2(VkPhysicalDevice device, VkFormat format, VkFormatProperties2 *out)
{
    property_queries++;
    VkDrmFormatModifierPropertiesListEXT *list=out->pNext;
    assert(list && list->sType==VK_STRUCTURE_TYPE_DRM_FORMAT_MODIFIER_PROPERTIES_LIST_EXT);
    properties(device,format,&out->formatProperties);
    if(list->pDrmFormatModifierProperties) {
        assert(list->drmFormatModifierCount>=2);
        list->pDrmFormatModifierProperties[0]=(VkDrmFormatModifierPropertiesEXT){123,1,features};
        VkFormatFeatureFlags bits=format_features(format);
        if(modifier_no_render) bits &= ~VK_FORMAT_FEATURE_COLOR_ATTACHMENT_BIT;
        list->pDrmFormatModifierProperties[1]=(VkDrmFormatModifierPropertiesEXT){missing_modifier?456:0,modifier_planes,bits};
    }
    list->drmFormatModifierCount=2;
}
static VkResult query(VkPhysicalDevice device, const VkPhysicalDeviceImageFormatInfo2 *in,
                      VkImageFormatProperties2 *out)
{
    queries++;
    assert(in->flags == VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT);
    const VkImageFormatListCreateInfo *list;
    if (g_linmod) {
        const VkPhysicalDeviceImageDrmFormatModifierInfoEXT *mod = in->pNext;
        assert(mod->sType == VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_DRM_FORMAT_MODIFIER_INFO_EXT);
        assert(mod->drmFormatModifier == 0);
        list = mod->pNext;
    } else list = in->pNext;
    assert(list->sType == VK_STRUCTURE_TYPE_IMAGE_FORMAT_LIST_CREATE_INFO && list->viewFormatCount >= 2);
    int self = 0;
    for (uint32_t i = 0; i < list->viewFormatCount; i++) {
        self |= list->pViewFormats[i] == in->format;
        VkFormatProperties p; properties(device, list->pViewFormats[i], &p);
        VkFormatFeatureFlags bits=g_linmod?format_features(list->pViewFormats[i]):p.linearTilingFeatures;
        assert((bits & nvmtl_vk_usage_features(in->usage)) == nvmtl_vk_usage_features(in->usage));
    }
    assert(self);
    return query_refused ? VK_ERROR_FORMAT_NOT_SUPPORTED : VK_SUCCESS;
}
static PFN_vkVoidFunction pvkGetInstanceProcAddr(VkInstance instance, const char *name)
{
    if(!strcmp(name,"vkGetPhysicalDeviceFormatProperties2")) return no_format_properties?NULL:(PFN_vkVoidFunction)properties2;
    assert(!strcmp(name, "vkGetPhysicalDeviceImageFormatProperties2"));
    return query_missing ? NULL : (PFN_vkVoidFunction)query;
}
FEATURES
static nvk_image select_views(uint32_t vkfmt, uint32_t bpp, int a8, int no_properties, VkImageUsageFlags usage)
{
    self_format = (VkFormat)vkfmt;
    no_format_properties=no_properties;
    PFN_vkGetPhysicalDeviceFormatProperties gfp = no_properties ? NULL : properties;
    VkImageDrmFormatModifierExplicitCreateInfoEXT mod = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_DRM_FORMAT_MODIFIER_EXPLICIT_CREATE_INFO_EXT };
    VkImageCreateInfo ici = { .sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
        .pNext = g_linmod ? &mod : NULL, .format = (VkFormat)vkfmt,
        .imageType = VK_IMAGE_TYPE_2D, .usage = usage,
        .tiling = g_linmod ? VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT : VK_IMAGE_TILING_LINEAR };
    SELECTION
    if (ici.flags & VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT) {
        const VkImageFormatListCreateInfo *list = ici.pNext;
        assert(list == &fl && list->viewFormatCount == nlist && list->pNext == (g_linmod ? &mod : NULL));
        assert(nlist >= 2 && vmask);
    } else assert(!vmask && !nlist);
    return (nvk_image){ .fmt = vkfmt, .bpp = bpp, .mtl_type = 2, .bw = 1,
        .vkflags = ici.flags, .usage = usage, .vfl = vmask };
}
static void reset(void)
{
    query_missing = query_refused = queries = only_self = no_self = 0;
    filtered_format = VK_FORMAT_UNDEFINED; remove_features = 0;
    no_format_properties=linear_no_render=modifier_no_render=missing_modifier=property_queries=0;modifier_planes=1;
    memset(g_alias_feature_cache,0,sizeof g_alias_feature_cache);
}
static int view(const nvk_image *image, VkFormat format)
{
    VkImageViewUsageCreateInfo usage;
    return nvmtl_vk_view_guard(image, format, 0, VK_IMAGE_VIEW_TYPE_2D, 1, &usage, "test");
}
int main(void)
{
    // The two tilings can expose different capabilities. The modifier query
    // must select modifier 0, not use plain-linear bits or the first modifier.
    reset();g_linmod=1;linear_no_render=1;
    assert(nvmtl_vk_buffer_tiling_features(properties,VK_FORMAT_R16G16B16A16_SFLOAT)&VK_FORMAT_FEATURE_COLOR_ATTACHMENT_BIT);
    assert(property_queries==2);
    assert(nvmtl_vk_buffer_tiling_features(properties,VK_FORMAT_R16G16B16A16_SFLOAT)&VK_FORMAT_FEATURE_COLOR_ATTACHMENT_BIT);
    assert(property_queries==2); // per-format cache avoids per-wrapper queries
    g_linmod=0;
    assert(!(nvmtl_vk_buffer_tiling_features(properties,VK_FORMAT_R16G16B16A16_SFLOAT)&VK_FORMAT_FEATURE_COLOR_ATTACHMENT_BIT));
    reset();g_linmod=1;modifier_no_render=1;
    assert(!(nvmtl_vk_buffer_tiling_features(properties,VK_FORMAT_R16G16B16A16_SFLOAT)&VK_FORMAT_FEATURE_COLOR_ATTACHMENT_BIT));
    reset();missing_modifier=1;
    assert(!nvmtl_vk_buffer_tiling_features(properties,VK_FORMAT_R16G16B16A16_SFLOAT));
    reset();modifier_planes=2;
    assert(!nvmtl_vk_buffer_tiling_features(properties,VK_FORMAT_R16G16B16A16_SFLOAT));
    reset();no_format_properties=1;
    assert(!nvmtl_vk_buffer_tiling_features(properties,VK_FORMAT_R16G16B16A16_SFLOAT));
    const VkImageUsageFlags usage = VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    for (uint32_t size = 1; size <= 16; size *= 2) {
        const VkFormat *formats; uint32_t count = nvmtl_vk_view_group(size, &formats);
        assert(count > 1 && count <= 32);
        for (uint32_t a = 0; a < count; a++) for (uint32_t b = a + 1; b < count; b++) assert(formats[a] != formats[b]);
        for (g_linmod = 0; g_linmod <= 1; g_linmod++) {
            reset(); nvk_image image = select_views(formats[0], size, 0, 0, usage);
            assert(queries == 1);
            for (uint32_t i = 0; i < count; i++) assert(nvmtl_vk_view_listed(&image, formats[i]) && view(&image, formats[i]) == 0);
        }
    }
    const VkFormat *none = (const VkFormat *)1;
    assert(nvmtl_vk_view_group(3, &none) == 0 && !none);
    for (g_linmod = 0; g_linmod <= 1; g_linmod++) {
        reset(); nvk_image image = select_views(VK_FORMAT_B8G8R8A8_UNORM, 4, 0, 0, usage);
        assert(view(&image, VK_FORMAT_R8G8B8A8_UNORM) == 0); // screen capture's reinterpretation
        assert(view(&image, VK_FORMAT_R16G16B16A16_SFLOAT) < 0); // a different size stays refused
        const VkImageUsageFlags uses[] = { VK_IMAGE_USAGE_SAMPLED_BIT, VK_IMAGE_USAGE_STORAGE_BIT,
            VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT, VK_IMAGE_USAGE_TRANSFER_SRC_BIT, VK_IMAGE_USAGE_TRANSFER_DST_BIT };
        const VkFormatFeatureFlags needs[] = { VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT, VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT,
            VK_FORMAT_FEATURE_COLOR_ATTACHMENT_BIT, VK_FORMAT_FEATURE_TRANSFER_SRC_BIT, VK_FORMAT_FEATURE_TRANSFER_DST_BIT };
        for (unsigned i = 0; i < sizeof uses / sizeof *uses; i++) {
            reset(); filtered_format = VK_FORMAT_R8G8B8A8_SRGB; remove_features = needs[i];
            image = select_views(VK_FORMAT_B8G8R8A8_UNORM, 4, 0, 0, usage | uses[i]);
            assert(!nvmtl_vk_view_listed(&image, filtered_format) && view(&image, filtered_format) < 0);
            assert(view(&image, VK_FORMAT_R8G8B8A8_UNORM) == 0);
        }
        for (int scenario = 0; scenario < 7; scenario++) {
            reset();
            query_missing = scenario == 0; query_refused = scenario == 1;
            only_self = scenario == 2; no_self = scenario == 3;
            image = select_views(VK_FORMAT_B8G8R8A8_UNORM, scenario == 6 ? 3 : 4,
                                 scenario == 5, scenario == 4, usage);
            assert(!image.vfl && !(image.vkflags & VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT));
            assert(view(&image, VK_FORMAT_B8G8R8A8_UNORM) == 0 && view(&image, VK_FORMAT_R8G8B8A8_UNORM) < 0);
            assert(queries == (scenario == 0 ? 0 : scenario == 1 ? 1 : 0));
        }
    }
    puts("PASS tiling-specific capabilities/cache, missing modifier/query refusal, format groups, capture views, usage filtering and LINEAR/DRM list fallbacks");
}
'''

with tempfile.TemporaryDirectory(prefix='nvmtl-buffer-views-') as tmp:
    src, exe = Path(tmp) / 'test.c', Path(tmp) / 'test'
    src.write_text(HARNESS.replace('TABLES', TABLES).replace('GUARD', GUARD).replace('FEATURES', FEATURES).replace('SELECTION', SELECTION))
    headers = Path(os.environ.get('VULKAN_HEADERS', Path.home() / 'Vulkan-Headers/include'))
    subprocess.run(['xcrun', 'clang', '-std=c11', '-O1', '-fsanitize=address,undefined',
                    '-fno-sanitize-recover=all', '-I' + str(headers), '-I' + str(ROOT / 'plugin'),
                    str(src), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
