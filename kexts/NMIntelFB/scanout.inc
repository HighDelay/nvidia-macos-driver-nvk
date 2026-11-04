// Experimental system-RAM scanout, adapted from nmx-intel stages 4a/4b.
// This file is part of intelfb.cpp; all transitions hold fRamLock.

void NMIntelFB::quarantineRam()
{
    fFrameGate.quarantine();
    if (!__atomic_exchange_n(&fQuarantineRetained, true, __ATOMIC_ACQ_REL)) {
        // Completion or scanout ownership is uncertain. Keep the pages, source
        // device and MMIO alive until reboot, rather than freeing busy memory.
        retain();
        if (fPCI) fPCI->setProperty("NMIntelFBBridgeDeclined", kOSBooleanTrue);
        setProperty("NMIntelFBQuarantined", kOSBooleanTrue);
        IFBLOG("experimental scanout quarantined; buffers retained until reboot");
    }
}

bool NMIntelFB::flipLatched(uint32_t address)
{
    const uint64_t waitNs = fFrameNs * 2 + 1000000;
    uint64_t duration;
    nanoseconds_to_absolutetime(waitNs > 100000000 ? 100000000 : waitNs, &duration);
    const uint64_t deadline = mach_absolute_time() + duration;
    do {
        if (!(rd(PLANE_CTL(fPipe)) & kEnable)) return false;
        if ((rd(PLANE_SURF(fPipe) + 0x10) & ~0xfffu) == address) return true;
        IOSleep(1);
    } while (mach_absolute_time() < deadline);
    ++fRamLate;
    OSIncrementAtomic64(&gIfbLate);
    return false;
}

bool NMIntelFB::ramPtes(unsigned buffer, bool ours)
{
    if (!fMMIO || !nmintelfb::ggttRangeFits(fMMIO->getLength(), fApertureLen,
                                          fRamGgtt[buffer], fRamCount)) return false;
    volatile uint64_t *gtt = reinterpret_cast<volatile uint64_t *>(fRegs + 0x800000) +
                             (fRamGgtt[buffer] >> 12);
    if (!ours) {
        // Do not overwrite entries changed by firmware or another driver.
        for (unsigned i = 0; i < fRamCount; ++i)
            if (gtt[i] != (fRamPages[buffer][i] | 1ull)) return false;
    }
    fRamMapped[buffer] = true;
    for (unsigned i = 0; i < fRamCount; ++i)
        gtt[i] = ours ? fRamPages[buffer][i] | 1ull : fOldPte[buffer][i];
    wr(0x101008, 1); // GFX_FLSH_CNTL_GEN6: invalidate the GGTT TLB.
    (void)gtt[fRamCount - 1];
    for (unsigned i = 0; i < fRamCount; ++i)
        if (gtt[i] != (ours ? fRamPages[buffer][i] | 1ull : fOldPte[buffer][i])) return false;
    fRamMapped[buffer] = ours;
    return true;
}

bool NMIntelFB::ramMap(unsigned buffer)
{
    if (!nmintelfb::ggttRangeFits(fMMIO->getLength(), fApertureLen,
                                 fRamGgtt[buffer], fRamCount)) return false;
    const size_t bytes = size_t(fRamCount) * 4096;
    if (!fRam[buffer]) {
        const IOOptionBits options = kIODirectionInOut;
        IOBufferMemoryDescriptor *memory = IOBufferMemoryDescriptor::inTaskWithPhysicalMask(
            kernel_task, options | kIOMemoryPhysicallyContiguous, bytes, 0x7ffffff000ull);
        if (!memory) memory = IOBufferMemoryDescriptor::inTaskWithPhysicalMask(
            kernel_task, options, bytes, 0x7ffffff000ull);
        if (!memory) return false;
        if (memory->prepare() != kIOReturnSuccess) { memory->release(); return false; }
        IOMemoryMap *map = memory->map(kIOMapWriteCombineCache);
        unsigned long long *pages = IONew(unsigned long long, fRamCount);
        unsigned long long *old = IONew(unsigned long long, fRamCount);
        bool valid = map && pages && old;
        for (unsigned i = 0; valid && i < fRamCount; ++i) {
            IOByteCount length = 0;
            pages[i] = memory->getPhysicalSegment(IOByteCount(i) * 4096, &length, kIOMemoryMapperNone);
            valid = pages[i] && !(pages[i] & 4095) && length >= 4096;
        }
        if (!valid) {
            if (pages) IODelete(pages, unsigned long long, fRamCount);
            if (old) IODelete(old, unsigned long long, fRamCount);
            OSSafeReleaseNULL(map);
            memory->complete(); memory->release();
            return false;
        }
        fRam[buffer] = memory;
        fRamMap[buffer] = map;
        fRamPages[buffer] = pages;
        fOldPte[buffer] = old;
    }
    volatile uint64_t *gtt = reinterpret_cast<volatile uint64_t *>(fRegs + 0x800000) +
                             (fRamGgtt[buffer] >> 12);
    for (unsigned i = 0; i < fRamCount; ++i) {
        // Buffer 1 is a newly reserved hole, not an arbitrary fixed offset.
        if (buffer && gtt[i]) return false;
        fOldPte[buffer][i] = gtt[i];
    }
    const void *source = reinterpret_cast<const void *>(buffer ?
        fRamMap[0]->getVirtualAddress() : fSurfMap->getVirtualAddress());
    memcpy(reinterpret_cast<void *>(fRamMap[buffer]->getVirtualAddress()), source,
           size_t(fRowBytes) * fHeight);
    __asm__ volatile("sfence" ::: "memory");
    if (!ramPtes(buffer, true)) { quarantineRam(); return false; }
    return true;
}

