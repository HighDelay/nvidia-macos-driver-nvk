/*
 * Copyright (c) 2026 NullMoth Systems.
 * SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
 */
#pragma once

#include <stdint.h>

#define NVRM_CONSOLE_PAUSE "NVRMBootConsolePause"
#define NVRM_CONSOLE_SCANOUT "NVRMBootConsoleScanout"
#define NVRM_CONSOLE_DONE "NVRMBootConsoleDone"

struct NvrmConsoleScanout {
    void *address;
    uint64_t size;
    uint32_t width, height, pitch;
};

static inline bool nvrmConsoleFits(uint64_t width, uint64_t height, uint64_t pitch, uint64_t length)
{
    return width && height && !(pitch & 3) && width <= pitch / 4 && height <= length / pitch;
}

static inline bool nvrmConsoleRangeFits(uint64_t offset, uint64_t length, uint64_t window)
{
    return length && offset <= window && length <= window - offset;
}
