// GPU readback for chained buffer/volume copies, padded planes and nonzero mip/Z offsets.
// Usage: test_volume_blit /absolute/path/NVMTLDriver (never installs the driver).
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOKit/IOKitLib.h>
#import <objc/message.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>

static void require(BOOL ok, const char *what) {
    if (!ok) { fprintf(stderr, "FAIL: %s\n", what); exit(1); }
}
int main(int argc, char **argv) { @autoreleasepool {
    require(argc == 2, "plugin path required");
    require(dlopen(argv[1], RTLD_NOW | RTLD_LOCAL) != NULL, "load plugin");
    io_service_t service = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("NVAccel"));
    require(service != MACH_PORT_NULL, "find accelerator");
    id<MTLDevice> device = ((id (*)(id, SEL, mach_port_t))objc_msgSend)(
        [NSClassFromString(@"NVMTLDevice") alloc], NSSelectorFromString(@"initWithAcceleratorPort:"), service);
    IOObjectRelease(service);
    require(device != nil, "create device");
    id<MTLCommandQueue> queue = [device newCommandQueue];
    MTLTextureDescriptor *desc = [MTLTextureDescriptor new];
    desc.textureType = MTLTextureType3D; desc.pixelFormat = MTLPixelFormatRGBA8Unorm;
    desc.width = 16; desc.height = 16; desc.depth = 8; desc.mipmapLevelCount = 2;
    desc.usage = MTLTextureUsageShaderRead; desc.storageMode = MTLStorageModePrivate;
    id<MTLTexture> texture = [device newTextureWithDescriptor:desc];
    require(texture != nil, "create volume");
    const NSUInteger row = 256, plane = row * 8, offset = 256, bytes = offset + plane * 3;
    id<MTLBuffer> input = [device newBufferWithLength:bytes options:MTLResourceStorageModeShared];
    id<MTLBuffer> output = [device newBufferWithLength:bytes options:MTLResourceStorageModeShared];
    require(input && output, "create padded buffers");
    for (unsigned frame = 0; frame < 8; frame++) { @autoreleasepool {
        const NSUInteger level = frame % 2, width = 5, height = 3, depth = 2;
        unsigned char *source = input.contents;
        memset(source, 0x5a, bytes);
        for (NSUInteger z = 0; z < depth; z++) for (NSUInteger y = 0; y < height; y++)
            for (NSUInteger x = 0; x < width * 4; x++)
                source[offset + z * plane + y * row + x] = (unsigned char)(frame * 19 + z * 73 + y * 11 + x);
        id<MTLCommandBuffer> command = [queue commandBuffer];
        id<MTLBlitCommandEncoder> blit = [command blitCommandEncoder];
        [blit fillBuffer:output range:NSMakeRange(0, bytes) value:0xa5];
        [blit copyFromBuffer:input sourceOffset:offset sourceBytesPerRow:row sourceBytesPerImage:plane
                 sourceSize:MTLSizeMake(width, height, depth) toTexture:texture destinationSlice:0
           destinationLevel:level destinationOrigin:MTLOriginMake(1, 2, 1)];
        [blit copyFromTexture:texture sourceSlice:0 sourceLevel:level sourceOrigin:MTLOriginMake(1, 2, 1)
                  sourceSize:MTLSizeMake(width, height, depth) toBuffer:output destinationOffset:offset
      destinationBytesPerRow:row destinationBytesPerImage:plane];
        [blit endEncoding]; [command commit]; [command waitUntilCompleted];
        require(command.status == MTLCommandBufferStatusCompleted, "complete chained volume copies");
        const unsigned char *result = output.contents;
        for (NSUInteger i = 0; i < bytes; i++) {
            BOOL copied = i >= offset && (i - offset) / plane < depth &&
                ((i - offset) % plane) / row < height && (i - offset) % row < width * 4;
            require(result[i] == (copied ? source[i] : 0xa5), "volume pixels and untouched buffer padding");
        }
    }}
    puts("PASS: 8 chained volume blits, padded rows/planes, mip 0/1, nonzero XYZ and buffer offsets");
    return 0;
}}
