// Matched NMIntelFB/NVAccel contract. Version 2 adds exclusive frame leases and
// explicit completion, so a timed-out DMA cannot leave reusable/freeable pages.
#pragma once
#define NMINTELFB_FRAME_VERSION 2u
#define NMINTELFB_FRAME_ID "nmintelfb-frame-abi:2"
static const char nmintelfb_frame_build_id[] __attribute__((used)) = NMINTELFB_FRAME_ID;
#define NMINTELFB_FN_BEGIN "nmIntelFBFrameBegin"
#define NMINTELFB_FN_END   "nmIntelFBFrameEnd"

enum NMIntelFBFrameResult : unsigned int {
    NMINTELFB_CANCEL = 0,
    NMINTELFB_CPU_READY = 1,
    NMINTELFB_GPU_READY = 2,
    NMINTELFB_GPU_UNCERTAIN = 3,
    NMINTELFB_GPU_HELD = 4,
};

struct NMIntelFBFrame {
    unsigned int version, result;
    unsigned int width, height, pitch, mode;
    unsigned long long token, length, pageCount;
    void *kva;
    const unsigned long long *pages;
};
static_assert(sizeof(NMIntelFBFrame) == 64, "matched Intel panel frame ABI");
