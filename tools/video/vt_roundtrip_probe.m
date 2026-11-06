/* SPDX-License-Identifier: MIT
 * Exercise the installed H.264/HEVC hardware encoder and decoder together.
 * Synthetic NV12 only; no camera, microphone or changes to codec registration.
 */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <VideoToolbox/VideoToolbox.h>
#include <math.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>

enum { Width = 128, Height = 96, Frames = 8 };
typedef struct {
    pthread_mutex_t lock;
    NSMutableArray *packets;
    NSMutableIndexSet *seen;
    unsigned encoded, decoded, errors;
    double squaredError;
    uint64_t samples;
} Result;

static void require(BOOL ok, const char *message) {
    if (!ok) { fprintf(stderr, "FAIL: %s\n", message); exit(1); }
}

static uint8_t pixel(unsigned plane, unsigned x, unsigned y, unsigned frame) {
    return plane ? ((x & 1) ? 145 - (int)(y / 4) : 100 + (int)(x / 8))
                 : 32 + ((x / 4 + y / 3 + frame * 7) % 170);
}

static void encoded(void *context, void *source, OSStatus status,
                    VTEncodeInfoFlags flags, CMSampleBufferRef sample) {
    (void)source; (void)flags;
    Result *result = context;
    pthread_mutex_lock(&result->lock);
    if (status || !sample || !CMSampleBufferDataIsReady(sample)) ++result->errors;
    else { [result->packets addObject:(__bridge id)sample]; ++result->encoded; }
    pthread_mutex_unlock(&result->lock);
}

static void decoded(void *context, void *source, OSStatus status, VTDecodeInfoFlags flags,
                    CVImageBufferRef image, CMTime pts, CMTime duration) {
    (void)source; (void)flags; (void)duration;
    Result *result = context;
    pthread_mutex_lock(&result->lock);
    int64_t frame = CMTimeConvertScale(pts, 30, kCMTimeRoundingMethod_RoundTowardZero).value;
    BOOL locked = NO;
    if (status || !image || !CMTIME_IS_NUMERIC(pts) || frame < 0 || frame >= Frames ||
        [result->seen containsIndex:(NSUInteger)frame] || CVPixelBufferGetWidth(image) != Width ||
        CVPixelBufferGetHeight(image) != Height || CVPixelBufferGetPlaneCount(image) != 2 ||
        !CVPixelBufferGetIOSurface(image)) goto failed;
    if (CVPixelBufferLockBaseAddress(image, kCVPixelBufferLock_ReadOnly)) goto failed;
    locked = YES;
    for (unsigned plane = 0; plane < 2; ++plane) {
        const uint8_t *base = CVPixelBufferGetBaseAddressOfPlane(image, plane);
        size_t row = CVPixelBufferGetBytesPerRowOfPlane(image, plane);
        if (!base || row < Width) goto failed;
        for (unsigned y = 0; y < (plane ? Height / 2 : Height); ++y)
            for (unsigned x = 0; x < Width; ++x) {
                double error = (double)base[y * row + x] - pixel(plane, x, y, (unsigned)frame);
                result->squaredError += error * error;
                ++result->samples;
            }
    }
    [result->seen addIndex:(NSUInteger)frame];
    ++result->decoded;
    CVPixelBufferUnlockBaseAddress(image, kCVPixelBufferLock_ReadOnly);
    pthread_mutex_unlock(&result->lock);
    return;
failed:
    if (locked) CVPixelBufferUnlockBaseAddress(image, kCVPixelBufferLock_ReadOnly);
    ++result->errors;
    pthread_mutex_unlock(&result->lock);
}

