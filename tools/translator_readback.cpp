// Bounded, offscreen compute readbacks using a selected built NVMTL plugin.
// Nothing is installed. Keep this header and the selected plugin at matching revisions.
#include <mach/mach.h>
#include <vulkan/vulkan.h>
extern "C" {
#include "../plugin/nvmtl_vk.h"
}
#include <dlfcn.h>
#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <limits>
#include <string>
#include <vector>

#define API(X) \
 X(nvmtl_vk_init) X(nvmtl_vk_device_name) X(nvmtl_vk_queue_create) \
 X(nvmtl_vk_buffer_create) X(nvmtl_vk_buffer_address) X(nvmtl_vk_buffer_destroy) \
 X(nvmtl_vk_compute_pipeline_create) X(nvmtl_vk_pipeline_destroy) \
 X(nvmtl_vk_cmd_begin) X(nvmtl_vk_bind_buffer) X(nvmtl_vk_cmd_dispatch_threads) \
 X(nvmtl_vk_submit_wait_at) X(nvmtl_vk_queue_destroy) \
 X(nvmtl_vk_image_create_typed) X(nvmtl_vk_image_write) X(nvmtl_vk_image_read) \
 X(nvmtl_vk_bind_texture_view) X(nvmtl_vk_bind_storage_view) X(nvmtl_vk_image_destroy) \
 X(nvmtl_vk_sampler_create) X(nvmtl_vk_bind_sampler) X(nvmtl_vk_sampler_destroy)
#define DECLARE(name) static decltype(&name) call_##name;
API(DECLARE)
#undef DECLARE

static void check(int rc, const char *operation) {
    if (rc) { fprintf(stderr, "FAIL %s: %d\n", operation, rc); std::exit(2); }
}
static void require(bool condition, const char *description) {
    if (!condition) { fprintf(stderr, "FAIL %s\n", description); std::exit(3); }
}
static uint32_t round_even(double v) {
    double f = std::floor(v), rest = v - f;
    return (uint32_t)f + (rest > 0.5 || (rest == 0.5 && ((uint32_t)f & 1)));
}
static uint32_t unorm(float v, uint32_t maximum) {
    if (std::isnan(v)) v = 0;
    return round_even((double)std::fmin(1.f, std::fmax(0.f, v)) * maximum);
}
static float half_value(uint16_t bits) {
    unsigned exponent = (bits >> 10) & 31, fraction = bits & 1023;
    float value = exponent == 31 ? (fraction ? std::numeric_limits<float>::quiet_NaN() : std::numeric_limits<float>::infinity())
        : std::ldexp((float)(fraction + (exponent ? 1024 : 0)), exponent ? (int)exponent - 25 : -24);
    return bits & 0x8000 ? -value : value;
}

int main(int argc, char **argv) {
    require(argc == 4, "usage: translator_readback plugin SPIRV case");
    void *plugin = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!plugin) { fprintf(stderr, "%s\n", dlerror()); return 2; }
#define LOAD(name) call_##name = reinterpret_cast<decltype(call_##name)>(dlsym(plugin, #name)); require(call_##name != nullptr, #name);
    API(LOAD)
