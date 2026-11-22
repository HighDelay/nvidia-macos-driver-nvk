/*
 * NullMoth NVIDIA driver for macOS
 * Copyright (c) 2026 NullMoth Systems.
 * SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
 */

#include <nv.h>
#include <nv-priv.h>
#include "gpu/gpu.h"
#include "kernel/gpu/bus/kern_bus.h"

int nvrm_bar1_console_preserved(nv_state_t *nv);

/* Call after successful rm_init_adapter(), before NVKMS can release the GOP
 * reservation. During RM initialization the copier has no BAR1 mapping. */
int nvrm_bar1_console_preserved(nv_state_t *nv)
{
    OBJGPU *gpu = NV_GET_NV_PRIV_PGPU(nv);
    KernelBus *bus = gpu ? GPU_GET_KERNEL_BUS(gpu) : NULL;
    return bus && bus->bBar1ConsolePreserved;
}
