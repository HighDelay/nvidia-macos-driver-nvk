/* SPDX-License-Identifier: MIT
 * Decode synthetic IVF through the real VideoToolbox API and compare every
 * luma/chroma sample with an independent I420 software reference. Supports both
 * process-local registration and the installed driver's automatic loading.
 */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <VideoToolbox/VideoToolbox.h>
#include <dlfcn.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    unsigned w,h,depth,expected,frames,dropped,errors;
    NSData *reference;
    NSMutableIndexSet *seen;
    pthread_mutex_t mutex;
} Result;
static void require(BOOL ok,const char *message) {
    if(!ok){fprintf(stderr,"FAIL: %s\n",message);exit(1);}
}
static uint32_t le32(const uint8_t *p){return p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;}
static void output(void *context,void *source,OSStatus status,VTDecodeInfoFlags flags,CVImageBufferRef image,CMTime pts,CMTime duration) {
    (void)source;(void)duration;Result *r=context;pthread_mutex_lock(&r->mutex);
    if(status){fprintf(stderr,"callback error=%d\n",(int)status);r->errors++;goto done;}
    if(!image){r->dropped+=(flags&kVTDecodeInfo_FrameDropped)!=0;goto done;}
    const int64_t index=CMTimeConvertScale(pts,30,kCMTimeRoundingMethod_RoundTowardZero).value;
    if(index<0||(uint64_t)index>=r->expected||[r->seen containsIndex:(NSUInteger)index]){r->errors++;goto done;}
    [r->seen addIndex:(NSUInteger)index];
    if(!CVPixelBufferGetIOSurface(image)){fprintf(stderr,"frame=%lld has no IOSurface backing\n",(long long)index);r->errors++;goto done;}
    if(CVPixelBufferGetWidth(image)!=r->w||CVPixelBufferGetHeight(image)!=r->h||CVPixelBufferGetPlaneCount(image)!=2){r->errors++;goto done;}
    const size_t bpp=r->depth>8?2:1,cw=(r->w+1)/2,ch=(r->h+1)/2,frameBytes=((size_t)r->w*r->h+2*cw*ch)*bpp;
    if(((size_t)index+1)*frameBytes>r->reference.length){r->errors++;goto done;}
    if(CVPixelBufferLockBaseAddress(image,kCVPixelBufferLock_ReadOnly)){r->errors++;goto done;}
    const uint8_t *ref=(const uint8_t *)r->reference.bytes+(size_t)index*frameBytes;
    unsigned wrong=0;
    for(unsigned plane=0;plane<2;plane++) {
        const size_t w=plane?cw:r->w,h=plane?ch:r->h,row=CVPixelBufferGetBytesPerRowOfPlane(image,plane);
        const uint8_t *base=CVPixelBufferGetBaseAddressOfPlane(image,plane);
        if(!base||row<w*bpp*(plane?2:1)||CVPixelBufferGetHeightOfPlane(image,plane)<h){wrong++;continue;}
        for(size_t y=0;y<h;y++)for(size_t x=0;x<w;x++)for(unsigned c=0;c<(plane?2u:1u);c++) {
            size_t at=(plane?(size_t)r->w*r->h+c*cw*ch:0)+y*w+x;
            unsigned expected=bpp==1?ref[at]:((const uint16_t *)ref)[at];
            unsigned actual=bpp==1?base[y*row+x*(plane?2:1)+c]:((const uint16_t *)(base+y*row))[x*(plane?2:1)+c]>>6;
            if(actual!=expected)wrong++;
        }
    }
    CVPixelBufferUnlockBaseAddress(image,kCVPixelBufferLock_ReadOnly);
    if(wrong){fprintf(stderr,"frame=%lld mismatched_samples=%u\n",(long long)index,wrong);r->errors++;}
    r->frames++;
done:pthread_mutex_unlock(&r->mutex);
}

