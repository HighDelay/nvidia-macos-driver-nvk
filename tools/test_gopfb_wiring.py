#!/usr/bin/env python3
"""NMGopFB is wired where it must be and off by default. Reads the kernel sources.
   python3 test_gopfb_wiring.py [tree-root]"""
import re, sys
from pathlib import Path

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
def rd(p):
    f = root / p
    return f.read_text(encoding="utf-8") if f.exists() else ""
src, hdr = rd("kexts/NMGopFB/gopfb.cpp"), rd("kexts/NMGopFB/nm_gopfb.h")
plist, build = rd("kexts/NMGopFB/Info.plist"), rd("kexts/NMGopFB/build.sh")
ifb = rd("kexts/NMIntelFB/intelfb.cpp")
handoff = rd("kexts/NVRM/nvrm_gopfb.h")
rm = rd("kexts/NVRM/NVRM.cpp")
accel = rd("kexts/NVRM/accel/nvrm-accel.cpp")
native = rd("kexts/NVRMFB/fb/nvrm-fb.cpp")
agdc = rd("kexts/NVRMAGDC/nvrm-agdc.cpp")
fails = []
def arm(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok: fails.append(name)

probe = src[src.index("IOService *NMGopFBClaim::probe"):src.index("bool NMGopFBClaim::start(")]
start = src[src.index("bool NMGopFB::start("):src.index("void NMGopFB::stop(")]
boot = rm[rm.index("void NVRM::autoGo("):rm.index("static void nv_xnu_dump_gsp_regs")]
gop_call = src[src.index("IOReturn NMGopFB::callPlatformFunction("):src.index("IOReturn NMGopFB::getAppleSense(")]
arm("the kext exists", bool(src and hdr and plist))
arm("the AMD bridge needs an explicit level-1 selection before PCI inspection",
    'PE_parse_boot_argn("nmhybrid", &level, sizeof level) && level == 1' in handoff and
    0 <= probe.find("!nvrm_gopfb_enabled()") < probe.find("configRead16"))
arm("recovery and manual startup opt out of AMD matching", all(arg in handoff for arg in ('"-nmgopfboff"', '"-nvrmnogo"')))
arm("the driver's recovery guard stops matching and starting the AMD panel",
    "nvrm_driver_off()" in probe and
    'if (nvrm_driver_off() || !IOService::start(provider)) return false;' in src)
arm("the choice is the tested nmgop_select, with NMXGPU's presence and the tester's override passed in",
    "nmgop_select(NMGOP_VENDOR_AMD," in probe and 'serviceMatching("NMXGPU")' in probe and '"nmgopid"' in probe)
arm("the framebuffer is checked by nmgop_geometry against the BARs before it is mapped",
    0 <= src.find("nmgop_geometry(") < src.find("withAddressRange(fGeometry.phys"))
arm("bad firmware geometry prevents the AMD claim or cancels the later attach",
    "firmwareGeometry(pci, &geom)" in probe and "firmwareGeometry(pci, &fGeometry)" in src and
    "panel->configure(pci, resources) && panel->attach(nub)" in src)
arm("the mapping is write-combined at the descriptor, not only at map time", "(kIOWriteCombineCache << kCacheShift)" in src)
arm("it leaves display engine registers and BAR programming alone",
    not re.search(r"configWrite|ioWrite|\bwr\(|\*\s*\(volatile uint32_t \*\)\s*\(f?[Rr]egs", src) and "mapDeviceMemoryWithRegister" not in src)
arm("the framebuffer is published under NVIDIA after checking the resource owner",
    'nvrmFramebufferPCIOwner(resources) != nvidia' in src and 'nub->attach(rm)' in src and 'panel->attach(nub)' in src and
    'nvrmFramebufferPCIOwner(newService) != me->fPCI' in accel)
arm("each controller starts with a local head zero and the AMD panel has stable driver slot 4",
    'setProperty("IOFBDependentID", kNvrmGopController, 64)' in start and
    'setProperty("IOFBDependentIndex", 0ull, 32)' in start and
    'setProperty(NVRM_DISPLAY_INDEX_KEY, kNvrmGopSlot, 32)' in start and 'kNvrmGopSlot = 4' in handoff)
arm("only the NVIDIA proxy address is returned, and the panel never offers a flip or import call",
    "r->phys = fProxy.phys" in gop_call and 'NVRM_VRAM_FN_SCANOUT' not in gop_call and
    'NMH_FN_IMPORT' not in gop_call and 'nvFlipToSurfacePure' not in gop_call)
arm("a headless NVIDIA device keeps its allocator without registering a display",
    'NVRMHeadlessResources, IOService' in native and 'resources->publish(provider, this)' in native and
    0 <= native.index('if (!kapiOk) {') < native.index('resources->publish(provider, this)') and
    'copyGopService(NVRM_HEADLESS_RESOURCES, s->fPCI)' in boot and 'gop ? gop : fFB' in accel)
arm("the AMD-only path completes AGDC and enables IOP before exposing the Metal plugin",
    'if (!gopIsPrimary()) publishMetalPlugin();' in accel and
    accel.index('panel->getProperty(NVRM_GOP_AGDC)') < accel.index('if (a->gopIsPrimary()) a->publishMetalPlugin();') and
    boot.index('ag = setAgdc(true);') < boot.index('nvrmSysW("debug.nvaccel_iop", 1)'))
arm("AGDC appends the panel at the verified pipe index and keeps ordinary NVIDIA slots in order",
    'pipe->unsigned64BitValue()' in agdc and 'while (n > index) tab[--n]->release();' in agdc and 'tab[n++] = gop' in agdc and
    'nvrmFramebufferPCIOwner(gop) == pci' in agdc and 'ix >= kAgdcNativeFB' in agdc)
arm("the boot hold cap, a failed start or a handoff timeout restore firmware matching",
    'declineGopPanel();' in boot and boot.index('if (gopClaim) gopCall(gopClaim, NVRM_GOP_FINISH);') <
    boot.index('not armed: hold released by the cap first') and 'IOLockSleepDeadline(claim->fLock, &claim->fDone' in src and
    'claim->terminate(kIOServiceRequired | kIOServiceSynchronous);' in src and 'retry->registerService();' in src)
arm("only AMD display-class devices are matched by the personality", "0x00001002&amp;0x0000ffff" in plist and "0x00008086" not in plist and "0x000010de" not in plist.lower())
arm("it supports macOS 15 and 26 only", "version_major != 24 && version_major != 25" in src and "-mmacosx-version-min" in build)
arm("the MIT notice and the link to PrimeBridge are in the file header",
    "https://github.com/myvoow/PrimeBridge" in src and "MIT License, Copyright (c) 2026 myvoow" in src and "MIT License, Copyright (c) 2026 myvoow" in hdr)
arm("the logic header is plain C (no kernel headers)", "#include <IOKit" not in hdr and "#include <stdint.h>" in hdr)
arm("NMIntelFB is untouched", "NMGopFB" not in ifb)
arm("NMIntelFB retains its separate opt-out and recovery guard",
    '"-nmintelfboff"' in ifb and 'if (ifb_nvrm_guard_off())' in ifb)
print(f"{len(fails)} failed")
sys.exit(1 if fails else 0)
