// NMIntelFB: framebuffer for the built-in panel of laptops whose panel is wired to an Intel GPU macOS has no driver for
// (display versions 12-20: Tiger Lake, Rocket Lake, Alder Lake, Raptor Lake, Meteor Lake, Arrow Lake, Lunar Lake).
//
// Phase 1 takes over the mode the firmware already lit: it finds the pipe whose primary plane is scanning out, checks
// that the plane is linear 32-bit XRGB, and publishes that surface (through the graphics aperture, BAR2 + the plane's GGTT
// offset) as an IOFramebuffer with the panel's real size and refresh. Nothing is written to the display engine, so a
// laptop it does not understand keeps exactly the screen it had: start() fails, every reason is logged, and the firmware
// framebuffer (IONDRVFramebuffer) takes the device as before.
//
// Register layout: Linux i915/xe display headers (MIT). Plane 1 of pipe P: PLANE_CTL 0x70180 + P*0x1000, STRIDE +0x08,
// SIZE +0x10, SURF +0x1C. Transcoder T timings at 0x60000 + T*0x1000, TRANSCONF at 0x70008 + P*0x1000, PIPESRC 0x6001C.

#include <IOKit/IOLib.h>
#include <IOKit/pci/IOPCIDevice.h>
#include <IOKit/graphics/IOFramebuffer.h>
#include <IOKit/graphics/IODisplay.h>
#include <IOKit/ndrvsupport/IOMacOSVideo.h>
#include <libkern/OSAtomic.h>
#include "nvrm_vram_abi.h"
#include "panel_policy.h"
#include "panel_scanout.h"
#include "panel_bridge_abi.h"
// Core panel bridge adapted from kitsuneislife/nmx-intel, b4bb3a0 (series 1-16, 21).
// Keep the driver's current NVKMS and RM-handle ABI, pinned to NVIDIA 620.70.06.
extern "C" {
#include "nvkms-kapi.h"
}
#include "../NVRM/agdc/AppleGraphicsDeviceControl_re.h"
#include "../NVRM/agdc/nvrm-agdc-k5.h"
#include "../NVRM/nvrm_gpuva_abi.h"
#include <IOKit/IOBufferMemoryDescriptor.h>
#include <IOKit/IOTimerEventSource.h>
#include <IOKit/IOWorkLoop.h>
#include <kern/thread_call.h>
#include <kern/sched_prim.h>
#include <sys/sysctl.h>
#include <sys/errno.h>
extern "C" int sysctlbyname(const char *, void *, size_t *, void *, size_t);
#include <libkern/version.h>

#define IFBLOG(fmt, ...) IOLog("NMIntelFB: " fmt "\n", ##__VA_ARGS__)

// nmx-intel: a trail of the steps in NVRAM ("nullmoth-intelfb-trail"), written and synced at each step, so a boot that
// freezes before the log is saved still shows the last step reached (`nvram nullmoth-intelfb-trail`). And a boot guard:
// "nullmoth-intelfb-attaching" is set while start() runs and removed when it returns; found at probe, the last start
// never returned, so this boot leaves the panel to the firmware framebuffer. To try again: sudo nvram -d
// nullmoth-intelfb-attaching
#include <IOKit/IONVRAM.h>
static IODTNVRAM *ifb_nvram()
{
    IORegistryEntry *e = IORegistryEntry::fromPath("/options", gIODTPlane);
    IODTNVRAM *n = OSDynamicCast(IODTNVRAM, e);
    if (n == nullptr && e != nullptr) e->release();
    return n;
}
static void ifb_nv_set(const char *key, const char *val)
{
    IODTNVRAM *n = ifb_nvram();
    if (!n) return;
    const OSSymbol *k = OSSymbol::withCString(key);
    if (k) {
        if (val) { if (OSData *d = OSData::withBytes(val, (unsigned)strlen(val))) { n->setProperty(k, d); d->release(); } }
        else n->removeProperty(k);
        k->release();
    }
    n->sync();
    n->release();
}
static bool ifb_nv_has(const char *key)
{
    IODTNVRAM *n = ifb_nvram();
    if (!n) return false;
    OSObject *o = n->copyProperty(key);
    bool has = o != nullptr;
    if (o) o->release();
    n->release();
    return has;
}
static IOLock *gIfbLock;
static volatile SInt64 gIfbFlips, gIfbLate;
static void ifb_trail(const char *step)
{
    // Persist only the most recent setup stage. No per-frame/heartbeat NVRAM I/O.
    IFBLOG("stage: %s", step);
    ifb_nv_set("nullmoth-intelfb-trail", step);
}

static uint32_t ifb_knob(const char *key, uint32_t def)
{
    IODTNVRAM *n = ifb_nvram();
    if (!n) return def;
    uint32_t v = def;
    OSObject *o = n->copyProperty(key);
    if (OSData *d = OSDynamicCast(OSData, o))
        v = nmintelfb::decimalKnob(d->getBytesNoCopy(), d->getLength(), def);
    OSSafeReleaseNULL(o);
    n->release();
    return v;
}

static bool ifb_nvrm_guard_off()
{
    IODTNVRAM *n = ifb_nvram();
    if (!n) return false;
    OSObject *remove = n->copyProperty("nullmoth-remove");
    OSObject *value = n->copyProperty("nullmoth-nv-starts");
    OSData *data = OSDynamicCast(OSData, value);
    const unsigned starts = data && data->getLength() ?
        *static_cast<const unsigned char *>(data->getBytesNoCopy()) : 0;
    const bool removing = remove != nullptr;
    OSSafeReleaseNULL(remove);
    OSSafeReleaseNULL(value);
    n->release();
    bool attached = false;
    if (starts >= 2 && !removing) {
        if (IORegistryIterator *it = IORegistryIterator::iterateOver(gIOServicePlane, kIORegistryIterateRecursively)) {
            while (IORegistryEntry *e = it->getNextObject())
                if (!strcmp(e->getMetaClass()->getClassName(), "NVRM")) { attached = true; break; }
            it->release();
        }
    }
    return nmintelfb::nvrmGuardOff(removing, starts, attached);
}

// kmutil places a kext in a kernel collection only with a _kmod_info symbol (Xcode generates it; this build does not)
#include <mach/kmod.h>
extern "C" kern_return_t _start(kmod_info_t *ki, void *d); extern "C" kern_return_t _stop(kmod_info_t *ki, void *d);
#ifdef NMINTELFB_VIRTUAL
extern "C" { KMOD_EXPLICIT_DECL(com.nullmoth.NMIntelFBVirtual, "0.2", _start, _stop) }
#else
extern "C" { KMOD_EXPLICIT_DECL(com.nullmoth.NMIntelFB, "0.2", _start, _stop) }
#endif
extern "C" kern_return_t _start(kmod_info_t *, void *) { gIfbLock = IOLockAlloc(); return gIfbLock ? KERN_SUCCESS : KERN_FAILURE; }
extern "C" kern_return_t _stop(kmod_info_t *, void *) { IOLockFree(gIfbLock); gIfbLock = nullptr; return KERN_SUCCESS; }

namespace {
// display version by PCI device ID, from Linux include/drm/intel/pciids.h (MIT); 0 = not ours
struct Id { uint16_t id; uint8_t ver; };
const Id kIds[] = {
#include "display_ids.inc"
};

uint8_t displayVersion(uint16_t dev)
{
    for (const Id &e : kIds) if (e.id == dev) return e.ver;
    return 0;
}

constexpr uint32_t PLANE_CTL(int p)    { return 0x70180u + (uint32_t)p * 0x1000u; }
constexpr uint32_t PLANE_STRIDE(int p) { return 0x70188u + (uint32_t)p * 0x1000u; }
constexpr uint32_t PLANE_SIZE(int p)   { return 0x70190u + (uint32_t)p * 0x1000u; }
constexpr uint32_t PLANE_SURF(int p)   { return 0x7019Cu + (uint32_t)p * 0x1000u; }
constexpr uint32_t TRANSCONF(int p)    { return 0x70008u + (uint32_t)p * 0x1000u; }
constexpr uint32_t HTOTAL(int t)       { return 0x60000u + (uint32_t)t * 0x1000u; }
constexpr uint32_t VTOTAL(int t)       { return 0x6000Cu + (uint32_t)t * 0x1000u; }
constexpr uint32_t HBLANK(int t)       { return 0x60004u + (uint32_t)t * 0x1000u; }
constexpr uint32_t HSYNC(int t)        { return 0x60008u + (uint32_t)t * 0x1000u; }
constexpr uint32_t VBLANK(int t)       { return 0x60010u + (uint32_t)t * 0x1000u; }
constexpr uint32_t VSYNC(int t)        { return 0x60014u + (uint32_t)t * 0x1000u; }
// one count per vblank (Linux PIPE_FRMCOUNT_G4X, 0x70040 + pipe): counting it is how the real refresh is known without
// reading the PLL and M/N values back
constexpr uint32_t FRMCOUNT(int p)     { return 0x70040u + (uint32_t)p * 0x1000u; }

// PLANE_CTL (display 11+): enable bit 31, format bits 27:23 (8 = XRGB 8:8:8:8, Linux PLANE_CTL_FORMAT_XRGB_8888 on
// ICL+), tiling bits 12:10 (0 = linear)
constexpr uint32_t kEnable = 1u << 31;
// the two power states NVRMFB registers: off, and on + usable
IOPMPowerState kPowerStates[2] = {
    { kIOPMPowerStateVersion1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 },
    { kIOPMPowerStateVersion1, kIOPMDeviceUsable, kIOPMPowerOn, kIOPMPowerOn, 0, 0, 0, 0, 0, 0, 0, 0 },
};
constexpr uint32_t fmtOf(uint32_t ctl)  { return (ctl >> 23) & 0x1F; }
constexpr uint32_t tileOf(uint32_t ctl) { return (ctl >> 10) & 0x7; }
}

// Panel brightness through the south display PWM (PCH display versions 12-20: CNP, ICP, TGP, ADP, MTP).
// Registers from Linux intel_backlight_regs.h (MIT): BXT_BLC_PWM_CTL 0xC8250 (bit 31 enable, bit 29 polarity),
// BXT_BLC_PWM_FREQ 0xC8254 (period in raw-clock ticks), BXT_BLC_PWM_DUTY 0xC8258. The method (keep the period and
// polarity the firmware programmed, scale only the duty, 64-bit product so long periods do not overflow, read the
// duty back) follows WhateverXe's XELP::PanelPWM, BSD-3-Clause (WhateverXe by b00t0x, derived from WhateverGreen, Copyright (c) 2018 vit9696); see NOTICE. Only a PWM the
// firmware already enabled is used: the period is never invented, so a panel lit another way keeps its brightness
// and gets no slider.
static const uint32_t kBlcPwmCtl = 0xC8250, kBlcPwmFreq = 0xC8254, kBlcPwmDuty = 0xC8258;
static const uint32_t kBlcPwmEnable = 1u << 31;
static const SInt32 kIntelMinBacklight = 5;   // never 0: some panels switch the backlight off completely
class NMIntelFB;
class NMIntelBacklight : public IODisplayParameterHandler {
    OSDeclareDefaultStructors(NMIntelBacklight)
    NMIntelFB *fFB = nullptr;
    thread_call_t fApply = nullptr;
    volatile SInt32 fPending = -1;
    SInt32 fLevel = 100;
    static void applyFire(thread_call_param_t me, thread_call_param_t);
public:
    static NMIntelBacklight *withFB(NMIntelFB *fb, SInt32 level);
    bool setDisplay(IODisplay *display) override;
    bool doIntegerSet(OSDictionary *params, const OSSymbol *paramName, UInt32 value) override;
    bool doDataSet(const OSSymbol *, OSData *) override { return false; }
    bool doUpdate() override { return true; }
    void free() override;
};

