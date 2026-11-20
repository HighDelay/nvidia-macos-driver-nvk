/*
 * Copyright (c) 2026 NullMoth Systems.
 * SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
 */
// Panel brightness through the eDP DPCD (VESA eDP 1.4 section on backlight control), for internal panels the resource manager
// registers no backlight for: no ACPI brightness method and nothing the firmware knows how to drive, so NVKMS reports no
// backlight and macOS shows no slider. Many such panels take brightness over the AUX channel: the sink advertises it in
// DPCD 0x702 bit 1 and the source can write levels to 0x722/0x723 while the panel is in DPCD mode.
// A panel that only has the PWM pin (0x702 bit 0 without bit 1) stays out of reach: its PWM comes from the card's firmware tables.
// Identical copies live in kexts/NVRMFB and kexts/NVRM only through nvrm_ddc_abi.h's request; this header is NVRMFB's.
#pragma once
enum {
    kEdpGeneralCap1   = 0x701,
    kEdpBacklightCap  = 0x702,
    kEdpDisplayCtl    = 0x720,
    kEdpBacklightMode = 0x721,
    kEdpBacklightMsb  = 0x722,
    kEdpBacklightLsb  = 0x723,
};
enum { kEdpCapAuxSet = 0x02, kEdpCapByteCount = 0x04, kEdpCtlBacklightEnable = 0x01, kEdpModeMask = 0x03, kEdpModeDpcd = 0x02 };
// What the sink advertises: AUX brightness control, and whether the level is one byte or two.
struct EdpBacklightCaps { bool auxSet; bool twoBytes; };
static inline EdpBacklightCaps edpBacklightCaps(unsigned char cap1, unsigned char cap2)
{
    EdpBacklightCaps c;
    c.auxSet = (cap1 & 0x01) && (cap2 & kEdpCapAuxSet);    // 0x701 bit 0: the TCON can adjust the backlight at all
    c.twoBytes = (cap2 & kEdpCapByteCount) != 0;
    return c;
}
// The level register value for a percentage (0..100) of full scale: 16-bit across MSB:LSB, or the MSB alone for one-byte sinks.
static inline unsigned edpBacklightLevel(unsigned percent, bool twoBytes)
{
    if (percent > 100) percent = 100;
    return twoBytes ? (percent * 0xFFFFu + 50) / 100 : (percent * 0xFFu + 50) / 100;
}
