# Optional Intel laptop panel framebuffer

The driver archive includes `Optional/NMIntelFB.kext`. The installer asks whether
to install it and defaults to **No**. Use it only for a laptop whose internal
screen is wired to an Intel GPU unsupported by macOS. Skip it on desktops,
Apple-supported Intel graphics and panels driven directly by NVIDIA.

The kext adopts a firmware-lit, linear XRGB8888 display mode. The bridge from
[nmx-intel](https://github.com/kitsuneislife/nmx-intel) lets NVIDIA render the
desktop while Intel scans out the panel. It includes headless-primary framebuffer,
NVKMS allocation, AGDC, brightness and boot-recovery changes adapted to this fork.
It does not initialize a panel that firmware left off or provide Intel 3D support.

The installer preserves an existing opt-in during updates. To select explicitly
or use a noninteractive installation:

```sh
sudo ./install.sh --intel-panel=yes
```

To remove an installed copy during the next driver installation:

```sh
sudo ./install.sh --intel-panel=no
```

New noninteractive installations skip it unless `--intel-panel=yes` is passed.
The choice participates in kernel-collection preflight, backup, rollback and
normal uninstallation. Reboot after installation or removal. For a diagnostic
opt-out at boot, `-nmintelfboff` prevents the real framebuffer from attaching.

## Test the bridge

Install the complete package with the Intel-panel option and reboot. The NVIDIA
parent bridge defaults to the source project's **45-second takeover delay**.
An explicit `nullmoth-intelfb-delay` setting overrides it, up to 120 seconds.
Ordinary acceleration starts automatically after the selected panel and NVIDIA
accelerator are ready. The NVIDIA-parent route is intended for one internal
panel; disconnect NVIDIA-driven external displays for this test.
From the extracted `pkgroot`, inspect or retry activation:

```sh
sudo bash Optional/Intel-panel.command enable
bash Optional/Intel-panel.command status
```

If the existing desktop has not adopted the accelerator, log out and back in.
Check that the desktop updates and `nvaccel_iop_ok` increases before trying fast
copies. The helper re-registers a panel that published after NVIDIA was armed.

## Experimental fast copies

```sh
sudo bash Optional/Intel-panel.command fast
```

This requests two RAM scanout buffers and NVIDIA copy-engine transfers, avoiding
the extra CPU copies. It requires matched NMIntelFB/NVAccel frame ABI 2, frames
already arriving from that accelerator, display version 12/13, the known 16 MiB
register aperture, and no competing Intel accelerator. Other layouts refuse it.
It reserves an unused GGTT range for the second buffer, checks mappings, waits for
flips before reusing buffers, and serializes CPU/GPU copies with mode changes.
The IOSurface's pending state remains intact for screenshots and CPU readers.

Fast copies default **off**. To return to ordinary panel copies:

```sh
sudo bash Optional/Intel-panel.command ordinary
```

Fast settings last until reboot; no automatic boot job is installed. Setting
`nullmoth-intelfb-auto=0` in NVRAM disables ordinary automatic activation.
The optional
one-colour-frame filter also defaults off (`debug.nvaccel_iop_blank_hold`, 0–8).
If a copy's completion or a scanout mapping becomes uncertain, the bridge retains
its resources and refuses further transfers. A negative `nmintelfb_ram` or a
nonzero `nvaccel_iop_quarantined` requires a reboot before retrying.

## Recovery and testing limits

`-nvoff`, `-nmintelfboff`, `nmxintel=1`, the NVIDIA recovery flag and a tripped boot
guard leave this driver off. `sudo nvram nullmoth-intelfb=0` also disables it.
After an interrupted panel start, remove `nullmoth-intelfb-attaching` with
`sudo nvram -d nullmoth-intelfb-attaching` only when ready to retry. The flag clears
two minutes after activation completes. Failed NVIDIA/panel initialization drops
the claim so the firmware framebuffer can match again.

**Sleep/wake remains unsupported by this bridge. Keep the test laptop awake.**
The source project reports an Acer Nitro V15 / Raptor Lake i5-13420H / RTX 3050
Laptop running Sequoia 15.8.1. This adapted port still needs physical laptop tests
for boot, brightness, acceleration and RAM scanout. Host simulation, compilation
and kernel linking do not establish those results.

## Build

The package builder compiles the real kext and excludes the virtual test variant.
Use NVIDIA's `open-gpu-kernel-modules` at **620.70.06**, commit
`e6a2e01579fbbd1ca2ed43fef2ee2589e9bd8bd9`. Actual included headers are checked
against that commit and recorded outside the installable payload.

```sh
OGKM=/path/to/open-gpu-kernel-modules bash build/build_intel_panel.sh out/intel-panel
MACOSX_DEPLOYMENT_TARGET=15.0 OGKM=/path/to/open-gpu-kernel-modules \
  bash build/accel_build_all.sh kexts/NVRM out/intel-panel-accel
```

`Optional/INTEL-PANEL-BUILD.json` in the external build-record directory records
source, dependency and payload hashes. The package requires both matched OS
accelerators. SMBIOS model names are not used to guess panel wiring.