class NMIntelFB : public IOFramebuffer {
    OSDeclareDefaultStructors(NMIntelFB)
    IOPCIDevice *fPCI = nullptr;
    IOMemoryMap *fMMIO = nullptr;
    IOMemoryMap *fSurfMap = nullptr;   // write-combined CPU view of the scan-out surface (NVIDIA frames land here)
    // WindowServer paces an accelerated display on its VBL interrupt: the NVIDIA display pipe completes each frame on it.
    // Without one the first frame never completed and WindowServer stalled every display (in testing, the
    // virtual panel froze the Mac). A 60 Hz timer is the VBL, as NVRMFB's fallback is.
    struct Irq { IOFBInterruptProc proc = nullptr; OSObject *target = nullptr; void *ref = nullptr; };
    Irq fVbl, fConnect;
    // intelfb-0007: the VBL runs on a kernel thread of its own, and only while IOFramebuffer has the VBL interrupt
    // enabled (setInterruptState). As a thread_call it ran in the HIGH group, which has one thread: the second VBL
    // callback blocked there (called before open() enabled it) and every HIGH thread_call in the kernel stopped behind
    // it (RTX-5: vbl=2, the heartbeat stopped at 21 s, then the Mac froze)
    uint64_t fVblNext = 0, fVblCalls = 0;
    bool fStopping = false, fVblOn = false, fVblRunning = false;
    bool fOwnGroup = false;   // intelfb-0009: own IOFBDependentID, index 4 for NVAccel until open(), 0 for IOGraphics after
    // intelfb-0012: the VBL fires whenever it is registered, as NVRMFB's does (its setInterruptState is a no-op).
    // IOGraphics treats a registered VBL as enabled and never switched it on, and NVAccel's registration switched it
    // off (RTX-10 trail "is vbl-off"): vbl=0 in every test, and the accelerated display pipe got no vsync.
    bool fVblAlways = true;
    // intelfb-0011: as head 0 this panel is NVAccel's primary framebuffer, which NVAccel asks for RM handles and VRAM
    // (NVRMFB answers on a machine with an NVIDIA display; here no NVRMFB starts). An NVKMS device of our own answers.
    struct NvKmsKapiFunctionsTable *fKms = nullptr;
    struct NvKmsKapiDevice *fDev = nullptr;
    NvU64 (*fPhysForVa)(const void *) = nullptr;
    uint64_t fVramBudget = 0, fVramBytes = 0, fVramCookie = 0;
    unsigned fVramLogged = 0;
    IOLock *fVramLock = nullptr;
    bool fKapiExposed = false;
    NVRMVramRequest fScanRes = {};   // intelfb-0016: the VRAM block that stands for this panel's scanout
    IOLock *fRamLock = nullptr;
    nmintelfb::FrameGate fFrameGate;
    IOBufferMemoryDescriptor *fRam[2] = {};
    IOMemoryMap *fRamMap[2] = {};
    unsigned long long *fRamPages[2] = {}, *fOldPte[2] = {};
    uint32_t fRamGgtt[2] = {}, fSurfLow = 0;
    unsigned fRamCount = 0, fFront = 0, fLeaseBuffer = 0;
    int fRamMode = 0;
    bool fRamMapped[2] = {}, fQuarantineRetained = false;
    bool fMatchedConsumer = false;
    SInt64 fRamFlips = 0, fRamLate = 0;
    int ramSetLocked(int);
    bool ramMap(unsigned);
    bool ramPtes(unsigned, bool);
    bool ramHardwareEligible() const;
    bool flipLatched(uint32_t);
    void quarantineRam();
    void freeRam();
    IOReturn beginFrame(NMIntelFBFrame *);
    IOReturn endFrame(const NMIntelFBFrame *);
public:
    int ramSet(int);
    int ramMode();
    bool quarantined() const { return __atomic_load_n(&fQuarantineRetained, __ATOMIC_ACQUIRE); }
private:
    bool kapiInit(IOService *nub);
    void kapiFree();
    IOReturn vramGrant(NVRMVramRequest *r);
    IOReturn vramRelease(NVRMVramRequest *r);
    IOLock *fWorkerLock = nullptr;
    IOLock *fIrqLock = nullptr;
    unsigned fVblInFlight = 0;
    thread_t fVblThread = nullptr;
    bool fGuardRunning = false;
    static void vblThread(void *, wait_result_t);
    static void guardThread(void *, wait_result_t);
    void activateBridge();
    bool armVbl();
    void drainWorkers();
#ifdef NMINTELFB_VIRTUAL
    IOBufferMemoryDescriptor *fVBuf = nullptr;
    IOTimerEventSource *fTimer = nullptr;
    unsigned fSamples = 0;
    // 64 pixels across the buffer: how many are non-black and how many differ from the first, published in the registry
public:
    // 64 pixels across the buffer, read on demand through sysctl debug.nmintelfb_sample (the registry property the
    // timer set never showed up)
    void sampleInto(char *b, size_t n) {
        if (!fSurfMap) { snprintf(b, n, "no surface"); return; }
        const volatile uint32_t *px = (const volatile uint32_t *)fSurfMap->getVirtualAddress();
        unsigned nz = 0, varied = 0; uint32_t first = px[0] & 0xFFFFFF;
        for (unsigned i = 0; i < 8; i++) for (unsigned j = 0; j < 8; j++) {
            uint32_t v = px[(fHeight * (2 * i + 1) / 16) * (fRowBytes / 4) + fWidth * (2 * j + 1) / 16] & 0xFFFFFF;
            if (v) nz++; if (v != first) varied++; }
        snprintf(b, n, "sample %u: nonzero %u/64 varied %u/64 first 0x%06x", ++fSamples, nz, varied, first);
    }
    void sample() { char b[96]; sampleInto(b, sizeof b); setProperty("NMIntelFBSample", b); }
private:
#endif   // write-combined CPU view of the scan-out surface (NVIDIA frames land here)
    volatile uint8_t *fRegs = nullptr;
    IOPhysicalAddress fApertureBase = 0;
    IOPhysicalLength fApertureLen = 0;
    uint32_t fWidth = 0, fHeight = 0, fRowBytes = 0, fSurf = 0, fRefresh16 = 60 << 16;
    uint64_t fFrameNs = 16666667ull;   // vblank period; measured from the pipe's frame counter on real hardware
    // transcoder timing (active, total, sync start/end) for getTimingInfoForDisplayMode
    uint32_t fHT = 0, fVT = 0, fHSS = 0, fHSE = 0, fVSS = 0, fVSE = 0;
    uint64_t fPclk = 0;
    int fPipe = -1;
    uint8_t fVer = 0;

    uint32_t rd(uint32_t off) const { return *(volatile uint32_t *)(fRegs + off); }
    void wr(uint32_t off, uint32_t v) const { *(volatile uint32_t *)(fRegs + off) = v; }
    bool adoptFirmwareMode();
    bool fPwmInverted = false;
    uint32_t fPwmPeriod = 0;                     // firmware's PWM period; 0 = no brightness control
    NMIntelBacklight *fBacklight = nullptr;
    void publishBacklight();
public:
    bool setPanelPercent(uint32_t percent);      // from NMIntelBacklight's thread call; false if the write did not stick
private:

public:
    IOService *probe(IOService *provider, SInt32 *score) override;
    bool init(OSDictionary *properties = nullptr) override;
    void free() override;
    bool start(IOService *provider) override;
    bool startReal(IOService *provider);
    void stop(IOService *provider) override;

    IOReturn enableController() override { return kIOReturnSuccess; }
    // intelfb-0009: IOGraphics reads IOFBDependentIndex only in open() (controller->fFbs[index] = this) and walks fFbs[]
    // from 0 up to the first NULL; kIOFBWSStartAttribute dereferences fFbs[0]. Alone in its group at index 4, fFbs[0] is
    // NULL (the RTX-2..7 freezes). NVAccel took its head number from the same property at publish time, which is over by
    // now, so the panel becomes index 0 of its own controller here.
    IOReturn open() override {
        if (fOwnGroup) { setProperty("IOFBDependentIndex", 0ull, 32); }
        return IOFramebuffer::open();
    }
    bool isConsoleDevice() override { return true; }
    IODeviceMemory *getApertureRange(IOPixelAperture aperture) override;
    const char *getPixelFormats() override { return IO32BitDirectPixels "\0"; }
    IOItemCount getDisplayModeCount() override { return 1; }
    IOReturn getDisplayModes(IODisplayModeID *modes) override { modes[0] = 1; return kIOReturnSuccess; }
    IOReturn getInformationForDisplayMode(IODisplayModeID mode, IODisplayModeInformation *info) override;
    UInt64 getPixelFormatsForDisplayMode(IODisplayModeID, IOIndex) override { return 0; }
    IOReturn getPixelInformation(IODisplayModeID mode, IOIndex depth, IOPixelAperture aperture, IOPixelInformation *pi) override;
    IOReturn getCurrentDisplayMode(IODisplayModeID *mode, IOIndex *depth) override { *mode = 1; *depth = 0; return kIOReturnSuccess; }
    IOReturn getStartupDisplayMode(IODisplayModeID *mode, IOIndex *depth) override { return getCurrentDisplayMode(mode, depth); }
    // WindowServer showed the panel at 0.00 Hz without this (virtual panel)
    IOReturn getTimingInfoForDisplayMode(IODisplayModeID mode, IOTimingInformation *info) override {
        if (mode != 1 || !info) return kIOReturnUnsupportedMode;
        bzero(info, sizeof(*info));
        info->appleTimingID = kIOTimingIDInvalid;
        if (!fPclk || fHT <= fWidth || fVT <= fHeight) return kIOReturnSuccess;
        IODetailedTimingInformationV2 &d = info->detailedInfo.v2;
        d.pixelClock = d.minPixelClock = d.maxPixelClock = fPclk;
        d.horizontalActive = fWidth; d.horizontalBlanking = fHT - fWidth;
        d.horizontalSyncOffset = fHSS > fWidth ? fHSS - fWidth : 0; d.horizontalSyncPulseWidth = fHSE > fHSS ? fHSE - fHSS : 0;
        d.verticalActive = fHeight; d.verticalBlanking = fVT - fHeight;
        d.verticalSyncOffset = fVSS > fHeight ? fVSS - fHeight : 0; d.verticalSyncPulseWidth = fVSE > fVSS ? fVSE - fVSS : 0;
        info->flags = kIODetailedTimingValid;
        return kIOReturnSuccess;
    }
    IOReturn setDisplayMode(IODisplayModeID mode, IOIndex depth) override { return (mode == 1 && depth == 0) ? kIOReturnSuccess : kIOReturnUnsupported; }
    IOItemCount getConnectionCount() override { return 1; }
    IOReturn callPlatformFunction(const OSSymbol *fn, bool wait, void *p1, void *p2, void *p3, void *p4) override;
    IOReturn registerForInterruptType(IOSelect, IOFBInterruptProc, OSObject *, void *, void **) override;
    IOReturn unregisterInterrupt(void *) override;
    IOReturn setInterruptState(void *, UInt32) override;
    IOReturn setGammaTable(UInt32, UInt32, UInt32, void *) override { return kIOReturnSuccess; }
    IOReturn setGammaTable(UInt32, UInt32, UInt32, void *, bool) override { return kIOReturnSuccess; }
    IOReturn setCLUTWithEntries(IOColorEntry *, UInt32, UInt32, IOOptionBits) override { return kIOReturnSuccess; }
    IOReturn setApertureEnable(IOPixelAperture, IOOptionBits) override { return kIOReturnSuccess; }
    IOReturn setStartupDisplayMode(IODisplayModeID, IOIndex) override { return kIOReturnSuccess; }
    // Power and connection handling copied from NVRMFB, which shares this controller (same IOFBDependentID). Left to
    // IOFramebuffer's base class, a power change queues on the shared controller thread and waits for an acknowledge
    // (IOFramebuffer::setPowerState returns a 45 s timeout, checkPowerWork -> setAttribute(kIOPowerStateAttribute));
    // NVRMFB acknowledges at once and answers the power attributes itself. With NMIntelFB under NVRM the
    // first WindowServer restart froze every display.
    IOReturn setPowerState(unsigned long ordinal, IOService *) override {
        handleEvent(ordinal ? kIOFBNotifyDidPowerOn : kIOFBNotifyWillPowerOff);
        return kIOPMAckImplied;
    }
    IOReturn setAttribute(IOSelect attribute, uintptr_t value) override {

        if (attribute == kIOWindowServerActiveAttribute) return kIOReturnSuccess;
        if (attribute == kIOPowerAttribute) {
            if (value) { IOReturn r = IOFramebuffer::setAttribute(attribute, value); handleEvent(kIOFBNotifyDidPowerOn);
                         return r == kIOReturnUnsupported ? kIOReturnSuccess : r; }
            handleEvent(kIOFBNotifyWillPowerOff);
            IOReturn r = IOFramebuffer::setAttribute(attribute, value);
            return r == kIOReturnUnsupported ? kIOReturnSuccess : r;
        }
        return IOFramebuffer::setAttribute(attribute, value);
    }
    IOReturn setAttributeForConnection(IOIndex idx, IOSelect attribute, uintptr_t value) override {

        switch (attribute) {
        case kConnectionPower: case kConnectionColorModesSupported: case kConnectionColorDepthsSupported:
        case kConnectionControllerColorDepth: case kConnectionControllerDepthsSupported: case kConnectionColorMode:
        case kConnectionDisplayFlags: case kConnectionFlags:
            return kIOReturnSuccess;
        default: return IOFramebuffer::setAttributeForConnection(idx, attribute, value);
        }
    }
    IOReturn connectFlags(IOIndex, IODisplayModeID, IOOptionBits *flags) override {
        if (flags) *flags = kDisplayModeValidFlag | kDisplayModeSafeFlag | kDisplayModeDefaultFlag;
        return kIOReturnSuccess;
    }
    IODeviceMemory *getVRAMRange() override { return getApertureRange(kIOFBSystemAperture); }
    IOReturn getAttribute(IOSelect attribute, uintptr_t *value) override {

        if (attribute == kIOWindowServerActiveAttribute) { if (value) *value = 1; return kIOReturnSuccess; }
        return IOFramebuffer::getAttribute(attribute, value);
    }
    IOReturn getAttributeForConnection(IOIndex idx, IOSelect attr, uintptr_t *value) override;
    IOReturn getAppleSense(IOIndex connectIndex, UInt32 *senseType, UInt32 *primary, UInt32 *extended, UInt32 *displayType) override;
};

