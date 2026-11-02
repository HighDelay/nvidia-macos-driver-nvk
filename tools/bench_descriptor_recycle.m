// Pixel-verified descriptor-set reuse across command buffers on an isolated plugin; never installs it.
// Each frame is its own command buffer, with up to three in flight, so snapshot sets come from the sets earlier frames
// retired. A frame draws a resource-free pipeline first (the command buffer has no binding table yet), then textured
// draws whose texture alternates per draw, then draws whose colour comes from setFragmentBytes; each of those writes
// its own pixel cell. A set reused with a stale descriptor shows up as a wrong texture or colour in that draw's cell.
// clang -fobjc-arc -O2 tools/bench_descriptor_recycle.m -framework Foundation -framework Metal -framework IOKit -o bench_descriptor_recycle
// bench_descriptor_recycle /absolute/path/NVMTLDriver [frames=64] [draws=600]
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOKit/IOKitLib.h>
#include <dlfcn.h>
#include <time.h>
#include <stdio.h>
#include <stdlib.h>

@interface NSObject (NVMTLProbe)
- (instancetype)initWithAcceleratorPort:(mach_port_t)port;
@end
static double cpu_ms(void) { struct timespec t; clock_gettime(CLOCK_THREAD_CPUTIME_ID, &t); return t.tv_sec * 1000.0 + t.tv_nsec / 1e6; }
static int compare(const void *a, const void *b) { double x = *(const double *)a, y = *(const double *)b; return (x > y) - (x < y); }

static id<MTLTexture> solid(id<MTLDevice> dev, uint32_t rgba)
{
    MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:4 height:4 mipmapped:NO];
    td.usage = MTLTextureUsageShaderRead; td.storageMode = MTLStorageModeShared;
    id<MTLTexture> t = [dev newTextureWithDescriptor:td];
    uint32_t px[16]; for (int i = 0; i < 16; i++) px[i] = rgba;
    [t replaceRegion:MTLRegionMake2D(0, 0, 4, 4) mipmapLevel:0 withBytes:px bytesPerRow:16];
    return t;
}

