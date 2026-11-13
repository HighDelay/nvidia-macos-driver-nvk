#include <metal_stdlib>
using namespace metal;

struct VOut {
    float4 pos [[position]];
    float4 col0 [[user(locn0)]];
    float4 col1 [[user(locn1)]];
    float4 uv0 [[user(locn2)]];
    float4 uv1 [[user(locn3)]];
    float4 uv2 [[user(locn4)]];
    float4 uv3 [[user(locn5)]];
    float fog [[user(locn6)]];
};
struct PSPar {
    float aref, usec, pad0, pad1;
    float4 proj;
    float4 c[32];
    float4 fog;
};
struct Group0 { constant PSPar* p [[id(2)]]; };
struct Group1 {
    texture2d<float> t0 [[id(0)]];
    texture2d<float> t1 [[id(1)]];
    sampler s0 [[id(4)]];
    sampler s1 [[id(5)]];
};
float2 tcoord(float4 q, float sel) {
    int m = int(sel) >> 4;
    return q.xy / select(select(1.0f, q.z, m == 1), q.w, m == 2);
}
fragment float4 fs(VOut i [[stage_in]], constant Group0& g0 [[buffer(0)]],
                   constant Group1& g1 [[buffer(1)]]) {
    constant PSPar& p = *g0.p;
    auto tex0 = g1.t0;
    auto tex1 = g1.t1;
    auto smp0 = g1.s0;
    auto smp1 = g1.s1;
    float4 a = tex0.sample(smp0, tcoord(i.uv0, p.proj[0]));
    float4 b = tex1.sample(smp1, tcoord(i.uv1, p.proj[1]));
    float4 tinted = a * i.col0;
    float3 rgb = b.rgb * tinted.www + tinted.rgb * 2.0f;
    return float4(mix(p.fog.rgb, rgb, mix(1.0f, saturate(i.fog), p.fog.a)), 1.0f);
}
