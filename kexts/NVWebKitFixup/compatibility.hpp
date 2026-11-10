// SPDX-License-Identifier: BSD-3-Clause
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace NVWebKitCompatibility {
constexpr unsigned Sequoia = 24;
constexpr unsigned Tahoe = 25;
constexpr size_t LongJump = 14;
// Lilu's instruction decoder requires min + 32 readable bytes.
constexpr size_t Lookahead = LongJump + 32;
enum class Hook { Open, Map };

inline bool kernel(unsigned major) {
    return major == Sequoia || major == Tahoe;
}

inline bool inImage(uintptr_t address, uintptr_t base, size_t size) {
    return address && base && size <= UINTPTR_MAX - base && address >= base &&
        size >= Lookahead && address - base <= size - Lookahead;
}

// These exact frame setup/register-save layouts contain no PC-relative operand,
// branch or call. Only the stack-frame size may vary between OS builds. Do not
// accept arbitrary instructions merely because Lilu can decode their length:
// its trampoline copies them without relocating PC-relative operands.
inline size_t prefix(const uint8_t *code, size_t available, Hook hook) {
    constexpr uint8_t open[] = {0x55,0x48,0x89,0xe5,0x41,0x57,0x41,0x56,0x41,0x55,0x41,0x54,0x53,0x48,0x81,0xec};
    constexpr uint8_t map[] = {0x55,0x48,0x89,0xe5,0x41,0x57,0x41,0x56,0x53,0x48,0x81,0xec};
    const auto bytes = hook == Hook::Open ? open : map;
    const size_t length = hook == Hook::Open ? sizeof(open) : sizeof(map);
    if (!code || available < length + 4 || memcmp(code, bytes, length)) return 0;
    const uint32_t frame = uint32_t(code[length]) | (uint32_t(code[length+1]) << 8) |
        (uint32_t(code[length+2]) << 16) | (uint32_t(code[length+3]) << 24);
    if (!frame || frame > 0x10000 || (frame & 7)) return 0;
    return length + 4;
}
}
