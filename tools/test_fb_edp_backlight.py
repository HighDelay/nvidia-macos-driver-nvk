#!/usr/bin/env python3
"""Exercise the production AUX brightness transaction against a panel model."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
rm = (root / 'kexts/NVRM/nvrm_ddc.cpp').read_text()
fb = (root / 'kexts/NVRMFB/fb/nvrm-fb.cpp').read_text()
assert 'p.size = r->len - 1' in rm and 'p.size != r->len' in rm
assert 'r->addr < 0x700 || r->addr >= 0x800 || r->addr + r->len > 0x800' in rm
assert 'r != kIOReturnSuccess && rm && fDisplay' in fb
assert 'PE_parse_boot_argn("nvfbedpbl"' in fb
transaction = rm[rm.index('extern "C" int nvrm_edp_backlight('):]
fixture = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include "nvrm_ddc_abi.h"
#include "nvrm_edp_backlight.h"
using UInt32 = uint32_t;
#define kprintf printf
static unsigned char panel[0x800];
static unsigned enabled = 1, writeAttempts;
static unsigned modeWrites, failModeWrites;
static bool failWrite, shortRead;
static bool PE_parse_boot_argn(const char *name, UInt32 *out, size_t size) {
    assert(strcmp(name, "nvfbedpbl") == 0 && size == sizeof(*out));
    *out = enabled;
    return true;
}
static int nvrm_dpaux_transfer(NVRMDpAuxRequest *r) {
    assert(r->gpuId == 42 && r->displayId == 16 && r->addr + r->len <= sizeof panel);
    if (r->write) {
        ++writeAttempts;
        if (r->addr == kEdpBacklightMode) {
            assert(r->len == 1 && panel[kEdpBacklightMsb] != 0);
            ++modeWrites;
            if (failModeWrites) {
                if (failModeWrites == 2) panel[r->addr] = r->data[0];
                --failModeWrites;
                return -1;
            }
            panel[r->addr] = r->data[0];
            return 0;
        }
        assert(r->addr == kEdpBacklightMsb);
        if (failWrite) {
            panel[r->addr] = r->data[0];
            failWrite = false;
            return -1;
        }
        memcpy(panel + r->addr, r->data, r->len);
    } else {
        if (shortRead) return -1;
        memcpy(r->data, panel + r->addr, r->len);
    }
    return 0;
}
''' + transaction + r'''
int main() {
    NVRMEdpBacklightRequest request {42, 16, false, 0};
    panel[kEdpGeneralCap1] = 1;
    panel[kEdpBacklightCap] = kEdpCapAuxSet | kEdpCapByteCount;
    panel[kEdpDisplayCtl] = 0x81;
    panel[kEdpBacklightMode] = 0xf0;
    assert(nvrm_edp_backlight(&request) == -1 && writeAttempts == 0);
    panel[kEdpBacklightMode] = 0xf2;
    panel[kEdpBacklightMsb] = 0x80;
    panel[kEdpBacklightLsb] = 0x11;
    assert(nvrm_edp_backlight(&request) == 0 && request.percent == 50 && writeAttempts == 0);
    request.set = true;
    request.percent = 20;
    failWrite = true;
    assert(nvrm_edp_backlight(&request) == -1 && writeAttempts == 2);
    assert(panel[kEdpBacklightMsb] == 0x80 && panel[kEdpBacklightLsb] == 0x11);
    assert(nvrm_edp_backlight(&request) == 0 && writeAttempts == 3);
    assert(panel[kEdpBacklightMsb] == 0x33 && panel[kEdpBacklightLsb] == 0x33);
    assert(panel[kEdpDisplayCtl] == 0x81 && panel[kEdpBacklightMode] == 0xf2);
    shortRead = true;
    assert(nvrm_edp_backlight(&request) == -1 && writeAttempts == 3);
    shortRead = false;
    panel[kEdpBacklightCap] = kEdpCapAuxSet;
    request.percent = 100;
    assert(nvrm_edp_backlight(&request) == 0);
    assert(panel[kEdpBacklightMsb] == 0xff && panel[kEdpBacklightLsb] == 0x33);
    enabled = 0;
    assert(nvrm_edp_backlight(&request) == -1 && writeAttempts == 4);
    enabled = 2;
    panel[kEdpBacklightMode] = 0xa0;
    panel[kEdpBacklightMsb] = 0x00;
    request.set = false;
    assert(nvrm_edp_backlight(&request) == 0 && request.percent == 100 && modeWrites == 0);
    request.set = true;
    request.percent = 0;
    failModeWrites = 1;
    assert(nvrm_edp_backlight(&request) == -1);
    assert(panel[kEdpBacklightMode] == 0xa0 && panel[kEdpBacklightMsb] == 0);
    assert(modeWrites == 2);
    failModeWrites = 2;
    assert(nvrm_edp_backlight(&request) == -1);
    assert(panel[kEdpBacklightMode] == 0xa2 && panel[kEdpBacklightMsb] == 13);
    assert(modeWrites == 4);
    panel[kEdpBacklightMode] = 0xa0;
    assert(nvrm_edp_backlight(&request) == 0 && modeWrites == 5);
    assert(panel[kEdpBacklightMode] == 0xa2 && panel[kEdpBacklightMsb] == 13);
    puts("PASS: active AUX control, exact lengths, partial writes and opt-in PWM takeover");
}
'''
with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    (d/'test.cpp').write_text(fixture)
    subprocess.run(['clang++', '-std=c++17', '-Wall', '-Werror',
                    '-fsanitize=address,undefined', f'-I{root}/kexts/NVRMFB',
                    str(d/'test.cpp'), '-o', str(d/'test')], check=True)
    subprocess.run([str(d/'test')], check=True)