OSDefineMetaClassAndStructors(NMIntelFB, IOFramebuffer)

bool NMIntelFB::init(OSDictionary *properties)
{
    if (!IOFramebuffer::init(properties)) return false;
    fWorkerLock = IOLockAlloc();
    fIrqLock = IOLockAlloc();
    fVramLock = IOLockAlloc();
    fRamLock = IOLockAlloc();
    return fWorkerLock && fIrqLock && fVramLock && fRamLock;
}

bool NMIntelFB::armVbl()
{
    IOLockLock(fWorkerLock);
    if (fStopping || fVblRunning) { IOLockUnlock(fWorkerLock); return false; }
    fVblRunning = true;
    retain();
    thread_t t = nullptr;
    const bool ok = kernel_thread_start(vblThread, this, &t) == KERN_SUCCESS;
    if (ok) { __atomic_store_n(&fVblThread, t, __ATOMIC_RELEASE); thread_deallocate(t); }
    else { fVblRunning = false; release(); }
    IOLockUnlock(fWorkerLock);
    return ok;
}

void NMIntelFB::vblThread(void *p, wait_result_t)
{
    NMIntelFB *me = static_cast<NMIntelFB *>(p);
    uint64_t period;
    nanoseconds_to_absolutetime(me->fFrameNs, &period);
    uint64_t next = mach_absolute_time() + period;
    IOLockLock(me->fWorkerLock);
    while (!me->fStopping) {
        IOLockSleepDeadline(me->fWorkerLock, &me->fVblNext, next, THREAD_UNINT);
        if (me->fStopping) break;
        if (mach_absolute_time() < next) continue;
        IOLockUnlock(me->fWorkerLock);
        IOLockLock(me->fIrqLock);
        const Irq irq = me->fVblOn ? me->fVbl : Irq{};
        if (irq.proc) {
            if (irq.target) irq.target->retain();
            ++me->fVblInFlight;
        }
        IOLockUnlock(me->fIrqLock);
        if (irq.proc) {
            irq.proc(irq.target, irq.ref);
            if (irq.target) irq.target->release();
            IOLockLock(me->fIrqLock);
            --me->fVblInFlight;
            IOLockWakeup(me->fIrqLock, &me->fVblInFlight, false);
            IOLockUnlock(me->fIrqLock);
        }
        const uint64_t now = mach_absolute_time();
        next += period;
        if (next <= now) next = now + period;
        IOLockLock(me->fWorkerLock);
    }
    __atomic_store_n(&me->fVblThread, nullptr, __ATOMIC_RELEASE);
    me->fVblRunning = false;
    IOLockWakeup(me->fWorkerLock, &me->fVblNext, false);
    IOLockUnlock(me->fWorkerLock);
    me->release();
    thread_terminate(current_thread());
}

void NMIntelFB::guardThread(void *p, wait_result_t)
{
    NMIntelFB *me = static_cast<NMIntelFB *>(p);
    me->activateBridge();
    uint64_t deadline;
    clock_interval_to_deadline(120, kSecondScale, &deadline);
    IOLockLock(me->fWorkerLock);
    while (!me->fStopping && mach_absolute_time() < deadline)
        IOLockSleepDeadline(me->fWorkerLock, &me->fVblNext, deadline, THREAD_UNINT);
    const bool alive = !me->fStopping;
    IOLockUnlock(me->fWorkerLock);
    if (alive) {
        ifb_trail("alive");
        ifb_nv_set("nullmoth-intelfb-attaching", nullptr);
    }
    IOLockLock(me->fWorkerLock);
    me->fGuardRunning = false;
    IOLockWakeup(me->fWorkerLock, &me->fVblNext, false);
    IOLockUnlock(me->fWorkerLock);
    me->release();
    thread_terminate(current_thread());
}

void NMIntelFB::activateBridge()
{
    // Same control order as NVRM's auto-go, outside framebuffer/worker locks.
    // Only an explicitly installed, successfully started NVIDIA-parent panel
    // reaches this path. Fast-copy knobs remain at their default zero.
    IOService *provider = getProvider();
    if (!provider || !provider->getProperty("nm-intel-panel") || !ifb_knob("nullmoth-intelfb-auto", 1)) return;
    const auto read = [](const char *name, int &value) {
        size_t size = sizeof(value);
        return sysctlbyname(name, &value, &size, nullptr, 0);
    };
    const auto write = [](const char *name, int value) {
        return sysctlbyname(name, nullptr, nullptr, &value, sizeof(value));
    };
    for (unsigned wait = 0; wait < 120 && !__atomic_load_n(&fStopping, __ATOMIC_ACQUIRE); ++wait) {
        int armed = 0;
        if (!read("debug.nvaccelfb", armed)) {
            bool ok = !write("debug.nvaccel_igpu", 1) && !write("debug.nvaccelfb", 3) &&
                      !write("debug.nmintelfb_agdc", 1);
            int agdc = 0;
            for (unsigned i = 0; ok && i < 100 && !__atomic_load_n(&fStopping, __ATOMIC_ACQUIRE); ++i) {
                if (read("debug.nmintelfb_agdc", agdc) || agdc < 0) { ok = false; break; }
                if (agdc == 1) break;
                IOSleep(100);
            }
            ok = ok && agdc == 1 && !__atomic_load_n(&fStopping, __ATOMIC_ACQUIRE) &&
                 !write("debug.nvaccel_iop", 1);
            setProperty("NMIntelFBAutoEnabled", ok ? kOSBooleanTrue : kOSBooleanFalse);
            provider->setProperty(ok ? "NMIntelFBActive" : "NMIntelFBFailed", kOSBooleanTrue);
            ifb_trail(ok ? "bridge-enabled" : "bridge-enable-failed");
            return;
        }
        IOSleep(1000);
    }
    IFBLOG("NVIDIA accelerator unavailable; ordinary bridge activation was not completed");
    provider->setProperty("NMIntelFBFailed", kOSBooleanTrue);
}

void NMIntelFB::drainWorkers()
{
    if (!fWorkerLock) return;
    IOLockLock(fWorkerLock);
    __atomic_store_n(&fStopping, true, __ATOMIC_RELEASE);
    IOLockWakeup(fWorkerLock, &fVblNext, false);
    while (fVblRunning || fGuardRunning)
        IOLockSleep(fWorkerLock, &fVblNext, THREAD_UNINT);
    IOLockUnlock(fWorkerLock);
}

IOReturn NMIntelFB::registerForInterruptType(IOSelect type, IOFBInterruptProc proc,
                                           OSObject *target, void *ref, void **iref)
{
    if (!proc || !iref) return kIOReturnBadArgument;
    Irq *irq = type == kIOFBVBLInterruptType ? &fVbl :
               type == kIOFBConnectInterruptType ? &fConnect : nullptr;
    if (!irq) return kIOReturnUnsupported;
    IOLockLock(fIrqLock);
    if (irq->proc || __atomic_load_n(&fStopping, __ATOMIC_ACQUIRE)) {
        IOLockUnlock(fIrqLock);
        return kIOReturnBusy;
    }
    if (target) target->retain();
    *irq = {proc, target, ref};
    if (irq == &fVbl) fVblOn = fVblAlways;
    *iref = irq;
    IOLockUnlock(fIrqLock);
    return kIOReturnSuccess;
}

IOReturn NMIntelFB::unregisterInterrupt(void *ref)
{
    if (ref != &fVbl && ref != &fConnect) return kIOReturnBadArgument;
    IOLockLock(fIrqLock);
    Irq *irq = static_cast<Irq *>(ref);
    OSObject *target = irq->target;
    *irq = {};
    if (irq == &fVbl) {
        fVblOn = false;
        // Reentrant unregister from the callback is already protected by its
        // snapshot reference; other callers must wait before freeing ref.
        while (fVblInFlight && current_thread() != __atomic_load_n(&fVblThread, __ATOMIC_ACQUIRE))
            IOLockSleep(fIrqLock, &fVblInFlight, THREAD_UNINT);
    }
    IOLockUnlock(fIrqLock);
    if (target) target->release();
    return kIOReturnSuccess;
}

IOReturn NMIntelFB::setInterruptState(void *ref, UInt32 state)
{
    if (ref != &fVbl && ref != &fConnect) return kIOReturnBadArgument;
    IOLockLock(fIrqLock);
    if (ref == &fVbl) fVblOn = fVbl.proc && (fVblAlways || state != 0);
    IOLockUnlock(fIrqLock);
    return kIOReturnSuccess;
}

void NMIntelFB::free()
{
    drainWorkers();
    if (fIrqLock) {
        unregisterInterrupt(&fVbl);
        unregisterInterrupt(&fConnect);
    }
    if (fBacklight) {
        fBacklight->terminate(kIOServiceSynchronous);
        fBacklight->detach(this);
        OSSafeReleaseNULL(fBacklight);
    }
    freeRam();

    kapiFree();
    OSSafeReleaseNULL(fSurfMap);
    OSSafeReleaseNULL(fMMIO);
    OSSafeReleaseNULL(fPCI);
    if (fVramLock) IOLockFree(fVramLock);
    if (fRamLock) IOLockFree(fRamLock);
    if (fIrqLock) IOLockFree(fIrqLock);
    if (fWorkerLock) IOLockFree(fWorkerLock);
    IOFramebuffer::free();
}
#ifdef NMINTELFB_VIRTUAL
static NMIntelFB *gVirt;
static int nmintelfb_sample_sysctl SYSCTL_HANDLER_ARGS
{
    char b[128] = "no panel";
    if (gVirt) gVirt->sampleInto(b, sizeof b);
    return sysctl_handle_string(oidp, b, sizeof b, req);
}
SYSCTL_PROC(_debug, OID_AUTO, nmintelfb_sample, CTLTYPE_STRING | CTLFLAG_RD | CTLFLAG_LOCKED, nullptr, 0,
            nmintelfb_sample_sysctl, "A", "64 pixels of the virtual panel: non-black and varied counts");
#endif

// intelfb-0013: the Intel GPU whose panel this driver shows, kept by NMIntelFBClaim when the framebuffer sits under NVRM
static IOPCIDevice *gIfbPCI = nullptr;
static IOService *gIfbNubFB = nullptr;   // intelfb-0014: the framebuffer on the nub, which the AGDC reports
static uint32_t gIfbK5[NVK5_TIMING_WORDS];   // intelfb-0015: the panel's timing in NVRMFB's NVRMTiming layout
static void ifb_agdc_register(bool on);
static void ifb_ram_register(bool on);
static bool ifb_pci_ok(IOPCIDevice *pci, uint8_t *ver);

static IOPCIDevice *ifb_copy_pci()
{
    IOLockLock(gIfbLock);
    IOPCIDevice *pci = gIfbPCI;
    if (pci) pci->retain();
    IOLockUnlock(gIfbLock);
    return pci;
}

bool NMIntelFB::ramHardwareEligible() const
{
    if (!fPCI) return false;
    bool eligible = true;
    if (IORegistryIterator *it = IORegistryIterator::iterateOver(fPCI, gIOServicePlane, kIORegistryIterateRecursively)) {
        while (IORegistryEntry *entry = it->getNextObject()) {
            if (entry->getProperty("MetalPluginName") || entry->metaCast("IOGraphicsAccelerator") ||
                entry->metaCast("IOGraphicsAccelerator2")) { eligible = false; break; }
        }
        it->release();
    } else eligible = false;
    return eligible;
}

