/* Bounded sampler-array pixel checks. metal_test_lookup.c can select a private
 * NVMTL bundle; no installation or application settings are changed.
 * clang -fobjc-arc tools/test_sampler_array_gpu.m -framework Foundation \
 *   -framework Metal -o sampler-array-test
 */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdio.h>
#include <string.h>

int main(void) { @autoreleasepool {
    id<MTLDevice> device=MTLCreateSystemDefaultDevice();
    if (!device || ![NSStringFromClass([device class]) isEqual:@"NVMTLDevice"]) return 2;
    NSString *source=@"#include <metal_stdlib>\nusing namespace metal;\n"
        "vertex float4 tri(uint i [[vertex_id]]) {float2 p=float2((i<<1)&2,i&2);return float4(p*2-1,0,1);}\n"
        "fragment float4 scalar(texture2d<float> t [[texture(0)]],sampler s [[sampler(0)]]){return t.sample(s,float2(0.5));}\n"
        "fragment float4 single(texture2d<float> t [[texture(0)]],array<sampler,1> s [[sampler(0)]]){return t.sample(s[0],float2(0.5));}\n"
        "fragment float4 both(array<texture2d<float>,1> t [[texture(0)]],array<sampler,1> s [[sampler(0)]]){return t[0].sample(s[0],float2(0.5));}\n"
        "fragment float4 offset(array<texture2d<float>,1> t [[texture(3)]],array<sampler,1> s [[sampler(5)]]){return t[0].sample(s[0],float2(0.5));}\n"
        "fragment float4 second(texture2d<float> t [[texture(0)]],array<sampler,2> s [[sampler(0)]]){return t.sample(s[1],float2(0.5));}\n";
    NSError *error=nil;
    id<MTLLibrary> library=[device newLibraryWithSource:source options:nil error:&error];
    if (!library) { fprintf(stderr,"library: %s\n",error.description.UTF8String); return 3; }
    MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:2 height:2 mipmapped:NO];
    td.storageMode=MTLStorageModeShared; td.usage=MTLTextureUsageShaderRead;
    id<MTLTexture> input=[device newTextureWithDescriptor:td];
    const unsigned char colors[]={255,0,0,255, 0,255,0,255, 0,0,255,255, 255,255,255,255};
    [input replaceRegion:MTLRegionMake2D(0,0,2,2) mipmapLevel:0 withBytes:colors bytesPerRow:8];
    id<MTLCommandQueue> queue=[device newCommandQueue];
    unsigned passed=0, failed=0;
    for (NSString *name in @[@"scalar",@"single",@"both",@"offset",@"second"]) { @autoreleasepool {
        MTLFunctionDescriptor *fd=[MTLFunctionDescriptor new]; fd.name=name;
        id<MTLFunction> function=[library newFunctionWithDescriptor:fd error:&error];
        if (!function) { printf("FAIL %s: %s\n",name.UTF8String,error.description.UTF8String); failed++; continue; }
        MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new]; pd.vertexFunction=[library newFunctionWithName:@"tri"];
        pd.fragmentFunction=function; pd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;
        id<MTLRenderPipelineState> pipeline=[device newRenderPipelineStateWithDescriptor:pd error:&error];
        if (!pipeline) { printf("FAIL %s pipeline: %s\n",name.UTF8String,error.description.UTF8String); failed++; continue; }
        for (unsigned mode=0; mode<3; ++mode) { @autoreleasepool {
            MTLSamplerDescriptor *sd=[MTLSamplerDescriptor new]; sd.sAddressMode=sd.tAddressMode=MTLSamplerAddressModeClampToEdge;
            sd.minFilter=sd.magFilter=mode==1?MTLSamplerMinMagFilterLinear:MTLSamplerMinMagFilterNearest;
            sd.normalizedCoordinates=mode!=2;
            id<MTLSamplerState> sampler=[device newSamplerStateWithDescriptor:sd];
            MTLSamplerDescriptor *other=[MTLSamplerDescriptor new];other.minFilter=other.magFilter=MTLSamplerMinMagFilterLinear;
            id<MTLSamplerState> decoy=[device newSamplerStateWithDescriptor:other];
            td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:8 height:8 mipmapped:NO];
            td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageRenderTarget;
            id<MTLTexture> target=[device newTextureWithDescriptor:td];
            MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=target;
            pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
            id<MTLCommandBuffer> cb=[queue commandBuffer];id<MTLRenderCommandEncoder> enc=[cb renderCommandEncoderWithDescriptor:pass];
            [enc setRenderPipelineState:pipeline];[enc setFragmentTexture:input atIndex:0];[enc setFragmentTexture:input atIndex:3];
            [enc setFragmentSamplerState:decoy atIndex:0];[enc setFragmentSamplerState:sampler atIndex:[name isEqual:@"second"]?1:0];
            [enc setFragmentSamplerState:sampler atIndex:5];[enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
            [enc endEncoding];[cb commit];[cb waitUntilCompleted];
            unsigned char bytes[8*8*4];[target getBytes:bytes bytesPerRow:32 fromRegion:MTLRegionMake2D(0,0,8,8) mipmapLevel:0];
            const unsigned expected[3][4]={{255,255,255,255},{128,128,128,255},{255,0,0,255}};
            unsigned bad=0;for(unsigned i=0;i<sizeof bytes;++i)bad+=abs((int)bytes[i]-(int)expected[mode][i%4])>1;
            BOOL ok=!bad && !cb.error;
            printf("%s %s mode=%u rgba=%u,%u,%u,%u bad=%u error=%s\n",ok?"PASS":"FAIL",name.UTF8String,mode,bytes[0],bytes[1],bytes[2],bytes[3],bad,cb.error.description.UTF8String);
            passed+=ok;failed+=!ok;
        } }
    } }
    printf("passed=%u failed=%u\n",passed,failed);return failed?1:0;
} }
