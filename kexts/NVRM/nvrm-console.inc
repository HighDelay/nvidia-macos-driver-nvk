/*
 * NullMoth NVIDIA driver for macOS
 * Copyright (c) 2026 NullMoth Systems.
 * SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
 */

extern "C" int nvrm_bar1_console_preserved(nv_state_t *nv);

// The console renders into RAM. Copy only changed rows to either the verified
// firmware window or the first framebuffer's owned, CPU-writable scanout.
struct NvrmBootConsole {
    IOLock *lock;
    thread_call_t call;
    unsigned outstanding;
    bool active, full;
    UInt64 deadline, bar, offset, bytes;
    IOPlatformExpert *platform;
    IOBufferMemoryDescriptor *shadow;
    IOMemoryMap *barMap;
    UInt8 *mirror;
    PE_Video video;
    NvrmConsoleScanout target;

    static NvrmBootConsole *start(IOPlatformExpert *, const PE_Video &, UInt64 base, UInt64 offset,
                                  const void *image);
    static void tick(thread_call_param_t, thread_call_param_t);
    void schedule();
    bool mapFirmware(UInt64);
    bool ownsScreen();
    void clearTarget();
    void pause();
    void afterRm(nv_state_t *, bool);
    bool takeScanout(const NvrmConsoleScanout &);
    void finish();
    void dispose();
};

bool NvrmBootConsole::mapFirmware(UInt64 phys)
{
    auto *memory = IOMemoryDescriptor::withPhysicalAddress(phys, bytes, kIODirectionInOut);
    if (!memory) return false;
    barMap = memory->map(kIOMapWriteCombineCache);
    memory->release();
    if (!barMap || !barMap->getVirtualAddress()) { OSSafeReleaseNULL(barMap); return false; }
    target = {(void *)barMap->getVirtualAddress(), bytes, (UInt32)video.v_width,
              (UInt32)video.v_height, (UInt32)video.v_rowBytes};
    full = true;
    return true;
}

NvrmBootConsole *NvrmBootConsole::start(IOPlatformExpert *platform, const PE_Video &video,
                                       UInt64 bar, UInt64 offset, const void *image)
{
    if (!image || !platform || video.v_depth != 32 ||
        !nvrmConsoleFits(video.v_width, video.v_height, video.v_rowBytes, 64ull << 20) ||
        bar > UINT64_MAX - offset)
        return nullptr;

    auto *cons = IONewZero(NvrmBootConsole, 1);
    if (!cons) return nullptr;
    cons->lock = IOLockAlloc();
    cons->bytes = video.v_rowBytes * video.v_height;
    cons->platform = platform;
    cons->video = video;
    cons->bar = bar;
    cons->offset = offset;
    cons->mirror = (UInt8 *)IOMalloc(cons->bytes);
    cons->shadow = IOBufferMemoryDescriptor::inTaskWithOptions(kernel_task, kIODirectionInOut,
                                                               cons->bytes, page_size);
    cons->call = thread_call_allocate_with_options(tick, cons, THREAD_CALL_PRIORITY_KERNEL,
                                                    THREAD_CALL_OPTIONS_ONCE);
    if (!cons->lock || !cons->shadow || !cons->mirror || !cons->call || !cons->mapFirmware(bar + offset)) {
        if (cons->call) thread_call_free(cons->call);
        OSSafeReleaseNULL(cons->shadow);
        if (cons->mirror) IOFree(cons->mirror, cons->bytes);
        if (cons->lock) IOLockFree(cons->lock);
        IODelete(cons, NvrmBootConsole, 1);
        return nullptr;
    }

    memcpy(cons->shadow->getBytesNoCopy(), image, cons->bytes);
    memcpy(cons->mirror, image, cons->bytes);
    PE_Video ram = video;
    ram.v_baseAddr = (unsigned long)cons->shadow->getBytesNoCopy();
    IOLockLock(cons->lock);
    platform->setConsoleInfo(&ram, kPEBaseAddressChange);
    platform->setConsoleInfo(nullptr, kPEEnableScreen);
    cons->active = true;
    clock_interval_to_deadline(15 * 60, kSecondScale, &cons->deadline);
    cons->schedule();
    IOLockUnlock(cons->lock);
    return cons;
}

