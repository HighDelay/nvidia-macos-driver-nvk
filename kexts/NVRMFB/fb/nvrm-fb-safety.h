/*
 * Copyright (c) 2026 NullMoth Systems.
 * SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
 */
#pragma once

// nvfbhotplugpoll=<seconds>. Zero leaves detection event-driven.
static inline bool nvrmHotplugPollValid(unsigned seconds)
{
    return seconds >= 1 && seconds <= 60;
}
enum { kNvrmBootRetries = 6 };
static inline unsigned nvrmBootRetryDelayMs(unsigned attempt)
{
    return attempt < kNvrmBootRetries ? 3000u : 0u;
}
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

// ---- Surround: several heads, one logical display (nvfbsurround) ----
// Tiled 5K/6K panels work this way on a Mac: one framebuffer with a raster as wide as the tiles together, each head scanning its
// slice of the same surface. Members sit side by side in index order and must run the same height and the same refresh rate.
struct NvrmSurMode { unsigned w, h, hz; };
enum { kNvrmSurMaxMembers = 4, kNvrmSurMaxModes = 64 };
// Choose one mode per member: the tallest height every member has, then the highest refresh rate every member has at that height
// (within 1 Hz), and for each member the widest mode at that height and rate. Returns false when the members share nothing.
static inline bool nvrmSurroundPlan(const NvrmSurMode modes[][kNvrmSurMaxModes], const unsigned *count, unsigned members,
                                    unsigned *pick, unsigned *totalW, unsigned *height, unsigned *hz)
{
    if (members < 2 || members > kNvrmSurMaxMembers) return false;
    unsigned bestH = 0, bestHz = 0;
    for (unsigned a = 0; a < count[0]; a++) {
        const unsigned h = modes[0][a].h, r = modes[0][a].hz;
        if (h < bestH || (h == bestH && r <= bestHz)) continue;
        bool all = true;
        for (unsigned m = 1; m < members && all; m++) {
            bool has = false;
            for (unsigned b = 0; b < count[m] && !has; b++) has = modes[m][b].h == h && modes[m][b].hz + 1 >= r && modes[m][b].hz <= r + 1;
            all = has;
        }
        if (all) { bestH = h; bestHz = r; }
    }
    if (!bestH) return false;
    unsigned sum = 0;
    for (unsigned m = 0; m < members; m++) {
        int at = -1; unsigned bw = 0;
        for (unsigned b = 0; b < count[m]; b++)
            if (modes[m][b].h == bestH && modes[m][b].hz + 1 >= bestHz && modes[m][b].hz <= bestHz + 1 && modes[m][b].w > bw) { bw = modes[m][b].w; at = (int)b; }
        if (at < 0) return false;
        pick[m] = (unsigned)at; sum += bw;
    }
    *totalW = sum; *height = bestH; *hz = bestHz;
    return true;
}
// Members from a bit mask of framebuffer indices: the master is the lowest index, the rest are members that do not start as their
// own framebuffer. Only indices below `connected` count.
static inline unsigned nvrmSurroundMembers(unsigned mask, unsigned connected, unsigned *idx)
{
    unsigned n = 0;
    for (unsigned i = 0; i < 8 && i < connected && n < kNvrmSurMaxMembers; i++) if (mask & (1u << i)) idx[n++] = i;
    return n;
}
// The slice offset of member m: the widths of the members before it.
static inline unsigned nvrmSurroundOffset(const unsigned *widths, unsigned m) { unsigned o = 0; for (unsigned i = 0; i < m; i++) o += widths[i]; return o; }
// The master of a surround mask is its lowest connected index; a member is any other connected index in the mask.
static inline unsigned nvrmSurroundMaster(unsigned mask, unsigned connected) { for (unsigned i = 0; i < 8 && i < connected; i++) if (mask & (1u << i)) return i; return 8; }
static inline bool nvrmSurroundIsMember(unsigned mask, unsigned index, unsigned connected)
{
    unsigned idx[kNvrmSurMaxMembers];
    return nvrmSurroundMembers(mask, connected, idx) >= 2 && index != nvrmSurroundMaster(mask, connected) && index < 8 && (mask & (1u << index));
}
// What a hot-plug scan does to a group. `total` = members in the mask, `up` = how many of them are connected now.
//   Formed and a member went away      -> Degrade: every display is its own again (members un-hidden).
//   Degraded and all members are back  -> Reform: members hidden, the master offers the combined mode again.
enum NvrmSurAction { kSurNone = 0, kSurDegrade = 1, kSurReform = 2 };
static inline NvrmSurAction nvrmSurroundNext(bool formed, bool degraded, unsigned total, unsigned up)
{
    if (formed && up < total) return kSurDegrade;
    if (degraded && total >= 2 && up == total) return kSurReform;
    return kSurNone;
}
