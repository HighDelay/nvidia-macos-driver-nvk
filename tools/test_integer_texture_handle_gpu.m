// Offscreen readback of the optimized WebGPU-style argument-buffer shader.
// clang -fobjc-arc -O2 tools/test_integer_texture_handle_gpu.m -o handle-readback \
//   -framework Foundation -framework Metal -framework IOKit
// handle-readback /absolute/path/NVMTLDriver tools/fixtures/integer_texture_handle.metal
// The selected driver and translator are loaded privately; nothing is installed.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOKit/IOKitLib.h>
#import <objc/message.h>
#include <dlfcn.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

static void require(BOOL ok, const char *message) {
    if (!ok) { fprintf(stderr, "FAIL: %s\n", message); exit(2); }
}

int main(int argc, char **argv) { @autoreleasepool {
    require(argc == 3, "driver and integer_texture_handle.metal paths required");
    setenv("NVMTL_PROCESS_ENV", "1", 1);
    if (!dlopen(argv[1], RTLD_NOW | RTLD_LOCAL)) {
        fprintf(stderr, "%s\n", dlerror()); return 2;
    }
    io_service_t accelerator = IOServiceGetMatchingService(
        kIOMainPortDefault, IOServiceMatching("NVAccel"));
    require(accelerator != MACH_PORT_NULL, "find accelerator");
    id<MTLDevice> device = ((id (*)(id, SEL, mach_port_t))objc_msgSend)(
        [NSClassFromString(@"NVMTLDevice") alloc],
        NSSelectorFromString(@"initWithAcceleratorPort:"), accelerator);
    IOObjectRelease(accelerator);
    require(device != nil, "create device");
    NSError *error = nil;
    NSString *source = [NSString stringWithContentsOfFile:@(argv[2])
        encoding:NSUTF8StringEncoding error:&error];
    require(source != nil, "read shader source");
    source = [source stringByAppendingString:
        @"\nvertex VOut vs(uint v [[vertex_id]]) {\n"
         "  float2 p=float2((v<<1)&2,v&2); VOut o;\n"
         "  o.pos=float4(p*2.0f-1.0f,0,1);\n"
         "  o.col0=float4(.2f,.4f,.6f,.5f); o.col1=float4(0);\n"
         "  o.uv0=float4(.5f,.5f,1,1); o.uv1=o.uv0;\n"
         "  o.uv2=o.uv0; o.uv3=o.uv0; o.fog=.25f; return o;\n}\n"];
    id<MTLLibrary> library = [device newLibraryWithSource:source options:nil error:&error];
    if (!library) fprintf(stderr, "%s\n", error.description.UTF8String);
    require(library != nil, "compile library");
    id<MTLFunction> fragment = [library newFunctionWithName:@"fs"];
    require(fragment != nil, "translate fragment with integer-copied texture handle");
    MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
    pd.vertexFunction = [library newFunctionWithName:@"vs"];
    pd.fragmentFunction = fragment;
    pd.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA32Float;
    id<MTLRenderPipelineState> pipeline = [device newRenderPipelineStateWithDescriptor:pd error:&error];
    if (!pipeline) fprintf(stderr, "%s\n", error.description.UTF8String);
    require(pipeline != nil, "create pipeline");

    MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:
        MTLPixelFormatRGBA32Float width:4 height:4 mipmapped:NO];
    td.storageMode = MTLStorageModeShared;
    td.usage = MTLTextureUsageShaderRead;
    id<MTLTexture> textures[2] = {
        [device newTextureWithDescriptor:td], [device newTextureWithDescriptor:td]
    };
    const float colors[2][4] = {{.125f,.25f,.5f,.8f},{.75f,.5f,.25f,.25f}};
    for (unsigned t=0; t<2; ++t) {
        require(textures[t] != nil, "create sampled texture");
        float pixels[4*4*4];
        for (unsigned i=0; i<16; ++i) memcpy(pixels+4*i, colors[t], 16);
        [textures[t] replaceRegion:MTLRegionMake2D(0,0,4,4) mipmapLevel:0
            withBytes:pixels bytesPerRow:4*16];
    }
    td.storageMode = MTLStorageModePrivate;
    td.usage = MTLTextureUsageRenderTarget;
    id<MTLTexture> target = [device newTextureWithDescriptor:td];
    id<MTLBuffer> readback = [device newBufferWithLength:256*4 options:MTLResourceStorageModeShared];
    id<MTLBuffer> uniforms = [device newBufferWithLength:560 options:MTLResourceStorageModeShared];
    require(target && readback && uniforms, "create render/readback resources");
    memset(uniforms.contents, 0, 560);
    MTLSamplerDescriptor *sd = [MTLSamplerDescriptor new];
    sd.minFilter = sd.magFilter = MTLSamplerMinMagFilterNearest;
    id<MTLSamplerState> sampler = [device newSamplerStateWithDescriptor:sd];
    require(sampler != nil, "create sampler");
    id<MTLArgumentEncoder> enc0 = [fragment newArgumentEncoderWithBufferIndex:0];
    id<MTLArgumentEncoder> enc1 = [fragment newArgumentEncoderWithBufferIndex:1];
    require(enc0 && enc1, "reflect both argument buffers");
    id<MTLBuffer> group0 = [device newBufferWithLength:enc0.encodedLength options:MTLResourceStorageModeShared];
    id<MTLBuffer> group1 = [device newBufferWithLength:enc1.encodedLength options:MTLResourceStorageModeShared];
    require(group0 && group1, "allocate argument buffers");
    [enc0 setArgumentBuffer:group0 offset:0];
    [enc0 setBuffer:uniforms offset:0 atIndex:2];
    [enc1 setArgumentBuffer:group1 offset:0];
    [enc1 setSamplerState:sampler atIndex:4];
    [enc1 setSamplerState:sampler atIndex:5];
    id<MTLCommandQueue> queue = [device newCommandQueue];
    require(queue != nil, "create command queue");
    unsigned failures = 0;
    for (unsigned frame=0; frame<4; ++frame) {
        unsigned swap = frame % 2;
        const float fog[4] = {.1f,.2f,.3f,frame < 2 ? 0.f : .5f};
        memcpy((char *)uniforms.contents+544, fog, sizeof fog);
        [enc1 setTexture:textures[swap] atIndex:0];
        [enc1 setTexture:textures[1-swap] atIndex:1];
        id<MTLCommandBuffer> cb = [queue commandBuffer];
        MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture = target;
        pass.colorAttachments[0].loadAction = MTLLoadActionClear;
        pass.colorAttachments[0].storeAction = MTLStoreActionStore;
        id<MTLRenderCommandEncoder> render = [cb renderCommandEncoderWithDescriptor:pass];
        [render setRenderPipelineState:pipeline];
        [render setFragmentBuffer:group0 offset:0 atIndex:0];
        [render setFragmentBuffer:group1 offset:0 atIndex:1];
        [render useResource:uniforms usage:MTLResourceUsageRead stages:MTLRenderStageFragment];
        for (unsigned t=0; t<2; ++t)
            [render useResource:textures[t] usage:MTLResourceUsageRead stages:MTLRenderStageFragment];
        [render drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        [render endEncoding];
        id<MTLBlitCommandEncoder> blit = [cb blitCommandEncoder];
        [blit copyFromTexture:target sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0)
            sourceSize:MTLSizeMake(4,4,1) toBuffer:readback destinationOffset:0
            destinationBytesPerRow:256 destinationBytesPerImage:256*4];
        [blit endEncoding]; [cb commit]; [cb waitUntilCompleted];
        require(cb.status == MTLCommandBufferStatusCompleted, "complete render/readback");
        float expected[4] = {0,0,0,1};
        const float tint[4] = {.2f,.4f,.6f,.5f};
        for (unsigned c=0; c<3; ++c)
            expected[c] = colors[1-swap][c]*colors[swap][3]*tint[3] + 2*colors[swap][c]*tint[c];
        const float factor = 1.f-.75f*fog[3];
        for (unsigned c=0; c<3; ++c)
            expected[c] = fog[c]*(1.f-factor)+expected[c]*factor;
        unsigned bad = 0;
        for (unsigned y=0; y<4; ++y) {
            const float *row = (const float *)((const char *)readback.contents+256*y);
            for (unsigned x=0; x<4; ++x) for (unsigned c=0; c<4; ++c)
                bad += !isfinite(row[4*x+c]) || fabsf(row[4*x+c]-expected[c]) > 0.00001f;
        }
        const float *first = readback.contents;
        printf("frame=%u swap=%u pixel=(%.6f,%.6f,%.6f,%.6f) expected=(%.6f,%.6f,%.6f,%.6f) errors=%u\n",
            frame, swap, first[0],first[1],first[2],first[3],expected[0],expected[1],expected[2],expected[3],bad);
        failures += bad;
    }
    return failures ? 1 : 0;
} }
