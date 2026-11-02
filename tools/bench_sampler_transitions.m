// Isolated GPU regression: app sampler -> constexpr sampler -> cached empty metadata.
// clang -fobjc-arc -O2 tools/bench_sampler_transitions.m -framework Foundation -framework Metal -framework IOKit -o bench_sampler_transitions
// bench_sampler_transitions /absolute/path/NVMTLDriver
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOKit/IOKitLib.h>
#include <dlfcn.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

@interface NSObject (NVMTLSamplerProbe)
- (instancetype)initWithAcceleratorPort:(mach_port_t)port;
@end
int main(int argc,char **argv) { @autoreleasepool {
    if(argc!=2 || !dlopen(argv[1],RTLD_NOW|RTLD_LOCAL)) return 2;
    Class cls=NSClassFromString(@"NVMTLDevice");
    io_service_t accel=IOServiceGetMatchingService(kIOMainPortDefault,IOServiceMatching("NVAccel"));
    id<MTLDevice> dev=[[cls alloc] initWithAcceleratorPort:accel];if(accel)IOObjectRelease(accel);
    if(!dev)return 2;
    setenv("NVMTL_NO_FBFETCH","1",1);
    NSString *src=@"#include <metal_stdlib>\nusing namespace metal;\n"
      "vertex float4 v(uint i [[vertex_id]]) { float2 p=float2((i<<1)&2,i&2); return float4(p*2-1,0,1); }\n"
      "fragment float4 app(texture2d<float> t [[texture(0)]], sampler s [[sampler(0)]]) { return t.sample(s,float2(0.5)); }\n"
      "fragment float4 fixed(texture2d<float> t [[texture(0)]]) { constexpr sampler s(coord::normalized,address::clamp_to_edge,filter::nearest); return t.sample(s,float2(0.25,0.5)); }\n";
    NSError *error=nil;id<MTLLibrary> lib=[dev newLibraryWithSource:src options:nil error:&error];
    if(!lib){fprintf(stderr,"library: %s\n",error.description.UTF8String);return 2;}
    MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];
    pd.vertexFunction=[lib newFunctionWithName:@"v"];pd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;
    id<MTLRenderPipelineState> pipelines[2];
    for(unsigned i=0;i<2;i++) {
        pd.fragmentFunction=[lib newFunctionWithName:i?@"fixed":@"app"];
        pipelines[i]=[dev newRenderPipelineStateWithDescriptor:pd error:&error];
        if(!pipelines[i]){fprintf(stderr,"pipeline: %s\n",error.description.UTF8String);return 2;}
    }
    MTLSamplerDescriptor *sd=[MTLSamplerDescriptor new];sd.minFilter=sd.magFilter=MTLSamplerMinMagFilterLinear;
    sd.sAddressMode=sd.tAddressMode=MTLSamplerAddressModeClampToEdge;
    id<MTLSamplerState> sampler=[dev newSamplerStateWithDescriptor:sd];
    MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:2 height:1 mipmapped:NO];
    td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageShaderRead;
    id<MTLTexture> source=[dev newTextureWithDescriptor:td];
    unsigned char texels[8]={255,0,0,255,0,0,255,255};
    if(!sampler || !source)return 2;
    [source replaceRegion:MTLRegionMake2D(0,0,2,1) mipmapLevel:0 withBytes:texels bytesPerRow:8];
    id<MTLCommandQueue> queue=[dev newCommandQueue];if(!queue)return 2;
    for(unsigned frame=0;frame<12;frame++) { @autoreleasepool {
        td.width=3;td.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
        id<MTLTexture> target=[dev newTextureWithDescriptor:td];if(!target)return 2;
        MTLRenderPassDescriptor *rd=[MTLRenderPassDescriptor renderPassDescriptor];
        rd.colorAttachments[0].texture=target;rd.colorAttachments[0].loadAction=MTLLoadActionClear;
        rd.colorAttachments[0].storeAction=MTLStoreActionStore;
        id<MTLCommandBuffer> cb=[queue commandBuffer];id<MTLRenderCommandEncoder> enc=[cb renderCommandEncoderWithDescriptor:rd];
        if(!enc)return 2;
        [enc setFragmentTexture:source atIndex:0];[enc setFragmentSamplerState:sampler atIndex:0];
        for(unsigned i=0;i<3;i++) {
            [enc setRenderPipelineState:pipelines[i==1]];
            [enc setScissorRect:(MTLScissorRect){i,0,1,1}];
            [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        }
        [enc endEncoding];[cb commit];[cb waitUntilCompleted];
        if(cb.status!=MTLCommandBufferStatusCompleted)return 1;
        unsigned char pixels[12];[target getBytes:pixels bytesPerRow:12 fromRegion:MTLRegionMake2D(0,0,3,1) mipmapLevel:0];
        int expected[12]={128,0,128,255,255,0,0,255,128,0,128,255};
        for(unsigned i=0;i<12;i++)if(abs(pixels[i]-expected[i])>1){
            fprintf(stderr,"frame=%u channel=%u got=%u expected=%d\n",frame,i,pixels[i],expected[i]);return 1;
        }
    }}
    unsigned drivers=0;
    for(uint32_t i=0;i<_dyld_image_count();i++) {
        const char *path=_dyld_get_image_name(i), *name=path?strrchr(path,'/'):NULL;
        if(name && !strcmp(name+1,"NVMTLDriver"))drivers++;
    }
    if(drivers!=1){fprintf(stderr,"isolation failed: %u NVMTLDriver images loaded\n",drivers);return 1;}
    printf("PASS: one plugin loaded; 12 recordings, linear app sampler -> constexpr nearest -> restored linear; all RGBA pixels matched on %s\n",dev.name.UTF8String);
}return 0;}
