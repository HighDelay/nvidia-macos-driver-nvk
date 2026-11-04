#pragma once
#include <stdint.h>
#include <stddef.h>

namespace nmintelfb {
// All methods run under the framebuffer's scanout lock.
class FrameGate {
    uint64_t sequence_ = 0, active_ = 0;
    bool quarantined_ = false;
public:
    bool busy() const { return active_ != 0; }
    bool quarantined() const { return quarantined_; }
    bool canChangeMode() const { return !busy() && !quarantined_; }
    bool acquire(bool backBufferSafe, uint64_t &token) {
        token = 0;
        if (!backBufferSafe || !canChangeMode()) return false;
        if (!++sequence_) ++sequence_;
        token = active_ = sequence_;
        return true;
    }
    bool owns(uint64_t token) const { return token && token == active_; }
    bool finish(uint64_t token, bool completionKnown) {
        if (!owns(token)) return false;
        if (!completionKnown) quarantined_ = true;
        active_ = 0;
        return true;
    }
    void quarantine() { quarantined_ = true; }
};

// A second scanout may occupy only completely unused GGTT entries. Never use
// a guessed address or replace another firmware/display mapping.
inline uint64_t emptyGgttRange(const volatile uint64_t *pte, uint64_t count,
                              uint64_t pages, uint64_t alignmentPages)
{
    if (!pte || !pages || pages > count || !alignmentPages ||
        (alignmentPages & (alignmentPages - 1))) return UINT64_MAX;
    for (uint64_t first = alignmentPages; first <= count - pages;) {
        uint64_t i = 0;
        while (i < pages && pte[first + i] == 0) ++i;
        if (i == pages) return first;
        const uint64_t after = first + i + 1;
        if (after > UINT64_MAX - (alignmentPages - 1)) break;
        first = (after + alignmentPages - 1) & ~(alignmentPages - 1);
    }
    return UINT64_MAX;
}

constexpr bool ggttRangeFits(uint64_t mmioBytes, uint64_t apertureBytes,
                             uint64_t offset, uint64_t pages)
{
    return mmioBytes == (16ull << 20) && pages && !(offset & 4095) &&
        offset <= apertureBytes && pages <= (apertureBytes - offset) / 4096 &&
        offset / 4096 <= (mmioBytes / 2) / 8 &&
        pages <= (mmioBytes / 2) / 8 - offset / 4096;
}
}
