#!/usr/bin/env python3
"""Retain the fork's repairs while accepting the reviewed upstream changes.

Functional fixtures cover new mode/audio, shader and surface interfaces. These
comparisons protect the existing fork behavior against the pre-merge checkpoint.
"""
import re
import subprocess
from test_support import ROOT, FB, method

BASE = "03f7ddd13f11f2ea43769f47c8f53b6e4e03990c"
RUNTIME_BASE = BASE

def historical(path, commit=BASE):
    return subprocess.check_output(["git", "show", commit + ":" + path], cwd=ROOT)

def same_methods(path, signatures):
    before = historical(path).decode()
    after = (ROOT / path).read_text()
    for signature in signatures:
        assert method(before, signature) == method(after, signature), (path, signature)

# Freeze the validated hotfix before the multi-display repair. Runtime, plugin
# and accelerator optimizations stay unchanged; only the reviewed head-monitor
# additions below are permitted in the framebuffer methods.
for path in subprocess.check_output(["git", "ls-tree", "-r", "--name-only", RUNTIME_BASE,
                                     "nvk", "runtime", "build"], cwd=ROOT, text=True).splitlines():
    if not path.endswith(".md"):
        assert (ROOT / path).read_bytes() == historical(path, RUNTIME_BASE), path
assert (ROOT / "translator/wrapper/src/uniform_cbuf.rs").read_bytes() == historical("translator/wrapper/src/uniform_cbuf.rs")

fb_path = "kexts/NVRMFB/fb/nvrm-fb.cpp"
old = historical(fb_path).decode()


def without_head_monitor(body):
    # The executable concurrency regression covers these additions. Keep the
    # original method bodies, cursor drain and publication behavior exact.
    for line in [
        '    NVRMHeadGate primary(startLock(), fPrimaryGate, true);\n',
        '    NVRMHeadGate primary(startLock(), fPrimaryGate, !pure);\n',
        '    if (!primary.valid() || OSAddAtomic(0, &fStopping)) return false;\n',
        '    if (!primary.valid() || OSAddAtomic(0, &fStopping) || fSubmissionFaulted) return false;\n',
        '    if (!primary.valid()) return false;\n',
        '    NVRMHeadGate::waitForCursor(startLock(), fPrimaryGate);\n',
        '    retain();\n    NVRMCallReference callReference(this);\n',
    ]:
        body = body.replace(line, '')
    return body

for name in ["cursorInit", "cursorFire", "cursorApply", "scheduleCall",
             "setCursorState", "setCursorImageLocked", "publishCursorGeometry",
             "vramGrant", "vramRelease", "getApertureRange", "getVRAMRange", "headOff",
             "notifyConnectionChanged", "connectNotifyFire", "configureRotation",
             "configureDisplayIdentity", "configureControllerIdentity", "requestProbe",
             "getCurrentDisplayMode", "setDetailedTimings", "dtIsCurrent", "publishRotatedSizes",
             "edidModeFor", "addKmsRefreshModes"]:
    signature = "NVRMNVDAFramebuffer::" + name + "("
    assert without_head_monitor(method(FB, signature)) == method(old, signature), name

# Drain the accepted primary transaction before callbacks are unregistered.
# Existing cursor accepted-work accounting and cancellation stay exact.
stop = method(FB, "NVRMNVDAFramebuffer::stop(")
drain = '''    // No new primary transaction may start after fStopping. Wait for the
    // accepted one before removing completion callbacks or other head state.
    // Drop both guards before draining workers that can enter this same head.
    {
        NVRMDisplayGate gate;
        if (gate.valid()) { NVRMHeadGate primary(startLock(), fPrimaryGate, true); }
    }
'''
assert stop.count(drain) == 1
assert stop.replace(drain, '') == method(old, "NVRMNVDAFramebuffer::stop(")
for name in ["applyMode", "flipToMemory"]:
    signature = "NVRMNVDAFramebuffer::" + name + "("
    assert without_head_monitor(method(FB, signature)) == method(old, signature), name
power = method(FB, "NVRMNVDAFramebuffer::setHeadPower(")
assert without_head_monitor(power) == method(old, "NVRMNVDAFramebuffer::setHeadPower(")
init = method(FB, "NVRMNVDAFramebuffer::kapiInit(")
assert init.count("validateInitialDviMode();") == 1
assert init.index("validateInitialDviMode();") < init.index("fBootW = fW")
assert "nvrmKmsRasterValid" in init
assert "fNewIdOnce" not in FB
assert not re.search(r"\bthread_call_cancel_wait\s*\(", FB)
assert "THREAD_CALL_PRIORITY_KERNEL, THREAD_CALL_OPTIONS_ONCE" in FB
assert "while (OSAddAtomic(0, &fCursorOutstanding)) IOSleep(1);" in FB

same_methods("kexts/NVRM/accel/nvrm-accel.cpp", ["static bool nvAccelRestoreParkedForDirty(", "static bool fbPublished("])
same_methods("plugin/NVMTLDevice.m", ["static bool nvmtl_is_sandboxed(", "static bool nvmtl_is_chromium_gpu_process(",
    "static bool nvmtl_can_register_decoder(", "static void nvmtl_nvdec_register(",
    "static void nvmtl_nvdec_hevc_register(", "static void nvmtl_angle_allow_nvidia("])
same_methods("plugin/NVMTLEventSync.m", ["static void nvmtl_gpu_uses_adjust(", "static void nvmtl_gpu_uses_raise(",
    "static void nvmtl_gpu_uses_lower(", "static void nvmtl_prepare_submission_copies("])
same_methods("plugin/nvmtl_vk.c", ["uint32_t nvmtl_spirv_stencil_emu("])
same_methods("plugin/NVMTLObjects.m", ["static unsigned nvmtl_mip_autofill_bpp(", "static void nvmtl_mip_autofill("])
assert not (ROOT / "plugin/NVMTLRotationInline.m").exists()
print("PASS: retained Tahoe rotation/current IDs, cursor serialization/drain, DVI, parked backing, decoder/ANGLE defaults, strict stencil, badge mips, GL completion/copies and native runtimes; reviewed audio/raster changes allowed")
