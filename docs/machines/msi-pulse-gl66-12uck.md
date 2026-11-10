# MSI Pulse GL66 12UCK — RTX 3050 Laptop, 4 GB

This fork's target machine. The PCI IDs below were read from the laptop on Windows
(driver 621.01, 2026-10-31). macOS has not been booted with this tree on this hardware.
Nothing here is a claim that the desktop, Metal, or a game already runs.

Upstream remains [HighDelay/nvidia-macos-driver-nvk](https://github.com/HighDelay/nvidia-macos-driver-nvk),
which is itself a fork of [nullmoth/nvidia-macos-driver](https://github.com/nullmoth/nvidia-macos-driver).

## Measured hardware

| | |
|---|---|
| Laptop | MSI Pulse GL66 12UCK, board MS-1584 REV:1.0 |
| BIOS | American Megatrends E1584IMS.115 (2025-08-28) |
| CPU | Intel Core i5-12450H (8 cores, 12 threads) |
| Memory | 24 GB |
| Panel | Chimei Innolux CMN1521, internal, 1920×1080, image size 34 cm × 19 cm. Refresh was not read. |
| iGPU | Intel UHD Graphics `8086:46A3` rev 0C, subsystem `1462:1331`. This is the only display that was active. |
| dGPU | NVIDIA GeForce RTX 3050 Laptop GPU, GA107M, `10de:25a2` rev A1 |
| Subsystem | `1462:1331` (MSI). GPU part `25A2-725-A1`. VBIOS `94.07.4e.00.6e` |
| VRAM | 4096 MiB total, 131 MiB reserved by the Windows driver |
| BAR1 | 4096 MiB (firmware Resizable BAR already covers the whole framebuffer) |
| Link | PCIe 4.0 x8 (device max x16) |
| Clocks | SM max 2100 MHz, memory 6001 MHz |
| Power | `power.limit` was N/A. Enforced limit while idle was 64.67 W. That is not a TGP rating. |
| Displays on the NVIDIA card | None. `nvidia-smi` reported Display Attached: No, Display Active: Disabled. |

`10de:25a2` is already in `SUPPORTED_IDS` in `package/install.sh`. `NVRM`'s personality matches PCI class `0x03` with the subclass free, so a muxless 3D controller (`0302`) matches as well as a VGA device (`0300`). `kexts/NMIntelFB/display_ids.inc` already lists `0x46a3` as Intel display version 13 (Alder Lake).

## How this laptop has to boot

The generic desktop instructions in [OPENCORE-SETUP.md](../OPENCORE-SETUP.md) assume the NVIDIA card owns the boot screen. On this machine it does not. Copying them unchanged removes the only framebuffer.

Use the generic boot arguments, SIP value, and `SecureBootModel = Disabled`. Then apply these deltas:

| Setting | Value on this laptop | Why |
|---|---|---|
| `UEFI → Quirks → ResizeGpuBars` | `-1` | Firmware already placed a 4096 MiB BAR1. `13` asks for 8 GB. This card has 4 GB, and `NVRM::placeLargeBar1` only grows a BAR that is still under 4 GB. |
| `Booter → Quirks → ResizeAppleGpuBars` | `-1` after the driver is installed | Keeps the 4 GB aperture macOS already inherited. Use `0` only for the macOS installer, as the generic guide says. |
| `Kernel → Block` of `com.apple.iokit.IONDRVSupport` | leave it **enabled** until `NMIntelFB` is installed | The block exists so the firmware framebuffer does not take a desktop NVIDIA card's display index. Here the firmware framebuffer is the panel. Blocking it while `NMIntelFB` is absent leaves no scan-out. |
| SMBIOS | a laptop model, not `iMacPro1,1` | 1401's `iMacPro1,1` has no iGPU and expects the discrete card to drive the console. `MacBookPro16,2` is the usual stand-in. It does not make Apple's driver bind to `46A3`. |
| `Kernel → Quirks → ProvideCurrentCpuInfo` | `True` | Alder Lake. This is an OpenCore requirement for the CPU, not something this driver configures. |
| WhateverGreen | do not load it for `46A3` | WhateverGreen does not drive Alder Lake-H/P. If some other kext pulls it in, add `gfxrst=4` as the generic guide says. |
| BIOS | Above 4G Decoding on, CSM off, Resizable BAR left on | Matches the 4096 MiB BAR1 already measured. |

Boot arguments, same as upstream, with nothing laptop-specific added:

```text
nvfb=1 nvaccel=1 nvfbheads=4 -nvkmsnosmooth amfi_get_out_of_my_way=0x1 amfi=0x80 ipc_control_port_options=0
```

Do not add `-nvaccelnoheadless`. That boot argument keeps a GPU with no connected display hidden from Metal, which is this card's normal state. Add `-nvrmnoflip` only if the picture flashes after the desktop has been up for about a minute.

`NVRM` leaves a BAR1 that is already at least 4 GB, already above 4 GB, and not covering the boot console where the firmware put it. Moving that window is what broke other hybrid laptops. Do not add a `DeviceProperties` entry for the NVIDIA card.

## Panel driver

`kexts/NMIntelFB` adopts the mode the firmware already lit on an Intel panel macOS cannot drive, and publishes it as the framebuffer NVIDIA frames are copied into. It is source in this tree. `package/install.sh` does not install it, and it cannot be built on Windows: `kexts/NMIntelFB/build.sh` needs the macOS SDK.

Since upstream v2.6.5-nvk the installer ships `Optional/NMIntelFB.kext` as an opt-in (default **No**; see [INTEL-PANEL.md](../INTEL-PANEL.md)). To test it on this laptop, on a spare boot drive: `sudo ./install.sh --intel-panel=yes`, reboot, and check the panel, brightness and sleep/wake. Keep `IONDRVSupport` unblocked until it is confirmed loaded. If the panel misbehaves, boot once with `-nmintelfboff`, then `sudo ./install.sh --intel-panel=no`. Without the kext the panel stays on the firmware framebuffer (`IONDRVFramebuffer`) and the RTX 3050 runs headless via `kexts/NVRM/accel/nvrm-accel.cpp`.

## Games on 4 GB

DXVK 3.1.1, the version this tree pins, will see a device-local heap of about 4 GB. [profiles/msi-pulse-gl66-12uck/dxvk.conf](../../profiles/msi-pulse-gl66-12uck/dxvk.conf) tells games the card has 3072 MiB of device memory and 512 MiB shared, so texture streaming stays inside what is left after the 131 MiB firmware reserve and the copies onto the Intel panel. DXVK documents `dxgi.maxDeviceMemory` as a reported size, not an allocator cap. `dxvk.maxMemoryBudget` is a debug limiter and is not set.

Point a prefix at that file with `DXVK_CONFIG_FILE`, or copy it to the game directory as `dxvk.conf`. The YAAGL backend already defaults `dxvk.enableDescriptorBuffer` to false; leave that as the launcher sets it.

1080p is the panel. A 4 GB GA107 does not have headroom for a 1440p or 4K D3D12 heap on top of the desktop copy.

## Recovery

If a boot does not reach the desktop, use the upstream removal steps in [COMBINED-INSTALL.md](../COMBINED-INSTALL.md): from Recovery, `nvram "7C436110-AB2A-4BBB-A880-FE41995C9F82:nullmoth-remove=1"`, reboot, run `sudo ./uninstall.sh`, then delete that nvram key before the next shutdown. The first macOS install itself still uses `ResizeGpuBars = -1`, `ResizeAppleGpuBars = 0`, and `IONDRVSupport` not excluded.
