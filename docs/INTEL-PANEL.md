# Optional Intel laptop panel framebuffer

The driver archive includes `Optional/NMIntelFB.kext`. The installer asks whether
to install it and defaults to **No**. Use it only for a laptop whose internal
screen is wired to an Intel GPU unsupported by macOS. Skip it on desktops,
Apple-supported Intel graphics and panels driven directly by NVIDIA.

The kext recognizes its supported Intel PCI IDs and adopts the display mode
already set by firmware. It does not provide an Intel 3D driver or initialize
a panel that firmware left off. NVIDIA-to-Intel panel acceleration remains
disabled in NVAccel by default; this option does not enable that experimental
path. Physical laptop boot, brightness and sleep/wake testing are still needed.

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

The package builder always compiles the real variant through
`build/build_intel_panel.sh`; the virtual test variant is excluded. To build
the optional kext separately:

```sh
bash build/build_intel_panel.sh out/intel-panel
```

`Optional/INTEL-PANEL-BUILD.json` records source and payload hashes. This feature
does not rely on a MacBook SMBIOS name: Hackintosh model names do not reliably
identify panel wiring.
