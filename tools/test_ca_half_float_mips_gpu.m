// Reproduce CoreAnimation's mipmapped client-storage upload and verify its
// minified levels, extended-range samples and explicit mip ownership.
// clang -fobjc-arc -O2 tools/test_ca_half_float_mips_gpu.m -o half-mips \
//   -framework Foundation -framework Metal -framework IOKit
// half-mips /absolute/path/NVMTLDriver
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOKit/IOKitLib.h>
#import <objc/message.h>
#include <dlfcn.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
@interface NSObject (ClientStorageTexture)
- (id<MTLTexture>)newTiledTextureWithDescriptor:(MTLTextureDescriptor *)d
                                      offset:(NSUInteger)offset bytesPerRow:(NSUInteger)row;
@end
static void require(BOOL ok,const char *why){if(!ok){fprintf(stderr,"FAIL: %s\n",why);exit(2);}}
static unsigned check(id<MTLTexture> t,NSUInteger level,unsigned channels,float value[4]) {
    NSUInteger w=MAX((NSUInteger)1,t.width>>level),h=MAX((NSUInteger)1,t.height>>level);
    NSMutableData *data=[NSMutableData dataWithLength:w*h*channels*2];
    [t getBytes:data.mutableBytes bytesPerRow:w*channels*2 fromRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:level];
    const _Float16 *p=data.bytes;unsigned bad=0;
    for(NSUInteger y=0;y<h;y++)for(NSUInteger x=0;x<w;x++)for(unsigned c=0;c<channels;c++){
        // The 128px source is a 2x2 checker; every lower level averages to value.
        float expected=value[c];if(!level&&t.width==128)expected+=.125f*((x&1?1:-1)+(y&1?1:-1));
        float actual=(float)p[(y*w+x)*channels+c];if(!isfinite(actual)||fabsf(actual-expected)>.002f)bad++;
    }
    printf("fmt=%lu size=%lux%lu level=%lu first=%g expected=%g errors=%u\n",(unsigned long)t.pixelFormat,(unsigned long)t.width,(unsigned long)t.height,(unsigned long)level,(float)p[0],value[0],bad);
    return bad;
}
int main(int argc,char **argv){@autoreleasepool{
    require(argc==2,"driver path");setprogname("WindowServer");setenv("NVMTL_PROCESS_ENV","1",1);
    require(dlopen(argv[1],RTLD_NOW|RTLD_LOCAL)!=NULL,"load driver");
    io_service_t accelerator=IOServiceGetMatchingService(kIOMainPortDefault,IOServiceMatching("NVAccel"));
    id<MTLDevice> device=((id(*)(id,SEL,mach_port_t))objc_msgSend)([NSClassFromString(@"NVMTLDevice") alloc],NSSelectorFromString(@"initWithAcceleratorPort:"),accelerator);IOObjectRelease(accelerator);require(device!=nil,"device");
    const MTLPixelFormat formats[]={MTLPixelFormatR16Float,MTLPixelFormatRG16Float,MTLPixelFormatRGBA16Float};
    const unsigned components[]={1,2,4};unsigned failures=0;
    for(unsigned f=0;f<3;f++)for(unsigned odd=0;odd<2;odd++){@autoreleasepool{
        unsigned channels=components[f];NSUInteger w=odd?63:128,h=odd?31:128,row=(w*channels*2+63)&~(NSUInteger)63,offset=16;
        NSMutableData *data=[NSMutableData dataWithLength:offset+row*h];
        float values[4]={-.5f,.25f,1.5f,1.f};
        for(NSUInteger y=0;y<h;y++)for(NSUInteger x=0;x<w;x++)for(unsigned c=0;c<channels;c++){
            float v=values[c];if(!odd)v+=.125f*((x&1?1:-1)+(y&1?1:-1));
            *((_Float16 *)((uint8_t *)data.mutableBytes+offset+y*row)+x*channels+c)=(_Float16)v;
        }
        id<MTLBuffer> b=[device newBufferWithBytes:data.bytes length:data.length options:MTLResourceStorageModeShared];require(b!=nil,"buffer");
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:formats[f] width:w height:h mipmapped:YES];d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageShaderRead;
        id<MTLTexture> t=[(id)b newTiledTextureWithDescriptor:d offset:offset bytesPerRow:row];require(t!=nil,"tiled texture");
        for(NSUInteger l=0;l<t.mipmapLevelCount;l++)failures+=check(t,l,channels,values);
        // An application-authored higher mip ends implicit generation.
        NSUInteger mw=MAX((NSUInteger)1,w>>1),mh=MAX((NSUInteger)1,h>>1);NSMutableData *custom=[NSMutableData dataWithLength:mw*mh*channels*2];_Float16 *cp=custom.mutableBytes;
        float customValues[4]={.375f,.375f,.375f,.375f};for(NSUInteger i=0;i<mw*mh*channels;i++)cp[i]=(_Float16).375f;
        [t replaceRegion:MTLRegionMake2D(0,0,mw,mh) mipmapLevel:1 withBytes:custom.bytes bytesPerRow:mw*channels*2];
        [t replaceRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0 withBytes:(uint8_t *)data.bytes+offset bytesPerRow:row];
        failures+=check(t,1,channels,customValues);
    }}
    printf("half-float mip errors=%u\n",failures);return failures?1:0;
}}
