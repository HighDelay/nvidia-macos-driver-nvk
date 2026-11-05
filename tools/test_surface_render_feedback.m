// Draw into a WindowServer-style IOSurface, order the writes with a render-target barrier, then sample the attachment
// from a later draw of the same pass. Core Animation reads its destination this way; a read that misses the earlier
// draws lets the transparent part of a layer overwrite what is under it.
// clang -fobjc-arc -O2 tools/test_surface_render_feedback.m -o surface_render_feedback \
//   -framework Foundation -framework Metal -framework IOSurface -framework IOKit
// surface_render_feedback /absolute/path/NVMTLDriver [rounds=4]
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOSurface/IOSurface.h>
#import <IOKit/IOKitLib.h>
#import <objc/message.h>
#include <dlfcn.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

enum { BARRIER_SCOPE, BARRIER_TEXTURE, BARRIER_RESOURCES, BARRIER_COUNT };
static const char *const barrier_name[BARRIER_COUNT] = { "scope", "texture", "resources" };

static void barrier(id<MTLRenderCommandEncoder> e, id<MTLTexture> t, int kind) {
    if (kind == BARRIER_SCOPE)
        [e memoryBarrierWithScope:MTLBarrierScopeRenderTargets afterStages:MTLRenderStageFragment beforeStages:MTLRenderStageFragment];
    else if (kind == BARRIER_TEXTURE) ((void (*)(id, SEL))objc_msgSend)(e, NSSelectorFromString(@"textureBarrier"));
    else { id<MTLResource> r[1] = { t }; [e memoryBarrierWithResources:r count:1 afterStages:MTLRenderStageFragment beforeStages:MTLRenderStageFragment]; }
}

static void read_pixel(const uint8_t *row, NSUInteger x, int half, float out[4]) {
    if (half) { const __fp16 *p = (const __fp16 *)row + 4 * x; for (int k = 0; k < 4; k++) out[k] = p[k]; }
    else { const uint8_t *p = row + 4 * x; out[0] = p[2] / 255.f; out[1] = p[1] / 255.f; out[2] = p[0] / 255.f; out[3] = p[3] / 255.f; }
}

// The base fill, a tile read that must keep it, and two reads that add to it: each must see the draw before it.
static unsigned check(const uint8_t *pixels, size_t row, NSUInteger w, NSUInteger h, int half, float *worst) {
    unsigned bad = 0;
    const NSUInteger tx = w / 4, ty = h / 4, tw = w / 2, th = h / 2;
    for (NSUInteger y = 0; y < h; y += 7) for (NSUInteger x = 0; x < w; x += 5) {
        const int tile = x >= tx && x < tx + tw && y >= ty && y < ty + th;
        const float expected[4] = { 0.5f + (tile ? 0.25f : 0), 0.5f, 0.25f, 1 };
        float c[4]; read_pixel(pixels + y * row, x, half, c);
        for (int k = 0; k < 4; k++) if (!isfinite(c[k]) || fabsf(c[k] - expected[k]) > 0.012f) {
            if (!bad) memcpy(worst, c, sizeof c);
            bad++; break;
        }
    }
    return bad;
}