#undef LOAD
    check(call_nvmtl_vk_init(), "Vulkan initialization");
    printf("GPU %s\n", call_nvmtl_vk_device_name()); fflush(stdout);
    std::ifstream input(argv[2], std::ios::binary);
    std::vector<char> code((std::istreambuf_iterator<char>(input)), {});
    require(!code.empty(), "SPIR-V input");
    const std::string mode = argv[3];
    const bool wrapped = mode.rfind("wrapped-", 0) == 0;
    const float uniform_slots[][4] = {{0.25f,-1.5f,2.f,16.f},{4.f,2.5f,-3.f,0.125f},{-1.f,8.f,0.5f,-16.f}};
    const uint32_t uniform_bits[] = {0x80000000u,0x7fc12345u,0xff800000u,0x3f800001u};
    nvk_queue queue{}; nvk_pipeline pipeline{};
    check(call_nvmtl_vk_queue_create(&queue), "queue");
    check(call_nvmtl_vk_compute_pipeline_create(code.data(), code.size(), &pipeline), "compute pipeline");
    nvk_buffer buffers[4]{};
    for (auto &b : buffers) {
        check(call_nvmtl_vk_buffer_create(4096, 1, &b), "host-visible buffer");
        require(b.map != nullptr, "mapped coherent output");
        memset(b.map, 0xa5, 4096);
    }
    nvk_image images[2]{}; nvk_sampler sampler{}, linear_sampler{};
    const uint32_t one[3] = {1, 1, 1};
    uint32_t threads[3] = {1, 1, 1};
    const float texture_a[4] = {0.125f, 0.25f, 0.5f, 1.f};
    const float texture_b[4] = {0.75f, 0.5f, 0.25f, 0.f};
    nvk_cmdbuf command{};
    if (wrapped) {
        const size_t stride = mode == "wrapped-trailing" || mode == "wrapped-array" ? 32 : 16;
        for (unsigned slot = 0; slot < 3; ++slot)
            memcpy((char *)buffers[1].map+(slot+1)*stride, uniform_slots[slot], 16);
        if (mode == "wrapped-bitcopy") memcpy((char *)buffers[1].map+stride,uniform_bits,16);
    } else if (mode == "forward0" || mode == "forward1") {
        ((uint64_t *)buffers[0].map)[0] = call_nvmtl_vk_buffer_address(&buffers[1]);
        ((uint64_t *)buffers[0].map)[1] = call_nvmtl_vk_buffer_address(&buffers[2]);
        ((float *)buffers[1].map)[0] = 4.25f;
        ((float *)buffers[2].map)[0] = 17.5f;
    } else if (mode == "helper") {
        // array_ref<void> points into the driver's synthesized address table:
        // Metal buffer slots 0, 1 and 2 are the two inputs and the output.
        for (unsigned i = 0; i < 16; i++) {
            ((float *)buffers[0].map)[i] = 4.f + (float)i;
            ((float *)buffers[2].map)[i] = 1.f + (float)i * 0.25f;
        }
        // Keep a regressed direct-load path inside a real canary allocation.
        // The correct path uses the driver table and writes slot 1 instead.
        ((uint64_t *)buffers[0].map)[0] = call_nvmtl_vk_buffer_address(&buffers[3]);
        ((uint64_t *)buffers[0].map)[1] = call_nvmtl_vk_buffer_address(&buffers[3]);
        for (unsigned i = 0; i < 4; i++) require(std::isfinite(((float *)buffers[0].map)[i]), "finite pointer-canary input");
        threads[0] = 8;
    } else if (mode == "table" || mode == "phi" || mode == "alloca" || mode == "callback") {
        ((uint64_t *)buffers[0].map)[0] = call_nvmtl_vk_buffer_address(&buffers[3]);
        ((uint64_t *)buffers[0].map)[1] = call_nvmtl_vk_buffer_address(&buffers[3]);
        ((uint16_t *)buffers[1].map)[0] = 0x17a5;
    } else if (mode == "i64") {
        ((uint64_t *)buffers[1].map)[0] = call_nvmtl_vk_buffer_address(&buffers[2]);
        ((uint64_t *)buffers[2].map)[1] = 0x1122334455667788ull;
    }

    const bool texture_array = mode.rfind("texture-array", 0) == 0;
    const bool sampler_array = mode.rfind("sampler-array", 0) == 0;
    const bool texture = mode == "texture5" || mode == "texture127" || mode == "texture1d" || mode == "storage40" || texture_array || sampler_array;
    if (texture) {
        // Metal type 0 is 1D; type 2 is 2D. Vulkan format 109 is RGBA32F.
        check(call_nvmtl_vk_image_create_typed(sampler_array ? 2 : 1, 1, 109, 16, 0, 1, 1,
                                              mode == "texture1d" ? 0 : 2, 1, &images[0]), "source image");
        float pixels[8]; memcpy(pixels, texture_a, 16); memcpy(pixels + 4, texture_b, 16);
        check(call_nvmtl_vk_image_write(&queue, &images[0], pixels, sampler_array ? 32 : 16), "source upload");
        if (mode == "storage40" || mode == "texture127" || texture_array) {
            check(call_nvmtl_vk_image_create_typed(1, 1, 109, 16, 0, 1, 1, 2, 1, &images[1]), "second image");
            check(call_nvmtl_vk_image_write(&queue, &images[1], texture_b, 16), "second upload");
        }
        check(call_nvmtl_vk_sampler_create(0, 0, &sampler), "nearest sampler");
        if (sampler_array) check(call_nvmtl_vk_sampler_create(1, 0, &linear_sampler), "linear sampler");
        if (texture_array) ((uint64_t *)buffers[1].map)[0] = 1;
    }

    // Run several round-to-even, clamp and NaN controls through the same pipeline.
    const float controls[][4] = {{0.f, 0.5f, 1.f, 0.5f}, {-1.f, 2.f, 0.25f, 1.f},
        {std::numeric_limits<float>::quiet_NaN(), 0.75f, 0.5f, 0.f},
        {1.f / 2046.f, 3.f / 2046.f, 5.f / 2046.f, 1.f / 6.f}};
    const uint16_t half_controls[][3] = {{0x3c00, 0x3800, 0}, {0x7e00, 0x3c00, 0x3800},
        {0xbc00, 0x4000, 0x3400}, {0x3800, 0x3800, 0x3800}, {0x7c00, 0xfc00, 1}};
    const unsigned runs = wrapped ? 3 : mode == "pack10" ? sizeof(controls) / sizeof(controls[0])
        : mode == "pack565dyn" ? sizeof(half_controls) / sizeof(half_controls[0]) : 1;
    for (unsigned run = 0; run < runs; run++) {
        if (wrapped) ((uint32_t *)buffers[1].map)[1] = run+1;
        std::vector<char> input_snapshot;
        if (wrapped) input_snapshot.assign((char *)buffers[1].map,(char *)buffers[1].map+4096);
        if (mode == "pack10") memcpy(buffers[1].map, controls[run], 16);
        if (mode == "pack565dyn") memcpy(buffers[1].map, half_controls[run], 6);
        check(call_nvmtl_vk_cmd_begin(&queue, &command), "command begin");
        for (unsigned i = 0; i < 4; i++) check(call_nvmtl_vk_bind_buffer(&command, 0, i, &buffers[i], 0), "bind buffer");
        if (texture) {
            unsigned index = mode == "texture5" ? 5 : mode == "texture127" || mode == "storage40" || texture_array || sampler_array ? 40 : 0;
            check(call_nvmtl_vk_bind_texture_view(&command, 0, index, images[0].view), "bind sampled image and heap slot");
            check(call_nvmtl_vk_bind_sampler(&command, 0, 2, &sampler), "bind sampler 2");
            if (mode == "texture127") {
                check(call_nvmtl_vk_bind_texture_view(&command, 0, 127, images[1].view), "bind sampled image 127 and heap slot");
                check(call_nvmtl_vk_bind_sampler(&command, 0, 8, &sampler), "bind sampler 8");
                check(call_nvmtl_vk_bind_sampler(&command, 0, 15, &sampler), "bind sampler 15");
            }
            if (mode == "storage40") check(call_nvmtl_vk_bind_storage_view(&command, 0, 40, images[1].view), "bind storage image 40");
            if (texture_array) check(call_nvmtl_vk_bind_texture_view(&command, 0, 41, images[1].view), "bind array element 1");
            if (texture_array || sampler_array) check(call_nvmtl_vk_bind_sampler(&command, 0, 14, &sampler), "bind array sampler 0");
            if (sampler_array) check(call_nvmtl_vk_bind_sampler(&command, 0, 15, &linear_sampler), "bind array sampler 1");
        }
        call_nvmtl_vk_cmd_dispatch_threads(&command, &pipeline, threads, one);
        require(!command.nobind && !command.skipped, "dispatch recorded");
        check(call_nvmtl_vk_submit_wait_at(&command, "translator_readback"), "bounded compute completion");
        if (wrapped) {
            if (mode == "wrapped-bitcopy") require(!memcmp(buffers[0].map,uniform_bits,16), "wrapped load preserves signed zero, NaN payload, infinity and adjacent float bits");
            else for (unsigned i = 0; i < 4; ++i) {
                float expected = uniform_slots[0][i]+uniform_slots[run][i];
                if (((float *)buffers[0].map)[i] != expected)
                    fprintf(stderr,"wrapped run %u lane %u: got %.9g expected %.9g\n",run,i,((float *)buffers[0].map)[i],expected);
                require(((float *)buffers[0].map)[i] == expected,"wrapped uniform loads preserve slot stride, lane order and dynamic index");
            }
            for (unsigned i = 4; i < 4096/4; ++i) require(((uint32_t *)buffers[0].map)[i] == 0xa5a5a5a5,"wrapped output stays inside float4");
            require(!memcmp(buffers[1].map,input_snapshot.data(),4096),"wrapped input is read-only");
        } else if (mode == "forward0" || mode == "forward1") {
            require(((float *)buffers[3].map)[0] == (mode == "forward0" ? 17.5f : 4.25f), "forward address phi selects the actual source");
            require(((uint32_t *)buffers[3].map)[1] == 0xa5a5a5a5, "forward output bounds");
        } else if (mode == "alloca") {
            require(((uint32_t *)buffers[3].map)[1] == 7, "device address survives local-pointer stores and advancement");
            require(((uint32_t *)buffers[3].map)[0] == 0xa5a5a5a5, "local-pointer store offset");
        } else if (mode == "callback") {
            require(((uint32_t *)buffers[3].map)[0] == 7, "generic helper retains its indirect device store");
            require(((uint32_t *)buffers[3].map)[1] == 0xa5a5a5a5, "generic helper output bounds");
        } else if (mode == "table") {
            require(((uint32_t *)buffers[0].map)[0] == 7, "array table resolves Metal buffer 0");
            require(((uint32_t *)buffers[3].map)[0] == 0xa5a5a5a5, "array table does not treat buffer data as an address");
        }
        else if (mode == "phi") {
            require(((uint32_t *)buffers[0].map)[0] == 9, "direct array load resolves Metal buffer 0");
            require(((uint32_t *)buffers[3].map)[0] == 0xa5a5a5a5, "direct array load does not dereference buffer contents");
            require(((uint16_t *)buffers[2].map)[0] == 0x17a5, "guarded loop source reaches destination");
            require(((uint16_t *)buffers[2].map)[1] == 0xa5a5, "loop stays bounded");
        } else if (mode == "helper") {
            for (unsigned i = 0; i < 8; i++) {
                float expected = ((float *)buffers[0].map)[i] - ((float *)buffers[2].map)[i];
                if (((float *)buffers[1].map)[i] != expected) fprintf(stderr, "helper %u: got %.9g expected %.9g\n", i, ((float *)buffers[1].map)[i], expected);
                require(((float *)buffers[1].map)[i] == expected, "both helper reads contribute");
            }
            require(((uint32_t *)buffers[1].map)[8] == 0xa5a5a5a5, "helper output bounds");
        } else if (mode == "i64") require(((uint64_t *)buffers[0].map)[0] == 0x1122334455667788ull, "guarded 64-bit address load");
        else if (mode == "pack565") require(((uint16_t *)buffers[0].map)[0] == 0x041f, "RGB565 round-to-even");
        else if (mode == "pack565dyn") {
            const uint16_t *v = half_controls[run];
            uint16_t expected = unorm(half_value(v[0]), 31) | unorm(half_value(v[1]), 63) << 5 | unorm(half_value(v[2]), 31) << 11;
            uint16_t actual = ((uint16_t *)buffers[0].map)[0];
            if (actual != expected) fprintf(stderr, "pack565 run %u: got %04x expected %04x\n", run, actual, expected);
            require(actual == expected, "RGB565 numeric/clamp/NaN/rounding result");
        }
        else if (mode == "pack10") {
            const float *v = controls[run];
            uint32_t expected = unorm(v[0], 1023) | unorm(v[1], 1023) << 10 | unorm(v[2], 1023) << 20 | unorm(v[3], 3) << 30;
            uint32_t actual = ((uint32_t *)buffers[0].map)[0];
            if (actual != expected) fprintf(stderr, "pack10 run %u: got %08x expected %08x\n", run, actual, expected);
            require(actual == expected, "RGB10A2 numeric/clamp/NaN/rounding result");
        } else if (texture) {
            float result[4];
            if (mode == "storage40") check(call_nvmtl_vk_image_read(&queue, &images[1], result, 16), "storage image readback");
            else memcpy(result, buffers[0].map, 16);
            for (unsigned i = 0; i < 4; i++) {
                float expected = texture_a[i] + (mode == "texture127" ? texture_b[i] : 0.f);
                if (texture_array && mode != "texture-array0") expected = texture_b[i];
                if (sampler_array) expected = mode == "sampler-array0" ? texture_b[i] : (texture_a[i] + texture_b[i]) * 0.5f;
                if (result[i] != expected) fprintf(stderr, "channel %u: got %.9g expected %.9g\n", i, result[i], expected);
                require(result[i] == expected, "texture/sampler/storage binding and pixels");
            }
            if (texture_array || sampler_array) require(((uint32_t *)buffers[0].map)[4] == 0xa5a5a5a5, "array output bounds");
        } else require(mode == "control" && ((uint32_t *)buffers[0].map)[0] == 0x12345678, "control output");
    }
    for (auto &image : images) if (image.img) call_nvmtl_vk_image_destroy(&image);
    if (sampler.s) call_nvmtl_vk_sampler_destroy(&sampler);
    if (linear_sampler.s) call_nvmtl_vk_sampler_destroy(&linear_sampler);
    for (auto &b : buffers) call_nvmtl_vk_buffer_destroy(&b);
    call_nvmtl_vk_pipeline_destroy(&pipeline); call_nvmtl_vk_queue_destroy(&queue);
    printf("PASS %s (%u dispatches)\n", mode.c_str(), runs);
    return 0;
}
