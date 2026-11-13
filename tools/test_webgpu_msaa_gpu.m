// Private GPU regression for WebGPU's argument-buffer MSAA resolve and query-only shaders.
// clang -fobjc-arc -O2 tools/test_webgpu_msaa_gpu.m -o msaa-readback \
//   -framework Foundation -framework Metal -framework IOKit
// msaa-readback /absolute/path/NVMTLDriver tools/fixtures/webgpu_msaa_resolve.metal
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
static id<MTLRenderPipelineState> pipeline(id<MTLDevice> device, id<MTLLibrary> library,
                                         NSString *fragment, NSUInteger samples) {
    MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
    pd.vertexFunction = [library newFunctionWithName:@"msaa_fullscreen"];
    pd.fragmentFunction = [library newFunctionWithName:fragment];
    pd.rasterSampleCount = samples;
    pd.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA32Float;
    NSError *error = nil;
    id<MTLRenderPipelineState> result = [device newRenderPipelineStateWithDescriptor:pd error:&error];
    if (!result) fprintf(stderr, "%s\n", error.description.UTF8String);
    require(result != nil, "create render pipeline");
    return result;
}
int main(int argc, char **argv) { @autoreleasepool {
    require(argc == 3, "driver and shader paths required");
    setenv("NVMTL_PROCESS_ENV", "1", 1);
    require(dlopen(argv[1], RTLD_NOW | RTLD_LOCAL) != NULL, "load private driver");
    io_service_t accel = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("NVAccel"));
    require(accel != MACH_PORT_NULL, "find accelerator");
    id<MTLDevice> device = ((id (*)(id, SEL, mach_port_t))objc_msgSend)(
        [NSClassFromString(@"NVMTLDevice") alloc], NSSelectorFromString(@"initWithAcceleratorPort:"), accel);
    IOObjectRelease(accel);
    require(device != nil, "create private device");
    NSError *error = nil;
    NSString *source = [NSString stringWithContentsOfFile:@(argv[2]) encoding:NSUTF8StringEncoding error:&error];
    id<MTLLibrary> library = [device newLibraryWithSource:source options:nil error:&error];
    if (!library) fprintf(stderr, "%s\n", error.description.UTF8String);
    require(library != nil, "compile fixture");
    MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:
        MTLPixelFormatRGBA32Float width:4 height:4 mipmapped:NO];
    td.storageMode = MTLStorageModePrivate;
    td.textureType = MTLTextureType2DMultisample;
    td.sampleCount = 4;
    td.usage = MTLTextureUsageShaderRead | MTLTextureUsageRenderTarget;
    id<MTLTexture> ms = [device newTextureWithDescriptor:td];
    td.textureType = MTLTextureType2D;
    td.sampleCount = 1;
    td.usage = MTLTextureUsageRenderTarget;
    id<MTLTexture> target = [device newTextureWithDescriptor:td];
    MTLTextureDescriptor *md = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:
        MTLPixelFormatRGBA8Unorm width:16 height:8 mipmapped:YES];
    md.storageMode = MTLStorageModePrivate;
    md.usage = MTLTextureUsageShaderRead;
    id<MTLTexture> mip = [device newTextureWithDescriptor:md];
    id<MTLBuffer> read = [device newBufferWithLength:1024 options:MTLResourceStorageModeShared];
    id<MTLCommandQueue> queue = [device newCommandQueue];
    require(ms && target && mip && read && queue, "allocate resources");
    id<MTLRenderPipelineState> fill = pipeline(device, library, @"msaa_fill", 4);
    NSString *names[] = {@"msaa_resolve", @"msaa_query", @"mip_query"};
    const float expected[][4] = {{.3125f,.4375f,.5625f,1},{4,4,4,1},{4,2,5,1}};
    unsigned failures = 0;
    for (unsigned mode=0; mode<2; mode++) for (unsigned test=0; test<3; test++) {
        id<MTLFunction> fn = [library newFunctionWithName:names[test]];
        require(fn != nil, "translate argument-buffer fragment");
        id<MTLRenderPipelineState> ps = pipeline(device, library, names[test], 1);
        id<MTLArgumentEncoder> ae;
        if (mode == 0) ae = [fn newArgumentEncoderWithBufferIndex:0];
        else {
            MTLArgumentDescriptor *arg = [MTLArgumentDescriptor argumentDescriptor];
            arg.index = 0; arg.dataType = MTLDataTypeTexture; arg.access = MTLArgumentAccessReadOnly;
            arg.textureType = test == 2 ? MTLTextureType2D : MTLTextureType2DMultisample;
            ae = [device newArgumentEncoderWithArguments:@[arg]];
        }
        require(ae != nil, "create reflected or descriptor-built argument encoder");
        id<MTLBuffer> group = [device newBufferWithLength:ae.encodedLength options:MTLResourceStorageModeShared];
        [ae setArgumentBuffer:group offset:0];
        id<MTLTexture> input = test == 2 ? mip : ms;
        [ae setTexture:input atIndex:0];
        id<MTLCommandBuffer> cb = [queue commandBuffer];
        MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture = ms;
        pass.colorAttachments[0].loadAction = MTLLoadActionClear;
        pass.colorAttachments[0].storeAction = MTLStoreActionStore;
        id<MTLRenderCommandEncoder> re = [cb renderCommandEncoderWithDescriptor:pass];
        [re setRenderPipelineState:fill];
        [re drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        [re endEncoding];
        pass.colorAttachments[0].texture = target;
        re = [cb renderCommandEncoderWithDescriptor:pass];
        [re setRenderPipelineState:ps];
        [re setFragmentBuffer:group offset:0 atIndex:0];
        [re useResource:input usage:MTLResourceUsageRead stages:MTLRenderStageFragment];
        [re drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        [re endEncoding];
        id<MTLBlitCommandEncoder> blit = [cb blitCommandEncoder];
        [blit copyFromTexture:target sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0)
            sourceSize:MTLSizeMake(4,4,1) toBuffer:read destinationOffset:0
            destinationBytesPerRow:256 destinationBytesPerImage:1024];
        [blit endEncoding]; [cb commit]; [cb waitUntilCompleted];
        require(cb.status == MTLCommandBufferStatusCompleted, "complete readback");
        unsigned bad = 0;
        for (unsigned y=0; y<4; y++) {
            const float *row = (const float *)((const char *)read.contents+256*y);
            for (unsigned x=0; x<4; x++) for (unsigned c=0; c<4; c++)
                bad += !isfinite(row[4*x+c]) || fabsf(row[4*x+c]-expected[test][c]) > 1e-5f;
        }
        const float *pixel = read.contents;
        printf("%s encoder=%s pixel=(%.6f,%.6f,%.6f,%.6f) errors=%u\n",
            names[test].UTF8String, mode ? "descriptor" : "reflected", pixel[0],pixel[1],pixel[2],pixel[3],bad);
        failures += bad;
    }
    return failures ? 1 : 0;
} }
