// Isolated NVMTL command-recording benchmark; never changes the installed plugin.
// clang -fobjc-arc -O2 tools/bench_command_bindings.m -framework Foundation -framework Metal -framework IOKit -o bench
// bench /absolute/path/NVMTLDriver [dispatches=2000] [frames=9] [fetch-off]
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
static double cpu_ms(void) {
    struct timespec t; clock_gettime(CLOCK_THREAD_CPUTIME_ID, &t);
    return t.tv_sec * 1000.0 + t.tv_nsec / 1e6;
}
static int compare(const void *a, const void *b) {
    double x=*(const double *)a,y=*(const double *)b; return (x>y)-(x<y);
}
int main(int argc, char **argv) { @autoreleasepool {
    if (argc<2) return 2;
    int draws=argc>2?atoi(argv[2]):2000, frames=argc>3?atoi(argv[3]):9;
    if (draws<1 || draws>100000 || frames<1 || frames>100) return 2;
    if (!dlopen(argv[1],RTLD_NOW|RTLD_LOCAL)) { fprintf(stderr,"load: %s\n",dlerror()); return 2; }
    Class cls=NSClassFromString(@"NVMTLDevice");
    io_service_t accel=IOServiceGetMatchingService(kIOMainPortDefault,IOServiceMatching("NVAccel"));
    id<MTLDevice> dev=[[cls alloc] initWithAcceleratorPort:accel];
    if (accel) IOObjectRelease(accel);
    if (!dev) { fprintf(stderr,"No isolated NVMTL device\n"); return 2; }
    // Release startup sanitizes diagnostic environment variables. Set this opt-out
    // after device creation and before the first pipeline, only in the probe process.
    if (argc>4 && !strcmp(argv[4],"fetch-off")) setenv("NVMTL_NO_FBFETCH","1",1);
    printf("device: %s\nplugin: %s\n",dev.name.UTF8String,argv[1]); fflush(stdout);
    NSError *error=nil;
    NSString *src=@"#include <metal_stdlib>\nusing namespace metal;\n"
    "kernel void a(device uint *out [[buffer(0)]], uint i [[thread_position_in_grid]]) { out[i]=i*3u+7u; }\n"
    "kernel void b(device uint *out [[buffer(0)]], uint i [[thread_position_in_grid]]) { out[i]=i*5u+11u; }\n"
    "vertex float4 v(uint i [[vertex_id]]) { float2 p=float2((i<<1)&2,i&2); return float4(p*2.0-1.0,0,1); }\n"
    "fragment float4 f(float4 old [[color(0)]]) { return old+float4(1.0/255.0,0,0,0); }\n";
    id<MTLLibrary> lib=[dev newLibraryWithSource:src options:nil error:&error];
    if (!lib) { fprintf(stderr,"library: %s\n",error.description.UTF8String); return 2; }
    id<MTLComputePipelineState> a=[dev newComputePipelineStateWithFunction:[lib newFunctionWithName:@"a"] error:&error];
    id<MTLComputePipelineState> b=[dev newComputePipelineStateWithFunction:[lib newFunctionWithName:@"b"] error:&error];
    id<MTLCommandQueue> q=[dev newCommandQueue];
    id<MTLBuffer> out=[dev newBufferWithLength:4096 options:MTLResourceStorageModeShared];
    id<MTLBuffer> indirect=[dev newBufferWithLength:256 options:MTLResourceStorageModeShared];
    if (!a || !b || !q || !out || !indirect) { fprintf(stderr,"resources: %s\n",error.description.UTF8String); return 2; }
    uint32_t *groups=indirect.contents; groups[0]=2; groups[1]=groups[2]=1;
    for (int mode=0; mode<3; mode++) {
        double times[100];
        for (int frame=-2; frame<frames; frame++) { @autoreleasepool {
            memset(out.contents,0,4096);
            id<MTLCommandBuffer> cb=[q commandBuffer];
            id<MTLComputeCommandEncoder> enc=[cb computeCommandEncoder];
            [enc setBuffer:out offset:0 atIndex:0];
            [enc setComputePipelineState:a];
            double start=cpu_ms();
            for(int i=0;i<draws;i++) {
                if (mode==1) {
                    [enc setComputePipelineState:(i&1)?b:a];
                    [enc setBufferOffset:(i&1)?256:0 atIndex:0];
                    [enc dispatchThreads:MTLSizeMake(65,1,1) threadsPerThreadgroup:MTLSizeMake(32,1,1)];
                } else if (mode==2) {
                    [enc dispatchThreadgroupsWithIndirectBuffer:indirect indirectBufferOffset:0 threadsPerThreadgroup:MTLSizeMake(32,1,1)];
                } else [enc dispatchThreads:MTLSizeMake(64,1,1) threadsPerThreadgroup:MTLSizeMake(32,1,1)];
            }
            double elapsed=cpu_ms()-start;
            [enc endEncoding]; [cb commit]; [cb waitUntilCompleted];
            if (cb.status!=MTLCommandBufferStatusCompleted) { fprintf(stderr,"command: %s\n",cb.error.description.UTF8String); return 1; }
            int use_b=mode==1 && ((draws-1)&1), count=mode==1?65:64;
            const uint32_t *data=(const uint32_t *)out.contents+(use_b?64:0);
            for (int i=0;i<count;i++) {
                uint32_t expected=use_b?i*5u+11u:i*3u+7u;
                if(data[i]!=expected) { fprintf(stderr,"mode %d frame %d index %d got %u expected %u\n",mode,frame,i,data[i],expected); return 1; }
            }
            if(frame>=0) times[frame]=elapsed;
        }}
        qsort(times,frames,sizeof(double),compare);
        printf("PASS mode=%s dispatches=%d frames=%d median_record_cpu_ms=%.6f\n",mode==0?"repeat":mode==1?"alternating-tail-offset":"indirect",draws,frames,times[frames/2]); fflush(stdout);
    }
    // Repeated fetch draws, then a new attachment/pass: cached descriptors must follow its view.
    MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];
    pd.vertexFunction=[lib newFunctionWithName:@"v"]; pd.fragmentFunction=[lib newFunctionWithName:@"f"];
    pd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;
    id<MTLRenderPipelineState> rp=[dev newRenderPipelineStateWithDescriptor:pd error:&error];
    if (!rp) { fprintf(stderr,"render pipeline: %s\n",error.description.UTF8String); return 2; }
    const char *off=getenv("NVMTL_NO_FBFETCH"); int fetch_off=off && off[0]=='1';
    for (int pass=0;pass<3;pass++) { @autoreleasepool {
        MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:16 height:16 mipmapped:NO];
        td.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
        td.storageMode=MTLStorageModeShared;
        id<MTLTexture> target=[dev newTextureWithDescriptor:td];
        MTLRenderPassDescriptor *rd=[MTLRenderPassDescriptor renderPassDescriptor];
        rd.colorAttachments[0].texture=target; rd.colorAttachments[0].loadAction=MTLLoadActionClear;
        rd.colorAttachments[0].storeAction=MTLStoreActionStore;
        rd.colorAttachments[0].clearColor=MTLClearColorMake(0,0,0,0);
        id<MTLCommandBuffer> cb=[q commandBuffer];
        id<MTLRenderCommandEncoder> enc=[cb renderCommandEncoderWithDescriptor:rd];
        [enc setRenderPipelineState:rp];
        for(int i=0;i<64;i++) [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        [enc endEncoding]; [cb commit]; [cb waitUntilCompleted];
        if(cb.status!=MTLCommandBufferStatusCompleted) return 1;
        unsigned char pixels[16*16*4]; [target getBytes:pixels bytesPerRow:64 fromRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0];
        for(int i=0;i<256;i++) if(pixels[i*4]!=(fetch_off?1:64)) {
            fprintf(stderr,"fetch pass %d pixel %d red %u expected %d\n",pass,i,pixels[i*4],fetch_off?1:64); return 1;
        }
    }}
    printf("PASS framebuffer_fetch=%s passes=3 draws_per_pass=64 pixels_per_pass=256\n",fetch_off?"dummy":"attachment");
    return 0;
}}
