// Exercise framebuffer fetch across mixed linear/tiled targets and independent IOSurface readers.
// clang -fobjc-arc -O2 -I/path/to/Vulkan-Headers/include tools/test_surface_render_formats.m -o surface_render_formats \
//   -framework Foundation -framework Metal -framework IOSurface -framework IOKit
// surface_render_formats /absolute/path/NVMTLDriver
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOSurface/IOSurface.h>
#import <IOKit/IOKitLib.h>
#import <objc/message.h>
#import <mach-o/dyld.h>
#include <dlfcn.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <vulkan/vulkan_core.h>
#include "../plugin/nvmtl_vk.h"

@interface NSObject (SurfaceRenderProbe)
- (nvk_image *)nvi;
@end

static float component(const void *pixels, NSUInteger n, NSUInteger channel, MTLPixelFormat format) {
    if (format == MTLPixelFormatRGBA16Float) return ((const __fp16 *)pixels)[4*n+channel];
    if (format == MTLPixelFormatRGBA16Unorm) return ((const uint16_t *)pixels)[4*n+channel]/65535.0f;
    if (format == MTLPixelFormatBGRA8Unorm && channel != 1 && channel != 3) channel=2-channel;
    return ((const uint8_t *)pixels)[4*n+channel]/255.0f;
}
static unsigned verify(const void *pixels, size_t row, NSUInteger width, NSUInteger height,
                       MTLPixelFormat format, unsigned frame) {
    unsigned bad=0;
    for(NSUInteger y=0;y<height;y++) for(NSUInteger x=0;x<width;x++) {
        const void *p=(const uint8_t *)pixels+y*row;
        float expected[]={((x+frame)&3)*0.25f,((y+frame)&3)*0.25f,frame*0.25f,1.0f}; if(x>=width/2) { expected[0]=expected[1]=expected[2]=expected[3]=0; }
        for(NSUInteger k=0;k<4;k++) {
            float value=component(p,x,k,format);
            bad+=!isfinite(value)||fabsf(value-expected[k])>0.006f;
        }
    }
    return bad;
}
int main(int argc,char **argv) { @autoreleasepool {
    alarm(90); setvbuf(stdout,NULL,_IONBF,0);
    if(argc!=2||!dlopen(argv[1],RTLD_NOW|RTLD_LOCAL)) { fprintf(stderr,"plugin: %s\n",dlerror()); return 2; }
    io_service_t accel=IOServiceGetMatchingService(kIOMainPortDefault,IOServiceMatching("NVAccel"));
    if(!accel)return 2;
    id<MTLDevice> dev=((id(*)(id,SEL,mach_port_t))objc_msgSend)([NSClassFromString(@"NVMTLDevice") alloc],NSSelectorFromString(@"initWithAcceleratorPort:"),accel);
    IOObjectRelease(accel); if(!dev)return 2;
    NSError *error=nil;
    id<MTLLibrary> lib=[dev newLibraryWithSource:@"#include <metal_stdlib>\nusing namespace metal;\n"
        "vertex float4 v(uint i [[vertex_id]]) { float2 p=float2((i<<1)&2,i&2); return float4(p*2.0-1.0,0,1); }\n"
        "fragment float4 f(float4 pos [[position]], constant uint &frame [[buffer(0)]], float4 previous [[color(0)]]) { uint2 p=uint2(pos.xy);"
        " return previous+float4(float((p.x+frame)&3)*0.25,float((p.y+frame)&3)*0.25,float(frame)*0.25,1); }\n"
        options:nil error:&error];
    if(!lib) { fprintf(stderr,"library: %s\n",error.description.UTF8String);return 2; }
    id<MTLCommandQueue> queue=[dev newCommandQueue];
    const MTLPixelFormat formats[]={MTLPixelFormatBGRA8Unorm,MTLPixelFormatRGBA8Unorm,MTLPixelFormatRGBA16Float,MTLPixelFormatRGBA16Unorm};
    const uint32_t surfaceFormats[]={0x42475241,0x52474241,0x52476841,0x6c363472};
    const MTLTextureUsage usages[]={MTLTextureUsageUnknown,MTLTextureUsageShaderRead,MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget};
    unsigned failures=0,cases=0;
    for(unsigned fi=0;fi<4;fi++) for(unsigned ui=0;ui<3;ui++)
    for(unsigned managed=0;managed<2;managed++) for(unsigned mixed=0;mixed<2;mixed++)
    for(unsigned padded=0;padded<2;padded++) { @autoreleasepool {
        const NSUInteger width=padded?52:128,height=padded?37:64;
        const size_t bpp=fi<2?4:8,row=(width*bpp+63)&~63;
        IOSurfaceRef surface=IOSurfaceCreate((__bridge CFDictionaryRef)@{(id)kIOSurfaceWidth:@(width),(id)kIOSurfaceHeight:@(height),
            (id)kIOSurfaceBytesPerElement:@(bpp),(id)kIOSurfaceBytesPerRow:@(row),(id)kIOSurfaceAllocSize:@(row*height),
            (id)kIOSurfacePixelFormat:@(surfaceFormats[fi])});
        if(!surface)return 2;
        IOSurfaceLock(surface,0,NULL); memset(IOSurfaceGetBaseAddress(surface),0x55,row*height); IOSurfaceUnlock(surface,0,NULL);
        MTLTextureDescriptor *desc=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:formats[fi] width:width height:height mipmapped:NO];
        desc.storageMode=managed?MTLStorageModeManaged:MTLStorageModeShared; desc.usage=MTLTextureUsageShaderRead;
        // The first wrapper can be read-only. A later writer must share a
        // renderable backing with it, including an optimal-tiled fallback.
        id<MTLTexture> first=[dev newTextureWithDescriptor:desc iosurface:surface plane:0];
        desc.usage=usages[ui];
        id<MTLTexture> target=[dev newTextureWithDescriptor:desc iosurface:surface plane:0];
        MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];
        pd.vertexFunction=[lib newFunctionWithName:@"v"]; pd.fragmentFunction=[lib newFunctionWithName:@"f"]; pd.colorAttachments[0].pixelFormat=formats[fi];
        MTLTextureDescriptor *auxDesc=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatR16Float width:width height:height mipmapped:NO];
        auxDesc.storageMode=MTLStorageModePrivate; auxDesc.usage=MTLTextureUsageRenderTarget;
        id<MTLTexture> aux=[dev newTextureWithDescriptor:auxDesc];
        pd.colorAttachments[1].pixelFormat=mixed?MTLPixelFormatR16Float:MTLPixelFormatInvalid; pd.colorAttachments[1].writeMask=MTLColorWriteMaskNone;
        id<MTLRenderPipelineState> pipe=[dev newRenderPipelineStateWithDescriptor:pd error:&error];
        if(!first||!target||!pipe) { fprintf(stderr,"setup: %s\n",error.description.UTF8String);return 2; }
        uint8_t *read=malloc(row*height); unsigned bad=0;
        if(!read)return 2;
        // Check the backing contract as well as pixels: some drivers execute
        // simple draws even without the required Vulkan image usage, while
        // RenderBox's layered rendering also needs matching feedback views.
        const uint32_t backingUsage=[(NSObject *)target nvi]->usage;
        bad+=!(backingUsage&VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT);
        for(unsigned frame=1;frame<=3;frame++) { @autoreleasepool {
            MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];
            pass.colorAttachments[0].texture=target; pass.colorAttachments[0].loadAction=MTLLoadActionClear;
            pass.colorAttachments[0].storeAction=MTLStoreActionStore; pass.colorAttachments[0].clearColor=MTLClearColorMake(0,0,0,0);
            pass.colorAttachments[1].texture=mixed?aux:nil; pass.colorAttachments[1].loadAction=MTLLoadActionClear; pass.colorAttachments[1].storeAction=MTLStoreActionDontCare;
            id<MTLCommandBuffer> cb=[queue commandBuffer];
            id<MTLRenderCommandEncoder> enc=[cb renderCommandEncoderWithDescriptor:pass];
            [enc setRenderPipelineState:pipe]; [enc setFragmentBytes:&frame length:sizeof frame atIndex:0];
            [enc setScissorRect:(MTLScissorRect){0,0,width/2,height}]; [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3]; [enc endEncoding]; [cb commit]; [cb waitUntilCompleted];
            if(cb.status!=MTLCommandBufferStatusCompleted) { fprintf(stderr,"command: %s\n",cb.error.description.UTF8String);return 2; }
            // A separately imported Shared wrapper observes the published
            // family bytes; it cannot rely on the Managed texture's cache.
            MTLTextureDescriptor *observer=[desc copy]; observer.storageMode=MTLStorageModeShared; observer.usage=MTLTextureUsageShaderRead;
            id<MTLTexture> external=[dev newTextureWithDescriptor:observer iosurface:surface plane:0];
            if(!external)return 2;
            [external getBytes:read bytesPerRow:row fromRegion:MTLRegionMake2D(0,0,width,height) mipmapLevel:0];
            bad+=verify(read,row,width,height,formats[fi],frame);
            [first getBytes:read bytesPerRow:row fromRegion:MTLRegionMake2D(0,0,width,height) mipmapLevel:0];
            bad+=verify(read,row,width,height,formats[fi],frame);
            if(IOSurfaceLock(surface,kIOSurfaceLockReadOnly,NULL))return 2;
            bad+=verify(IOSurfaceGetBaseAddress(surface),IOSurfaceGetBytesPerRow(surface),width,height,formats[fi],frame);
            IOSurfaceUnlock(surface,kIOSurfaceLockReadOnly,NULL);
        }}
        printf("format=%lu usage=0x%lx managed=%u mixed=%u padded=%u backing_usage=0x%x frames=3 errors=%u\n",(unsigned long)formats[fi],(unsigned long)usages[ui],managed,mixed,padded,backingUsage,bad);
        failures+=!!bad; cases++; free(read); CFRelease(surface);
    }}
    unsigned drivers=0;
    for(uint32_t i=0;i<_dyld_image_count();i++) {const char *p=_dyld_get_image_name(i),*name=strrchr(p,'/');if(name&&!strcmp(name+1,"NVMTLDriver"))drivers++;}
    if(drivers!=1) { fprintf(stderr,"isolation failed: %u driver images\n",drivers);return 2; }
    printf("%s: %u cases, %u failures, one driver image\n",failures?"FAIL":"PASS",cases,failures);
    return failures?1:0;
}}
