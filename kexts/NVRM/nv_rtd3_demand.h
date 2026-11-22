/*
 * Copyright (c) 2026 NullMoth Systems.
 * SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
 */
#pragma once
#include <IOKit/IOService.h>
#include <pexpert/pexpert.h>

#define NVRM_RTD3_DEMAND "NVRMRuntimeDemand"
enum { kNvrmRuntimeRelease, kNvrmRuntimeWake, kNvrmRuntimeTry };

static inline bool nvrmRuntimeRequested()
{
    UInt32 enable = 0;
    return PE_parse_boot_argn("nvrtd3", &enable, sizeof enable) && enable == 1;
}

// Acquisitions from other kexts precede their display and NVKMS locks.
// Try is for background scans and never wakes a GPU that is already cold.
class NvrmRuntimeDemand {
    IOService *rm = nullptr;
    const OSSymbol *symbol = nullptr;
public:
    bool acquire(IOService *owner, uintptr_t operation = kNvrmRuntimeWake)
    {
        if (!nvrmRuntimeRequested()) return true;
        if (!owner) return false;
        const OSSymbol *fn = OSSymbol::withCStringNoCopy(NVRM_RTD3_DEMAND);
        if (!fn) return false;
        if (owner->callPlatformFunction(fn, false, (void *)operation, nullptr, nullptr, nullptr) != kIOReturnSuccess) {
            fn->release();
            return false;
        }
        owner->retain();
        rm = owner;
        symbol = fn;
        return true;
    }
    void release()
    {
        if (!rm) return;
        rm->callPlatformFunction(symbol, false, nullptr, nullptr, nullptr, nullptr);
        rm->release();
        symbol->release();
        rm = nullptr;
        symbol = nullptr;
    }
    NvrmRuntimeDemand() = default;
    NvrmRuntimeDemand(const NvrmRuntimeDemand &) = delete;
    NvrmRuntimeDemand &operator=(const NvrmRuntimeDemand &) = delete;
    ~NvrmRuntimeDemand() { release(); }
};
