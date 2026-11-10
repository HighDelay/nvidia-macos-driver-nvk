#!/usr/bin/env python3
"""The GL66 12UCK profile stays attached to the IDs and the BAR rule this laptop needs.

Reads the tree. Does not need a Mac, a compiler, or a GPU.
"""
import pathlib
import sys

root = pathlib.Path(__file__).resolve().parents[1]
fails = 0


def check(name, ok, detail=""):
    global fails
    print(("ok   " if ok else "FAIL ") + name + (f" ({detail})" if detail else ""))
    fails += not ok


install = (root / "package/install.sh").read_text(encoding="utf-8")
ids = install.split('SUPPORTED_IDS="', 1)[1].split('"', 1)[0].split()
check("installer accepts 10de:25a2", "25A2" in ids)

display_ids = (root / "kexts/NMIntelFB/display_ids.inc").read_text(encoding="utf-8")
check("NMIntelFB knows Intel UHD 8086:46a3 as display version 13", "{0x46a3, 13}," in display_ids)

nvrm_plist = (root / "kexts/NVRM/Info.plist").read_text(encoding="utf-8")
check(
    "NVRM matches PCI class 03 with the subclass free (0300 and 0302)",
    "0x03000000&amp;0xff000000" in nvrm_plist,
)

nvrm = (root / "kexts/NVRM/NVRM.cpp").read_text(encoding="utf-8")
check(
    "placeLargeBar1 does not grow a BAR that is already at least 4 GB",
    "bar1Size < (4ULL << 30)" in nvrm,
)
check("install.sh does not ship NMIntelFB", "NMIntelFB" not in install)

doc = (root / "docs/machines/msi-pulse-gl66-12uck.md").read_text(encoding="utf-8")
for needle in (
    "10de:25a2",
    "8086:46A3",
    "1462:1331",
    "4096",
    "ResizeGpuBars",
    "IONDRVSupport",
    "MacBookPro16,2",
    "-nvaccelnoheadless",
):
    check(f"machine doc names {needle}", needle in doc)

conf = (root / "profiles/msi-pulse-gl66-12uck/dxvk.conf").read_text(encoding="utf-8")
device = shared = None
for line in conf.splitlines():
    line = line.split("#", 1)[0].strip()
    if line.startswith("dxgi.maxDeviceMemory"):
        device = int(line.split("=", 1)[1].strip())
    if line.startswith("dxgi.maxSharedMemory"):
        shared = int(line.split("=", 1)[1].strip())
check("DXVK reports at most 3584 MiB of device memory", device is not None and 2048 <= device <= 3584, device)
check("DXVK shared memory is set and under 1 GB", shared is not None and 0 < shared <= 1024, shared)
active_budget = any(
    not ln.strip().startswith("#") and "dxvk.maxMemoryBudget" in ln
    for ln in conf.splitlines()
)
check("no active dxvk.maxMemoryBudget assignment", not active_budget)

print("FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