// Validate the inherited plane before claiming the firmware framebuffer's
// match category. A failed bridge must leave a usable firmware fallback.
static bool ifb_firmware_supported(IOPCIDevice *pci)
{
    IOMemoryMap *mmio = pci->mapDeviceMemoryWithRegister(kIOPCIConfigBaseAddress0);
    IODeviceMemory *aperture = pci->getDeviceMemoryWithRegister(kIOPCIConfigBaseAddress2);
    if (!mmio || !aperture || mmio->getLength() < PLANE_SURF(3) + 4) {
        OSSafeReleaseNULL(mmio);
        return false;
    }
    const volatile uint8_t *regs = reinterpret_cast<const volatile uint8_t *>(mmio->getVirtualAddress());
    const auto read = [regs](uint32_t offset) { return *reinterpret_cast<const volatile uint32_t *>(regs + offset); };
    bool valid = false;
    for (int pipe = 0; pipe < 4; ++pipe) {
        const uint32_t control = read(PLANE_CTL(pipe));
        if (!(read(TRANSCONF(pipe)) & kEnable) || !(control & kEnable)) continue;
        const uint32_t size = read(PLANE_SIZE(pipe));
        valid = fmtOf(control) == 8 && tileOf(control) == 0 &&
            nmintelfb::surfaceFits((size & 0xffff) + 1, (size >> 16) + 1,
                (read(PLANE_STRIDE(pipe)) & 0x7ff) * 64,
                read(PLANE_SURF(pipe)) & ~0xfffu, aperture->getLength());
        break;
    }
    mmio->release();
    return valid;
}

IOService *NMIntelFB::probe(IOService *provider, SInt32 *score)
{
    if (version_major != 24 && version_major != 25) return nullptr;   /* the 2.x line supports macOS 15 (Darwin 24) and macOS 26 (Darwin 25) only */
#ifdef NMINTELFB_VIRTUAL
    if (!provider->getProperty("nm-intel-panel")) return nullptr;   // only the nub the loader published
    // test build: a virtual 1920x1080 "panel" in system memory, so the NVIDIA -> Intel frame path can be proven on a
    // machine with no Intel GPU (frames composited on the NVIDIA card must arrive in this buffer)
    fVer = 0xFF; return IOFramebuffer::probe(provider, score);
#endif
    // intelfb-0013: the Intel-panel nub NMIntelFBClaim publishes under NVRM (the PCI checks ran in the claim's probe)
    if (provider->getProperty("nm-intel-panel")) {
        IOPCIDevice *pci = ifb_copy_pci();
        if (!pci) return nullptr;
        const bool ok = ifb_pci_ok(pci, &fVer);
        pci->release();
        return ok ? IOFramebuffer::probe(provider, score) : nullptr;
    }
    IOPCIDevice *pci = OSDynamicCast(IOPCIDevice, provider);
    if (!pci) return nullptr;
    return ifb_pci_ok(pci, &fVer) ? IOFramebuffer::probe(provider, score) : nullptr;
}

// The checks that decide whether this driver takes the Intel panel at all (NMIntelFB's own probe before intelfb-0013)
static bool ifb_pci_ok(IOPCIDevice *pci, uint8_t *ver)
{
    uint8_t &fVer = *ver;
    if (pci->configRead16(kIOPCIConfigVendorID) != 0x8086 ||
        pci->getProperty("NMIntelFBBridgeDeclined")) return false;
    if (PE_parse_boot_argn("-nmintelfboff", nullptr, 0)) { IFBLOG("off by boot-arg"); return false; }
    // nmx-intel: "Start once without NVIDIA driver" (-nvoff) also leaves the panel to the firmware framebuffer, so a
    // hang never needs the Recovery; and with the AMD/Intel test driver on (nmxintel=1) i915 owns this GPU's display
    if (PE_parse_boot_argn("-nvoff", nullptr, 0)) { IFBLOG("off: -nvoff"); return false; }
    { uint32_t nmx = 0; if (PE_parse_boot_argn("nmxintel", &nmx, sizeof nmx) && nmx) { IFBLOG("off: nmxintel=1 (NMXGPU drives this GPU)"); return false; } }
    uint16_t dev = pci->configRead16(kIOPCIConfigDeviceID);
    fVer = displayVersion(dev);
    if (!fVer) return false;   // an Intel GPU this driver does not know (older ones have Apple's own driver)
    if (ifb_nv_has("nullmoth-intelfb-attaching")) {
        IFBLOG("the last start never returned (boot guard) - leaving the firmware screen; sudo nvram -d nullmoth-intelfb-attaching");
        ifb_nv_set("nullmoth-intelfb-trail", "guard: the last start never returned");
        return false;
    }
    if (ifb_knob("nullmoth-intelfb", 1) == 0) { IFBLOG("off: nullmoth-intelfb=0"); return false; }
    if (ifb_nvrm_guard_off()) { IFBLOG("off: NVIDIA recovery/boot guard; keeping the firmware framebuffer"); return false; }
    IFBLOG("8086:%04x display version %u", dev, fVer);
    return true;
}

bool NMIntelFB::adoptFirmwareMode()
{
    if (!fMMIO || fMMIO->getLength() < PLANE_SURF(3) + 4) return false;
    // the panel is on whichever pipe the firmware used; take the first pipe whose transcoder and plane 1 are both on
    for (int p = 0; p < 4; p++) {
        uint32_t conf = rd(TRANSCONF(p)), ctl = rd(PLANE_CTL(p));
        if (!(conf & kEnable) || !(ctl & kEnable)) continue;
        if (fmtOf(ctl) != 8) { IFBLOG("pipe %c: plane format %u is not XRGB8888 - leaving the firmware screen", 'A' + p, fmtOf(ctl)); return false; }
        if (tileOf(ctl) != 0) { IFBLOG("pipe %c: plane is tiled (%u) - leaving the firmware screen", 'A' + p, tileOf(ctl)); return false; }
        uint32_t size = rd(PLANE_SIZE(p)), stride = rd(PLANE_STRIDE(p)) & 0x7FF;
        fWidth = (size & 0xFFFF) + 1; fHeight = ((size >> 16) & 0xFFFF) + 1;
        fRowBytes = stride * 64;                     // linear planes count the stride in 64-byte units
        fSurf = rd(PLANE_SURF(p)) & ~0xFFFu;         // GGTT offset of the surface, 4 KiB aligned
        if (!nmintelfb::surfaceFits(fWidth, fHeight, fRowBytes, fSurf, fApertureLen)) {
            IFBLOG("pipe %c: plane %ux%u stride %u is not a usable surface", 'A' + p, fWidth, fHeight, fRowBytes); return false;
        }
        // the transcoder that feeds pipe p is p on these display versions (eDP on transcoder A in every recording)
        fHT = (rd(HTOTAL(p)) >> 16) + 1; fVT = (rd(VTOTAL(p)) >> 16) + 1;
        fHSS = (rd(HSYNC(p)) & 0xFFFF) + 1; fHSE = (rd(HSYNC(p)) >> 16) + 1;
        fVSS = (rd(VSYNC(p)) & 0xFFFF) + 1; fVSE = (rd(VSYNC(p)) >> 16) + 1;
        // refresh: count vblanks for 250 ms (a 30-240 Hz panel gives 7-60 counts); fall back to 60 Hz if it does not move
        uint32_t f0 = rd(FRMCOUNT(p)); uint64_t t0 = mach_absolute_time();
        IOSleep(250);
        uint32_t f1 = rd(FRMCOUNT(p)); uint64_t t1 = mach_absolute_time(), ns = 0;
        absolutetime_to_nanoseconds(t1 - t0, &ns);
        uint32_t frames = f1 - f0;
        if (frames >= 5 && frames <= 100 && ns) {
            fFrameNs = ns / frames;
            uint64_t mhz = (uint64_t)frames * 1000000000000ull / ns;   // refresh in milli-Hz
            fRefresh16 = (uint32_t)((mhz << 16) / 1000);
            fPclk = (uint64_t)fHT * fVT * mhz / 1000;
        } else fPclk = (uint64_t)fHT * fVT * 60;
        fPipe = p;
        IFBLOG("pipe %c: %ux%u stride %u surface 0x%x totals %ux%u, %u vblanks in %llu ms -> %u.%02u Hz, pclk %llu", 'A' + p,
               fWidth, fHeight, fRowBytes, fSurf, fHT, fVT, frames, (unsigned long long)(ns / 1000000),
               fRefresh16 >> 16, ((fRefresh16 & 0xFFFF) * 100) >> 16, (unsigned long long)fPclk);
        return true;
    }
    IFBLOG("no pipe is scanning out - the firmware left the panel off; leaving it to the firmware framebuffer");
    return false;
}

bool NMIntelFB::start(IOService *provider)
{
#ifdef NMINTELFB_VIRTUAL
    fWidth = 1920; fHeight = 1080; fRowBytes = 7680; fSurf = 0; fPipe = 0;
    fHT = 2200; fVT = 1125; fHSS = 2008; fHSE = 2052; fVSS = 1084; fVSE = 1089; fPclk = 148500000ull;   // CEA 1080p60
    fVBuf = IOBufferMemoryDescriptor::inTaskWithPhysicalMask(kernel_task, kIODirectionInOut | kIOMemoryPhysicallyContiguous,
                                                             (mach_vm_size_t)fRowBytes * fHeight, 0xFFFFFFFFFFFFF000ull);
    if (!fVBuf || fVBuf->prepare() != kIOReturnSuccess) { IFBLOG("virtual: no contiguous buffer"); OSSafeReleaseNULL(fVBuf); return false; }
    bzero(fVBuf->getBytesNoCopy(), fVBuf->getLength());
    fApertureBase = fVBuf->getPhysicalSegment(0, nullptr, kIOMemoryMapperNone); fApertureLen = fVBuf->getLength();
    if (!IOFramebuffer::start(provider)) { fVBuf->complete(); OSSafeReleaseNULL(fVBuf); return false; }
    fSurfMap = fVBuf->map();
    setProperty("IOFBDependentID", 0x4E56524D00000001ull, 64);
    setProperty("IOFBDependentIndex", 4ull, 32);
    if (IOWorkLoop *wl = getWorkLoop()) {
        fTimer = IOTimerEventSource::timerEventSource(this, [](OSObject *o, IOTimerEventSource *t) {
            ((NMIntelFB *)o)->sample(); t->setTimeoutMS(2000); });
        if (fTimer && wl->addEventSource(fTimer) == kIOReturnSuccess) fTimer->setTimeoutMS(2000);
    }
    armVbl();
    registerPowerDriver(this, kPowerStates, 2);
    gVirt = this; sysctl_register_oid(&sysctl__debug_nmintelfb_sample);
    IFBLOG("virtual panel published (1920x1080 at phys 0x%llx)", (unsigned long long)fApertureBase);
    return true;
#endif
    ifb_nv_set("nullmoth-intelfb-attaching", "1");
    ifb_trail("start");
    // intelfb-0008: nullmoth-intelfb-delay=<s> waits before taking the panel, so the boot log reaches the disk and the
    // NVIDIA stack (NVRM auto-go, NVAccel) has started before this framebuffer exists. The firmware screen stays meanwhile
    if (uint32_t w = ifb_knob("nullmoth-intelfb-delay", provider->getProperty("nm-intel-panel") ? 45 : 0)) {
        if (w > 120) w = 120;
        IFBLOG("waiting %u s before taking the panel (nullmoth-intelfb-delay)", w);
        ifb_trail("wait");
        IOSleep(w * 1000);
        ifb_trail("go");
    }
    bool ok = startReal(provider);
    ifb_trail(ok ? "ok" : "declined");
    // intelfb-0005: RTX-3 froze after start() returned, so the boot guard stays armed until the Mac has lived two
    // minutes with this framebuffer; a freeze before that leaves the panel to the firmware on the next boot
    if (ok) {
        IOLockLock(fWorkerLock);
        fGuardRunning = true;
        retain();
        thread_t bt = nullptr;
        if (kernel_thread_start(guardThread, this, &bt) == KERN_SUCCESS) thread_deallocate(bt);
        else { fGuardRunning = false; release(); IFBLOG("boot-guard worker failed; guard remains armed"); }
        IOLockUnlock(fWorkerLock);
    } else ifb_nv_set("nullmoth-intelfb-attaching", nullptr);
    if (provider->getProperty("nm-intel-panel"))
        provider->setProperty(ok ? "NMIntelFBReady" : "NMIntelFBFailed", kOSBooleanTrue);
    return ok;
}

