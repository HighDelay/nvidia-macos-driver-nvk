/* Kernel-only handoff between NVRM, NVAccel, NVRMAGDC and the optional AMD panel. */
#pragma once

constexpr unsigned kNvrmGopSlot = 4;
constexpr unsigned kNvrmGopMask = 1u << kNvrmGopSlot;
constexpr unsigned long long kNvrmGopController = 0x4e4d474f00000001ull;

#define NVRM_GOP_CLAIM "NMGopFBClaim"
#define NVRM_GOP_PANEL "NMGopFB"
#define NVRM_GOP_PIPE "NMGopFBPipeIndex"
#define NVRM_GOP_AGDC "NMGopFBAGDCReady"
#define NVRM_GOP_ATTACH "NMGopFBAttach"
#define NVRM_GOP_FINISH "NMGopFBFinish"
#define NVRM_GOP_COPY "NMGopFBCopy"
#define NVRM_GOP_RESOURCE "NMGopFBResource"
#define NVRM_HEADLESS_RESOURCES "NVRMHeadlessResources"

struct NvrmGopCopy {
    const volatile unsigned *pixels;
    unsigned width, height, pitch, format;
};

#ifdef KERNEL
#include <pexpert/pexpert.h>

static inline bool nvrm_gopfb_enabled()
{
    int level = 0, fb = 0, accel = 0;
    char flag[8];
    return PE_parse_boot_argn("nmhybrid", &level, sizeof level) && level == 1 &&
           PE_parse_boot_argn("nvfb", &fb, sizeof fb) && fb &&
           PE_parse_boot_argn("nvaccel", &accel, sizeof accel) && accel &&
           !PE_parse_boot_argn("-nmgopfboff", flag, sizeof flag) &&
           !PE_parse_boot_argn("-nvrmnogo", flag, sizeof flag);
}
#endif
