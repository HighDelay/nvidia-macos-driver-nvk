# Experimental AMD laptop panel

`Optional/NMGopFB.kext` exposes an existing 32-bit UEFI GOP framebuffer on
supported AMD GPUs that macOS cannot drive. It uses the mode the firmware left
active. NVIDIA-accelerated presentation to an AMD-wired muxless panel still
requires separate bridge work. [Supported AMD device IDs](https://github.com/HighDelay/nvidia-macos-driver-nvk/blob/main/kexts/NMGopFB/nm_gopfb.h)

## Install

From `pkgroot/`, choose **Yes** for the experimental AMD panel when the installer
asks. The default is **No**, and updates retain an existing selection. For a
noninteractive install, use:

```sh
sudo ./install.sh --amd-panel=yes
```

The AMD and Intel options share a display slot. To switch from Intel to AMD in
one install, pass `--intel-panel=no --amd-panel=yes`.

The installer adds the kext to the normal driver transaction. To try the
firmware framebuffer, add `nmhybrid=1` to the OpenCore boot arguments and reboot.
The AMD GPU must already provide the boot framebuffer. Keep a working display
route for testing. The GOP path uses a fixed firmware resolution and 60 Hz
software pacing.

## Recover or remove

Add `-nmgopfboff` to the boot arguments to bypass NMGopFB. To remove the kext
during a driver update, use `sudo ./install.sh --amd-panel=no` and reboot.
Normal driver uninstallation removes the installed panel kext. The installer
leaves the OpenCore boot arguments for this option under your control.