bool NMIntelFB::startReal(IOService *provider)
{
    fPCI = OSDynamicCast(IOPCIDevice, provider);
    if (fPCI) fPCI->retain();
    else fPCI = ifb_copy_pci();
    if (!fPCI) return false;
    const bool bridge = provider->getProperty("nm-intel-panel") != nullptr;
    fPCI->setMemoryEnable(true);
    fMMIO = fPCI->mapDeviceMemoryWithRegister(kIOPCIConfigBaseAddress0);
    IODeviceMemory *ap = fPCI->getDeviceMemoryWithRegister(kIOPCIConfigBaseAddress2);
    if (!fMMIO || !ap) { IFBLOG("BAR0 or BAR2 is not mapped - leaving the firmware screen"); OSSafeReleaseNULL(fMMIO); return false; }
    fRegs = (volatile uint8_t *)fMMIO->getVirtualAddress();
    ifb_trail("mmio");
    fApertureBase = ap->getPhysicalAddress(); fApertureLen = ap->getLength();
    if (!adoptFirmwareMode()) { OSSafeReleaseNULL(fMMIO); fRegs = nullptr; return false; }
    { char t[64]; snprintf(t, sizeof t, "mode-%c-%ux%u-s%x", 'A' + fPipe, fWidth, fHeight, fSurf); ifb_trail(t); }
    // Head 4 of the NVIDIA accelerator (NVAccel): WindowServer composites this panel on the NVIDIA card and the
    // display pipe copies each frame into fSurfMap. Without NVAccel the panel stays a plain framebuffer.
    if (IODeviceMemory *sm = IODeviceMemory::withRange(fApertureBase + fSurf, (IOPhysicalLength)fRowBytes * fHeight)) {
        fSurfMap = sm->map(kIOMapWriteCombineCache); sm->release();
    }
    ifb_trail(fSurfMap ? "surf" : "surf-fail");
    // nmx-intel (RTX-2 hung in registerPowerDriver with these set after IOFramebuffer::start): the dependent ID decides
    // the controller IOFramebuffer::start builds, so it goes in before, as NVRMFB does. nullmoth-intelfb-dep=0
    // publishes a plain framebuffer, without joining NVAccel as head 4.
    // intelfb-0009: on a laptop with nothing on the NVIDIA ports no NVRMFB head ever starts (NVRM "heads 0x0"), so in
    // NVRM's group this panel sat alone at index 4 with fFbs[0] NULL. Default (dep=1): a controller of its own (see
    // open()); dep=2 keeps NVRM's group as before. Index 4 at publish is what makes NVAccel treat it as head 4.
    uint32_t dep = fSurfMap ? ifb_knob("nullmoth-intelfb-dep", bridge ? 1 : 0) : 0;
    if (dep) {
        fOwnGroup = dep != 2;
        setProperty("IOFBDependentID", fOwnGroup ? 0x4E4D494E00000001ull : 0x4E56524D00000001ull, 64);
        // intelfb-0010: NVAccel makes only head 0 its primary framebuffer (fFB), and registers pipes (debug.nvaccelfb)
        // only once fFB is set; NVRMFB head 0 never starts here, so as head 4 the panel was never registered and the
        // 60 s headless check gave WindowServer up (RTX-8). In its own group the panel publishes as head 0
        // (nullmoth-intelfb-head=4: head 4, as in RTX-8).
        uint32_t head = fOwnGroup ? ifb_knob("nullmoth-intelfb-head", 0) : 4;
        setProperty("IOFBDependentIndex", head == 4 ? 4ull : 0ull, 32);
        ifb_trail(!fOwnGroup ? "dep-nvrm" : head == 4 ? "dep-own-h4" : "dep-own-h0");
    } else IFBLOG("the surface would not map write-combined, or nullmoth-intelfb-dep=0: NVIDIA rendering stays off for this panel");
    // Publish neither a primary framebuffer without RM handles nor a display
    // without a VBL worker. Registration can synchronously notify NVAccel.
    if (bridge && fOwnGroup && ifb_knob("nullmoth-intelfb-head", 0) != 4 && !kapiInit(provider)) return false;
    fVblAlways = ifb_knob("nullmoth-intelfb-vbl", 1) != 0;
    if (!armVbl()) return false;
    publishBacklight();   // Keep the fork's brightness handler before framebuffer registration.
    if (!IOFramebuffer::start(provider)) { ifb_trail("fb-start-failed"); if (fBacklight) { fBacklight->terminate(kIOServiceSynchronous); fBacklight->detach(this); OSSafeReleaseNULL(fBacklight); } OSSafeReleaseNULL(fSurfMap); OSSafeReleaseNULL(fMMIO); fRegs = nullptr; return false; }
    ifb_trail("fb-started");
    setProperty("NMIntelFBPipe", fPipe, 32);
    setProperty("NMIntelFBDisplayVersion", fVer, 32);
    setProperty("built-in", kOSBooleanTrue);   // the panel is the laptop's own (kConnectionFlags says so too)
    ifb_trail(fVblAlways ? "vbl-always" : "vbl");
    // nmx-intel: nullmoth-intelfb-pm=0 leaves power management out (RTX-2 hung inside this call)
    if (ifb_knob("nullmoth-intelfb-pm", 1)) {
        ifb_trail("pm");
        registerPowerDriver(this, kPowerStates, 2);
        ifb_trail("power");
    } else ifb_trail("no-pm");
    if (bridge) {
        // intelfb-0015: the timing NVRMFB publishes as NVRMTiming (refresh in mHz, positive syncs as eDP panels use)
        const uint32_t k5[NVK5_TIMING_WORDS] = { NVK5_TIMING_TAG, (uint32_t)fPclk, fWidth, fHSS, fHSE, fHT, fHeight, fVSS, fVSE, fVT,
                                                 (uint32_t)(((uint64_t)fRefresh16 * 1000) >> 16), 1, 1 };
        memcpy(gIfbK5, k5, sizeof k5);
        if (OSData *d = OSData::withBytes(k5, sizeof k5)) { setProperty("NVRMTiming", d); d->release(); }
        int bad = nvk5_timing_bad(gIfbK5, sizeof gIfbK5);
        IFBLOG("K5 timing: pclk %u %ux%u h %u-%u/%u v %u-%u/%u %u mHz%s", k5[1], fWidth, fHeight, fHSS, fHSE, fHT, fVSS, fVSE, fVT,
               k5[10], bad ? " - REJECTED by nvk5_timing_bad" : "");
        IOLockLock(gIfbLock);
        gIfbNubFB = this;
        IOLockUnlock(gIfbLock);
        ifb_agdc_register(true);
        ifb_ram_register(true);
    }
    IFBLOG("built-in panel published (%ux%u)", fWidth, fHeight);
    return true;
}

void NMIntelFB::stop(IOService *provider)
{
    drainWorkers();
#ifdef NMINTELFB_VIRTUAL
    if (gVirt == this) { sysctl_unregister_oid(&sysctl__debug_nmintelfb_sample); gVirt = nullptr; }
#endif
    IOLockLock(gIfbLock);
    const bool published = gIfbNubFB == this;
    if (published) gIfbNubFB = nullptr;
    IOLockUnlock(gIfbLock);
    if (published) { ifb_ram_register(false); ifb_agdc_register(false); }
    unregisterInterrupt(&fVbl);
    unregisterInterrupt(&fConnect);
    freeRam();
    IOLockLock(fVramLock);
    if (__atomic_load_n(&fKapiExposed, __ATOMIC_ACQUIRE) && !quarantined()) {
        // NVAccel caches the primary device handle. A failed bridge may detach
        // while clients still own allocations; keep that device valid to reboot.
        __atomic_store_n(&fQuarantineRetained, true, __ATOMIC_RELEASE);
        retain();
        if (fPCI) fPCI->setProperty("NMIntelFBBridgeDeclined", kOSBooleanTrue);
        IFBLOG("retired primary NVKMS device retained until reboot");
    }
    IOLockUnlock(fVramLock);
    kapiFree();
    if (fBacklight) { fBacklight->terminate(kIOServiceSynchronous); fBacklight->detach(this); OSSafeReleaseNULL(fBacklight); }
    fPwmPeriod = 0;
    if (!quarantined()) { OSSafeReleaseNULL(fSurfMap); OSSafeReleaseNULL(fMMIO); fRegs = nullptr; }
    IOFramebuffer::stop(provider);
}

// intelfb-0011. The table of VRAM blocks handed to NVAccel (one NMIntelFB per machine).
struct IfbVram { struct NvKmsKapiMemory *m; void *k; uint64_t n, cookie; };
static IfbVram gIfbVram[2048];

static void ifb_kapi_event(const struct NvKmsKapiEvent *) {}

// The NVRMDisplay nub provides the GPU id and forwards the kernel-only display-pointer request to NVRM.
// Ownership is not grabbed: it is for mode sets, and NVRMFB may still hold it.
bool NMIntelFB::kapiInit(IOService *nub)
{
    if (!ifb_knob("nullmoth-intelfb-kapi", 1)) return false;
    NVRMDisplayPointers displayPtrs = {};
    displayPtrs.version = NVRM_DISPLAY_PTRS_ABI_VERSION;
    const OSSymbol *displayPtrsFn = OSSymbol::withCString(NVRM_DISPLAY_PTRS_FN);
    IOReturn displayStatus = displayPtrsFn ? nub->callPlatformFunction(displayPtrsFn, false, &displayPtrs, nullptr, nullptr, nullptr)
                                          : kIOReturnNoMemory;
    OSSafeReleaseNULL(displayPtrsFn);
    if (displayStatus != kIOReturnSuccess) { IFBLOG("display-pointer request failed: 0x%x", displayStatus); return false; }
    OSNumber *g = OSDynamicCast(OSNumber, nub->getProperty("gpu-id"));
    fKms = static_cast<NvKmsKapiFunctionsTable *>(displayPtrs.kapi);
    fPhysForVa = reinterpret_cast<NvU64 (*)(const void *)>(displayPtrs.physForVa);
    uint64_t bar = 0;
    IOService *rm = nub->getProvider();
    if (IOPCIDevice *pci = rm ? OSDynamicCast(IOPCIDevice, rm->getProvider()) : nullptr)
        for (IOItemCount i = 0; i < pci->getDeviceMemoryCount() && !bar; ++i)
            if (IODeviceMemory *m = pci->getDeviceMemoryWithIndex(i))
                if (m->getLength() >= (256ull << 20)) bar = m->getLength();
    fVramBudget = bar >= (4ull << 30) ? bar / 2 : bar / 4 * 3;
    if (!fKms || !g || !fPhysForVa || !fVramBudget || !fKms->allocateDevice ||
        !fKms->freeDevice || !fKms->allocateMemory || !fKms->freeMemory ||
        !fKms->mapMemory || !fKms->unmapMemory) return false;
    NvKmsKapiAllocateDeviceParams params = {};
    params.gpuId = g->unsigned32BitValue();
    params.privateData = this;
    params.eventCallback = ifb_kapi_event;
    fDev = fKms->allocateDevice(&params);
    if (!fDev) return false;
    // The family needs an NVIDIA resource for its display pipe. This allocation
    // represents that resource only; copied pixels still land on the Intel plane.
    fScanRes.version = NVRM_VRAM_ABI_VERSION;
    fScanRes.size = uint64_t(fRowBytes) * fHeight;
    if (vramGrant(&fScanRes) != kIOReturnSuccess) return false;
    ifb_trail("kapi-ready");
    return true;
}

void NMIntelFB::kapiFree()
{
    if (quarantined()) return;
    if (!fVramLock) return;
    IOLockLock(fVramLock);
    if (fKms && fDev) {
        for (IfbVram &entry : gIfbVram) {
            if (!entry.m) continue;
            fKms->unmapMemory(fDev, entry.m, NVKMS_KAPI_MAPPING_TYPE_KERNEL, entry.k);
            fKms->freeMemory(fDev, entry.m);
            entry = {};
        }
        fKms->freeDevice(fDev);
    }
    fDev = nullptr;
    fVramBytes = 0;
    fScanRes = {};
    IOLockUnlock(fVramLock);
}

