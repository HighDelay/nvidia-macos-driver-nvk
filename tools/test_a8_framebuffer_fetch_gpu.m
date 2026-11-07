// Verify A8 framebuffer reads for plain textures and R8-backed A8 views.
// clang -fobjc-arc -O2 tools/test_a8_framebuffer_fetch_gpu.m -o a8-fetch \
//   -framework Foundation -framework Metal -framework IOKit
// a8-fetch /absolute/path/NVMTLDriver
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOKit/IOKitLib.h>
#import <objc/message.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
static void require(BOOL ok,const char *message){if(!ok){fprintf(stderr,"FAIL: %s\n",message);exit(2);}}
int main(int argc,char **argv){@autoreleasepool{
    require(argc==2,"driver path");setprogname("WindowServer");
    setenv("NVMTL_PROCESS_ENV","1",1);setenv("NVMTL_FBFETCH_WS","1",1);
    require(dlopen(argv[1],RTLD_NOW|RTLD_LOCAL)!=NULL,"load driver");
    io_service_t accelerator=IOServiceGetMatchingService(kIOMainPortDefault,IOServiceMatching("NVAccel"));
    id<MTLDevice> device=((id(*)(id,SEL,mach_port_t))objc_msgSend)([NSClassFromString(@"NVMTLDevice") alloc],NSSelectorFromString(@"initWithAcceleratorPort:"),accelerator);IOObjectRelease(accelerator);require(device!=nil,"device");
    NSError *error=nil;
    id<MTLLibrary> library=[device newLibraryWithSource:@"#include <metal_stdlib>\nusing namespace metal;\nvertex float4 v(uint i [[vertex_id]]){float2 p=float2((i<<1)&2,i&2);return float4(p*2.-1.,0,1);}\nfragment float4 f(float4 previous [[color(0)]]){return float4(0,0,0,previous.a+0.2f);}\n" options:nil error:&error];
    if(!library)fprintf(stderr,"%s\n",error.description.UTF8String);require(library!=nil,"library");
    id<MTLCommandQueue> queue=[device newCommandQueue];unsigned failures=0;
    for(unsigned view=0;view<2;view++){@autoreleasepool{
        MTLTextureDescriptor *descriptor=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:view?MTLPixelFormatR8Unorm:MTLPixelFormatA8Unorm width:38 height:38 mipmapped:NO];
        descriptor.storageMode=MTLStorageModePrivate;descriptor.usage=MTLTextureUsageShaderRead|MTLTextureUsageShaderWrite|MTLTextureUsageRenderTarget|MTLTextureUsagePixelFormatView;
        id<MTLTexture> backing=[device newTextureWithDescriptor:descriptor];id<MTLTexture> texture=view?[backing newTextureViewWithPixelFormat:MTLPixelFormatA8Unorm]:backing;require(texture!=nil,"alpha texture");
        MTLRenderPipelineDescriptor *pipelineDescriptor=[MTLRenderPipelineDescriptor new];pipelineDescriptor.vertexFunction=[library newFunctionWithName:@"v"];pipelineDescriptor.fragmentFunction=[library newFunctionWithName:@"f"];pipelineDescriptor.colorAttachments[0].pixelFormat=MTLPixelFormatA8Unorm;pipelineDescriptor.colorAttachments[0].writeMask=MTLColorWriteMaskAlpha;
        id<MTLRenderPipelineState> pipeline=[device newRenderPipelineStateWithDescriptor:pipelineDescriptor error:&error];if(!pipeline)fprintf(stderr,"%s\n",error.description.UTF8String);require(pipeline!=nil,"alpha pipeline");
        id<MTLBuffer> output=[device newBufferWithLength:256*38 options:MTLResourceStorageModeShared];
        for(unsigned frame=0;frame<4;frame++){
            id<MTLCommandBuffer> command=[queue commandBuffer];MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=texture;pass.colorAttachments[0].loadAction=frame?MTLLoadActionLoad:MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;pass.colorAttachments[0].clearColor=MTLClearColorMake(0,0,0,.1);
            id<MTLRenderCommandEncoder> encoder=[command renderCommandEncoderWithDescriptor:pass];[encoder setRenderPipelineState:pipeline];[encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[encoder endEncoding];
            id<MTLBlitCommandEncoder> blit=[command blitCommandEncoder];[blit copyFromTexture:texture sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(38,38,1) toBuffer:output destinationOffset:0 destinationBytesPerRow:256 destinationBytesPerImage:256*38];[blit endEncoding];[command commit];[command waitUntilCompleted];require(command.status==MTLCommandBufferStatusCompleted,"complete");
            unsigned expected=77+51*frame,bad=0;unsigned char *pixels=output.contents;
            for(unsigned y=0;y<38;y++)for(unsigned x=0;x<38;x++){int delta=(int)pixels[y*256+x]-(int)expected;if(delta < -1 || delta > 1)bad++;}
            printf("view=%u frame=%u first=%u expected=%u errors=%u\n",view,frame,pixels[0],expected,bad);failures+=bad;
        }
    }}return failures?1:0;
}}