int main(int argc,char **argv){@autoreleasepool{
    require(argc==6||argc==7||argc==3,"usage: vt-codec-probe LIBRARY|installed INPUT.ivf REFERENCE.yuv DEPTH sync|async|reject [--minimal-output] | LIBRARY|installed capabilities");
    BOOL minimal=argc==7;
    if(minimal)require(!strcmp(argv[6],"--minimal-output"),"minimal browser output contract");
    setvbuf(stdout,NULL,_IOLBF,0);
    BOOL installed=!strcmp(argv[1],"installed");
    if(installed)require(MTLCreateSystemDefaultDevice()!=nil,"initialize installed driver");
    void *library=dlopen(installed?"/Library/GPUBundles/NVMTLDriver.bundle/Contents/MacOS/libnvencvt.dylib":argv[1],installed?RTLD_LAZY|RTLD_LOCAL|RTLD_NOLOAD:RTLD_NOW|RTLD_LOCAL);
    if(!library)fprintf(stderr,"dlopen: %s\n",dlerror());require(library!=NULL,installed?"driver loaded the codec addon automatically":"load codec library");
    OSStatus (*reg)(int)=(void *)dlsym(library,"NVEncVT_Register");require(reg!=NULL,"codec registration entrypoint");
    OSStatus status=installed?noErr:reg(1200);printf("registration=%s status=%d\n",installed?"installed driver":"process-local",(int)status);require(status==noErr,"register Turing decoders");
    if(argc==3) {
        require(!strcmp(argv[2],"capabilities"),"capability mode");
        int *family=dlsym(library,"gNvencvtGpu");require(family&&*family==4,"probe is running on TU10x");
        CFArrayRef encoders=NULL;require(VTCopyVideoEncoderList(NULL,&encoders)==noErr,"list available encoders");
        unsigned addonEncoders=0;
        for(NSDictionary *entry in (__bridge NSArray *)encoders) {
            NSString *name=entry[(id)kVTVideoEncoderList_EncoderID];
            if([name hasPrefix:@"org.nvenc.videoencoder."])addonEncoders++;
        }
        CFRelease(encoders);
        printf("family=TU10x vp8_hw=%d vp9_hw=%d av1_hw=%d addon_encoders=%u\n",VTIsHardwareDecodeSupported('vp08'),VTIsHardwareDecodeSupported('vp09'),VTIsHardwareDecodeSupported('av01'),addonEncoders);
        require(VTIsHardwareDecodeSupported('vp08')&&VTIsHardwareDecodeSupported('vp09'),"Turing advertises both additional hardware decoders");
        require(!addonEncoders&&!VTIsHardwareDecodeSupported('av01'),"Turing keeps stock encoding and does not advertise AV1");
        return 0;
    }
    FILE *input=fopen(argv[2],"rb");require(input!=NULL,"open IVF input");uint8_t header[32];
    require(fread(header,1,32,input)==32&&!memcmp(header,"DKIF",4),"IVF header");
    BOOL vp9=!memcmp(header+8,"VP90",4);require(vp9||!memcmp(header+8,"VP80",4),"VP8/VP9 stream");
    Result r={0};r.w=header[12]|header[13]<<8;r.h=header[14]|header[15]<<8;r.expected=le32(header+24);r.depth=(unsigned)atoi(argv[4]);
    require(r.w&&r.h&&r.expected&&r.expected<=10000&&(r.depth==8||r.depth==10),"bounded fixture description");
    r.reference=[NSData dataWithContentsOfFile:@(argv[3])];require(r.reference!=nil,"read independent reference");r.seen=[NSMutableIndexSet indexSet];pthread_mutex_init(&r.mutex,NULL);
    CMVideoCodecType codec=vp9?'vp09':'vp08';
    uint8_t config[12]={1,0,0,0,0,10,(uint8_t)((r.depth<<4)|2),1,1,1,0,0};if(r.depth>8)config[4]=2;
    NSDictionary *extensions=vp9?@{(id)kCMFormatDescriptionExtension_SampleDescriptionExtensionAtoms:@{@"vpcC":[NSData dataWithBytes:config length:sizeof config]}}:@{};
    CMVideoFormatDescriptionRef format=NULL;require(CMVideoFormatDescriptionCreate(NULL,codec,r.w,r.h,(__bridge CFDictionaryRef)extensions,&format)==noErr,"create compressed format");
    NSDictionary *spec=@{(id)kVTVideoDecoderSpecification_RequireHardwareAcceleratedVideoDecoder:@YES};
    OSType pixel=r.depth>8?kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange:kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange;
    NSDictionary *attrs=minimal?@{(id)kCVPixelBufferPixelFormatTypeKey:@(pixel)}:@{(id)kCVPixelBufferPixelFormatTypeKey:@(pixel),(id)kCVPixelBufferIOSurfacePropertiesKey:@{}};
    VTDecompressionOutputCallbackRecord callback={output,&r};VTDecompressionSessionRef session=NULL;
    status=VTDecompressionSessionCreate(NULL,format,(__bridge CFDictionaryRef)spec,(__bridge CFDictionaryRef)attrs,&callback,&session);
    printf("session=%d codec=%c%c%c%c size=%ux%u depth=%u\n",(int)status,codec>>24,(codec>>16)&255,(codec>>8)&255,codec&255,r.w,r.h,r.depth);
    require(status==noErr&&session!=NULL,"create required hardware session");
    CFTypeRef hardware=NULL;require(VTSessionCopyProperty(session,kVTDecompressionPropertyKey_UsingHardwareAcceleratedVideoDecoder,NULL,&hardware)==noErr,"query hardware decoder");
    require(hardware==kCFBooleanTrue,"session uses hardware acceleration");CFRelease(hardware);
    BOOL async=!strcmp(argv[5],"async"),reject=!strcmp(argv[5],"reject");require(async||reject||!strcmp(argv[5],"sync"),"decode mode");
    unsigned submitted=0;uint8_t frameHeader[12];
    while(fread(frameHeader,1,12,input)==12){@autoreleasepool{
        size_t bytes=le32(frameHeader);require(bytes&&bytes<=64*1024*1024,"bounded compressed payload");
        CMBlockBufferRef block=NULL;require(CMBlockBufferCreateWithMemoryBlock(NULL,NULL,bytes,NULL,NULL,0,bytes,0,&block)==noErr,"allocate sample data");
        require(CMBlockBufferAssureBlockMemory(block)==noErr,"materialize sample data");
        char *buffer=NULL;require(CMBlockBufferGetDataPointer(block,0,NULL,NULL,&buffer)==noErr&&fread(buffer,1,bytes,input)==bytes,"read compressed sample");
        CMSampleTimingInfo time={CMTimeMake(1,30),CMTimeMake(submitted,30),kCMTimeInvalid};CMSampleBufferRef sample=NULL;
        require(CMSampleBufferCreateReady(NULL,block,format,1,1,&time,1,&bytes,&sample)==noErr,"create compressed sample");
        VTDecodeInfoFlags flags=0;status=VTDecompressionSessionDecodeFrame(session,sample,async?kVTDecodeFrame_EnableAsynchronousDecompression:0,NULL,&flags);
        CFRelease(sample);CFRelease(block);if(!reject)require(status==noErr,"decode compressed sample");submitted++;
        if(reject)break;
    }}
    require(VTDecompressionSessionFinishDelayedFrames(session)==noErr,"finish delayed frames");
    require(VTDecompressionSessionWaitForAsynchronousFrames(session)==noErr,"finish asynchronous frames");
    VTDecompressionSessionInvalidate(session);CFRelease(session);CFRelease(format);fclose(input);
    BOOL passed=reject?!r.frames&&(status||r.errors):!r.errors&&r.frames==r.expected;
    printf("%s: hardware=1 submitted=%u returned=%u expected=%u errors=%u dropped=%u mode=%s\n",passed?"PASS":"FAIL",submitted,r.frames,r.expected,r.errors,r.dropped,argv[5]);
    pthread_mutex_destroy(&r.mutex);return passed?0:1;
}}