int main(int argc, char **argv) { @autoreleasepool {
    alarm(120); setvbuf(stdout, NULL, _IONBF, 0);
    if (argc < 2) return 2;
    // Applications widen an IOSurface's usage onto a private image. WindowServer keeps the shared linear import, which
    // is the case under test; the name selects that path and takes no display or framebuffer.
    setprogname("WindowServer");
    if (!dlopen(argv[1], RTLD_NOW | RTLD_LOCAL)) { fprintf(stderr, "plugin: %s\n", dlerror()); return 2; }
    const int rounds = argc > 2 ? atoi(argv[2]) : 4;
    io_service_t accel = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("NVAccel"));
    if (!accel) return 2;
    id<MTLDevice> dev = ((id (*)(id, SEL, mach_port_t))objc_msgSend)([NSClassFromString(@"NVMTLDevice") alloc], NSSelectorFromString(@"initWithAcceleratorPort:"), accel);
    IOObjectRelease(accel);
    if (!dev) return 2;
    NSError *error = nil;
    id<MTLLibrary> lib = [dev newLibraryWithSource:@"#include <metal_stdlib>\nusing namespace metal;\n"
        "struct O { float4 p [[position]]; };\n"
        "vertex O v(uint i [[vertex_id]]) { float2 q=float2((i<<1)&2,i&2); O o; o.p=float4(q*2.0-1.0,0,1); return o; }\n"
        "fragment float4 solid(constant float4 &c [[buffer(0)]]) { return c; }\n"
        "fragment float4 over(O in [[stage_in]], texture2d<float, access::read> dst [[texture(0)]], constant float4 &s [[buffer(0)]]) {\n"
        "  return s + dst.read(uint2(in.p.xy)) * (1.0 - s.a); }\n" options:nil error:&error];
    if (!lib) { fprintf(stderr, "library: %s\n", error.description.UTF8String); return 2; }
    id<MTLCommandQueue> queue = [dev newCommandQueue];
    const struct { NSUInteger w, h; } sizes[] = { {936, 541}, {363, 475}, {1292, 892}, {1920, 1080}, {1024, 768} };
    const struct { MTLPixelFormat format; uint32_t fourcc; uint32_t bytes; const char *name; } formats[] = {
        { MTLPixelFormatBGRA8Unorm, 'BGRA', 4, "BGRA8" }, { MTLPixelFormatRGBA16Float, 'RGhA', 8, "RGBA16F" } };
    unsigned failed = 0, passed = 0;
    for (unsigned f = 0; f < sizeof formats / sizeof formats[0]; f++) {
        MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
        pd.vertexFunction = [lib newFunctionWithName:@"v"]; pd.colorAttachments[0].pixelFormat = formats[f].format;
        pd.fragmentFunction = [lib newFunctionWithName:@"solid"];
        id<MTLRenderPipelineState> solid = [dev newRenderPipelineStateWithDescriptor:pd error:&error];
        pd.fragmentFunction = [lib newFunctionWithName:@"over"];
        id<MTLRenderPipelineState> over = [dev newRenderPipelineStateWithDescriptor:pd error:&error];
        if (!solid || !over) { fprintf(stderr, "pipeline: %s\n", error.description.UTF8String); return 2; }
        for (unsigned s = 0; s < sizeof sizes / sizeof sizes[0]; s++) for (int kind = 0; kind < BARRIER_COUNT; kind++) {
            const NSUInteger w = sizes[s].w, h = sizes[s].h;
            IOSurfaceRef surface = IOSurfaceCreate((__bridge CFDictionaryRef)@{ (id)kIOSurfaceWidth: @(w), (id)kIOSurfaceHeight: @(h),
                (id)kIOSurfaceBytesPerElement: @(formats[f].bytes), (id)kIOSurfacePixelFormat: @(formats[f].fourcc) });
            if (!surface) return 2;
            MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:formats[f].format width:w height:h mipmapped:NO];
            td.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
            id<MTLTexture> target = [dev newTextureWithDescriptor:td iosurface:surface plane:0];
            MTLTextureDescriptor *rd = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:formats[f].format width:w height:h mipmapped:NO];
            rd.usage = MTLTextureUsageShaderRead; rd.storageMode = MTLStorageModeShared;
            id<MTLTexture> readback = [dev newTextureWithDescriptor:rd];
            if (!target || !readback) { CFRelease(surface); return 2; }
            const size_t row = w * formats[f].bytes;
            uint8_t *pixels = malloc(row * h);
            unsigned bad = 0; float worst[4] = { 0 };
            for (int r = 0; r < rounds; r++) @autoreleasepool {
                id<MTLCommandBuffer> cb = [queue commandBuffer];
                MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
                rp.colorAttachments[0].texture = target; rp.colorAttachments[0].loadAction = MTLLoadActionClear;
                rp.colorAttachments[0].storeAction = MTLStoreActionStore; rp.colorAttachments[0].clearColor = MTLClearColorMake(0, 0, 0, 0);
                id<MTLRenderCommandEncoder> e = [cb renderCommandEncoderWithDescriptor:rp];
                const float base[4] = { 0.5f, 0.5f, 0.25f, 1 }, none[4] = { 0, 0, 0, 0 }, add[4] = { 0.125f, 0, 0, 0 };
                [e setRenderPipelineState:solid]; [e setFragmentBytes:base length:sizeof base atIndex:0];
                [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
                barrier(e, target, kind);
                barrier(e, target, kind);   // nothing drawn since the last one: it must not lose the pass's contents
                [e setRenderPipelineState:over]; [e setFragmentTexture:target atIndex:0];
                [e setScissorRect:(MTLScissorRect){ w / 4, h / 4, w / 2, h / 2 }];
                [e setFragmentBytes:none length:sizeof none atIndex:0];
                [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
                for (int i = 0; i < 2; i++) {
                    barrier(e, target, kind);
                    [e setFragmentBytes:add length:sizeof add atIndex:0];
                    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
                }
                [e endEncoding];
                id<MTLBlitCommandEncoder> b = [cb blitCommandEncoder];
                [b copyFromTexture:target sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0, 0, 0) sourceSize:MTLSizeMake(w, h, 1)
                         toTexture:readback destinationSlice:0 destinationLevel:0 destinationOrigin:MTLOriginMake(0, 0, 0)];
                [b endEncoding];
                [cb commit]; [cb waitUntilCompleted];
                if (cb.status != MTLCommandBufferStatusCompleted) { bad++; continue; }
                [readback getBytes:pixels bytesPerRow:row fromRegion:MTLRegionMake2D(0, 0, w, h) mipmapLevel:0];
                bad += check(pixels, row, w, h, formats[f].bytes == 8, worst);
            }
            const size_t pitch = IOSurfaceGetBytesPerRow(surface);
            printf("%-7s %4lux%-4lu pitch %5zu %s %-9s %s", formats[f].name, (unsigned long)w, (unsigned long)h, pitch, pitch % 128 ? "shadow" : "direct",
                   barrier_name[kind], bad ? "FAIL" : "ok");
            if (bad) { printf(" %u bad samples, first %.3f %.3f %.3f %.3f", bad, worst[0], worst[1], worst[2], worst[3]); failed++; } else passed++;
            printf("\n");
            free(pixels); CFRelease(surface);
        }
    }
    printf("%u passed, %u failed\n", passed, failed);
    return failed != 0;
} }
