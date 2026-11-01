/* SPDX-License-Identifier: MIT */
/* Synthetic Core Image preview; never opens a camera or installs the plugin. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <CoreImage/CoreImage.h>
#import <IOSurface/IOSurface.h>
#import <IOKit/IOKitLib.h>
#import <objc/message.h>
#import <objc/runtime.h>
#include <dlfcn.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>

static void require(BOOL ok, const char *what) {
    if (!ok) { fprintf(stderr,"FAIL: %s\n",what); exit(1); }
}
static BOOL flag(id object, const char *name) {
    Ivar iv = class_getInstanceVariable([object class],name);
    require(iv != NULL,"texture diagnostic field");
    return *(const unsigned char *)((const char *)(__bridge void *)object + ivar_getOffset(iv)) != 0;
}
int main(int argc, char **argv) { @autoreleasepool {
    require(argc == 2,"usage: photobooth-surface-probe /path/to/NVMTLDriver");
    setvbuf(stdout,NULL,_IOLBF,0);
    require(dlopen(argv[1],RTLD_NOW|RTLD_LOCAL) != NULL,"load plugin");
    io_service_t svc=IOServiceGetMatchingService(kIOMainPortDefault,IOServiceMatching("NVAccel"));
    require(svc != MACH_PORT_NULL,"find accelerator");
    id<MTLDevice> dev=((id(*)(id,SEL,mach_port_t))objc_msgSend)([NSClassFromString(@"NVMTLDevice") alloc],NSSelectorFromString(@"initWithAcceleratorPort:"),svc);
    IOObjectRelease(svc);require(dev != nil,"create device");
    printf("device=%s\n",dev.name.UTF8String);
    CIContext *context=[CIContext contextWithMTLDevice:dev options:@{kCIContextWorkingColorSpace:[NSNull null]}];
    require(context != nil,"create Core Image context");
    unsigned cases=0;
    for (unsigned bgra=0;bgra<2;bgra++) for (unsigned private=0;private<2;private++) { @autoreleasepool {
        const NSUInteger w=private ? 508 : 512,h=64,row=w*4;
        IOSurfaceRef surf=IOSurfaceCreate((__bridge CFDictionaryRef)@{(id)kIOSurfaceWidth:@(w),(id)kIOSurfaceHeight:@(h),(id)kIOSurfaceBytesPerRow:@(row),(id)kIOSurfaceBytesPerElement:@4,(id)kIOSurfacePixelFormat:@(bgra ? 0x42475241 : 0x52474241)});
        require(surf != NULL,"create synthetic surface");
        require(IOSurfaceGetBytesPerRow(surf)==row,"preserve selected pitch");
        MTLTextureDescriptor *desc=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:bgra ? MTLPixelFormatBGRA8Unorm : MTLPixelFormatRGBA8Unorm width:w height:h mipmapped:NO];
        desc.usage=MTLTextureUsageShaderRead;desc.storageMode=MTLStorageModeShared;
        id<MTLTexture> reader=[dev newTextureWithDescriptor:desc iosurface:surf plane:0];
        id<MTLTexture> writer=[dev newTextureWithDescriptor:desc iosurface:surf plane:0];
        require(reader && writer,"create repeated surface wrappers");
        printf("%s %s usage=0x%lx vram=%d storage=%d\n",bgra?"BGRA":"RGBA",private?"private/cache":"shared",(unsigned long)writer.usage,flag(writer,"_vramOn"),flag(writer,"_stex"));
        require(writer.usage & MTLTextureUsageShaderWrite,"read-only drawable descriptor must produce writable backing");
        require(flag(writer,"_stex"),"writable usage must have a real storage view");
        if (private) require(!flag(writer,"_vramOn"),"unaligned pitch must exercise private cache");
        else require(flag(writer,"_vramOn"),"aligned pitch must retain shared VRAM");
        require(!writer.framebufferOnly,"Core Image destination is not framebuffer-only");
        CIRenderDestination *destination=[[CIRenderDestination alloc]initWithMTLTexture:writer commandBuffer:nil];
        require(destination != nil,"Core Image accepts render destination");
        destination.colorSpace=NULL;
        printf("destination default flipped=%d\n",destination.flipped);
        for (unsigned green=0;green<2;green++) {
            CIColor *color=[CIColor colorWithRed:green?0:1 green:green?1:0 blue:0 alpha:1];
            CIImage *image=[[CIImage imageWithColor:color]imageByCroppingToRect:CGRectMake(0,0,w,h)];
            NSError *error=nil;
            CIRenderTask *task=[context startTaskToRender:image fromRect:CGRectMake(0,0,w,h) toDestination:destination atPoint:CGPointZero error:&error];
            if (!task) fprintf(stderr,"Core Image error: %s\n",error.description.UTF8String);
            require(task != nil,"start Core Image task");
            CIRenderInfo *info=[task waitUntilCompletedAndReturnError:&error];
            if (!info) fprintf(stderr,"Core Image error: %s\n",error.description.UTF8String);
            require(info != nil,"complete Core Image task");
            NSMutableData *bytes=[NSMutableData dataWithLength:row*h];
            [reader getBytes:bytes.mutableBytes bytesPerRow:row fromRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0];
            const unsigned char *p=bytes.bytes;
            for (NSUInteger pixel=0;pixel<w*h;pixel++) {
                const unsigned char *v=p+pixel*4;
                unsigned expected[4]={0,0,0,255};expected[green?1:bgra?2:0]=255;
                for (unsigned c=0;c<4;c++) if (abs((int)v[c]-(int)expected[c])>1) {
                    fprintf(stderr,"pixel %lu got %u %u %u %u\n",(unsigned long)pixel,v[0],v[1],v[2],v[3]);
                    require(NO,"rendered surface pixels");
                }
            }
            cases++;
        }
        // Solid clears cannot detect an inverted preview. Core Image's logical
        // origin is lower-left; flipped determines which logical row is stored
        // at the texture's base address (see CIRenderDestination.h).
        CIImage *pattern=[[CIImage imageWithColor:[CIColor colorWithRed:0 green:0 blue:0 alpha:0]]imageByCroppingToRect:CGRectMake(0,0,w,h)];
        for (unsigned quadrant=0;quadrant<4;quadrant++) {
            unsigned high=quadrant/2,right=quadrant%2;
            CIColor *color=[CIColor colorWithRed:right?0:1 green:right?1:0 blue:high alpha:1];
            CIImage *tile=[[CIImage imageWithColor:color]imageByCroppingToRect:CGRectMake(right*w/2,high*h/2,w/2,h/2)];
            pattern=[tile imageByCompositingOverImage:pattern];
        }
        for (unsigned flipped=0;flipped<2;flipped++) {
            destination.flipped=flipped;
            NSError *error=nil;
            CIRenderTask *task=[context startTaskToRender:pattern fromRect:CGRectMake(0,0,w,h) toDestination:destination atPoint:CGPointZero error:&error];
            require(task && [task waitUntilCompletedAndReturnError:&error],"render orientation pattern");
            NSMutableData *bytes=[NSMutableData dataWithLength:row*h];
            [reader getBytes:bytes.mutableBytes bytesPerRow:row fromRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0];
            const unsigned char *p=bytes.bytes;
            unsigned mismatches=0;
            for(NSUInteger y=0;y<h;y++) for(NSUInteger x=0;x<w;x++) {
                unsigned high=(flipped?h-1-y:y)>=h/2,right=x>=w/2;
                unsigned expected[4]={right?0:255,right?255:0,high?255:0,255};
                if(bgra){unsigned swap=expected[0];expected[0]=expected[2];expected[2]=swap;}
                const unsigned char *v=p+y*row+x*4;
                for(unsigned c=0;c<4;c++) if(abs((int)v[c]-(int)expected[c])>1) {
                    if(!mismatches)fprintf(stderr,"orientation flipped=%u (%lu,%lu) got %u %u %u %u expected %u %u %u %u\n",flipped,(unsigned long)x,(unsigned long)y,v[0],v[1],v[2],v[3],expected[0],expected[1],expected[2],expected[3]);
                    mismatches++;break;
                }
            }
            printf("orientation flipped=%u mismatches=%u/%lu\n",flipped,mismatches,(unsigned long)(w*h));
            require(!mismatches,"orientation of every rendered pixel");
            cases++;
        }
        CFRelease(surf);
    }}
    unsigned images=0;
    for(uint32_t i=0;i<_dyld_image_count();i++) {
        const char *name=_dyld_get_image_name(i),*base=strrchr(name,'/');
        if(base && !strcmp(base+1,"NVMTLDriver")) { images++;printf("plugin_image=%s\n",name); }
    }
    require(images==1,"exactly one plugin image must be loaded");
    printf("PASS: %u Core Image frames, repeated wrappers, shared/private backing and every readback pixel\n",cases);
    return 0;
}}