// NVRMFB's vramGrant without its NVIDIA-display parts (no NVIDIA scanout or console to keep clear of here)
IOReturn NMIntelFB::vramGrant(NVRMVramRequest *r)
{
    r->phys = 0; r->kva = nullptr; r->handle = nullptr; r->actualSize = 0; r->mappedTotal = 0;
    r->allocationCookie = 0;
    IOLockLock(fVramLock);
    if (!fKms || !fDev || __atomic_load_n(&fStopping, __ATOMIC_ACQUIRE)) {
        IOLockUnlock(fVramLock); return kIOReturnNotReady;
    }
    const NvU64 want = nmintelfb::allocationSize(r->size, fVramBudget, fVramBytes);
    if (!want) { IOLockUnlock(fVramLock); return kIOReturnNoMemory; }
    IfbVram *slot = nullptr;
    for (IfbVram &e : gIfbVram) if (!e.m) { slot = &e; break; }
    if (!slot) {
        IOLockUnlock(fVramLock);
        IFBLOG("nvAllocVram(%llu): refused (%s, %llu of %llu bytes)", want, slot ? "budget" : "table full", fVramBytes, fVramBudget);
        return kIOReturnNoMemory;
    }
    struct NvKmsKapiAllocateMemoryParams mp = {};
    NvU8 compressible = 0;
    mp.layout = NvKmsSurfaceMemoryLayoutPitch; mp.type = NVKMS_KAPI_ALLOCATION_TYPE_SCANOUT;
    mp.size = want; mp.useVideoMemory = NV_TRUE; mp.compressible = &compressible;
    struct NvKmsKapiMemory *m = fKms->allocateMemory(fDev, &mp);
    void *k = nullptr;
    NvU64 p = 0;
    if (m && fKms->mapMemory(fDev, m, NVKMS_KAPI_MAPPING_TYPE_KERNEL, &k) && k) p = fPhysForVa(k);
    bool live = false;
    if (p) {
        bzero(k, (size_t)want);
        volatile NvU64 *q = (volatile NvU64 *)k;
        q[0] = 0x5A5AA5A5C0FFEE01ull; live = q[0] == 0x5A5AA5A5C0FFEE01ull; q[0] = 0;
    }
    if (!live) {
        if (k) fKms->unmapMemory(fDev, m, NVKMS_KAPI_MAPPING_TYPE_KERNEL, k);
        if (m) fKms->freeMemory(fDev, m);
        IOLockUnlock(fVramLock);
        IFBLOG("nvAllocVram(%llu): %s", want, !m ? "allocateMemory failed" : !k ? "mapMemory failed" : !p ? "no physical address" : "the mapping is not live");
        return kIOReturnNoMemory;
    }
    *slot = { m, k, want, ++fVramCookie };
    fVramBytes += want;
    r->handle = m; r->kva = k; r->phys = p; r->actualSize = want; r->mappedTotal = fVramBytes;
    r->allocationCookie = slot->cookie;
    if (r != &fScanRes) __atomic_store_n(&fKapiExposed, true, __ATOMIC_RELEASE);
    IOLockUnlock(fVramLock);
    if (fVramLogged < 16) { fVramLogged++; IFBLOG("nvAllocVram: %llu bytes VRAM, phys 0x%llx (%llu MB in use)", want, p, fVramBytes >> 20); }
    if (fVramLogged == 1) ifb_trail("vram1");
    return kIOReturnSuccess;
}

IOReturn NMIntelFB::vramRelease(NVRMVramRequest *r)
{
    IOLockLock(fVramLock);
    if (r->handle && r->handle == fScanRes.handle) { IOLockUnlock(fVramLock); return kIOReturnBadArgument; }
    if (!fKms || !fDev || !r->handle) { IOLockUnlock(fVramLock); return kIOReturnBadArgument; }
    for (IfbVram &e : gIfbVram) {
        if (e.m != r->handle || e.cookie != r->allocationCookie) continue;
        fKms->unmapMemory(fDev, e.m, NVKMS_KAPI_MAPPING_TYPE_KERNEL, e.k);
        fKms->freeMemory(fDev, e.m);
        fVramBytes -= e.n;
        e = {};
        IOLockUnlock(fVramLock);
        r->handle = nullptr; r->kva = nullptr; r->phys = 0; r->actualSize = 0; r->allocationCookie = 0;
        return kIOReturnSuccess;
    }
    IOLockUnlock(fVramLock);
    return kIOReturnBadArgument;
}

IODeviceMemory *NMIntelFB::getApertureRange(IOPixelAperture aperture)
{
    if (aperture != kIOFBSystemAperture) return nullptr;
    return IODeviceMemory::withRange(fApertureBase + fSurf, (IOPhysicalLength)fRowBytes * fHeight);
}

IOReturn NMIntelFB::getInformationForDisplayMode(IODisplayModeID mode, IODisplayModeInformation *info)
{
    if (mode != 1 || !info) return kIOReturnBadArgument;
    bzero(info, sizeof(*info));
    info->maxDepthIndex = 0; info->nominalWidth = fWidth; info->nominalHeight = fHeight;
    info->refreshRate = fRefresh16; info->flags = kDisplayModeValidFlag | kDisplayModeSafeFlag | kDisplayModeDefaultFlag;
    return kIOReturnSuccess;
}

IOReturn NMIntelFB::getPixelInformation(IODisplayModeID mode, IOIndex depth, IOPixelAperture aperture, IOPixelInformation *pi)
{
    if (mode != 1 || depth != 0 || aperture != kIOFBSystemAperture || !pi) return kIOReturnUnsupportedMode;
    bzero(pi, sizeof(*pi));
    pi->bytesPerRow = fRowBytes; pi->bytesPerPlane = 0; pi->bitsPerPixel = 32; pi->pixelType = kIORGBDirectPixels;
    pi->componentCount = 3; pi->bitsPerComponent = 8;
    pi->componentMasks[0] = 0x00FF0000; pi->componentMasks[1] = 0x0000FF00; pi->componentMasks[2] = 0x000000FF;
    strlcpy(pi->pixelFormat, IO32BitDirectPixels, sizeof(pi->pixelFormat));
    pi->activeWidth = fWidth; pi->activeHeight = fHeight;
    return kIOReturnSuccess;
}

OSDefineMetaClassAndStructors(NMIntelBacklight, IODisplayParameterHandler)

NMIntelBacklight *NMIntelBacklight::withFB(NMIntelFB *fb, SInt32 level)
{
    NMIntelBacklight *h = OSTypeAlloc(NMIntelBacklight);
    if (!h || !h->init()) { OSSafeReleaseNULL(h); return nullptr; }
    h->fFB = fb;   // not retained: the framebuffer owns this handler and terminates it in stop()
    h->fApply = thread_call_allocate(applyFire, h);
    h->fLevel = level < 0 ? 0 : level > 100 ? 100 : level;
    if (!h->fApply) { h->release(); return nullptr; }
    return h;
}

void NMIntelBacklight::free()
{
    if (fApply) { thread_call_cancel(fApply); while (!thread_call_free(fApply)) { thread_call_cancel(fApply); IOSleep(1); } fApply = nullptr; }
    IODisplayParameterHandler::free();
}

// The display may have no IODisplayParameters yet; IODisplay::setProperties() only accepts parameters listed there.
bool NMIntelBacklight::setDisplay(IODisplay *display)
{
    if (!display) return false;
    OSObject *old = display->copyProperty(gIODisplayParametersKey);
    OSDictionary *cur = OSDynamicCast(OSDictionary, old);
    OSDictionary *params = cur ? OSDictionary::withDictionary(cur) : OSDictionary::withCapacity(2);
    OSSafeReleaseNULL(old);
    if (!params) return false;
    IODisplay::addParameter(params, gIODisplayBrightnessKey, 0, 100);
    IODisplay::setParameter(params, gIODisplayBrightnessKey, fLevel);
    IODisplay::addParameter(params, gIODisplayLinearBrightnessKey, 0, 100);
    IODisplay::setParameter(params, gIODisplayLinearBrightnessKey, fLevel);
    display->setProperty(gIODisplayParametersKey, params);
    params->release();
    IFBLOG("backlight: brightness parameters on %s, panel at %d%%", display->getName(), (int)fLevel);
    return true;
}

// IODisplay calls this with the framebuffer lock held, so the register write runs from a thread call.
bool NMIntelBacklight::doIntegerSet(OSDictionary *, const OSSymbol *paramName, UInt32 value)
{
    if (paramName != gIODisplayBrightnessKey && paramName != gIODisplayLinearBrightnessKey) return false;
    fPending = value > 100 ? 100 : (SInt32)value;
    thread_call_enter(fApply);
    return true;
}

void NMIntelBacklight::applyFire(thread_call_param_t p, thread_call_param_t)
{
    NMIntelBacklight *me = (NMIntelBacklight *)p;
    SInt32 v;
    do { v = me->fPending; } while (!OSCompareAndSwap((UInt32)v, (UInt32)-1, (volatile UInt32 *)&me->fPending));
    if (v < 0) return;
    uint32_t percent = (uint32_t)(v < kIntelMinBacklight ? kIntelMinBacklight : v);
    if (me->fFB->setPanelPercent(percent)) me->fLevel = v;
    else IFBLOG("backlight: setting %u%% did not stick", percent);
}

bool NMIntelFB::setPanelPercent(uint32_t percent)
{
    if (!fRegs || !fPwmPeriod || __atomic_load_n(&fStopping, __ATOMIC_ACQUIRE) || percent > 100) return false;
    uint32_t duty = nmintelfb::pwmDuty(fPwmPeriod, percent, fPwmInverted);
    wr(kBlcPwmDuty, duty);
    return rd(kBlcPwmDuty) == duty;
}

// Brightness only when the firmware enabled the PWM with a usable period; otherwise the panel keeps the level it has.
void NMIntelFB::publishBacklight()
{
    if (!ifb_knob("nullmoth-intelfb-backlight", 1) || !fMMIO || fMMIO->getLength() < kBlcPwmDuty + 4) return;
    uint32_t ctl = rd(kBlcPwmCtl), period = rd(kBlcPwmFreq), duty = rd(kBlcPwmDuty);
    if (ctl == ~0u || period == ~0u || !(ctl & kBlcPwmEnable) || !period || duty > period) {
        IFBLOG("backlight: PWM ctl 0x%08x period %u duty %u - not enabled by the firmware, no brightness control", ctl, period, duty);
        return;
    }
    fPwmPeriod = period;
    fPwmInverted = (ctl & (1u << 29)) != 0;
    SInt32 level = (SInt32)((uint64_t)(fPwmInverted ? period - duty : duty) * 100 / period);
    fBacklight = NMIntelBacklight::withFB(this, level);
    if (!fBacklight) { fPwmPeriod = 0; IFBLOG("backlight: handler allocation failed"); return; }
    fBacklight->setName("backlight");
    if (!fBacklight->attach(this)) { OSSafeReleaseNULL(fBacklight); fPwmPeriod = 0; IFBLOG("backlight: handler attach failed"); return; }
    fBacklight->registerService(kIOServiceSynchronous);
    setProperty("NMIntelFBPwmPeriod", period, 32);
    IFBLOG("backlight: PWM period %u, panel at %d%% - brightness keys and slider through AppleBacklightDisplay", period, (int)level);
}

// AppleBacklightDisplay::probe takes only an LCD connection; without a backlight handler the panel stays a plain display.
IOReturn NMIntelFB::getAppleSense(IOIndex connectIndex, UInt32 *senseType, UInt32 *primary, UInt32 *extended, UInt32 *displayType)
{
    if (connectIndex != 0 || !fBacklight) return kIOReturnUnsupported;
    if (senseType) *senseType = 0;
    if (primary) *primary = 0;
    if (extended) *extended = 0;
    if (displayType) *displayType = kGenericLCD;
    return kIOReturnSuccess;
}

IOReturn NMIntelFB::getAttributeForConnection(IOIndex idx, IOSelect attr, uintptr_t *value)
{

    if (idx != 0 || !value) return kIOReturnBadArgument;
    switch (attr) {
    case kConnectionEnable: *value = 1; return kIOReturnSuccess;
    case kConnectionCheckEnable: *value = 1; return kIOReturnSuccess;
    case kConnectionFlags: *value = kIOConnectionBuiltIn; return kIOReturnSuccess;
    // the colour answers NVRMFB gives (RGB, 8 bits per component), which WindowServer asks before compositing
    case kConnectionColorModesSupported: case kConnectionColorMode: *value = 0x00000001; return kIOReturnSuccess;
    case kConnectionColorDepthsSupported: case kConnectionControllerColorDepth: case kConnectionControllerDepthsSupported:
        *value = 0x00000002; return kIOReturnSuccess;
    default: return IOFramebuffer::getAttributeForConnection(idx, attr, value);
    }
}

