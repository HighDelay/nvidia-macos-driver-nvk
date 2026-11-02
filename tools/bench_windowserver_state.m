// Isolated compositor-like render-state benchmark; never changes the installed plugin.
// clang -fobjc-arc -O2 tools/bench_windowserver_state.m -framework Foundation -framework Metal -framework IOKit -o bench_windowserver_state
// bench_windowserver_state /absolute/path/NVMTLDriver [draws=1200] [frames=9]
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOKit/IOKitLib.h>
#include <dlfcn.h>
#include <time.h>
#include <stdlib.h>
#include <stdio.h>

@interface NSObject (NVMTLProbe)
- (instancetype)initWithAcceleratorPort:(mach_port_t)port;
@end
static double cpu_ms(void) { struct timespec t; clock_gettime(CLOCK_THREAD_CPUTIME_ID, &t); return t.tv_sec * 1000.0 + t.tv_nsec / 1e6; }
static int compare(const void *a, const void *b) { double x=*(const double *)a,y=*(const double *)b; return (x>y)-(x<y); }

int main(int argc, char **argv) { @autoreleasepool {
    if (argc < 2) return 2;
    int draws = argc > 2 ? atoi(argv[2]) : 1200, frames = argc > 3 ? atoi(argv[3]) : 9;
    if (draws < 1 || draws > 100000 || frames < 1 || frames > 100) return 2;
    if (!dlopen(argv[1], RTLD_NOW | RTLD_LOCAL)) { fprintf(stderr, "load: %s\n", dlerror()); return 2; }
    Class cls = NSClassFromString(@"NVMTLDevice");
    io_service_t accel = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("NVAccel"));
    id<MTLDevice> dev = [[cls alloc] initWithAcceleratorPort:accel]; if (accel) IOObjectRelease(accel);
    if (!dev) { fprintf(stderr, "No isolated NVMTL device\n"); return 2; }
    setenv("NVMTL_NO_FBFETCH", "1", 1);
    NSError *error = nil;
    NSString *src = @"#include <metal_stdlib>\nusing namespace metal;\n"
      "vertex float4 v(uint i [[vertex_id]]) { float2 p=float2((i<<1)&2,i&2); return float4(p*2.0-1.0,0,1); }\n"
      "fragment float4 f() { return float4(0.2,0.4,0.6,1.0); }\n";
    id<MTLLibrary> lib = [dev newLibraryWithSource:src options:nil error:&error];
    if (!lib) { fprintf(stderr, "library: %s\n", error.description.UTF8String); return 2; }
    MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
    pd.vertexFunction = [lib newFunctionWithName:@"v"]; pd.fragmentFunction = [lib newFunctionWithName:@"f"];
    pd.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA8Unorm;
    id<MTLRenderPipelineState> pipeline = [dev newRenderPipelineStateWithDescriptor:pd error:&error];
    id<MTLCommandQueue> queue = [dev newCommandQueue];
    if (!pipeline || !queue) { fprintf(stderr, "pipeline: %s\n", error.description.UTF8String); return 2; }
    printf("device: %s\nplugin: %s\n", dev.name.UTF8String, argv[1]);
    for (int mode = 0; mode < 2; mode++) {
        double times[100];
        for (int frame = -2; frame < frames; frame++) @autoreleasepool {
            MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:128 height:128 mipmapped:NO];
            td.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead; td.storageMode = MTLStorageModeShared;
            id<MTLTexture> target = [dev newTextureWithDescriptor:td];
            MTLRenderPassDescriptor *rd = [MTLRenderPassDescriptor renderPassDescriptor];
            rd.colorAttachments[0].texture = target; rd.colorAttachments[0].loadAction = MTLLoadActionClear; rd.colorAttachments[0].storeAction = MTLStoreActionStore;
            id<MTLCommandBuffer> cb = [queue commandBuffer]; id<MTLRenderCommandEncoder> enc = [cb renderCommandEncoderWithDescriptor:rd];
            double start = cpu_ms();
            for (int i = 0; i < draws; i++) {
                [enc setRenderPipelineState:pipeline];
                if (mode == 0) {
                    [enc setViewport:(MTLViewport){0,0,128,128,0,1}]; [enc setScissorRect:(MTLScissorRect){0,0,128,128}];
                    [enc setCullMode:MTLCullModeBack]; [enc setFrontFacingWinding:MTLWindingClockwise]; [enc setBlendColorRed:0.1 green:0.2 blue:0.3 alpha:0.4];
                } else {
                    NSUInteger n = (NSUInteger)(i & 7); [enc setViewport:(MTLViewport){(double)n,0,128-n,128,0,1}];
                    [enc setScissorRect:(MTLScissorRect){(NSUInteger)n,0,128-n,128}]; [enc setCullMode:(i&1)?MTLCullModeBack:MTLCullModeNone];
                    [enc setFrontFacingWinding:(i&1)?MTLWindingClockwise:MTLWindingCounterClockwise]; [enc setBlendColorRed:(float)n/8 green:0.2 blue:0.3 alpha:0.4];
                }
                [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
            }
            double elapsed = cpu_ms() - start; [enc endEncoding]; [cb commit]; [cb waitUntilCompleted];
            if (cb.status != MTLCommandBufferStatusCompleted) { fprintf(stderr, "command failed\n"); return 1; }
            if (frame >= 0) times[frame] = elapsed;
        }
        qsort(times, frames, sizeof(double), compare);
        printf("PASS mode=%s draws=%d frames=%d median_record_cpu_ms=%.6f\n", mode ? "varying" : "repeated", draws, frames, times[frames/2]);
    }
    return 0;
} }
