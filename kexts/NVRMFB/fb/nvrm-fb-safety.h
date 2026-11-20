/*
 * Copyright (c) 2026 NullMoth Systems.
 * SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
 */
#pragma once
static inline bool nvrmCheckedPitch(unsigned width, unsigned alignment, unsigned *out)
{
    if (!out || !width || !alignment || (alignment & (alignment - 1))) return false;
    const unsigned long long pitch = ((unsigned long long)width * 4 + alignment - 1) &
                                      ~((unsigned long long)alignment - 1);
    if (pitch > 0xffffffffULL) return false;
    *out = (unsigned)pitch;
    return true;
}
static inline bool nvrmKmsRasterValid(unsigned width, unsigned height)
{
    return width && height && width <= 0xffffu && height <= 0xffffu;
}
static inline unsigned long long nvrmApertureBytes(unsigned pitch, unsigned height,
                                                  unsigned long long owned)
{
    if (!pitch || !height) return 0;
    const unsigned long long need = (unsigned long long)pitch * height;
    if (need > owned) return 0;
    const unsigned long long spare = owned - need;
    return need + (spare < 128 ? spare : 128);
}

// ---- Variable refresh rate (adaptive sync) ----
// IOGraphics' model, from its public types: a display's IODisplayTimingRange carries kIORangeSupportsVRR, each detailed timing
// carries verticalBlankingExtension (the most blanking lines the display can stretch a frame by; 0 = none), and the
// kIOFBVariableRefreshRate attribute switches it on and off for the connection.
// CTA-861 HDR static metadata block with the ST 2084 EOTF.
static inline bool nvrmEdidHdrPq(const unsigned char *edid, unsigned bytes)
{
    if (!edid || bytes < 256) return false;
    const unsigned available = bytes / 128 - 1;
    const unsigned count = edid[126] < available ? edid[126] : available;
    for (unsigned i = 1; i <= count; ++i) {
        const unsigned char *block = edid + 128 * i;
        if (block[0] != 0x02 || block[2] < 4 || block[2] > 127) continue;
        for (unsigned pos = 4; pos < block[2];) {
            const unsigned length = block[pos] & 31;
            if (pos + 1 + length > block[2]) break;
            if ((block[pos] >> 5) == 7 && length >= 3 && block[pos + 1] == 6 && (block[pos + 2] & 4))
                return true;
            pos += 1 + length;
        }
    }
    return false;
}

// The panel's vertical rate range comes from the EDID range-limits descriptor (tag 0xFD): min and max vertical rate in Hz,
// with the offset flags in byte 4 adding 255 Hz to the two when set. 0 when the EDID gives no range.
static inline bool nvrmEdidVerticalRange(const unsigned char *e, unsigned n, unsigned *minHz, unsigned *maxHz)
{
    if (!e || n < 128 || !minHz || !maxHz) return false;
    for (unsigned off = 54; off + 18 <= 126; off += 18) {
        const unsigned char *d = e + off;
        if (d[0] || d[1] || d[2] != 0 || d[3] != 0xFD) continue;
        unsigned lo = d[5], hi = d[6];
        if (d[4] & 2) hi += 255;
        if (d[4] & 1) lo += 255;
        if (!lo || hi < lo) return false;
        *minHz = lo; *maxHz = hi; return true;
    }
    return false;
}
// Lines a frame of `vTotal` lines at `hz` can be stretched by before it drops under the display's minimum rate.
static inline unsigned nvrmVrrBlankExtension(unsigned vTotal, unsigned hz, unsigned minHz, unsigned maxHz)
{
    if (!vTotal || !hz || !minHz || hz > maxHz || hz < minHz) return 0;
    const unsigned long long stretched = ((unsigned long long)vTotal * hz + minHz / 2) / minHz;
    return stretched > vTotal ? (unsigned)(stretched - vTotal) : 0;
}
