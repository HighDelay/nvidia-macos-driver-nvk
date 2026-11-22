#!/usr/bin/env python3
"""Compile the real request replication and check group ownership at its callers."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1] / 'kexts/NVRMFB/fb'
source = (root / 'nvrm-fb.cpp').read_text()
group = (root / 'nvrm-surround.inc').read_text()
audio = source[source.index('void NVRMNVDAFramebuffer::publishDisplayAudioPropertiesNow'):
               source.index('static bool nvrmEdidSerialString')]
assert 'fHidden' not in audio
assert 'return !fDpyLost && !fHidden' in source
assert 'if (surround) surroundReplicate(cfg);' in source
assert 'fSubmissionHeads = submissionHeads;' in source
assert 'NVRMHeadGate group(startLock(), gSurroundGate, true);' in group
assert 'fIndex < 4 && (gSurroundMask & (1u << fIndex)) ? gSurroundGate' in source
activate = group[group.index('bool NVRMNVDAFramebuffer::switchSurround()'):
                 group.index('bool NVRMNVDAFramebuffer::releaseSurround()')]
assert activate.index('waitFlipDone(kFlipStuckUs)') < activate.index('submitConfig(')
assert activate.index('if (!accepted) return false;') < activate.index('fb->fHidden = true;')
detach = group[group.index('bool NVRMNVDAFramebuffer::releaseSurround()'):]
assert detach.index('if (!detached) return false;') < detach.index('fb->fHidden = false;')
start = group.index('void NVRMNVDAFramebuffer::surroundReplicate(')
replicate = group[start:group.index('\n}\n', start) + 3]
fixture = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
using NvU16 = uint16_t;
enum { NV_FALSE, NV_TRUE, NVKMS_KAPI_LAYER_PRIMARY_IDX = 0 };
enum { NV_KMS_DPY_ATTRIBUTE_CURRENT_COLOR_BPC_8 = 8 };
struct Mode { struct { unsigned vVisible; } timings; };
struct Layer {
    struct { void *surface; NvU16 srcX, srcWidth, dstWidth, dstHeight; } config;
    struct { bool srcXYChanged, srcWHChanged, dstWHChanged; } flags;
};
struct Request {
    struct { bool bActive, vrrEnabled; unsigned displays[1]; Mode mode; unsigned colorBpc, colorFormat; } modeSetConfig;
    struct { bool modeChanged, activeChanged, vrrEnabledChanged; } flags;
    struct { void *surface; struct { bool surfaceChanged, dstXYChanged; } flags; } cursorRequestedConfig;
    Layer layerRequestedConfig[1];
};
struct NvKmsKapiRequestedModeSetConfig { unsigned headsMask; Request headRequestedConfig[4]; };
static void nvrmSetColorRange(Request &, unsigned, int) {}
struct NVRMNVDAFramebuffer {
    struct Member { NVRMNVDAFramebuffer *fb; unsigned display, head, x, w; Mode mode; };
    Member fSur[3] = {};
    unsigned fSurN = 0, fHead = 0, fOutputBpc = 0, fOutputFormat = 3, fOutputRange = 0;
    bool fDviModeValidation = false;
    void surroundReplicate(NvKmsKapiRequestedModeSetConfig *);
};
''' + replicate + r'''
int main() {
    NVRMNVDAFramebuffer f, dvi, dp;
    dvi.fDviModeValidation = true;
    f.fSurN = 3; f.fHead = 1;
    f.fSur[0] = {&f, 101, 1, 0, 2560, {{1440}}};
    f.fSur[1] = {&dvi, 102, 0, 2560, 1920, {{1440}}};
    f.fSur[2] = {&dp, 103, 3, 4480, 3440, {{1440}}};
    NvKmsKapiRequestedModeSetConfig cfg = {};
    cfg.headsMask = 1u << 1;
    auto &master = cfg.headRequestedConfig[1];
    master.modeSetConfig.bActive = true;
    master.modeSetConfig.vrrEnabled = true;
    master.flags.modeChanged = true;
    master.layerRequestedConfig[0].config.surface = &f;
    master.cursorRequestedConfig.surface = &f;
    f.surroundReplicate(&cfg);
    assert(cfg.headsMask == 0xb);
    for (const auto &member : f.fSur) {
        const auto &head = cfg.headRequestedConfig[member.head];
        const auto &layer = head.layerRequestedConfig[0];
        assert(head.modeSetConfig.displays[0] == member.display);
        assert(layer.config.surface == &f && layer.config.srcX == member.x);
        assert(layer.config.srcWidth == member.w && layer.config.dstWidth == member.w && layer.config.dstHeight == 1440);
        assert(layer.flags.srcXYChanged && layer.flags.srcWHChanged && layer.flags.dstWHChanged);
        assert(!head.cursorRequestedConfig.surface && !head.modeSetConfig.vrrEnabled);
    }
    assert(cfg.headRequestedConfig[0].modeSetConfig.colorFormat == 0);
    assert(cfg.headRequestedConfig[0].modeSetConfig.colorBpc == 8);
    // Display sleep turns off all the grouped heads in the same request.
    cfg = {};
    cfg.headsMask = 1u << f.fHead;
    cfg.headRequestedConfig[f.fHead].flags.activeChanged = true;
    f.surroundReplicate(&cfg);
    assert(cfg.headsMask == 0xb);
    for (const auto &member : f.fSur)
        assert(cfg.headRequestedConfig[member.head].flags.activeChanged &&
               !cfg.headRequestedConfig[member.head].modeSetConfig.bActive);
    puts("PASS: grouped flips, DVI RGB8, software cursor, display sleep and ownership transition");
}
'''
with tempfile.TemporaryDirectory() as directory:
    temporary = Path(directory)
    source_path = temporary / 'surround.cpp'
    source_path.write_text(fixture)
    binary = temporary / 'surround'
    subprocess.run(['clang++', '-std=c++17', '-Wall', '-Werror', str(source_path), '-o', str(binary)], check=True)
    subprocess.run([binary], check=True)
