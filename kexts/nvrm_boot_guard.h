// The driver stays off when the boot picker's "1401: Remove NVIDIA driver" tool asked for it.
// The tool writes -nvoff to boot-args and sets the NVRAM flag nullmoth-remove. OpenCore rewrites boot-args at every
// start (the install lists it under NVRAM Delete), so -nvoff only reached macOS when the user picked macOS in the same
// picker session; anyone who restarted instead got the driver back and the same failed start. The flag is not rewritten
// and stays set until the recover daemon has removed the driver, so it is read too.
//
// Boot guard: a driver that cannot bring its display up must not leave the Mac on a black screen until someone types
// -nvoff. NVRM counts each start in the NVRAM variable nullmoth-nv-starts; NVRMFB clears it the moment the display path
// is up (its first head is lit, or there is no display on this card to light). Two starts in a row that never got that
// far turn the driver off on the next start, exactly like -nvoff: the firmware's framebuffer gives a desktop, and the
// install's recovery puts the previous driver back. The first check with NVRAM available records a boot-wide decision. Only NVRM's
// start counts. A variable that cannot be written (NVRAM not native) only means the guard never trips.
#pragma once
#include <IOKit/IORegistryEntry.h>
#include <IOKit/IODeviceTreeSupport.h>
#include <IOKit/IOLib.h>
#include <libkern/c++/OSBoolean.h>
#include <libkern/c++/OSData.h>
#include <pexpert/pexpert.h>

#define NVRM_GUARD_VAR "nullmoth-nv-starts"
#define NVRM_GUARD_OK_VAR "nullmoth-nv-ok"
#define NVRM_GUARD_LIMIT 2
#define NVRM_GUARD_BOOT_KEY "nullmoth-guard-off-this-boot"

static inline unsigned nvrm_guard_starts(IORegistryEntry *options)
{
    OSObject *o = options->copyProperty(NVRM_GUARD_VAR);
    OSData *d = OSDynamicCast(OSData, o);
    unsigned n = (d && d->getLength() >= 1) ? *(const unsigned char *)d->getBytesNoCopy() : 0;
    if (o) o->release();
    return n;
}

// NVRM increments NVRAM before NVAccel and NVRMFB start. Share the original
// decision in the registry root so that increment applies to the next boot.
static inline bool nvrm_guard_read_decision(IORegistryEntry *root, bool &off)
{
    if (!root) return false;
    OSObject *decision = root->copyProperty(NVRM_GUARD_BOOT_KEY);
    OSBoolean *value = OSDynamicCast(OSBoolean, decision);
    if (value) off = value->isTrue();
    if (decision) decision->release();
    return value != nullptr;
}

static inline bool nvrm_driver_off(void)
{
    char arg[8];
    if (PE_parse_boot_argn("-nvoff", arg, sizeof arg))
        return true;
    IORegistryEntry *options = IORegistryEntry::fromPath("/options", gIODTPlane);
    if (!options)
        return false;  // NVRAM not published yet: the boot-arg alone decides
    OSObject *flag = options->copyProperty("nullmoth-remove");
    if (flag) {
        flag->release();
        options->release();
        return true;
    }

    // Read the counter before looking up the decision. A writer can increment
    // only after the decision is visible to late readers.
    bool off = nvrm_guard_starts(options) >= NVRM_GUARD_LIMIT;
    options->release();
    IORegistryEntry *root = IORegistryEntry::getRegistryRoot();
    if (!nvrm_guard_read_decision(root, off) && root)
        root->setProperty(NVRM_GUARD_BOOT_KEY, off ? kOSBooleanTrue : kOSBooleanFalse);
    return off;
}