int NMIntelFB::ramSetLocked(int mode)
{
    if (mode < 0 || mode > 2) return -EINVAL;
    if (!fFrameGate.canChangeMode()) return fFrameGate.quarantined() ? -EIO : -EBUSY;
    if (mode == fRamMode) return mode;
    if (mode && (!fMatchedConsumer || !ramHardwareEligible() || __atomic_load_n(&fStopping, __ATOMIC_ACQUIRE) || !fRegs || !fSurfMap ||
                 (fVer != 12 && fVer != 13) || !fMMIO || fMMIO->getLength() != (16ull << 20)))
        return -ENOTSUP;
    const uint64_t length = uint64_t(fRowBytes) * fHeight;
    if (mode && (!length || length > (64ull << 20))) return -ENOTSUP;

    if (fRamMode == 2 && mode < 2) {
        // Ensure the current front has latched before touching either backing.
        if (!flipLatched(fRamGgtt[fFront])) { quarantineRam(); return -EIO; }
        if (fFront != 0) {
            memcpy(reinterpret_cast<void *>(fRamMap[0]->getVirtualAddress()),
                   reinterpret_cast<const void *>(fRamMap[fFront]->getVirtualAddress()), size_t(length));
            __asm__ volatile("sfence" ::: "memory");
            wr(PLANE_SURF(fPipe), fRamGgtt[0] | fSurfLow);
            if (!flipLatched(fRamGgtt[0])) { quarantineRam(); return -EIO; }
            fFront = 0;
        }
        if (!ramPtes(1, false)) { quarantineRam(); return -EIO; }
        fRamMode = 1;
    }
    if (!mode) {
        if (fRamMapped[0] && !ramPtes(0, false)) { quarantineRam(); return -EIO; }
        fRamMode = 0;
        // Preserve the latest picture when returning to the firmware's pages.
        if (fRamMap[0]) {
            memcpy(reinterpret_cast<void *>(fSurfMap->getVirtualAddress()),
                   reinterpret_cast<const void *>(fRamMap[0]->getVirtualAddress()), size_t(length));
            __asm__ volatile("sfence" ::: "memory");
        }
        return 0;
    }

    const int previous = fRamMode;
    if (!fRamMode) {
        if (!flipLatched(fSurf)) return -EBUSY;
        fRamCount = static_cast<unsigned>((length + 4095) / 4096);
        fRamGgtt[0] = fSurf;
        fSurfLow = rd(PLANE_SURF(fPipe)) & 4095;
        if (!ramMap(0)) return fFrameGate.quarantined() ? -EIO : -ENOMEM;
        fFront = 0;
        fRamMode = 1;
    }
    if (mode == 2 && fRamMode == 1) {
        const uint64_t entries = fApertureLen / 4096 < (1ull << 20) ?
            fApertureLen / 4096 : (1ull << 20);
        const uint64_t first = nmintelfb::emptyGgttRange(
            reinterpret_cast<volatile uint64_t *>(fRegs + 0x800000), entries, fRamCount, 512);
        if (first != UINT64_MAX) fRamGgtt[1] = static_cast<uint32_t>(first * 4096);
        if (first == UINT64_MAX || !ramMap(1)) {
            if (!previous && !fFrameGate.quarantined()) (void)ramSetLocked(0);
            return fFrameGate.quarantined() ? -EIO : -ENOMEM;
        }
        fRamMode = 2;
    }
    return fRamMode;
}

int NMIntelFB::ramSet(int mode)
{
    IOLockLock(fRamLock);
    const int result = ramSetLocked(mode);
    IOLockUnlock(fRamLock);
    return result;
}

int NMIntelFB::ramMode()
{
    IOLockLock(fRamLock);
    const int mode = fFrameGate.quarantined() ? -EIO : fRamMode;
    IOLockUnlock(fRamLock);
    return mode;
}

