/* SPDX-License-Identifier: MIT
 * Smoke-test the retained driver encoders alongside the new decoder addon.
 * Synthetic images only; requires the exact NVMTL encoder IDs and hardware.
 */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <VideoToolbox/VideoToolbox.h>
#include <dlfcn.h>
#include <stdatomic.h>
#include <stdio.h>

typedef struct { _Atomic unsigned frames,errors;_Atomic unsigned long long bytes;CMVideoCodecType codec; } Result;
static void require(BOOL ok,const char *message){if(!ok){fprintf(stderr,"FAIL: %s\n",message);exit(1);}}
static void encoded(void *context,void *source,OSStatus status,VTEncodeInfoFlags flags,CMSampleBufferRef sample){
    (void)source;(void)flags;Result *r=context;
    if(status||!sample||!CMSampleBufferDataIsReady(sample)||CMFormatDescriptionGetMediaSubType(CMSampleBufferGetFormatDescription(sample))!=r->codec){atomic_fetch_add(&r->errors,1);return;}
    atomic_fetch_add(&r->frames,1);atomic_fetch_add(&r->bytes,CMSampleBufferGetTotalSampleSize(sample));
}
#include "vt_service_test.h"

int main(int argc,char **argv){@autoreleasepool{
    require(argc==2,"usage: vt-encode-probe /path/to/libnvencvt.dylib | encoder-service:/path/to/NVMTLDriver");setvbuf(stdout,NULL,_IOLBF,0);
    void *service=vt_service_test(argv[1],"encoder");
    if(!service) {
        void *library=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL);require(library!=NULL,"load additional codec library");
        OSStatus (*reg)(int)=(void *)dlsym(library,"NVEncVT_Register");require(reg&&reg(1200)==noErr,"register additional decoders");
        id<MTLDevice> device=MTLCreateSystemDefaultDevice();require(device!=nil,"create driver device for built-in codec registration");
        printf("device=%s\n",device.name.UTF8String);
    }
    for(unsigned hevc=0;hevc<2;hevc++){@autoreleasepool{
        Result r={0};r.codec=hevc?kCMVideoCodecType_HEVC:kCMVideoCodecType_H264;
        NSString *encoder=hevc?@"com.nvmtl.videoencoder.hevc.nvenc":@"com.nvmtl.videoencoder.avc.nvenc";
        NSDictionary *spec=@{(id)kVTVideoEncoderSpecification_EncoderID:encoder,(id)kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder:@YES};
        NSDictionary *attrs=@{(id)kCVPixelBufferPixelFormatTypeKey:@(kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange),
                              (id)kCVPixelBufferWidthKey:@128,(id)kCVPixelBufferHeightKey:@96,(id)kCVPixelBufferIOSurfacePropertiesKey:@{}};
        VTCompressionSessionRef session=NULL;OSStatus status=VTCompressionSessionCreate(NULL,128,96,r.codec,(__bridge CFDictionaryRef)spec,(__bridge CFDictionaryRef)attrs,NULL,encoded,&r,&session);
        printf("encoder=%s create=%d\n",encoder.UTF8String,(int)status);require(status==noErr&&session,"create original hardware encoder");
        require(VTSessionSetProperty(session,kVTCompressionPropertyKey_RealTime,kCFBooleanTrue)==noErr,"real-time encoding");
        require(VTCompressionSessionPrepareToEncodeFrames(session)==noErr,"prepare original encoder");
        CFTypeRef hardware=NULL;require(VTSessionCopyProperty(session,kVTCompressionPropertyKey_UsingHardwareAcceleratedVideoEncoder,NULL,&hardware)==noErr&&hardware==kCFBooleanTrue,"encoder uses hardware");
        if(hardware)CFRelease(hardware);
        NSMutableArray *keep=[NSMutableArray array];
        for(unsigned f=0;f<8;f++){
            CVPixelBufferRef image=NULL;require(CVPixelBufferCreate(NULL,128,96,kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange,(__bridge CFDictionaryRef)attrs,&image)==kCVReturnSuccess,"synthetic input image");
            require(CVPixelBufferLockBaseAddress(image,0)==kCVReturnSuccess,"lock input");
            for(unsigned p=0;p<2;p++){
                uint8_t *base=CVPixelBufferGetBaseAddressOfPlane(image,p);size_t row=CVPixelBufferGetBytesPerRowOfPlane(image,p),height=CVPixelBufferGetHeightOfPlane(image,p);
                for(size_t y=0;y<height;y++)memset(base+y*row,p?128:32+f*20,128);
            }
            CVPixelBufferUnlockBaseAddress(image,0);
            VTEncodeInfoFlags flags=0;status=VTCompressionSessionEncodeFrame(session,image,CMTimeMake(f,30),CMTimeMake(1,30),NULL,NULL,&flags);
            [keep addObject:(__bridge id)image];CVPixelBufferRelease(image);require(status==noErr,"encode synthetic frame");
        }
        require(VTCompressionSessionCompleteFrames(session,kCMTimeInvalid)==noErr,"finish encoding");
        VTCompressionSessionInvalidate(session);CFRelease(session);
        printf("%s: %s hardware=1 packets=%u errors=%u bytes=%llu\n",r.frames==8&&!r.errors&&r.bytes?"PASS":"FAIL",hevc?"HEVC":"H.264",atomic_load(&r.frames),atomic_load(&r.errors),atomic_load(&r.bytes));
        require(r.frames==8&&!r.errors&&r.bytes,"all frames encoded by retained driver codec");
    }}if(service)vt_service_isolation();return 0;
}}
