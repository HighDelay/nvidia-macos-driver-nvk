#!/usr/bin/env python3
"""Hybrid panel framebuffers (NMIntelFB, NMGopFB) set IOFBDependentID before IOFramebuffer::start (issue 555).

IOFramebuffer::start attaches a framebuffer to its shared controller only when the dependent ID already exists. Set
afterwards, the controller stays unset until open(), and the power change that registerPowerDriver() queues at the end
of start() takes the controller's lock through a null pointer. NVRMFB already sets it first.

   python3 tools/test_hybrid_fb_dependent_id.py [tree-root]
The negative control runs the same check on the pre-fix sources (git HEAD~ or a supplied old tree) and must fail."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
FILES = {"NMIntelFB": "kexts/NMIntelFB/intelfb.cpp", "NMGopFB": "kexts/NMGopFB/gopfb.cpp"}
fails = []


def arm(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name)


def start_bodies(src, cls):
    """Each `bool <cls>::start(` body, split at #ifdef branches so both start paths are checked on their own."""
    head = f"bool {cls}::start(IOService *provider)"
    i = src.index(head)
    end = src.index("\n}\n", i)
    body = src[i:end]
    if "#ifdef" in body and "#endif" in body:
        # NMIntelFB: the virtual panel path returns inside #ifdef ... #endif; the real panel path follows it
        a = body.index("#ifdef")
        c = body.index("#endif", a)
        return [body[a:c], body[c:]]
    return [body]


def ordered(body):
    """True when every IOFramebuffer::start call comes after both dependent properties are set."""
    if "IOFramebuffer::start(provider)" not in body:
        return None
    s = body.index("IOFramebuffer::start(provider)")
    d = body.find('setProperty("IOFBDependentID"')
    x = body.find('setProperty("IOFBDependentIndex"')
    return 0 <= d < s and 0 <= x < s


def check(read):
    results = {}
    for cls, rel in FILES.items():
        src = read(rel)
        bodies = start_bodies(src, cls)
        if cls == 'NMIntelFB' and 'bool NMIntelFB::startReal(' in src:
            i = src.index('bool NMIntelFB::startReal(')
            bodies.append(src[i:src.index('\n}\n', i)])
        for n, body in enumerate(bodies):
            r = ordered(body)
            if r is not None:
                results[f"{cls} start path {n}"] = r
    return results


now = check(lambda rel: (ROOT / rel).read_text(encoding="utf-8"))
arm("three start paths were found (NMIntelFB virtual, NMIntelFB panel, NMGopFB)", len(now) == 3)
for name, ok in now.items():
    arm(f"{name}: the dependent ID and index are set before IOFramebuffer::start", ok)

ifb = (ROOT / FILES["NMIntelFB"]).read_text(encoding="utf-8")
i = ifb.index('bool NMIntelFB::startReal(')
panel = ifb[i:ifb.index('\n}\n', i)]
arm("NMIntelFB: a surface that will not map stops start before IOFramebuffer::start (no controller-less framebuffer)",
    panel.find("if (!fSurfMap) {") != -1 and panel.find("if (!fSurfMap) {") < panel.index("IOFramebuffer::start(provider)"))
PUBLISHERS = ("kapiInit(provider)", "armVbl()", "publishBacklight();")


def map_before_publish(body):
    m = body.find("if (!fSurfMap) {")
    return m >= 0 and "return false;" in body[m:body.index("\n", m)] and all(body.find(x) > m for x in PUBLISHERS)


arm("NMIntelFB: missing surface returns before RM handles, VBL or backlight publication", map_before_publish(panel))
start = start_bodies(ifb, "NMIntelFB")[-1]
arm("NMIntelFB: every failed startReal runs shared unwind before disarming the attach guard",
    'bool ok = startReal(provider);' in start and
    start.index('startUnwind();') < start.index('ifb_nv_set("nullmoth-intelfb-attaching", nullptr);'))
unwind = ifb[ifb.index("void NMIntelFB::startUnwind()"):]
unwind = unwind[:unwind.index("\n}\n")]
arm("NMIntelFB: failure and stop drain workers before removing callbacks or releasing mappings",
    all(x in unwind for x in ("drainWorkers();", "if (published) gIfbNubFB = nullptr;", "ifb_ram_register(false)",
                              "ifb_agdc_register(false)", "unregisterInterrupt(&fVbl)", "freeRam();", "kapiFree();",
                              "fBacklight->detach(this)", "OSSafeReleaseNULL(fSurfMap)", "OSSafeReleaseNULL(fMMIO)")) and
    unwind.index('drainWorkers();') < unwind.index('unregisterInterrupt(&fVbl)') < unwind.index('OSSafeReleaseNULL(fSurfMap)'))
arm("NMIntelFB: exposed RM handles keep the quarantine lifetime rule",
    'fKapiExposed' in unwind and 'retain();' in unwind and 'if (!quarantined())' in unwind)
stop = ifb[ifb.index("void NMIntelFB::stop(IOService *provider)"):]
stop = stop[:stop.index("\n}\n")]
arm("NMIntelFB: stop uses the same unwind before stopping the superclass",
    stop.index("startUnwind();") < stop.index("IOFramebuffer::stop(provider);"))
arm("negative control: removing the map guard is caught", not map_before_publish(panel.replace('if (!fSurfMap) {', 'if (false) {')))
arm("negative control: mapping after worker publication is caught",
    not map_before_publish('armVbl();\n' + panel))
nvfb = (ROOT / "kexts/NVRMFB/fb/nvrm-fb.cpp").read_text(encoding="utf-8")
arm("NVRMFB still sets its dependent ID before super::start (the reference order)",
    nvfb.index('if (!configureControllerIdentity(provider))') < nvfb.index("if (!super::start(provider)) {")
    and 'setProperty("IOFBDependentID", group, 64)' in nvfb)

# Negative control: the 2.9 order (properties after start) must be caught by the same check.
old_tree = Path(sys.argv[2]) if len(sys.argv) > 2 else None


def read_old(rel):
    if old_tree:
        return (old_tree / rel).read_text(encoding="utf-8")
    return subprocess.run(["git", "-C", str(ROOT), "show", f"7412cdde81447f78da0ebb64216dc258d8beaedb:{rel}"],
                          capture_output=True, text=True, check=True).stdout



try:
    old = check(read_old)
    arm("negative control: the check fails on the old order (pre-fix sources)", len(old) == 3 and not any(old.values()))
except (subprocess.CalledProcessError, ValueError, FileNotFoundError) as e:
    arm(f"negative control could not run ({e.__class__.__name__}): pass the pre-fix tree as the second argument", False)

print(f"{len(fails)} failed")
sys.exit(1 if fails else 0)