int main(void) { @autoreleasepool {
    setvbuf(stdout, NULL, _IOLBF, 0);
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    require(device != nil, "initialize installed driver");
    printf("device=%s\n", device.name.UTF8String);
    for (unsigned hevc = 0; hevc < 2; ++hevc) { @autoreleasepool {
        Result result = {.lock = PTHREAD_MUTEX_INITIALIZER};
        result.packets = [NSMutableArray new]; result.seen = [NSMutableIndexSet new];
        CMVideoCodecType codec = hevc ? kCMVideoCodecType_HEVC : kCMVideoCodecType_H264;
        NSString *identifier = hevc ? @"com.nvmtl.videoencoder.hevc.nvenc" : @"com.nvmtl.videoencoder.avc.nvenc";
        NSDictionary *encoderSpec = @{(id)kVTVideoEncoderSpecification_EncoderID: identifier,
            (id)kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder: @YES};
        NSDictionary *attributes = @{(id)kCVPixelBufferPixelFormatTypeKey: @(kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange),
            (id)kCVPixelBufferWidthKey: @(Width), (id)kCVPixelBufferHeightKey: @(Height),
            (id)kCVPixelBufferIOSurfacePropertiesKey: @{}};
        VTCompressionSessionRef encoder = NULL;
        require(VTCompressionSessionCreate(NULL, Width, Height, codec, (__bridge CFDictionaryRef)encoderSpec,
            (__bridge CFDictionaryRef)attributes, NULL, encoded, &result, &encoder) == noErr, "create hardware encoder");
        require(VTSessionSetProperty(encoder, kVTCompressionPropertyKey_RealTime, kCFBooleanTrue) == noErr, "real-time encoder");
        require(VTCompressionSessionPrepareToEncodeFrames(encoder) == noErr, "prepare encoder");
        CFTypeRef hardware = NULL;
        require(VTSessionCopyProperty(encoder, kVTCompressionPropertyKey_UsingHardwareAcceleratedVideoEncoder,
            NULL, &hardware) == noErr && hardware == kCFBooleanTrue, "hardware encoding selected");
        CFRelease(hardware);
        for (unsigned frame = 0; frame < Frames; ++frame) { @autoreleasepool {
            CVPixelBufferRef image = NULL;
            require(CVPixelBufferCreate(NULL, Width, Height, kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange,
                (__bridge CFDictionaryRef)attributes, &image) == kCVReturnSuccess, "allocate synthetic image");
            require(CVPixelBufferLockBaseAddress(image, 0) == kCVReturnSuccess, "lock synthetic image");
            for (unsigned plane = 0; plane < 2; ++plane) {
                uint8_t *base = CVPixelBufferGetBaseAddressOfPlane(image, plane);
                size_t row = CVPixelBufferGetBytesPerRowOfPlane(image, plane);
                for (unsigned y = 0; y < (plane ? Height / 2 : Height); ++y)
                    for (unsigned x = 0; x < Width; ++x) base[y * row + x] = pixel(plane, x, y, frame);
            }
            CVPixelBufferUnlockBaseAddress(image, 0);
            OSStatus status = VTCompressionSessionEncodeFrame(encoder, image, CMTimeMake(frame, 30),
                CMTimeMake(1, 30), NULL, NULL, NULL);
            CVPixelBufferRelease(image);
            require(status == noErr, "encode synthetic image");
        }}
        require(VTCompressionSessionCompleteFrames(encoder, kCMTimeInvalid) == noErr, "finish encoder");
        VTCompressionSessionInvalidate(encoder); CFRelease(encoder);
        require(result.encoded == Frames && !result.errors, "all frames encoded");
        NSDictionary *decoderSpec = @{(id)kVTVideoDecoderSpecification_RequireHardwareAcceleratedVideoDecoder: @YES};
        VTDecompressionOutputCallbackRecord output = {decoded, &result};
        CMSampleBufferRef first = (__bridge CMSampleBufferRef)result.packets.firstObject;
        VTDecompressionSessionRef decoder = NULL;
        require(VTDecompressionSessionCreate(NULL, CMSampleBufferGetFormatDescription(first),
            (__bridge CFDictionaryRef)decoderSpec, (__bridge CFDictionaryRef)attributes, &output, &decoder) == noErr,
            "create required hardware decoder");
        hardware = NULL;
        require(VTSessionCopyProperty(decoder, kVTDecompressionPropertyKey_UsingHardwareAcceleratedVideoDecoder,
            NULL, &hardware) == noErr && hardware == kCFBooleanTrue, "hardware decoding selected");
        CFRelease(hardware);
        for (id sample in result.packets)
            require(VTDecompressionSessionDecodeFrame(decoder, (__bridge CMSampleBufferRef)sample,
                kVTDecodeFrame_EnableAsynchronousDecompression, NULL, NULL) == noErr, "decode encoded image");
        require(VTDecompressionSessionFinishDelayedFrames(decoder) == noErr, "finish delayed decode");
        require(VTDecompressionSessionWaitForAsynchronousFrames(decoder) == noErr, "finish asynchronous decode");
        VTDecompressionSessionInvalidate(decoder); CFRelease(decoder);
        double psnr = result.squaredError ? 10 * log10(255.0 * 255.0 * result.samples / result.squaredError) : INFINITY;
        printf("%s hardware encode/decode: encoded=%u decoded=%u errors=%u PSNR=%.2f dB\n",
            hevc ? "HEVC" : "H.264", result.encoded, result.decoded, result.errors, psnr);
        require(result.decoded == Frames && !result.errors && psnr >= 30, "complete correctly oriented hardware roundtrip");
        pthread_mutex_destroy(&result.lock);
    }}
    puts("PASS: installed H.264 and HEVC hardware encoder/decoder roundtrips");
}return 0;}
