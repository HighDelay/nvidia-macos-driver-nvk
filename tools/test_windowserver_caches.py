#!/usr/bin/env python3
"""Exercise the bounded caches used by WindowServer-like repeated render setup."""
import pathlib
import subprocess
import tempfile

root = pathlib.Path(__file__).resolve().parents[1]
source = (root / "plugin/nvmtl_vk.c").read_text()
header = (root / "plugin/nvmtl_vk.h").read_text()

def function(name, end):
    start = source.index(name)
    return source[start:source.index(end, start)]

viewport = function("void nvmtl_vk_cmd_set_viewport(", "\nstatic void nvmtl_vk_zero_dummies")
viewport = viewport[:viewport.index("\n}", viewport.index("{") + 1) + 2]
scissor = function("void nvmtl_vk_cmd_set_scissor(", "\nvoid nvmtl_vk_cmd_bind_pipeline")
cull = function("void nvmtl_vk_cmd_set_cull(", "\n// An index offset")
blend = function("void nvmtl_vk_cmd_set_blend_color(", "\nvoid nvmtl_vk_cmd_set_stencil_ref")
topology = function("int nvmtl_vk_cmd_set_topology(", "\nint nvmtl_vk_cmd_vertex_inputs")

fixture = r'''
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <mach/mach.h>
#include <vulkan/vulkan.h>
#include "nvmtl_vk.h"
int g_topo_any = 1;
#define NVMTL_DYN_VIEWPORT (1u << 0)
#define NVMTL_DYN_SCISSOR  (1u << 1)
#define NVMTL_DYN_TOPOLOGY (1u << 2)
#define NVMTL_DYN_CULL    (1u << 3)
#define NVMTL_DYN_BLEND   (1u << 4)
static unsigned vp_calls, sc_calls, topo_calls, restart_calls, cull_calls, front_calls, blend_calls;
static void pvkCmdSetViewport(VkCommandBuffer c, unsigned first, unsigned count, const VkViewport *v) { (void)c;(void)first;(void)count;(void)v; vp_calls++; }
static void pvkCmdSetScissor(VkCommandBuffer c, unsigned first, unsigned count, const VkRect2D *r) { (void)c;(void)first;(void)count;(void)r; sc_calls++; }
static void pvkCmdSetPrimitiveTopology(VkCommandBuffer c, VkPrimitiveTopology t) { (void)c;(void)t; topo_calls++; }
static void pvkCmdSetPrimitiveRestartEnable(VkCommandBuffer c, VkBool32 e) { (void)c;(void)e; restart_calls++; }
static void pvkCmdSetCullMode(VkCommandBuffer c, VkCullModeFlags m) { (void)c;(void)m; cull_calls++; }
static void pvkCmdSetFrontFace(VkCommandBuffer c, VkFrontFace f) { (void)c;(void)f; front_calls++; }
static void pvkCmdSetBlendConstants(VkCommandBuffer c, const float *v) { (void)c;(void)v; blend_calls++; }
static void nvlog(const char *fmt, ...) { (void)fmt; }
static nvk_pipeline *nvmtl_uniform_cbuf_pick(nvk_cmdbuf *c, nvk_pipeline *p) { (void)c; return p; }
''' + "\n".join((viewport, scissor, topology, cull, blend)) + r'''
int main(void) {
    nvk_cmdbuf c = {0}; c.cb = (void *)1;
    for (unsigned i = 0; i < 10000; i++) {
        nvmtl_vk_cmd_set_viewport(&c, 0, 0, 1920, 1080, 0, 1);
        nvmtl_vk_cmd_set_scissor(&c, 0, 0, 1920, 1080);
        nvmtl_vk_cmd_set_topology(&c, 3);
        nvmtl_vk_cmd_set_cull(&c, 2);
        const float blend[] = { 0.1f, 0.2f, 0.3f, 0.4f };
        nvmtl_vk_cmd_set_blend_color(&c, blend);
    }
    assert(vp_calls == 1 && sc_calls == 1 && topo_calls == 1 && restart_calls == 1);
    assert(cull_calls == 1 && front_calls == 1 && blend_calls == 1);
    nvmtl_vk_cmd_set_viewport(&c, 0, 0, 1280, 720, NAN, 2); assert(vp_calls == 2);
    nvmtl_vk_cmd_set_scissor(&c, 1, 2, 3, 4); assert(sc_calls == 2);
    nvmtl_vk_cmd_set_topology(&c, 4); assert(topo_calls == 2 && restart_calls == 2);
    c.restart_off = 1; nvmtl_vk_cmd_set_topology(&c, 4); assert(topo_calls == 3 && restart_calls == 3);
    nvmtl_vk_cmd_set_topology(&c, 3); assert(topo_calls == 4 && restart_calls == 4);
    nvmtl_vk_cmd_set_cull(&c, 0); assert(cull_calls == 2 && front_calls == 2);
    const float changed[] = { 0.1f, 0.2f, 0.3f, 0.5f }; nvmtl_vk_cmd_set_blend_color(&c, changed); assert(blend_calls == 2);
    memset(&c, 0, sizeof c); c.cb = (void *)1;
    nvmtl_vk_cmd_set_viewport(&c, 0, 0, 1920, 1080, 0, 1); nvmtl_vk_cmd_set_scissor(&c, 0, 0, 1920, 1080);
    nvmtl_vk_cmd_set_topology(&c, 3); nvmtl_vk_cmd_set_cull(&c, 2); nvmtl_vk_cmd_set_blend_color(&c, (float[]){0.1f,0.2f,0.3f,0.4f});
    assert(vp_calls == 3 && sc_calls == 3 && topo_calls == 5 && restart_calls == 5 && cull_calls == 3 && front_calls == 3 && blend_calls == 3);
    puts("PASS: repeated viewport/scissor/topology/cull/blend setup is elided; changed state and fresh recordings emit");
}
'''

with tempfile.TemporaryDirectory(prefix="nvmtl-windowserver-cache-") as directory:
    d = pathlib.Path(directory)
    test = d / "test.c"
    exe = d / "test"
    test.write_text(fixture)
    subprocess.run(["xcrun", "clang", "-O2", "-fsanitize=address,undefined", str(test),
                    "-I" + str(root / "plugin"), "-I" + str(pathlib.Path.home() / "Vulkan-Headers/include"),
                    "-o", str(exe)], check=True)
    subprocess.run([str(exe)], check=True)

device = (root / "plugin/NVMTLDevice.m").read_text()
assert 'CFSTR("NVMTLVramAt:p0")' in device and 'CFSTR("NVMTLVramAt:p3")' in device
assert "if (plane < sizeof common / sizeof common[0])" in device
assert 'CFStringCreateWithFormat(NULL, NULL, CFSTR("NVMTLVramAt:p%lu")' in device
print("PASS: common IOSurface VRAM tag keys use retained constants and uncommon planes keep the formatted fallback")