int main(int argc, char **argv) { @autoreleasepool {
    if (argc < 2) return 2;
    int frames = argc > 2 ? atoi(argv[2]) : 64, draws = argc > 3 ? atoi(argv[3]) : 600;
    if (frames < 2 || frames > 4096 || draws < 3 || draws > 100000) return 2;
    if (!dlopen(argv[1], RTLD_NOW | RTLD_LOCAL)) { fprintf(stderr, "load: %s\n", dlerror()); return 2; }
    Class cls = NSClassFromString(@"NVMTLDevice");
    io_service_t accel = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("NVAccel"));
    id<MTLDevice> dev = [[cls alloc] initWithAcceleratorPort:accel]; if (accel) IOObjectRelease(accel);
    if (!dev) { fprintf(stderr, "No isolated NVMTL device\n"); return 2; }
    NSError *error = nil;
    NSString *src = @"#include <metal_stdlib>\nusing namespace metal;\n"
      "struct V { float4 p [[position]]; float2 uv; };\n"
      "vertex V v(uint i [[vertex_id]]) { float2 p=float2((i<<1)&2,i&2); V o; o.p=float4(p*2.0-1.0,0,1); o.uv=p; return o; }\n"
      "fragment float4 plain() { return float4(0,0,1,1); }\n"
      "fragment float4 tex(V in [[stage_in]], texture2d<float> t [[texture(0)]]) { constexpr sampler s(filter::nearest); return t.sample(s, in.uv); }\n"
      "fragment float4 bytes(constant float4 &c [[buffer(0)]]) { return c; }\n";
    id<MTLLibrary> lib = [dev newLibraryWithSource:src options:nil error:&error];
    if (!lib) { fprintf(stderr, "library: %s\n", error.description.UTF8String); return 2; }
    id<MTLRenderPipelineState> ps[3]; NSString *names[3] = { @"plain", @"tex", @"bytes" };
    for (int i = 0; i < 3; i++) {
        MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
        pd.vertexFunction = [lib newFunctionWithName:@"v"]; pd.fragmentFunction = [lib newFunctionWithName:names[i]];
        pd.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA8Unorm;
        ps[i] = [dev newRenderPipelineStateWithDescriptor:pd error:&error];
        if (!ps[i]) { fprintf(stderr, "pipeline %s: %s\n", names[i].UTF8String, error.description.UTF8String); return 2; }
    }
    id<MTLTexture> texs[2] = { solid(dev, 0xff0000ffu), solid(dev, 0xff00ff00u) };   // red, green (RGBA8 little-endian)
    const uint32_t texpx[2] = { 0xff0000ffu, 0xff00ff00u };
    id<MTLCommandQueue> queue = [dev newCommandQueue];
    // Three frames in flight, each with its own target, so sets retired by one frame are reused while later frames are
    // still queued. Every textured and every bytes draw writes its own 1x1 cell (cell = draw % 256), so a stale set
    // used by any draw shows up, not only one used by the last draw.
    enum { RING = 3 };
    id<MTLTexture> targets[RING]; id<MTLCommandBuffer> cbs[RING] = { nil }; int slotFrame[RING];
    MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:48 height:16 mipmapped:NO];
    td.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead; td.storageMode = MTLStorageModeShared;
    for (int k = 0; k < RING; k++) targets[k] = [dev newTextureWithDescriptor:td];
    printf("device: %s\nplugin: %s\n", dev.name.UTF8String, argv[1]);
    const int firstTex = (draws + 2) / 3, firstBytes = (2 * draws + 2) / 3;   // first i with i*3/draws == 1, == 2
    const int ntex = firstBytes - firstTex, nbytes = draws - firstBytes;
    double *times = calloc((size_t)frames, sizeof *times); __block int bad = 0;
    const uint32_t tex0 = texpx[0], tex1 = texpx[1];
    void (^verify)(id<MTLTexture>, int) = ^(id<MTLTexture> target, int frame) {
        uint32_t px[48 * 16];
        [target getBytes:px bytesPerRow:48 * 4 fromRegion:MTLRegionMake2D(0, 0, 48, 16) mipmapLevel:0];
        int wrong = 0, firstBad = -1;
        for (int y = 0; y < 16; y++) for (int x = 0; x < 48; x++) {
            uint32_t p = px[y * 48 + x], want;
            const int c = y * 16 + (x & 15);
            if (x < 16) want = 0xffff0000u;
            else if (x < 32) {
                int j = c < ntex ? c + ((ntex - 1 - c) / 256) * 256 : -1;   // the last textured draw in this cell
                want = j < 0 ? 0 : (((frame + j) & 1) ? tex1 : tex0);
            } else {
                int b = c < nbytes ? c + ((nbytes - 1 - c) / 256) * 256 : -1;
                if (b < 0) want = 0;
                else { uint32_t r = (uint32_t)((frame * 37 + b * 11) % 200 + 40); want = 0xff800000u | ((255u - r) << 8) | r; }
            }
            uint32_t d = 0; for (int k = 0; k < 32; k += 8) { int e = (int)((p >> k) & 0xff) - (int)((want >> k) & 0xff); if (abs(e) > d) d = (uint32_t)abs(e); }
            if (d > 1) { wrong++; if (firstBad < 0) firstBad = y * 48 + x; }
        }
        if (wrong) { bad++; if (bad <= 4) fprintf(stderr, "frame %d: %d wrong pixels (first at x=%d y=%d: %08x)\n", frame, wrong, firstBad % 48, firstBad / 48, px[firstBad]); }
    };
    for (int frame = 0; frame < frames; frame++) @autoreleasepool {
        const int slot = frame % RING;
        if (cbs[slot]) { [cbs[slot] waitUntilCompleted]; verify(targets[slot], slotFrame[slot]); cbs[slot] = nil; }
        MTLRenderPassDescriptor *rd = [MTLRenderPassDescriptor renderPassDescriptor];
        rd.colorAttachments[0].texture = targets[slot]; rd.colorAttachments[0].loadAction = MTLLoadActionClear;
        rd.colorAttachments[0].clearColor = MTLClearColorMake(0, 0, 0, 0); rd.colorAttachments[0].storeAction = MTLStoreActionStore;
        id<MTLCommandBuffer> cb = [queue commandBuffer]; id<MTLRenderCommandEncoder> enc = [cb renderCommandEncoderWithDescriptor:rd];
        double start = cpu_ms();
        for (int i = 0; i < draws; i++) {
            const int kind = i * 3 / draws;   // all resource-free draws first, then textured, then bytes
            [enc setRenderPipelineState:ps[kind]];
            if (kind == 0) [enc setViewport:(MTLViewport){ 0, 0, 16, 16, 0, 1 }];
            else {
                const int n = i - (kind == 1 ? firstTex : firstBytes), c = n % 256;
                [enc setViewport:(MTLViewport){ 16.0 * kind + (c & 15), (double)(c >> 4), 1, 1, 0, 1 }];
                if (kind == 1) [enc setFragmentTexture:texs[(frame + n) & 1] atIndex:0];
                else {
                    const float r = (float)((frame * 37 + n * 11) % 200 + 40) / 255.0f;
                    float col[4] = { r, 1.0f - r, 128.0f / 255.0f, 1.0f }; [enc setFragmentBytes:col length:sizeof col atIndex:0];
                }
            }
            [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        }
        times[frame] = cpu_ms() - start;
        [enc endEncoding]; [cb commit];
        cbs[slot] = cb; slotFrame[slot] = frame;
    }
    for (int k = 0; k < RING; k++) { int slot = (frames + k) % RING; if (cbs[slot]) { [cbs[slot] waitUntilCompleted]; verify(targets[slot], slotFrame[slot]); } }
    qsort(times + 1, (size_t)frames - 1, sizeof *times, compare);
    printf("%s frames=%d draws=%d bad_frames=%d median_record_cpu_ms=%.6f\n", bad ? "FAIL" : "PASS", frames, draws, bad, times[1 + (frames - 1) / 2]);
    return bad ? 1 : 0;
}}
