// Isolated fetch/descriptor benchmark with pixel verification; never installs a driver.
// clang -fobjc-arc -O2 tools/bench_framebuffer_fetch.m -framework Foundation -framework Metal -framework IOKit -o bench_fetch
// bench_fetch /absolute/path/NVMTLDriver [draws=1200] [frames=9] [mask-off|fetch-off] [mode=0|1|2]
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOKit/IOKitLib.h>
#include <dlfcn.h>
#include <time.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

@interface NSObject (NVMTLFetchProbe)
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
    int draws=argc>2?atoi(argv[2]):1200, frames=argc>3?atoi(argv[3]):9;
    if(draws<256 || draws>256*200 || frames<1 || frames>100) return 2;
    if(!dlopen(argv[1],RTLD_NOW|RTLD_LOCAL)) { fprintf(stderr,"load: %s\n",dlerror()); return 2; }
    Class cls=NSClassFromString(@"NVMTLDevice");
    io_service_t accel=IOServiceGetMatchingService(kIOMainPortDefault,IOServiceMatching("NVAccel"));
    id<MTLDevice> dev=[[cls alloc] initWithAcceleratorPort:accel];
    if(accel) IOObjectRelease(accel);
    if(!dev) return 2;
    // Release startup sanitizes diagnostics; set controls after creating this isolated device.
    int fetch_off=argc>4 && !strcmp(argv[4],"fetch-off");
    if(fetch_off) setenv("NVMTL_NO_FBFETCH","1",1); else unsetenv("NVMTL_NO_FBFETCH");
    setenv("NVMTL_DESC_MASK",argc>4 && !strcmp(argv[4],"mask-off")?"0":"1",1);
    NSError *error=nil;
    NSString *src=@"#include <metal_stdlib>\nusing namespace metal;\n"
        "vertex float4 v(uint i [[vertex_id]]) { float2 p=float2((i<<1)&2,i&2); return float4(p*2.0-1.0,0,1); }\n"
        "fragment float4 f(float4 old [[color(0)]], constant float4 &step [[buffer(0)]], texture2d<float,access::read> tex [[texture(0)]]) { return float4(old.r+step.x,tex.read(uint2(0)).g,step.z,1); }\n";
    id<MTLLibrary> lib=[dev newLibraryWithSource:src options:nil error:&error];
    if(!lib) { fprintf(stderr,"library: %s\n",error.description.UTF8String); return 2; }
    MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];
    pd.vertexFunction=[lib newFunctionWithName:@"v"]; pd.fragmentFunction=[lib newFunctionWithName:@"f"];
    pd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;
    id<MTLRenderPipelineState> pipeline=[dev newRenderPipelineStateWithDescriptor:pd error:&error];
    id<MTLCommandQueue> queue=[dev newCommandQueue];
    if(!pipeline || !queue) { fprintf(stderr,"pipeline: %s\n",error.description.UTF8String); return 2; }
    MTLTextureDescriptor *sd=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:1 height:1 mipmapped:NO];
    sd.usage=MTLTextureUsageShaderRead; sd.storageMode=MTLStorageModeShared;
    id<MTLTexture> sources[2]={[dev newTextureWithDescriptor:sd],[dev newTextureWithDescriptor:sd]};
    for(int i=0;i<2;i++) {
        unsigned char pixel[4]={0,(unsigned char)(i?153:51),0,255};
        if(!sources[i]) return 2;
        [sources[i] replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:pixel bytesPerRow:4];
    }
    printf("device: %s\nplugin: %s\ndescriptor_mask: %s\nframebuffer_fetch: %s\n",dev.name.UTF8String,argv[1],getenv("NVMTL_DESC_MASK"),fetch_off?"off":"on"); fflush(stdout);
    const char *names[]={"stable-fetch","uniform-churn","texture-churn"};
    for(int mode=0;mode<3;mode++) {
        if(argc>5 && mode!=atoi(argv[5])) continue;
        double times[100];
        for(int frame=-2;frame<frames;frame++) { @autoreleasepool {
            MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:16 height:16 mipmapped:NO];
            td.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead; td.storageMode=MTLStorageModeShared;
            id<MTLTexture> target=[dev newTextureWithDescriptor:td]; if(!target) return 2;
            MTLRenderPassDescriptor *rd=[MTLRenderPassDescriptor renderPassDescriptor];
            rd.colorAttachments[0].texture=target; rd.colorAttachments[0].loadAction=MTLLoadActionClear;
            rd.colorAttachments[0].storeAction=MTLStoreActionStore;
            rd.colorAttachments[0].clearColor=MTLClearColorMake(0,0,0,0);
            id<MTLCommandBuffer> cb=[queue commandBuffer];
            id<MTLRenderCommandEncoder> enc=[cb renderCommandEncoderWithDescriptor:rd];
            if(!enc) return 2;
            [enc setRenderPipelineState:pipeline]; [enc setFragmentTexture:sources[0] atIndex:0];
            float step[4]={1.0f/255.0f,0,17.0f/255.0f,0};
            [enc setFragmentBytes:step length:sizeof step atIndex:0];
            double start=cpu_ms();
            for(int i=0;i<draws;i++) {
                if(mode) { step[2]=(float)(i%251)/255; [enc setFragmentBytes:step length:sizeof step atIndex:0]; }
                if(mode==2) [enc setFragmentTexture:sources[i&1] atIndex:0];
                [enc setScissorRect:(MTLScissorRect){(NSUInteger)(i%16),(NSUInteger)((i/16)%16),1,1}];
                [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
            }
            double elapsed=cpu_ms()-start;
            [enc endEncoding]; [cb commit]; [cb waitUntilCompleted];
            if(cb.status!=MTLCommandBufferStatusCompleted) { fprintf(stderr,"command: %s\n",cb.error.description.UTF8String); return 1; }
            unsigned char pixels[16*16*4];
            [target getBytes:pixels bytesPerRow:64 fromRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0];
            for(int p=0;p<256;p++) {
                int count=(draws-1-p)/256+1, last=p+(count-1)*256;
                int expected[4]={fetch_off?1:count,mode==2 && (last&1)?153:51,mode?last%251:17,255};
                for(int k=0;k<4;k++) if(abs((int)pixels[p*4+k]-expected[k])>1) {
                    fprintf(stderr,"mode=%s frame=%d pixel=%d channel=%d got=%u expected=%d\n",names[mode],frame,p,k,pixels[p*4+k],expected[k]); return 1;
                }
            }
            if(frame>=0) times[frame]=elapsed;
        }}
        qsort(times,frames,sizeof(double),compare);
        printf("PASS mode=%s draws=%d frames=%d pixels=256 median_record_cpu_ms=%.6f\n",names[mode],draws,frames,times[frames/2]); fflush(stdout);
    }
    return 0;
}}
