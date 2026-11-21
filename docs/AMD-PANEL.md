# Experimental AMD laptop panel

On a muxless AMD + NVIDIA laptop, `Optional/NMGopFB.kext` lets NVIDIA render
the desktop for a panel wired to the AMD GPU. The CPU copies finished frames
to the 32-bit UEFI GOP framebuffer that the AMD GPU is already scanning out.
NVIDIA can render for this panel with its own display ports empty.
[Supported AMD device IDs](https://github.com/HighDelay/nvidia-macos-driver-nvk/blob/main/kexts/NMGopFB/nm_gopfb.h)

## Install

From `pkgroot/`, choose **Yes** for the experimental AMD panel when the installer
asks. The default is **No**, and updates retain an existing selection. For a
noninteractive install, use:

```sh
sudo ./install.sh --amd-panel=yes
```

The AMD and Intel options share a display slot. To switch from Intel to AMD in
one install, pass `--intel-panel=no --amd-panel=yes`.

Install the panel kext from the same package as NVRM, NVRMFB, NVAccel and
NVRMAGDC. Add `nmhybrid=1` to the OpenCore boot arguments alongside the normal
driver settings `nvfb=1` and `nvaccel=1`, then reboot. The AMD GPU must already
provide the boot framebuffer. Keep a working recovery setup for testing.

The panel uses the firmware resolution and 60 Hz software pacing. Presentation
has a CPU copy cost, and the driver leaves brightness, refresh and link setup
to firmware. The experimental path builds its display-pipe map at boot. Connect
any NVIDIA-driven displays you want to use before booting. Internal-panel
login, sustained presentation and sleep/wake still need testing on AMD + NVIDIA
laptop hardware.

## Check the handoff

Look for `NMGopFBActive` in IORegistry and check that the copy counter increases
while moving a window. The AMD panel uses driver slot 4. On an AMD-only display
setup, it is the first AGDC pipe.

```sh
ioreg -r -c NMGopFB -l
sysctl debug.nvaccel_heads_piped debug.nvaccel_iop_ok_h4 debug.nvrmfb_agdc_fbmap
```

## Recover or remove

If the NVIDIA handoff fails, the driver releases the AMD device for firmware
framebuffer matching. Add `-nmgopfboff` to the boot arguments to bypass NMGopFB.
To remove the kext during a driver update, use `sudo ./install.sh --amd-panel=no`
and reboot.
Normal driver uninstallation removes the installed panel kext. The installer
leaves the OpenCore boot arguments for this option under your control.
