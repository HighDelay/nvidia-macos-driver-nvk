# Native OpenGL and screen-capture driver package

The installer offers [optional Intel laptop panel support](INTEL-PANEL.md),
defaulting to **No**. This is separate from the normal NVIDIA display driver.

This is a complete fork driver package with an **optional Zink/NVK OpenGL
renderer**, enabled for Intel **Sequoia 15.5+ and Tahoe 26**. Local hardware
testing covers **26.7.1 / 25G241**. The Sequoia candidate uses reviewed
**15.8.1 / 24H32** CGL evidence. The RTX 2070 SUPER machine with 256 MiB BAR1
passes all 25 isolated native Zink tests, including GPU-copy pixel checks,
contexts, sharing, worker threads, resize and VSync. Minecraft 26.3 menus and
single-player world rendering are also user-confirmed after the small-BAR
mapping repair. A fresh Sequoia kernel-collection build remains unqualified.
It includes the existing
Minecraft and Wine runtimes, plus bounded IOSurface import reuse and GPU copies
for supported unaligned video planes. See `PACKAGE-IDENTITY.json` and the
accompanying validation/release notes for source identity and the latest
application and boot-test results. Integration probes alone do not qualify a
complete desktop session.

Extract the archive to obtain `pkgroot/`. With the matching NVIDIA kernel driver
already running, double-click `Verify.command` to check hashes and run GPU/CGL
probes. Diagnostic windows briefly appear. The probes use package-local
libraries; passing them does not prove global GLD registration or a successful
reboot.

When ready to install, retain your previous working driver archive and run:

```sh
cd /path/to/pkgroot
sudo ./install.sh
```

Alternatively double-click `Install.command`. Follow its approval and reboot
instructions. The installer verifies payload and kernel linking, backs up the
previous driver and preserves its boot fallback. It accepts later Tahoe builds
without an exact-build whitelist. Zink can continue working without rebuilding
when Apple's private CGL ABI remains compatible; changes to that ABI still
require updating the adapter. Other OS builds have not been hardware-tested.
It installs `NVKZinkGLDriver.bundle` in `/Library/GPUBundles` and selects it in
both Sequoia and Tahoe accelerator variants. Existing OpenCore/SIP requirements still apply;
no System volume mounting or authenticated-root change is needed.

Normal OpenGL applications then use Zink without a companion mod. The default
`auto` presentation prefers direct GPU copies into native drawable VRAM, with a
GPU IOSurface/Apple presentation path for clients without an AppKit drawable.
CPU frame transfer requires the explicit `NVKZINK_PRESENT=cpu` diagnostic.
To require the new direct path for a particular app:

```sh
NVKZINK_PRESENT=native /path/to/application/executable
```

Check that `GL_RENDERER` contains **zink** and **NVK**. Minecraft's native Vulkan
bridge and Wine's runtime are independent and retain their existing setup.

The screen-capture changes apply to shared driver operations used by capture
apps. `NVMTL_VRAM_KEEP=0` disables cross-wrapper import reuse for a process;
`NVMTL_VRAM_KEEP_MB` adjusts its idle-import budget (128 MiB by default).
OBS separately uses automatic Apple OpenGL/BGRA8 SDR compatibility when this
Zink runtime is loaded. It has an explicit opt-out; no encoder is added.
See `CAPTURE-REVIEW.md` for the scope and application-testing limits.

For rollback, reinstall the previous complete driver package and reboot. The
normal installer removes the optional GLD bundle and restores the Apple OpenGL
selection from that package. `Uninstall.command` removes the NVIDIA driver
entirely; it is not a Zink-only toggle. Existing Recovery/boot removal guidance
from the driver README remains applicable if the desktop cannot start.

## Recovery removal

If the desktop cannot start:

1. Boot into macOS Recovery, open Terminal, and run
   `nvram "7C436110-AB2A-4BBB-A880-FE41995C9F82:nullmoth-remove=1"`, then reboot.
2. Run `sudo ./uninstall.sh` from the extracted `pkgroot`.
3. Before rebooting again, clear the recovery flag with
   `nvram -d "7C436110-AB2A-4BBB-A880-FE41995C9F82:nullmoth-remove=1"`.
4. Reinstall the previous working driver package.

See `Library/GPUBundles/nvmtl/zink/ZINK-OPENGL.md` for build reproduction,
context/API limits, mode selection and validation details. Other OS builds,
GPUs, full games, sandboxed clients and a performance improvement remain
unqualified. Mesa's reported GL 4.6 version does not extend Apple's callable
framework API automatically.
