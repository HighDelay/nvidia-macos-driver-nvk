// NMGopFB: panel provider for laptops whose built-in screen is wired to an integrated AMD GPU macOS has no driver for
// (Rembrandt, Phoenix, Hawk Point, Strix and the like).
//
// It writes nothing to the display engine. The firmware (GOP) lit the panel and left the GPU scanning out a plain 32-bit
// framebuffer. The claim holds the AMD device's framebuffer category and NVRM starts the framebuffer under the
// NVIDIA device. The pixels are still copied into AMD memory through a write-combined mapping.
// If anything about the framebuffer is not what it expects, start() fails with the reason logged and the firmware
// framebuffer keeps the device exactly as before.
//
// NVAccel submits copies through a kernel-only call. NMGopFB keeps the AMD mapping private.
//
// The idea, and the way the firmware framebuffer is found, checked against the GPU's BAR and mapped write-combined, come
// from PrimeBridge (https://github.com/myvoow/PrimeBridge, BootFramebuffer.cpp), MIT License, Copyright (c) 2026 myvoow.
// Permission is hereby granted, free of charge, to any person obtaining a copy of that software and associated
// documentation files, to deal in it without restriction, subject to the following condition: the above copyright
// notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE
// IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND; the full MIT text is in NOTICE.
//
// The bridge is opt-in with nmhybrid=1. -nmgopfboff leaves the display to the firmware driver.

#include <IOKit/IOLib.h>
// IOPCIDevice (with its setBusMasterEnable) is marked deprecated in favour of PCIDriverKit, which runs in user space and
// cannot replace a kernel extension's PCI access; that deprecation is ignored from here on in this kext's sources.
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
#include <IOKit/pci/IOPCIDevice.h>
#include <IOKit/graphics/IOFramebuffer.h>
#include <IOKit/ndrvsupport/IOMacOSVideo.h>
#include <IOKit/IOPlatformExpert.h>
#include <IOKit/IODeviceMemory.h>
#include <kern/sched_prim.h>
#include <mach/mach_time.h>
#include <libkern/version.h>
#include "../NVRM/nvrm_vram_abi.h"
#include "../NVRM/nvrm_gpuva_abi.h"
#include "../NVRM/nvrm_display_identity.h"
#include "../NVRM/nvrm_gopfb.h"
#include "../NVRM/accel/nv_iop_pixel.h"
#include "nm_gopfb.h"
#include "../NVRMFB/nvrm_off.h"

#define GFBLOG(fmt, ...) IOLog("NMGopFB: " fmt "\n", ##__VA_ARGS__)

#include <mach/kmod.h>
extern "C" kern_return_t _start(kmod_info_t *ki, void *d); extern "C" kern_return_t _stop(kmod_info_t *ki, void *d);
extern "C" { KMOD_EXPLICIT_DECL(com.nullmoth.NMGopFB, "0.1", _start, _stop) }
extern "C" kern_return_t _start(kmod_info_t *ki, void *d) { return KERN_SUCCESS; }
extern "C" kern_return_t _stop(kmod_info_t *ki, void *d)  { return KERN_SUCCESS; }

namespace {
constexpr char kDeclined[] = "NMGopFBDeclined";
constexpr uint64_t kFrameNs = 16666667ull;
IOPMPowerState kPowerStates[2] = {
    { kIOPMPowerStateVersion1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 },
    { kIOPMPowerStateVersion1, kIOPMDeviceUsable, kIOPMPowerOn, kIOPMPowerOn, 0, 0, 0, 0, 0, 0, 0, 0 },
};

int firmwareGeometry(IOPCIDevice *pci, nmgop_geom *out)
{
    PE_Video video = {};
    IOPlatformExpert *platform = IOService::getPlatform();
    if (!platform || platform->getConsoleInfo(&video) != kIOReturnSuccess) return NMGOP_E_EMPTY;
    nmgop_bar bars[6] = {};
    const unsigned regs[] = { kIOPCIConfigBaseAddress0, kIOPCIConfigBaseAddress1, kIOPCIConfigBaseAddress2,
                              kIOPCIConfigBaseAddress3, kIOPCIConfigBaseAddress4, kIOPCIConfigBaseAddress5 };
    unsigned count = 0;
    for (unsigned reg : regs) {
        if (IODeviceMemory *memory = pci->getDeviceMemoryWithRegister((UInt8)reg))
            bars[count++] = {memory->getPhysicalAddress(), memory->getLength()};
    }
    return nmgop_geometry(video.v_baseAddr, video.v_width, video.v_height, video.v_rowBytes, video.v_depth,
                          bars, count, out);
}

IOReturn call(IOService *service, const char *name, void *arg)
{
    const OSSymbol *fn = OSSymbol::withCStringNoCopy(name);
    if (!fn) return kIOReturnNoMemory;
    IOReturn status = service->callPlatformFunction(fn, false, arg, nullptr, nullptr, nullptr);
    fn->release();
    return status;
}
}

