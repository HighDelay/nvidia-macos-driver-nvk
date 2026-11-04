// Bounds shared by the real framebuffer and its host-side regression tests.
#pragma once
#include <stdint.h>
#include <stddef.h>

namespace nmintelfb {
constexpr bool surfaceFits(uint32_t width, uint32_t height, uint32_t pitch,
                           uint64_t offset, uint64_t aperture)
{
    return width >= 320 && width <= 16384 && height >= 200 && height <= 16384 &&
           pitch >= uint64_t(width) * 4 && !(pitch & 3) && offset <= aperture &&
           uint64_t(pitch) * height <= aperture - offset;
}

constexpr uint64_t allocationSize(uint64_t size, uint64_t budget, uint64_t used)
{
    if (!size || size > UINT64_MAX - 0xffff || used > budget) return 0;
    const uint64_t rounded = (size + 0xffff) & ~uint64_t(0xffff);
    return rounded <= budget - used ? rounded : 0;
}

inline uint32_t decimalKnob(const void *bytes, size_t length, uint32_t fallback)
{
    if (!bytes || !length || length > 11) return fallback;
    const unsigned char *s = static_cast<const unsigned char *>(bytes);
    if (s[length - 1] == 0) --length;
    if (!length) return fallback;
    uint32_t value = 0;
    for (size_t i = 0; i < length; ++i) {
        if (s[i] < '0' || s[i] > '9') return fallback;
        const uint32_t digit = s[i] - '0';
        if (value > (UINT32_MAX - digit) / 10) return fallback;
        value = value * 10 + digit;
    }
    return value;
}

constexpr bool nvrmGuardOff(bool removing, unsigned starts, bool nvrmAttached)
{
    // An attached NVRM has already passed its guard and may have incremented it.
    return removing || (starts >= 2 && !nvrmAttached);
}

constexpr uint32_t pwmDuty(uint32_t period, uint32_t percent, bool inverted)
{
    const uint32_t on = static_cast<uint32_t>(uint64_t(period) * percent / 100);
    return inverted ? period - on : on;
}
}
