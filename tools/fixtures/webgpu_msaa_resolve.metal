#include <metal_stdlib>
using namespace metal;

vertex float4 msaa_fullscreen(uint v [[vertex_id]]) {
    float2 p = float2((v << 1) & 2, v & 2);
    return float4(p * 2.0f - 1.0f, .5f, 1.0f);
}
fragment float4 msaa_fill(uint sample [[sample_id]]) {
    return float4(float(sample + 1) * .125f, float(sample + 2) * .125f,
                  float(sample + 3) * .125f, 1.0f);
}
struct MSGroup { texture2d_ms<float> src [[id(0)]]; };
fragment float4 msaa_resolve(float4 pos [[position]], constant MSGroup& g [[buffer(0)]]) {
    uint2 c = uint2(pos.xy);
    return (g.src.read(c, 0) + g.src.read(c, 1) +
            g.src.read(c, 2) + g.src.read(c, 3)) * .25f;
}
fragment float4 msaa_query(constant MSGroup& g [[buffer(0)]]) {
    return float4(g.src.get_width(), g.src.get_height(), g.src.get_num_samples(), 1);
}
struct MipGroup { texture2d<float> src [[id(0)]]; };
fragment float4 mip_query(constant MipGroup& g [[buffer(0)]]) {
    return float4(g.src.get_width(2), g.src.get_height(2), g.src.get_num_mip_levels(), 1);
}