// NVAccel's display pipe asks every head for its scan-out (NVRM_VRAM_FN_SCANOUT). This head answers with a CPU mapping and
// no VRAM address: phys 0 keeps NVAccel from treating Intel memory as NVIDIA VRAM (it is not in the NVIDIA card's BAR1),
// so frames reach the panel only through the copy into kva.
IOReturn NMIntelFB::callPlatformFunction(const OSSymbol *fn, bool wait, void *p1, void *p2, void *p3, void *p4)
{
    if (fn && fn->isEqualTo(NMINTELFB_FN_BEGIN)) return beginFrame(static_cast<NMIntelFBFrame *>(p1));
    if (fn && fn->isEqualTo(NMINTELFB_FN_END)) return endFrame(static_cast<NMIntelFBFrame *>(p1));
    if (fn && fn->isEqualTo(NVRM_VRAM_FN_SCANOUT)) {
        NVRMVramRequest *r = (NVRMVramRequest *)p1;
        if (!r || r->version != NVRM_VRAM_ABI_VERSION) return kIOReturnBadArgument;
        if (!fSurfMap) return kIOReturnNotReady;
        r->kva = (void *)fSurfMap->getVirtualAddress();
        r->phys = 0; r->actualSize = 0;
        if (fScanRes.phys) {
            r->phys = fScanRes.phys;
            r->actualSize = fScanRes.actualSize;
            r->handle = fScanRes.handle;
            r->allocationCookie = fScanRes.allocationCookie;
        }
        r->width = fWidth; r->height = fHeight; r->pitch = fRowBytes;
        return kIOReturnSuccess;
    }
    // intelfb-0011: what NVAccel asks its primary framebuffer for, answered from our NVKMS device
    if (fn && fn->isEqualTo(NVRM_GPUVA_FN_HANDLES)) {
        NVRMRmHandles *h = (NVRMRmHandles *)p1;
        if (!h || h->version != NVRM_GPUVA_ABI_VERSION) return kIOReturnBadArgument;
        IOLockLock(fVramLock);
        if (!fDev || __atomic_load_n(&fStopping, __ATOMIC_ACQUIRE)) { IOLockUnlock(fVramLock); return kIOReturnNotReady; }
        __atomic_store_n(&fKapiExposed, true, __ATOMIC_RELEASE);
        h->kapiDevice = fDev; h->hClient = 0; h->hDevice = 0; h->hSubDevice = 0;
        IOLockUnlock(fVramLock);
        return kIOReturnSuccess;
    }
    const bool alloc = fn && fn->isEqualTo(NVRM_VRAM_FN_ALLOC), release = fn && fn->isEqualTo(NVRM_VRAM_FN_FREE);
    if (alloc || release) {
        NVRMVramRequest *r = (NVRMVramRequest *)p1;
        if (!r || r->version != NVRM_VRAM_ABI_VERSION) return kIOReturnBadArgument;
        return alloc ? vramGrant(r) : vramRelease(r);
    }
    return IOFramebuffer::callPlatformFunction(fn, wait, p1, p2, p3, p4);
}

// intelfb-0013. CoreDisplay pairs a framebuffer with the GPU above it in the registry: under the Intel GPU the panel
// had no display pipe ("System set to use IOP but no display pipes are present", RTX-11) while NVAccel's pipes sat on
// the NVIDIA GPU with no framebuffer. NMIntelFBClaim holds the Intel GPU's framebuffer match category (so no
// IONDRVFramebuffer takes it, as NVRMFBClaim does on the NVIDIA GPU) and publishes an NVRMDisplay nub marked as the
// Intel panel under NVRM; NMIntelFB starts on that nub, as in nullmoth's virtual build, and drives the Intel GPU from
// gIfbPCI. nullmoth-intelfb-nvparent=0: no claim, NMIntelFB on the Intel GPU as before.
class NMIntelFBClaim : public IOService {
    OSDeclareDefaultStructors(NMIntelFBClaim)
    IOPCIDevice *fClaimPCI = nullptr;
    IOService *fPanelNub = nullptr;
    volatile bool fCancel = false, fPublishing = false;
public:
    IOService *probe(IOService *, SInt32 *) override;
    bool start(IOService *) override;
    void stop(IOService *) override;
    void free() override;
    static void publishThread(void *, wait_result_t);
};
OSDefineMetaClassAndStructors(NMIntelFBClaim, IOService)

IOService *NMIntelFBClaim::probe(IOService *provider, SInt32 *score)
{
    if (version_major != 24 && version_major != 25) return nullptr;
    IOPCIDevice *pci = OSDynamicCast(IOPCIDevice, provider);
    uint8_t ver = 0;
    if (!pci || !ifb_knob("nullmoth-intelfb-nvparent", 1) ||
        !ifb_knob("nullmoth-intelfb-dep", 1) || !ifb_pci_ok(pci, &ver) ||
        !ifb_firmware_supported(pci)) return nullptr;
    return IOService::probe(provider, score);
}

bool NMIntelFBClaim::start(IOService *provider)
{
    if (version_major != 24 && version_major != 25) return false;
    IOPCIDevice *pci = OSDynamicCast(IOPCIDevice, provider);
    if (!pci || !IOService::start(provider)) return false;
    IOLockLock(gIfbLock);
    if (gIfbPCI) { IOLockUnlock(gIfbLock); return false; }
    pci->retain();
    fClaimPCI = pci;
    gIfbPCI = pci;
    IOLockUnlock(gIfbLock);
    __atomic_store_n(&fPublishing, true, __ATOMIC_RELEASE);
    retain();
    thread_t thread = nullptr;
    if (kernel_thread_start(publishThread, this, &thread) == KERN_SUCCESS) {
        thread_deallocate(thread);
        return true;
    }
    __atomic_store_n(&fPublishing, false, __ATOMIC_RELEASE);
    release();
    IOLockLock(gIfbLock);
    if (gIfbPCI == pci) gIfbPCI = nullptr;
    IOLockUnlock(gIfbLock);
    OSSafeReleaseNULL(fClaimPCI);
    return false;
}

void NMIntelFBClaim::stop(IOService *provider)
{
    __atomic_store_n(&fCancel, true, __ATOMIC_RELEASE);
    while (__atomic_load_n(&fPublishing, __ATOMIC_ACQUIRE)) IOSleep(5);
    if (fPanelNub) {
        fPanelNub->terminate(kIOServiceRequired | kIOServiceSynchronous);
        OSSafeReleaseNULL(fPanelNub);
    }
    IOLockLock(gIfbLock);
    if (gIfbPCI == fClaimPCI) gIfbPCI = nullptr;
    IOLockUnlock(gIfbLock);
    OSSafeReleaseNULL(fClaimPCI);
    IOService::stop(provider);
}

void NMIntelFBClaim::free()
{
    OSSafeReleaseNULL(fPanelNub);
    OSSafeReleaseNULL(fClaimPCI);
    IOService::free();
}

void NMIntelFBClaim::publishThread(void *p, wait_result_t)
{
    NMIntelFBClaim *me = static_cast<NMIntelFBClaim *>(p);
    IOService *nub0 = nullptr;
    for (unsigned seconds = 0; seconds < 120 && !nub0 &&
         !__atomic_load_n(&me->fCancel, __ATOMIC_ACQUIRE); ++seconds) {
        if (ifb_nvrm_guard_off()) break;
        if (OSDictionary *matching = IOService::serviceMatching("NVRMDisplay")) {
            if (OSIterator *it = IOService::getMatchingServices(matching)) {
                while (OSObject *o = it->getNextObject()) {
                    IOService *n = OSDynamicCast(IOService, o);
                    OSNumber *index = n ? OSDynamicCast(OSNumber, n->getProperty("fb-index")) : nullptr;
                    if (index && index->unsigned32BitValue() == 0 && !n->getProperty("nm-intel-panel")) {
                        nub0 = n; nub0->retain(); break;
                    }
                }
                it->release();
            }
            matching->release();
        }
        if (!nub0) IOSleep(1000);
    }
    IOService *nvrm = nub0 ? nub0->getProvider() : nullptr;
    if (nvrm) nvrm->retain();
    IOService *nub = nvrm ? OSDynamicCast(IOService, OSMetaClass::allocClassWithName("NVRMDisplay")) : nullptr;
    bool attached = false;
    OSNumber *connected = nub0 ? OSDynamicCast(OSNumber, nub0->getProperty("connected-displays")) : nullptr;
    bool ok = nub && nub->init() && (!connected || !connected->unsigned32BitValue()) &&
              !__atomic_load_n(&me->fCancel, __ATOMIC_ACQUIRE);
    if (ok) {
        const char *keys[] = {"gpu-id"};
        for (const char *key : keys)
            if (OSObject *v = nub0->getProperty(key)) nub->setProperty(key, v);
        nub->setProperty("fb-index", 4ull, 32);
        nub->setProperty("nm-intel-panel", kOSBooleanTrue);
        attached = nub->attach(nvrm);
        ok = attached;
        if (ok) {
            nub->registerService();
            // Wait for success, including an explicitly configured takeover delay.
            // A failed start must not leave the firmware category claimed forever.
            ok = false;
            for (unsigned seconds = 0; seconds < 300 &&
                 !__atomic_load_n(&me->fCancel, __ATOMIC_ACQUIRE); ++seconds) {
                if (nub->getProperty("NMIntelFBReady") &&
                    (!ifb_knob("nullmoth-intelfb-auto", 1) || nub->getProperty("NMIntelFBActive"))) { ok = true; break; }
                if (nub->getProperty("NMIntelFBFailed")) break;
                IOSleep(1000);
            }
        }
    }
    if (ok) { me->fPanelNub = nub; nub = nullptr; }
    else {
        if (nub && attached) nub->terminate(kIOServiceRequired | kIOServiceSynchronous);
        // Suppress only this boot's automatic retry, allowing IONDRVFramebuffer
        // to match again rather than entering another 120-second claim loop.
        me->fClaimPCI->setProperty("NMIntelFBBridgeDeclined", kOSBooleanTrue);
    }
    OSSafeReleaseNULL(nub);
    OSSafeReleaseNULL(nub0);
    OSSafeReleaseNULL(nvrm);
    IFBLOG("claim: %s", ok ? "Intel panel ready under NVIDIA" : "declined; restoring firmware matching");
    IOPCIDevice *retry = !ok ? me->fClaimPCI : nullptr;
    if (retry) retry->retain();
    __atomic_store_n(&me->fPublishing, false, __ATOMIC_RELEASE);
    if (!ok) me->terminate(kIOServiceRequired | kIOServiceSynchronous);
    if (retry) { retry->registerService(); retry->release(); }
    me->release();
    thread_terminate(current_thread());
}

// intelfb-0014. WindowServer builds one display pipe per framebuffer that the GPU's AppleGraphicsDeviceControl lists
// in its reply to command 0x980 (nullmoth's NVRMAGDC); without an AGDC, IOPresentment logged "Unable to get AGDC
// information ... Unable to acquire map" and CoreDisplay "System set to use IOP but no display pipes are present"
// (RTX-12, with the panel already under the NVIDIA GPU). NVRMAGDC lists only NVRMFB framebuffers, so this one lists
// the panel: vendor info (1) and the framebuffer table (0x980), as NVRMAGDC answers them. Started by sysctl
// debug.nmintelfb_agdc=1 (tools/intelfb/enable.sh), in the order NVRM's auto-go uses: accelerator, AGDC, iop.
#pragma pack(push, 1)
struct IfbAgdcVendorInfo { uint32_t version; char vendor[0x20]; uint32_t vendorID, vendorClass; };
struct IfbAgdcGPUCapability {
    uint64_t portMask, portFeatureA, portFeatureB, portFeatureC;
    uint32_t countA, countB, countC, countD, maxFramebuffers;
    IOService *pciDevice;
    IOService *framebuffer[20];
};
#pragma pack(pop)
static_assert(sizeof(IfbAgdcVendorInfo) == 0x2c, "AGDCVendorInfo_t is 0x2c");
static_assert(sizeof(IfbAgdcGPUCapability) == 0xdc, "AGDCGPUCapability_t is 0xdc");

class NMIntelFBAGDC : public AppleGraphicsDeviceControl {
    OSDeclareDefaultStructors(NMIntelFBAGDC)
public:
    IOService *fPci = nullptr, *fFB = nullptr;
    void free() override {
        OSSafeReleaseNULL(fFB);
        OSSafeReleaseNULL(fPci);
        AppleGraphicsDeviceControl::free();
    }
    using AppleGraphicsDeviceControl::vendor_doDeviceAttribute;
    IOReturn vendor_doDeviceAttribute(unsigned int cmd, unsigned long *in, unsigned long inSize, unsigned long *out,
                                      unsigned long *outSize, IOExternalMethodArguments *args) override;
};
OSDefineMetaClassAndStructors(NMIntelFBAGDC, AppleGraphicsDeviceControl)