bool NvrmBootConsole::ownsScreen()
{
    PE_Video video = {};
    return platform->getConsoleInfo(&video) == kIOReturnSuccess &&
        (video.v_baseAddr & ~3ull) == (uintptr_t)shadow->getBytesNoCopy();
}

void NvrmBootConsole::schedule()
{
    uint64_t when;
    clock_interval_to_deadline(33, kMillisecondScale, &when);
    if (!thread_call_enter_delayed(call, when)) ++outstanding;
}

void NvrmBootConsole::clearTarget()
{
    target = {};
    __asm__ volatile("sfence" ::: "memory");
    OSSafeReleaseNULL(barMap);
}

void NvrmBootConsole::tick(thread_call_param_t arg, thread_call_param_t)
{
    auto *cons = static_cast<NvrmBootConsole *>(arg);
    IOLockLock(cons->lock);
    if (cons->active && (!cons->ownsScreen() || mach_absolute_time() >= cons->deadline)) {
        cons->active = false;
        cons->clearTarget();
    }
    if (cons->active && cons->target.address) {
        const auto &dst = cons->target;
        const auto &src = cons->video;
        const unsigned width = src.v_width < dst.width ? (unsigned)src.v_width : dst.width;
        const unsigned lines = src.v_height < dst.height ? (unsigned)src.v_height : dst.height;
        const unsigned rowBytes = width * 4;
        const size_t sourceX = (src.v_width - width) / 2 * 4;
        const size_t targetX = (dst.width - width) / 2 * 4;
        const size_t sourceY = src.v_height - lines;
        const size_t targetY = (dst.height - lines) / 2;
        auto *shadow = static_cast<const UInt8 *>(cons->shadow->getBytesNoCopy());
        for (unsigned row = 0; row < lines; ++row) {
            const size_t offset = (sourceY + row) * src.v_rowBytes + sourceX;
            auto *last = cons->mirror + offset;
            if (!cons->full && memcmp(shadow + offset, last, rowBytes) == 0) continue;
            memcpy(last, shadow + offset, rowBytes);
            memcpy(static_cast<UInt8 *>(dst.address) + (targetY + row) * dst.pitch + targetX,
                   last, rowBytes);
        }
        __asm__ volatile("sfence" ::: "memory");
        cons->full = false;
    }
    if (cons->active) cons->schedule();
    --cons->outstanding;
    IOLockWakeup(cons->lock, &cons->outstanding, false);
    IOLockUnlock(cons->lock);
}

void NvrmBootConsole::pause()
{
    IOLockLock(lock);
    clearTarget();
    IOLockUnlock(lock);
}

void NvrmBootConsole::afterRm(nv_state_t *nv, bool started)
{
    IOLockLock(lock);
    if (active && started && !offset && nv->fb && nv->fb->cpu_address == bar &&
        nvrmConsoleRangeFits(0, bytes, nv->fb->size) && nvrm_bar1_console_preserved(nv))
        mapFirmware(bar);
    IOLockUnlock(lock);
}

bool NvrmBootConsole::takeScanout(const NvrmConsoleScanout &next)
{
    IOLockLock(lock);
    const bool ready = active && next.address && nvrmConsoleFits(next.width, next.height, next.pitch, next.size)
                      && ownsScreen();
    if (ready) {
        clearTarget();
        target = next;
        full = true;
    }
    IOLockUnlock(lock);
    return ready;
}

void NvrmBootConsole::finish()
{
    IOLockLock(lock);
    active = false;
    if (ownsScreen()) platform->setConsoleInfo(nullptr, kPEDisableScreen);
    clearTarget();
    if (thread_call_cancel(call)) --outstanding;
    while (outstanding) IOLockSleep(lock, &outstanding, THREAD_UNINT);
    IOLockUnlock(lock);
}

void NvrmBootConsole::dispose()
{
    finish();
    thread_call_free(call);
    IOFree(mirror, bytes);
    // The platform console may still cache the RAM base after it is disabled.
    // Keep its backing until the platform has adopted another console.
    PE_Video current = {};
    if (platform->getConsoleInfo(&current) == kIOReturnSuccess &&
        (current.v_baseAddr & ~3ull) != (uintptr_t)shadow->getBytesNoCopy())
        shadow->release();
    IOLockFree(lock);
    IODelete(this, NvrmBootConsole, 1);
}