class NMGopFBDisplay : public IOService {
    OSDeclareDefaultStructors(NMGopFBDisplay)
};
OSDefineMetaClassAndStructors(NMGopFBDisplay, IOService)

class NMGopFB : public IOFramebuffer {
    OSDeclareDefaultStructors(NMGopFB)
    IOMemoryMap *fSurfMap = nullptr;
    IOService *fResources = nullptr;
    NVRMVramRequest fProxy = {};
    nmgop_geom fGeometry = {};
    IOLock *fLock = nullptr;
    struct Irq { IOFBInterruptProc proc = nullptr; OSObject *target = nullptr; void *ref = nullptr; };
    Irq fVbl, fConnect;
    thread_t fVblThread = nullptr;
    bool fVblEnabled = false, fInVbl = false, fStopping = false;
    bool fPowered = true, fGpuReady = true;
    static void vblWorker(void *, wait_result_t);
    void drainVbl();
    void setPanelPower(bool on);
    IOReturn copyFrame(const NvrmGopCopy *frame);

public:
    bool configure(IOPCIDevice *pci, IOService *resources);
    bool start(IOService *provider) override;
    void stop(IOService *provider) override;
    void free() override;

    IOReturn enableController() override { return kIOReturnSuccess; }
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
    IOReturn getTimingInfoForDisplayMode(IODisplayModeID mode, IOTimingInformation *info) override {
        if (mode != 1 || !info) return kIOReturnUnsupportedMode;
        bzero(info, sizeof(*info));
        info->appleTimingID = kIOTimingIDInvalid;      // no timing is known: the firmware's, which this driver never reads
        return kIOReturnSuccess;
    }
    IOReturn setDisplayMode(IODisplayModeID mode, IOIndex depth) override { return (mode == 1 && depth == 0) ? kIOReturnSuccess : kIOReturnUnsupported; }
    IOItemCount getConnectionCount() override { return 1; }
    IOReturn callPlatformFunction(const OSSymbol *fn, bool wait, void *p1, void *p2, void *p3, void *p4) override;
    IOReturn registerForInterruptType(IOSelect type, IOFBInterruptProc proc, OSObject *target, void *ref, void **iref) override;
    IOReturn unregisterInterrupt(void *) override;
    IOReturn setInterruptState(void *, UInt32) override;
    IOReturn setGammaTable(UInt32, UInt32, UInt32, void *) override { return kIOReturnSuccess; }
    IOReturn setGammaTable(UInt32, UInt32, UInt32, void *, bool) override { return kIOReturnSuccess; }
    IOReturn setCLUTWithEntries(IOColorEntry *, UInt32, UInt32, IOOptionBits) override { return kIOReturnSuccess; }
    IOReturn setApertureEnable(IOPixelAperture, IOOptionBits) override { return kIOReturnSuccess; }
    IOReturn setStartupDisplayMode(IODisplayModeID, IOIndex) override { return kIOReturnSuccess; }
    // Acknowledge power changes without programming the AMD display engine.
    IOReturn setPowerState(unsigned long ordinal, IOService *) override {
        setPanelPower(ordinal != 0);
        handleEvent(ordinal ? kIOFBNotifyDidPowerOn : kIOFBNotifyWillPowerOff);
        return kIOPMAckImplied;
    }
    IOReturn setAttribute(IOSelect attribute, uintptr_t value) override {
        if (attribute == kIOWindowServerActiveAttribute) return kIOReturnSuccess;
        if (attribute == kIOPowerAttribute) {
            if (!value) { setPanelPower(false); handleEvent(kIOFBNotifyWillPowerOff); }
            IOReturn r = IOFramebuffer::setAttribute(attribute, value);
            if (value) { setPanelPower(true); handleEvent(kIOFBNotifyDidPowerOn); }
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

OSDefineMetaClassAndStructors(NMGopFB, IOFramebuffer)

bool NMGopFB::configure(IOPCIDevice *pci, IOService *resources)
{
    // Re-read the console after NVIDIA startup, which can change the console owner.
    const int why = firmwareGeometry(pci, &fGeometry);
    if (why != NMGOP_OK) { GFBLOG("%s - leaving the firmware screen", nmgop_why_name(why)); return false; }
    uint32_t timing[13];
    if (!nmgop_logical_timing(fGeometry.width, fGeometry.height, timing)) return false;
    OSData *data = OSData::withBytes(timing, sizeof timing);
    const bool published = data && setProperty("NVRMTiming", data);
    OSSafeReleaseNULL(data);
    if (!published) return false;

    fLock = IOLockAlloc();
    if (!fLock) return false;
    // XNU chooses the cache mode when the descriptor is created.
    constexpr IOOptionBits kCacheShift = 28;
    IOMemoryDescriptor *md = IOMemoryDescriptor::withAddressRange(fGeometry.phys, fGeometry.bytes,
        kIODirectionInOut | kIOMemoryMapperNone | (kIOWriteCombineCache << kCacheShift), TASK_NULL);
    fSurfMap = md ? md->createMappingInTask(kernel_task, 0, kIOMapAnywhere | kIOMapWriteCombineCache) : nullptr;
    OSSafeReleaseNULL(md);
    if (!fSurfMap || !fSurfMap->getVirtualAddress() || fSurfMap->getLength() < fGeometry.bytes) return false;

    // IOPresentment needs a NVIDIA resource to describe the pipe. Its memory
    // belongs to NVIDIA. Only copyFrame() can write to the AMD aperture.
    resources->retain();
    fResources = resources;
    fProxy.version = NVRM_VRAM_ABI_VERSION;
    fProxy.size = uint64_t(fGeometry.width) * fGeometry.height * 4;
    if (call(resources, NVRM_VRAM_FN_ALLOC, &fProxy) != kIOReturnSuccess ||
        !fProxy.handle || !fProxy.phys || fProxy.actualSize < fProxy.size) return false;
    return true;
}

bool NMGopFB::start(IOService *provider)
{
    if (!fSurfMap || !fProxy.handle || !OSDynamicCast(NMGopFBDisplay, provider)) return false;
    // The family walks controller-local members starting at zero. Keep the
    // AMD controller separate while reporting stable driver slot 4.
    if (!setProperty("IOFBDependentID", kNvrmGopController, 64) ||
        !setProperty("IOFBDependentIndex", 0ull, 32) ||
        !setProperty(NVRM_DISPLAY_INDEX_KEY, kNvrmGopSlot, 32) ||
        !setProperty("built-in", kOSBooleanTrue)) return false;
    IOLockLock(fLock);
    retain();
    const bool worker = kernel_thread_start(vblWorker, this, &fVblThread) == KERN_SUCCESS;
    if (worker) thread_deallocate(fVblThread);
    IOLockUnlock(fLock);
    if (!worker) { release(); return false; }
    if (!IOFramebuffer::start(provider)) { drainVbl(); return false; }
    registerPowerDriver(this, kPowerStates, 2);
    GFBLOG("panel published under NVIDIA: %ux%u stride %u", fGeometry.width, fGeometry.height, fGeometry.pitch);
    return true;
}

void NMGopFB::stop(IOService *provider)
{
    drainVbl();
    unregisterInterrupt(&fVbl);
    unregisterInterrupt(&fConnect);
    OSSafeReleaseNULL(fSurfMap);
    IOFramebuffer::stop(provider);
}

void NMGopFB::free()
{
    OSSafeReleaseNULL(fSurfMap);
    if (fResources && fProxy.handle) call(fResources, NVRM_VRAM_FN_FREE, &fProxy);
    OSSafeReleaseNULL(fResources);
    if (fLock) IOLockFree(fLock);
    IOFramebuffer::free();
}

void NMGopFB::vblWorker(void *arg, wait_result_t)
{
    NMGopFB *fb = static_cast<NMGopFB *>(arg);
    IOLockLock(fb->fLock);
    uint64_t period;
    nanoseconds_to_absolutetime(kFrameNs, &period);
    uint64_t next = mach_absolute_time() + period;
    while (!fb->fStopping) {
        if (!fb->fVblEnabled || !fb->fPowered || !fb->fGpuReady) {
            IOLockSleep(fb->fLock, &fb->fVblThread, THREAD_UNINT);
            next = mach_absolute_time() + period;
            continue;
        }
        if (mach_absolute_time() < next) {
            IOLockSleepDeadline(fb->fLock, &fb->fVblThread, next, THREAD_UNINT);
            continue;
        }
        const Irq irq = fb->fVbl;
        if (irq.target) irq.target->retain();
        fb->fInVbl = true;
        IOLockUnlock(fb->fLock);
        irq.proc(irq.target, irq.ref);
        if (irq.target) irq.target->release();
        IOLockLock(fb->fLock);
        fb->fInVbl = false;
        IOLockWakeup(fb->fLock, &fb->fInVbl, false);
        next += period;
        const uint64_t now = mach_absolute_time();
        if (next <= now) next = now + period;
    }
    fb->fVblThread = nullptr;
    IOLockWakeup(fb->fLock, &fb->fVblThread, false);
    IOLockUnlock(fb->fLock);
    fb->release();
    thread_terminate(current_thread());
}

void NMGopFB::drainVbl()
{
    IOLockLock(fLock);
    fStopping = true;
    IOLockWakeup(fLock, &fVblThread, false);
    while (fVblThread) IOLockSleep(fLock, &fVblThread, THREAD_UNINT);
    IOLockUnlock(fLock);
}

void NMGopFB::setPanelPower(bool on)
{
    IOLockLock(fLock);
    fPowered = on;
    IOLockWakeup(fLock, &fVblThread, false);
    IOLockUnlock(fLock);
}

IOReturn NMGopFB::registerForInterruptType(IOSelect type, IOFBInterruptProc proc, OSObject *target, void *ref, void **iref)
{
    if (!proc || !iref) return kIOReturnBadArgument;
    Irq *irq = type == kIOFBVBLInterruptType ? &fVbl : type == kIOFBConnectInterruptType ? &fConnect : nullptr;
    if (!irq) return kIOReturnUnsupported;
    IOLockLock(fLock);
    const bool available = !fStopping && !irq->proc;
    if (available) {
        if (target) target->retain();
        *irq = {proc, target, ref};
        *iref = irq;
        if (irq == &fVbl) fVblEnabled = true;
        IOLockWakeup(fLock, &fVblThread, false);
    }
    IOLockUnlock(fLock);
    return available ? kIOReturnSuccess : kIOReturnBusy;
}

IOReturn NMGopFB::unregisterInterrupt(void *ref)
{
    if (ref != &fVbl && ref != &fConnect) return kIOReturnBadArgument;
    IOLockLock(fLock);
    Irq *irq = static_cast<Irq *>(ref);
    OSObject *target = irq->target;
    *irq = {};
    if (irq == &fVbl) {
        fVblEnabled = false;
        while (fInVbl && current_thread() != fVblThread) IOLockSleep(fLock, &fInVbl, THREAD_UNINT);
    }
    IOLockUnlock(fLock);
    OSSafeReleaseNULL(target);
    return kIOReturnSuccess;
}

IOReturn NMGopFB::setInterruptState(void *ref, UInt32 state)
{
    if (ref != &fVbl && ref != &fConnect) return kIOReturnBadArgument;
    IOLockLock(fLock);
    if (ref == &fVbl) fVblEnabled = fVbl.proc && state != 0;
    IOLockWakeup(fLock, &fVblThread, false);
    IOLockUnlock(fLock);
    return kIOReturnSuccess;
}

IODeviceMemory *NMGopFB::getApertureRange(IOPixelAperture aperture)
{
    if (aperture != kIOFBSystemAperture) return nullptr;
    return IODeviceMemory::withRange(fGeometry.phys, fGeometry.bytes);
}

IOReturn NMGopFB::getInformationForDisplayMode(IODisplayModeID mode, IODisplayModeInformation *info)
{
    if (mode != 1 || !info) return kIOReturnBadArgument;
    bzero(info, sizeof(*info));
    info->maxDepthIndex = 0; info->nominalWidth = fGeometry.width; info->nominalHeight = fGeometry.height;
    info->refreshRate = 60 << 16; info->flags = kDisplayModeValidFlag | kDisplayModeSafeFlag | kDisplayModeDefaultFlag;
    return kIOReturnSuccess;
}

IOReturn NMGopFB::getPixelInformation(IODisplayModeID mode, IOIndex depth, IOPixelAperture aperture, IOPixelInformation *pi)
{
    if (mode != 1 || depth != 0 || aperture != kIOFBSystemAperture || !pi) return kIOReturnUnsupportedMode;
    bzero(pi, sizeof(*pi));
    pi->bytesPerRow = fGeometry.pitch; pi->bytesPerPlane = 0; pi->bitsPerPixel = 32; pi->pixelType = kIORGBDirectPixels;
    pi->componentCount = 3; pi->bitsPerComponent = 8;
    pi->componentMasks[0] = 0x00FF0000; pi->componentMasks[1] = 0x0000FF00; pi->componentMasks[2] = 0x000000FF;
    strlcpy(pi->pixelFormat, IO32BitDirectPixels, sizeof(pi->pixelFormat));
    pi->activeWidth = fGeometry.width; pi->activeHeight = fGeometry.height;
    return kIOReturnSuccess;
}

IOReturn NMGopFB::copyFrame(const NvrmGopCopy *frame)
{
    if (!frame || !frame->pixels || frame->width != fGeometry.width || frame->height != fGeometry.height ||
        frame->pitch < frame->width * 4 || (frame->pitch & 3) ||
        (frame->format != NV_IOP_FMT_BGRA && frame->format != NV_IOP_FMT_L10R)) return kIOReturnBadArgument;

    IOLockLock(fLock);
    if (fStopping || !fPowered || !fGpuReady) {
        IOLockUnlock(fLock);
        return kIOReturnNotReady;
    }
    auto *dst = reinterpret_cast<volatile unsigned *>(fSurfMap->getVirtualAddress());
    for (unsigned y = 0; y < frame->height; ++y) {
        const volatile unsigned *source = frame->pixels + size_t(y) * (frame->pitch / 4);
        volatile unsigned *row = dst + size_t(y) * (fGeometry.pitch / 4);
        nv_iop_convert_row(row, source, frame->width, frame->format, 0);
    }
    __asm__ volatile("sfence" ::: "memory");
    IOLockUnlock(fLock);
    return kIOReturnSuccess;
}

IOReturn NMGopFB::callPlatformFunction(const OSSymbol *fn, bool wait, void *p1, void *p2, void *p3, void *p4)
{
    if (fn && fn->isEqualTo(NVRM_GOP_COPY)) return copyFrame(static_cast<const NvrmGopCopy *>(p1));
    if (fn && fn->isEqualTo(NVRM_GOP_RESOURCE)) {
        auto *r = static_cast<NVRMVramRequest *>(p1);
        if (!r || r->version != NVRM_VRAM_ABI_VERSION) return kIOReturnBadArgument;
        r->phys = fProxy.phys;
        r->actualSize = fProxy.actualSize;
        r->width = fGeometry.width;
        r->height = fGeometry.height;
        r->pitch = fGeometry.width * 4;
        return kIOReturnSuccess;
    }
    if (fn && fn->isEqualTo("NVRMGpuPower")) {
        IOLockLock(fLock);
        fGpuReady = reinterpret_cast<uintptr_t>(p1) == 1;
        IOLockWakeup(fLock, &fVblThread, false);
        IOLockUnlock(fLock);
        return kIOReturnSuccess;
    }
    if (fn && (fn->isEqualTo(NVRM_VRAM_FN_ALLOC) || fn->isEqualTo(NVRM_VRAM_FN_FREE) ||
               fn->isEqualTo(NVRM_GPUVA_FN_HANDLES)))
        return fResources->callPlatformFunction(fn, wait, p1, p2, p3, p4);
    return IOFramebuffer::callPlatformFunction(fn, wait, p1, p2, p3, p4);
}

// No backlight handler: the display engine is not touched, so the panel is a plain display without a brightness slider.
IOReturn NMGopFB::getAppleSense(IOIndex, UInt32 *, UInt32 *, UInt32 *, UInt32 *)
{
    return kIOReturnUnsupported;
}

IOReturn NMGopFB::getAttributeForConnection(IOIndex idx, IOSelect attr, uintptr_t *value)
{
    if (idx != 0 || !value) return kIOReturnBadArgument;
    switch (attr) {
    case kConnectionEnable: *value = 1; return kIOReturnSuccess;
    case kConnectionCheckEnable: *value = 1; return kIOReturnSuccess;
    case kConnectionFlags: *value = kIOConnectionBuiltIn; return kIOReturnSuccess;
    case kConnectionColorModesSupported: case kConnectionColorMode: *value = 0x00000001; return kIOReturnSuccess;
    case kConnectionColorDepthsSupported: case kConnectionControllerColorDepth: case kConnectionControllerDepthsSupported:
        *value = 0x00000002; return kIOReturnSuccess;
    default: return IOFramebuffer::getAttributeForConnection(idx, attr, value);
    }
}

class NMGopFBClaim : public IOService {
    OSDeclareDefaultStructors(NMGopFBClaim)
    IOLock *fLock = nullptr;
    NMGopFBDisplay *fNub = nullptr;
    NMGopFB *fPanel = nullptr;
    bool fStarting = false, fWaiting = false, fDone = false, fActive = false, fStopping = false;

    static void waitForHandoff(void *, wait_result_t);
    IOReturn attachPanel(IOService *rm, IOService *resources);
public:
    IOService *probe(IOService *, SInt32 *) override;
    bool start(IOService *) override;
    void stop(IOService *) override;
    void free() override;
    IOReturn callPlatformFunction(const OSSymbol *, bool, void *, void *, void *, void *) override;
};
OSDefineMetaClassAndStructors(NMGopFBClaim, IOService)

IOService *NMGopFBClaim::probe(IOService *provider, SInt32 *score)
{
    if (!nvrm_gopfb_enabled() || nvrm_driver_off() || (version_major != 24 && version_major != 25)) return nullptr;
    auto *pci = OSDynamicCast(IOPCIDevice, provider);
    if (!pci || pci->getProperty(kDeclined) || pci->configRead16(kIOPCIConfigVendorID) != NMGOP_VENDOR_AMD) return nullptr;
    uint32_t overrideId = 0;
    PE_parse_boot_argn("nmgopid", &overrideId, sizeof overrideId);
    OSDictionary *match = serviceMatching("NMXGPU");
    IOService *nmx = match ? copyMatchingService(match) : nullptr;
    const int selected = nmgop_select(NMGOP_VENDOR_AMD, pci->configRead16(kIOPCIConfigDeviceID), 0, nmx != nullptr, overrideId);
    OSSafeReleaseNULL(nmx);
    OSSafeReleaseNULL(match);
    if (selected != NMGOP_USE_GOPFB) return nullptr;
    nmgop_geom geom = {};
    const int why = firmwareGeometry(pci, &geom);
    if (why != NMGOP_OK) {
        GFBLOG("%s - leaving the firmware screen", nmgop_why_name(why));
        return nullptr;
    }
    return IOService::probe(provider, score);
}

bool NMGopFBClaim::start(IOService *provider)
{
    if (nvrm_driver_off() || !IOService::start(provider)) return false;
    fLock = IOLockAlloc();
    if (!fLock) return false;
    fWaiting = true;
    retain();
    thread_t thread = nullptr;
    if (kernel_thread_start(waitForHandoff, this, &thread) != KERN_SUCCESS) {
        fWaiting = false;
        release();
        return false;
    }
    thread_deallocate(thread);
    registerService();
    return true;
}

void NMGopFBClaim::waitForHandoff(void *arg, wait_result_t)
{
    auto *claim = static_cast<NMGopFBClaim *>(arg);
    // Allow the existing small-BAR settling time and boot-hold budget. NVRM
    // sends the outcome immediately on a normal boot, including a failed go.
    uint32_t settleMs = 100000, holdSec = 120;
    PE_parse_boot_argn("nvrmsettle", &settleMs, sizeof settleMs);
    uint32_t requestedHold = 0;
    if (PE_parse_boot_argn("nvrmholdcap", &requestedHold, sizeof requestedHold) && requestedHold >= 40 && requestedHold <= 600)
        holdSec = requestedHold;
    uint64_t deadline;
    clock_interval_to_deadline(settleMs / 1000 + holdSec + 20, kSecondScale, &deadline);

    IOLockLock(claim->fLock);
    while (!claim->fDone && !claim->fStopping && mach_absolute_time() < deadline)
        IOLockSleepDeadline(claim->fLock, &claim->fDone, deadline, THREAD_UNINT);
    claim->fDone = true;
    IOService *retry = !claim->fActive && !claim->fStopping ? claim->getProvider() : nullptr;
    if (retry) {
        retry->retain();
        retry->setProperty(kDeclined, kOSBooleanTrue);
    }
    claim->fWaiting = false;
    IOLockWakeup(claim->fLock, &claim->fDone, false);
    IOLockUnlock(claim->fLock);
    if (retry) {
        GFBLOG("NVIDIA handoff did not complete - restoring firmware framebuffer matching");
        claim->terminate(kIOServiceRequired | kIOServiceSynchronous);
        retry->registerService();
        retry->release();
    }
    claim->release();
    thread_terminate(current_thread());
}

IOReturn NMGopFBClaim::attachPanel(IOService *rm, IOService *resources)
{
    auto *pci = OSDynamicCast(IOPCIDevice, getProvider());
    IOService *nvidia = rm ? rm->getProvider() : nullptr;
    if (!pci || !rm || !rm->metaCast("NVRM") || !nvidia || !resources || resources->isInactive() ||
        nvrmFramebufferPCIOwner(resources) != nvidia ||
        !(resources->metaCast(NVRM_HEADLESS_RESOURCES) || resources->metaCast("NVRMNVDAFramebuffer")))
        return kIOReturnBadArgument;
    IOLockLock(fLock);
    const bool available = !fDone && !fStopping && !fStarting && !fPanel;
    if (available) fStarting = true;
    IOLockUnlock(fLock);
    if (!available) return kIOReturnNotReady;

    NMGopFBDisplay *nub = OSTypeAlloc(NMGopFBDisplay);
    NMGopFB *panel = OSTypeAlloc(NMGopFB);
    const bool nubAttached = nub && nub->init() && nub->attach(rm);
    if (nubAttached) nub->registerService();
    const bool panelAttached = nubAttached && panel && panel->init() && panel->configure(pci, resources) && panel->attach(nub);
    const bool started = panelAttached && panel->start(nub);
    if (!started) {
        if (panelAttached) panel->detach(nub);
        OSSafeReleaseNULL(panel);
        if (nubAttached) nub->terminate(kIOServiceRequired | kIOServiceSynchronous);
        OSSafeReleaseNULL(nub);
    }
    IOLockLock(fLock);
    fNub = nub;
    fPanel = panel;
    fStarting = false;
    IOLockWakeup(fLock, &fDone, false);
    IOLockUnlock(fLock);
    return started ? kIOReturnSuccess : kIOReturnNotReady;
}

IOReturn NMGopFBClaim::callPlatformFunction(const OSSymbol *fn, bool wait, void *p1, void *p2, void *p3, void *p4)
{
    if (fn && fn->isEqualTo(NVRM_GOP_ATTACH))
        return attachPanel(static_cast<IOService *>(p1), static_cast<IOService *>(p2));
    if (fn && fn->isEqualTo(NVRM_GOP_FINISH)) {
        IOLockLock(fLock);
        if (!fDone) {
            fActive = p1 && fPanel && !fStopping && !fPanel->isInactive() &&
                      fPanel->getProperty(NVRM_GOP_AGDC) == kOSBooleanTrue;
            fDone = true;
            if (fActive) fPanel->setProperty("NMGopFBActive", kOSBooleanTrue);
            else if (fPanel) fPanel->removeProperty(NVRM_GOP_PIPE);
            IOLockWakeup(fLock, &fDone, false);
        }
        const bool active = fActive;
        IOLockUnlock(fLock);
        return active || !p1 ? kIOReturnSuccess : kIOReturnNotReady;
    }
    return IOService::callPlatformFunction(fn, wait, p1, p2, p3, p4);
}

void NMGopFBClaim::stop(IOService *provider)
{
    IOLockLock(fLock);
    fStopping = true;
    IOLockWakeup(fLock, &fDone, false);
    while (fStarting || fWaiting) IOLockSleep(fLock, &fDone, THREAD_UNINT);
    IOLockUnlock(fLock);
    if (fPanel) {
        fPanel->terminate(kIOServiceRequired | kIOServiceSynchronous);
        OSSafeReleaseNULL(fPanel);
    }
    if (fNub) {
        fNub->terminate(kIOServiceRequired | kIOServiceSynchronous);
        OSSafeReleaseNULL(fNub);
    }
    IOService::stop(provider);
}

void NMGopFBClaim::free()
{
    if (fLock) IOLockFree(fLock);
    IOService::free();
}