static int gIfbAgdcState = 0;   // 0 off, 2 starting, 1 up, < 0 failed
static NMIntelFBAGDC *gIfbAgdc;
static unsigned gIfbAgdcLogged = 0;

IOReturn NMIntelFBAGDC::vendor_doDeviceAttribute(unsigned int cmd, unsigned long *in, unsigned long inSize,
                                                 unsigned long *out, unsigned long *outSize, IOExternalMethodArguments *)
{
    if (cmd == 1) {
        if (!out || !outSize || *outSize != sizeof(IfbAgdcVendorInfo)) return kIOReturnBadArgument;
        IfbAgdcVendorInfo *v = (IfbAgdcVendorInfo *)out; bzero(v, sizeof *v);
        v->version = 0x30000; strlcpy(v->vendor, "NVIDIA", sizeof v->vendor); v->vendorID = 0x10de; v->vendorClass = 2;
        return kIOReturnSuccess;
    }
    if (cmd == 0x980) {
        if (!out || !outSize || *outSize != sizeof(IfbAgdcGPUCapability)) return kIOReturnBadArgument;
        IfbAgdcGPUCapability *c = (IfbAgdcGPUCapability *)out; bzero(c, sizeof *c);
        c->portMask = 1ull << 1;
        c->countA = c->countB = c->countC = c->countD = 1;
        c->maxFramebuffers = 1; c->pciDevice = fPci; c->framebuffer[0] = fFB;
        return kIOReturnSuccess;
    }
    // intelfb-0015: kAGDCLinkConfig (0x921) and the plane-scaler capability (0x711), which NVRMAGDC answers once NVRM's
    // auto-go sets debug.nvrmfb_agdc_k5; unanswered, CoreDisplay logged "Attempting to get capabilities ... with no
    // devices" and "No plane indices", and WindowServer sent no frame (RTX-13)
    if (cmd == NVK5_CMD_LINKCONFIG || cmd == NVK5_CMD_FBCAPEX) {
        int why = 0;
        int r = nvk5_answer(cmd, in, inSize, out, outSize ? *outSize : 0, gIfbK5, sizeof gIfbK5, &why);
        if (r == NVK5_R_LINK) { return kIOReturnSuccess; }
        if (r == NVK5_R_CAPS) { return kIOReturnSuccess; }
        if (r != NVK5_R_UNSUPPORTED) {
            if (gIfbAgdcLogged < 20) { gIfbAgdcLogged++; IFBLOG("AGDC K5: command %#x not answered (why %#x)", cmd, why); }
            return r == NVK5_R_BADARG ? kIOReturnBadArgument : kIOReturnNotFound;
        }
    }
    if (gIfbAgdcLogged < 20) { gIfbAgdcLogged++; IFBLOG("AGDC: command %#x (in %lu bytes) -> Unsupported", cmd, inSize); }
    return kIOReturnUnsupported;
}

static void ifb_agdc_up(void *p, wait_result_t)
{
    IOService *fb = static_cast<IOService *>(p), *pci = fb->getProvider();
    for (int hops = 0; pci && !OSDynamicCast(IOPCIDevice, pci) && hops < 8; hops++) pci = pci->getProvider();
    if (pci) pci->retain();
    NMIntelFBAGDC *d = (fb && OSDynamicCast(IOPCIDevice, pci)) ? OSTypeAlloc(NMIntelFBAGDC) : nullptr;
    int st = !d ? -1 : !d->init(nullptr) ? -2 : 0;
    if (!st) {
        d->fPci = pci; pci->retain();
        d->fFB = fb; fb->retain();
        if (!d->attach(pci)) st = -3;
        else if (!d->start(pci)) { d->detach(pci); st = -4; }
        else st = 1;
    }
    if (st != 1) OSSafeReleaseNULL(d);
    IOLockLock(gIfbLock);
    gIfbAgdc = d;
    gIfbAgdcState = st;
    IOLockWakeup(gIfbLock, &gIfbAgdcState, false);
    IOLockUnlock(gIfbLock);
    ifb_trail(st == 1 ? "agdc" : "agdc-fail");
    IFBLOG("AGDC: %s (state %d) on %s", st == 1 ? "UP" : "not started", st, pci ? pci->getName() : "no NVIDIA PCI device");
    OSSafeReleaseNULL(pci);
    fb->release();
    thread_terminate(current_thread());
}

static int ifb_agdc_sysctl SYSCTL_HANDLER_ARGS
{
    IOLockLock(gIfbLock);
    int v = gIfbAgdcState;
    IOLockUnlock(gIfbLock);
    const int err = sysctl_handle_int(oidp, &v, 0, req);
    if (err || !req->newptr) return err;
    if (v != 1) return EINVAL;
    IOLockLock(gIfbLock);
    if (gIfbAgdcState == 1 || gIfbAgdcState == 2) { IOLockUnlock(gIfbLock); return 0; }
    IOService *fb = gIfbNubFB;
    if (!fb) { IOLockUnlock(gIfbLock); return ENXIO; }
    fb->retain();
    gIfbAgdcState = 2;
    IOLockUnlock(gIfbLock);
    thread_t t = nullptr;
    if (kernel_thread_start(ifb_agdc_up, fb, &t) != KERN_SUCCESS) {
        IOLockLock(gIfbLock);
        gIfbAgdcState = -5;
        IOLockWakeup(gIfbLock, &gIfbAgdcState, false);
        IOLockUnlock(gIfbLock);
        fb->release();
        return ENOMEM;
    }
    thread_deallocate(t);
    return 0;
}
SYSCTL_PROC(_debug, OID_AUTO, nmintelfb_agdc, CTLTYPE_INT | CTLFLAG_RW | CTLFLAG_LOCKED | CTLFLAG_KERN, nullptr, 0,
            ifb_agdc_sysctl, "I", "1 = list the Intel panel in an AppleGraphicsDeviceControl on the NVIDIA GPU (1 up, 2 starting, < 0 failed)");
static void ifb_agdc_register(bool on)
{
    if (on) { sysctl_register_oid(&sysctl__debug_nmintelfb_agdc); return; }
    sysctl_unregister_oid(&sysctl__debug_nmintelfb_agdc);
    IOLockLock(gIfbLock);
    while (gIfbAgdcState == 2) IOLockSleep(gIfbLock, &gIfbAgdcState, THREAD_UNINT);
    NMIntelFBAGDC *d = gIfbAgdc;
    gIfbAgdc = nullptr;
    gIfbAgdcState = 0;
    IOLockUnlock(gIfbLock);
    if (d) { d->terminate(kIOServiceRequired | kIOServiceSynchronous); d->release(); }
}

static int ifb_ram_sysctl SYSCTL_HANDLER_ARGS
{
    IOLockLock(gIfbLock);
    NMIntelFB *fb = OSDynamicCast(NMIntelFB, gIfbNubFB);
    if (fb) fb->retain();
    IOLockUnlock(gIfbLock);
    if (!fb) return ENXIO;
    int mode = fb->ramMode();
    int error = sysctl_handle_int(oidp, &mode, 0, req);
    if (!error && req->newptr) {
        const int result = fb->ramSet(mode);
        if (result < 0) error = -result;
    }
    fb->release();
    return error;
}
SYSCTL_PROC(_debug, OID_AUTO, nmintelfb_ram, CTLTYPE_INT | CTLFLAG_RW | CTLFLAG_LOCKED | CTLFLAG_KERN,
            nullptr, 0, ifb_ram_sysctl, "I", "experimental: 0 firmware pages, 1 RAM, 2 double-buffered RAM; negative = quarantined");
SYSCTL_QUAD(_debug, OID_AUTO, nmintelfb_flips, CTLFLAG_RD | CTLFLAG_LOCKED,
            (SInt64 *)&gIfbFlips, "completed frames queued for Intel RAM scanout");
SYSCTL_QUAD(_debug, OID_AUTO, nmintelfb_flip_late, CTLFLAG_RD | CTLFLAG_LOCKED,
            (SInt64 *)&gIfbLate, "flip waits refused; the buffer was not reused");
static void ifb_ram_register(bool on)
{
    sysctl_oid *oids[] = {&sysctl__debug_nmintelfb_ram, &sysctl__debug_nmintelfb_flips, &sysctl__debug_nmintelfb_flip_late};
    for (sysctl_oid *oid : oids) {
        if (on) sysctl_register_oid(oid);
        else sysctl_unregister_oid(oid);
    }
}

#include "scanout.inc"

#ifdef NMINTELFB_VIRTUAL
// Test loader: attaches a virtual panel under the NVIDIA PCI device on demand, the way the real driver will place the
// Intel panel so WindowServer composites it on the NVIDIA accelerator.
class NMIntelFBLoader : public IOService {
    OSDeclareDefaultStructors(NMIntelFBLoader)
public:
    bool start(IOService *provider) override;
    void stop(IOService *provider) override;
};
OSDefineMetaClassAndStructors(NMIntelFBLoader, IOService)
static int gAttach = 0;
static int nmintelfb_attach_sysctl SYSCTL_HANDLER_ARGS
{
    int v = gAttach, err = sysctl_handle_int(oidp, &v, 0, req);
    if (err || !req->newptr || v != 1 || gAttach == 1) return err;
    // A spare NVRMDisplay nub under NVRM (NVRM's own class), marked as the Intel panel: NMIntelFB claims it ahead of
    // NVRMFB, so the panel sits where NVRMFB does (GPU -> NVRM -> NVRMDisplay -> framebuffer). Starting a framebuffer
    // directly on the NVIDIA PCI device hung the kernel.
    IOService *nub0 = nullptr, *nvrm = nullptr;
    if (OSDictionary *m = IOService::serviceMatching("NVRMDisplay")) {
        if (OSIterator *it = IOService::getMatchingServices(m)) {
            while (OSObject *o = it->getNextObject()) {
                IOService *n = OSDynamicCast(IOService, o);
                OSNumber *fi = n ? OSDynamicCast(OSNumber, n->getProperty("fb-index")) : nullptr;
                if (fi && fi->unsigned32BitValue() == 0) { nub0 = n; nub0->retain(); break; }
            }
            it->release();
        }
        m->release();
    }
    if (nub0) { nvrm = nub0->getProvider(); if (nvrm) nvrm->retain(); }
    if (!nub0 || !nvrm) { OSSafeReleaseNULL(nub0); OSSafeReleaseNULL(nvrm); IFBLOG("attach: no NVRMDisplay 0 / NVRM"); return ENODEV; }
    IOService *nub = OSDynamicCast(IOService, OSMetaClass::allocClassWithName("NVRMDisplay"));
    if (!nub || !nub->init()) { OSSafeReleaseNULL(nub); nub0->release(); nvrm->release(); return ENOMEM; }
    static const char *const kCopy[] = {"gpu-id"};
    for (const char *k : kCopy) if (OSObject *v = nub0->getProperty(k)) nub->setProperty(k, v);
    nub->setProperty("fb-index", 4ull, 32);
    nub->setProperty("nm-intel-panel", kOSBooleanTrue);
    nub0->release();
    if (!nub->attach(nvrm)) { nub->release(); nvrm->release(); return EIO; }
    nvrm->release();
    nub->registerService();
    nub->release();
    gAttach = 1;
    IFBLOG("attach: Intel-panel NVRMDisplay nub published under NVRM");
    return 0;
}
SYSCTL_PROC(_debug, OID_AUTO, nmintelfb_attach, CTLTYPE_INT | CTLFLAG_RW | CTLFLAG_LOCKED | CTLFLAG_KERN, nullptr, 0,
            nmintelfb_attach_sysctl, "I", "1 = attach the virtual panel under the NVIDIA display device (test build)");
bool NMIntelFBLoader::start(IOService *provider)
{
    if (version_major != 24 && version_major != 25) return false;   /* the 2.x line supports macOS 15 (Darwin 24) and macOS 26 (Darwin 25) only */
    if (!IOService::start(provider)) return false;
    sysctl_register_oid(&sysctl__debug_nmintelfb_attach);
    IFBLOG("loader idle until debug.nmintelfb_attach=1");
    return true;
}
void NMIntelFBLoader::stop(IOService *provider)
{
    sysctl_unregister_oid(&sysctl__debug_nmintelfb_attach);
    IOService::stop(provider);
}
#endif
