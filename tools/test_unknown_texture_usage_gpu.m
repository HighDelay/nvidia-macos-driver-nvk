// Verify shader writes through Unknown-usage roots and mip views against
// explicit ShaderWrite textures. Build with Foundation, Metal and IOKit.
// test-unknown-usage /absolute/path/NVMTLDriver
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOKit/IOKitLib.h>
#import <objc/message.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
static void require(BOOL ok,const char *message) {
    if(!ok){fprintf(stderr,"FAIL: %s\n",message);exit(2);}
}
int main(int argc,char **argv){@autoreleasepool{
    require(argc==2,"driver path");setprogname("WindowServer");setenv("NVMTL_PROCESS_ENV","1",1);
    require(dlopen(argv[1],RTLD_NOW|RTLD_LOCAL)!=NULL,"load driver");
    io_service_t accelerator=IOServiceGetMatchingService(kIOMainPortDefault,IOServiceMatching("NVAccel"));
    id<MTLDevice> device=((id(*)(id,SEL,mach_port_t))objc_msgSend)([NSClassFromString(@"NVMTLDevice") alloc],NSSelectorFromString(@"initWithAcceleratorPort:"),accelerator);
    IOObjectRelease(accelerator);require(device!=nil,"device");
    NSError *error=nil;
    id<MTLLibrary> library=[device newLibraryWithSource:@"#include <metal_stdlib>\nusing namespace metal;\nkernel void fill(texture2d<half,access::write> dst [[texture(0)]],uint2 p [[thread_position_in_grid]]){if(p.x<dst.get_width()&&p.y<dst.get_height())dst.write(half4(.25h,.5h,.75h,1.h),p);}\n" options:nil error:&error];
    if(!library)fprintf(stderr,"%s\n",error.description.UTF8String);require(library!=nil,"library");
    id<MTLComputePipelineState> pipeline=[device newComputePipelineStateWithFunction:[library newFunctionWithName:@"fill"] error:&error];require(pipeline!=nil,"pipeline");
    id<MTLCommandQueue> queue=[device newCommandQueue];unsigned failures=0;
    for(unsigned known=0;known<2;known++)for(unsigned mip=0;mip<2;mip++)for(unsigned view=0;view<2;view++){@autoreleasepool{
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA16Float width:32 height:32 mipmapped:mip];
        d.storageMode=MTLStorageModePrivate;
        d.usage=known?(MTLTextureUsageShaderRead|MTLTextureUsageShaderWrite|MTLTextureUsagePixelFormatView):MTLTextureUsageUnknown;
        id<MTLTexture> base=[device newTextureWithDescriptor:d];
        id<MTLTexture> target=view?[base newTextureViewWithPixelFormat:MTLPixelFormatRGBA16Float textureType:MTLTextureType2D levels:NSMakeRange(mip?2:0,1) slices:NSMakeRange(0,1)]:base;
        require(target!=nil,"texture/view");
        id<MTLBuffer> output=[device newBufferWithLength:256*32 options:MTLResourceStorageModeShared];require(output!=nil,"readback buffer");
        id<MTLCommandBuffer> cb=[queue commandBuffer];id<MTLComputeCommandEncoder> encoder=[cb computeCommandEncoder];
        [encoder setComputePipelineState:pipeline];[encoder setTexture:target atIndex:0];
        [encoder dispatchThreads:MTLSizeMake(target.width,target.height,1) threadsPerThreadgroup:MTLSizeMake(8,8,1)];[encoder endEncoding];
        id<MTLBlitCommandEncoder> blit=[cb blitCommandEncoder];
        [blit copyFromTexture:target sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(target.width,target.height,1) toBuffer:output destinationOffset:0 destinationBytesPerRow:256 destinationBytesPerImage:256*target.height];
        [blit endEncoding];[cb commit];[cb waitUntilCompleted];require(cb.status==MTLCommandBufferStatusCompleted,"complete");
        const float expected[]={.25f,.5f,.75f,1.f};unsigned bad=0;
        for(NSUInteger y=0;y<target.height;y++){
            const _Float16 *row=(const _Float16 *)((const uint8_t *)output.contents+y*256);
            for(NSUInteger x=0;x<target.width;x++)for(unsigned c=0;c<4;c++)bad+=(float)row[x*4+c]!=expected[c];
        }
        printf("known=%u mip=%u view=%u errors=%u\n",known,mip,view,bad);failures+=bad;
    }}
    return failures?1:0;
}}
