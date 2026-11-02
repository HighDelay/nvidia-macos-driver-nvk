// Pixel-verified high storage slots on an isolated plugin; never installs it.
// clang -fobjc-arc -O2 tools/bench_storage_binding_bounds.m -framework Foundation -framework Metal -framework IOKit -o bench_storage
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOKit/IOKitLib.h>
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
@interface NSObject (NVMTLStorageProbe)
- (instancetype)initWithAcceleratorPort:(mach_port_t)port;
@end
int main(int argc,char **argv) { @autoreleasepool {
    if(argc!=2 || !dlopen(argv[1],RTLD_NOW|RTLD_LOCAL)) { fprintf(stderr,"plugin load failed\n"); return 2; }
    io_service_t accel=IOServiceGetMatchingService(kIOMainPortDefault,IOServiceMatching("NVAccel"));
    id<MTLDevice> dev=[[NSClassFromString(@"NVMTLDevice") alloc] initWithAcceleratorPort:accel];
    if(accel) IOObjectRelease(accel); if(!dev) return 2;
    id<MTLCommandQueue> queue=[dev newCommandQueue]; if(!queue) return 2;
    for(unsigned slot=62;slot<128;slot=slot==62?63:slot==63?127:128) { @autoreleasepool {
        NSError *error=nil;
        NSString *source=[NSString stringWithFormat:@"#include <metal_stdlib>\nusing namespace metal;\nkernel void write_pixel(texture2d<uint,access::write> dst [[texture(%u)]], constant uint &value [[buffer(0)]]) { dst.write(uint4(value,0,0,0),uint2(0)); }\n",slot];
        id<MTLLibrary> lib=[dev newLibraryWithSource:source options:nil error:&error];
        if(!lib) { fprintf(stderr,"library %u: %s\n",slot,error.description.UTF8String); return 2; }
        id<MTLComputePipelineState> pipeline=[dev newComputePipelineStateWithFunction:[lib newFunctionWithName:@"write_pixel"] error:&error];
        if(!pipeline) { fprintf(stderr,"pipeline %u: %s\n",slot,error.description.UTF8String); return 2; }
        MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatR32Uint width:1 height:1 mipmapped:NO];
        td.usage=MTLTextureUsageShaderRead|MTLTextureUsageShaderWrite;td.storageMode=MTLStorageModeShared;
        id<MTLTexture> targets[2]={[dev newTextureWithDescriptor:td],[dev newTextureWithDescriptor:td]};
        for(int i=0;i<2;i++) { if(!targets[i]) return 2; uint32_t zero=0; [targets[i] replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:&zero bytesPerRow:4]; }
        id<MTLCommandBuffer> cb=[queue commandBuffer];
        id<MTLComputeCommandEncoder> enc=[cb computeCommandEncoder]; if(!enc) return 2;
        [enc setComputePipelineState:pipeline];
        for(uint32_t i=0;i<4;i++) {
            uint32_t value=17+i*13;
            [enc setTexture:targets[i&1] atIndex:slot]; [enc setBytes:&value length:4 atIndex:0];
            [enc dispatchThreadgroups:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(1,1,1)];
        }
        [enc endEncoding];[cb commit];[cb waitUntilCompleted];
        if(cb.status!=MTLCommandBufferStatusCompleted) return 1;
        for(int i=0;i<2;i++) {
            uint32_t pixel=0;
            [targets[i] getBytes:&pixel bytesPerRow:4 fromRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0];
            uint32_t expected=17+(i?3:2)*13;
            if(pixel!=expected) { fprintf(stderr,"slot %u target %d got %u expected %u\n",slot,i,pixel,expected); return 1; }
        }
        printf("PASS storage_slot=%u binding=%u alternating_targets=2 dispatches=4\n",slot,193+slot);fflush(stdout);
    }}
    return 0;
}}
