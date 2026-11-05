/* An unfenced IOSurface must be published before a scheduled handoff lets another
 * reader consume it. Run with the NVMTL device; metal_test_lookup.c can select a
 * private bundle. Pass 3 for the old-policy negative control, or managed
 * for the tiled Managed IOSurface publication regression.
 * clang -fobjc-arc tools/test_surface_schedule_gpu.m -framework Foundation \
 *   -framework Metal -framework IOSurface -o surface-schedule-test
 */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOSurface/IOSurface.h>
#include <stdio.h>

int main(int argc, char **argv) { @autoreleasepool {
    id<MTLDevice> device=MTLCreateSystemDefaultDevice();
    if (!device || ![NSStringFromClass([device class]) isEqual:@"NVMTLDevice"]) {
        fprintf(stderr,"This probe requires the NVMTL device\n"); return 2;
    }
    // Release plugins sanitize inherited tuning variables at load time.
    const BOOL managed=argc==2 && !strcmp(argv[1],"managed");
    if (argc==2 && !managed) setenv("NVMTL_SCHEDULE_AFTER_SURFACE_WRITES",argv[1],1);
    id<MTLCommandQueue> queue=[device newCommandQueue];
    unsigned stale=0, finalBad=0;
    for (unsigned frame=0; frame<64; ++frame) { @autoreleasepool {
        IOSurfaceRef surface=IOSurfaceCreate((__bridge CFDictionaryRef)@{
            (id)kIOSurfaceWidth:@256, (id)kIOSurfaceHeight:@256,
            (id)kIOSurfaceBytesPerElement:@4, (id)kIOSurfaceBytesPerRow:@1024,
            (id)kIOSurfacePixelFormat:@((uint32_t)'BGRA')});
        if (!surface || IOSurfaceLock(surface,0,NULL)) return 3;
        memset(IOSurfaceGetBaseAddress(surface),0,IOSurfaceGetAllocSize(surface));
        IOSurfaceUnlock(surface,0,NULL);
        MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
            width:256 height:256 mipmapped:NO];
        td.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
        td.storageMode=managed?MTLStorageModeManaged:MTLStorageModeShared;
        id<MTLTexture> texture=[device newTextureWithDescriptor:td iosurface:surface plane:0];
        if (!texture) return 4;
        MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture=texture;
        pass.colorAttachments[0].loadAction=MTLLoadActionClear;
        pass.colorAttachments[0].storeAction=MTLStoreActionStore;
        pass.colorAttachments[0].clearColor=MTLClearColorMake(1,0.5,0,1);
        id<MTLCommandBuffer> cb=[queue commandBuffer];
        id<MTLRenderCommandEncoder> encoder=[cb renderCommandEncoderWithDescriptor:pass];
        if (!encoder) return 5;
        [encoder endEncoding];
        __block unsigned oldPixels=0;
        [cb addScheduledHandler:^(id<MTLCommandBuffer> done) {
            (void)done;
            if (IOSurfaceLock(surface,kIOSurfaceLockReadOnly,NULL)) { oldPixels=UINT_MAX; return; }
            const unsigned char *bytes=IOSurfaceGetBaseAddress(surface);
            for (unsigned i=0; i<256*256; ++i)
                oldPixels += bytes[4*i+2]!=255 || bytes[4*i+3]!=255;
            IOSurfaceUnlock(surface,kIOSurfaceLockReadOnly,NULL);
        }];
        [cb commit]; [cb waitUntilCompleted];
        if (cb.error || IOSurfaceLock(surface,kIOSurfaceLockReadOnly,NULL)) return 6;
        const unsigned char *bytes=IOSurfaceGetBaseAddress(surface);
        for (unsigned i=0; i<256*256; ++i)
            finalBad += bytes[4*i+2]!=255 || bytes[4*i+3]!=255;
        IOSurfaceUnlock(surface,kIOSurfaceLockReadOnly,NULL);
        stale += oldPixels!=0;
        CFRelease(surface);
    } }
    printf("frames=64 stale_handoffs=%u bad_completed_pixels=%u\n",stale,finalBad);
    return stale || finalBad ? 1 : 0;
} }