IOReturn NMIntelFB::beginFrame(NMIntelFBFrame *frame)
{
    if (!frame || frame->version != NMINTELFB_FRAME_VERSION) return kIOReturnBadArgument;
    IOLockLock(fRamLock);
    if (__atomic_load_n(&fStopping, __ATOMIC_ACQUIRE) || !fSurfMap ||
        fFrameGate.busy() || fFrameGate.quarantined()) {
        IOLockUnlock(fRamLock); return kIOReturnNotReady;
    }
    if (fRamMode) {
        for (unsigned b = 0; b < static_cast<unsigned>(fRamMode); ++b) {
            const volatile uint64_t *gtt = reinterpret_cast<const volatile uint64_t *>(fRegs + 0x800000) +
                                           (fRamGgtt[b] >> 12);
            for (unsigned i = 0; i < fRamCount; ++i)
                if (gtt[i] != (fRamPages[b][i] | 1ull)) {
                    quarantineRam();
                    IOLockUnlock(fRamLock); return kIOReturnIOError;
                }
        }
    }
    if (fRamMode && !flipLatched(fRamGgtt[fFront])) {
        IOLockUnlock(fRamLock); return kIOReturnBusy;
    }
    uint64_t token = 0;
    if (!fFrameGate.acquire(true, token)) { IOLockUnlock(fRamLock); return kIOReturnBusy; }
    fLeaseBuffer = fRamMode == 2 ? fFront ^ 1u : 0;
    frame->token = token;
    frame->mode = static_cast<unsigned>(fRamMode);
    frame->width = fWidth; frame->height = fHeight; frame->pitch = fRowBytes;
    frame->length = uint64_t(fRowBytes) * fHeight;
    frame->kva = reinterpret_cast<void *>(fRamMode ?
        fRamMap[fLeaseBuffer]->getVirtualAddress() : fSurfMap->getVirtualAddress());
    frame->pages = fRamMode ? fRamPages[fLeaseBuffer] : nullptr;
    frame->pageCount = fRamMode ? fRamCount : 0;
    if (!fMatchedConsumer) { fMatchedConsumer = true; setProperty("NMIntelFBMatchedFrameABI", NMINTELFB_FRAME_VERSION, 32); }
    IOLockUnlock(fRamLock);
    return kIOReturnSuccess;
}

IOReturn NMIntelFB::endFrame(const NMIntelFBFrame *frame)
{
    if (!frame || frame->version != NMINTELFB_FRAME_VERSION || frame->result > NMINTELFB_GPU_HELD)
        return kIOReturnBadArgument;
    IOLockLock(fRamLock);
    if (!fFrameGate.owns(frame->token)) { IOLockUnlock(fRamLock); return kIOReturnBadArgument; }
    const bool known = frame->result != NMINTELFB_GPU_UNCERTAIN;
    if (!known) quarantineRam();
    else if (!__atomic_load_n(&fStopping, __ATOMIC_ACQUIRE) &&
             (frame->result == NMINTELFB_CPU_READY || frame->result == NMINTELFB_GPU_READY)) {
        if (frame->result == NMINTELFB_CPU_READY) __asm__ volatile("sfence" ::: "memory");
        if (fRamMode == 2) {
            wr(PLANE_SURF(fPipe), fRamGgtt[fLeaseBuffer] | fSurfLow);
            if ((rd(PLANE_SURF(fPipe)) & ~0xfffu) != fRamGgtt[fLeaseBuffer]) quarantineRam();
            else { fFront = fLeaseBuffer; ++fRamFlips; OSIncrementAtomic64(&gIfbFlips); }
        }
    }
    fFrameGate.finish(frame->token, known);
    IOLockWakeup(fRamLock, &fFrameGate, false);
    const IOReturn result = fFrameGate.quarantined() ? kIOReturnIOError : kIOReturnSuccess;
    IOLockUnlock(fRamLock);
    return result;
}

void NMIntelFB::freeRam()
{
    if (!fRamLock) return;
    IOLockLock(fRamLock);
    while (fFrameGate.busy()) IOLockSleep(fRamLock, &fFrameGate, THREAD_UNINT);
    if (fFrameGate.quarantined() || ramSetLocked(0) < 0) {
        quarantineRam();
        IOLockUnlock(fRamLock);
        return;
    }
    for (unsigned b = 0; b < 2; ++b) {
        OSSafeReleaseNULL(fRamMap[b]);
        if (fRam[b]) { fRam[b]->complete(); OSSafeReleaseNULL(fRam[b]); }
        if (fRamPages[b]) IODelete(fRamPages[b], unsigned long long, fRamCount);
        if (fOldPte[b]) IODelete(fOldPte[b], unsigned long long, fRamCount);
        fRamPages[b] = fOldPte[b] = nullptr;
    }
    IOLockUnlock(fRamLock);
}
