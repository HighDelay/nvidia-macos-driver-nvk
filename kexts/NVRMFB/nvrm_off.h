#pragma once
#include "../nvrm_boot_guard.h"

#define NVRM_BACKLIGHT_VAR "nullmoth-backlight"

// IODisplay logical precision is independent of NVKMS's integer-percent output.
enum { kNVRMBacklightMax = 0xFFFF, kNVRMMinBacklight = 5 };

static inline UInt32 nvrm_backlight_from_percent(UInt32 percent)
{
    if (percent <= kNVRMMinBacklight) return 0;
    return ((percent - kNVRMMinBacklight) * kNVRMBacklightMax + (100 - kNVRMMinBacklight) / 2)
        / (100 - kNVRMMinBacklight);
}

static inline UInt32 nvrm_backlight_to_percent(UInt32 level)
{
    return kNVRMMinBacklight + (level * (100 - kNVRMMinBacklight) + kNVRMBacklightMax / 2)
        / kNVRMBacklightMax;
}

static inline bool nvrm_backlight_read(IORegistryEntry *options, UInt32 *level)
{
    if (!options || !level) return false;
    OSObject *o = options->copyProperty(NVRM_BACKLIGHT_VAR);
    OSData *d = OSDynamicCast(OSData, o);
    bool valid = false;
    UInt32 value = 0;
    if (d) {
        const unsigned char *bytes = (const unsigned char *)d->getBytesNoCopy();
        if (d->getLength() == 1 && bytes[0] <= 100) {
            value = nvrm_backlight_from_percent(bytes[0]);
            valid = true;
        } else if (d->getLength() == 3 && (bytes[0] == 1 || bytes[0] == 2)) {
            value = (UInt32)bytes[1] | ((UInt32)bytes[2] << 8);
            // Version 1 used the full 0..100 span; retain its rounded hardware output.
            if (bytes[0] == 1)
                value = nvrm_backlight_from_percent((value * 100 + kNVRMBacklightMax / 2) / kNVRMBacklightMax);
            valid = true;
        }
    }
    if (o) o->release();
    if (!valid) return false;
    *level = value;
    return true;
}

static inline bool nvrm_backlight_write(IORegistryEntry *options, UInt32 level)
{
    if (!options || level > kNVRMBacklightMax) return false;
    UInt32 current;
    if (nvrm_backlight_read(options, &current) && current == level) return true;
    // Version 2 uses the 5..100 hardware span; older records are converted when read.
    unsigned char bytes[] = {2, (unsigned char)level, (unsigned char)(level >> 8)};
    OSData *d = OSData::withBytes(bytes, sizeof bytes);
    if (!d) return false;
    bool ok = options->setProperty(NVRM_BACKLIGHT_VAR, d);
    d->release();
    return ok;
}

// NVRMFB, once the display path is up: this start counts as good, and the install's trial is over.
static inline void nvrm_guard_display_up(const char *why)
{
    static bool cleared;
    if (cleared) return;
    cleared = true;
    IORegistryEntry *options = IORegistryEntry::fromPath("/options", gIODTPlane);
    if (!options) return;
    options->removeProperty(NVRM_GUARD_VAR);
    OSObject *ok = options->copyProperty(NVRM_GUARD_OK_VAR);
    if (ok) ok->release();
    else if (OSData *d = OSData::withBytes("1", 1)) { options->setProperty(NVRM_GUARD_OK_VAR, d); d->release(); }
    options->release();
    IOLog("NVRM: boot guard cleared: %s\n", why);
}
