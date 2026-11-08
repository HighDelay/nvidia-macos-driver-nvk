# Optional MetalFX DLSS

Upstream 2.8 adds DLSS behind native MetalFX upscaling, denoising and frame
interpolation. Apps must use those MetalFX APIs; this does not enable DLSS in
all apps or add hardware capabilities to a GPU.

`Install.command` asks whether to install native MetalFX DLSS, default **No**.
For a noninteractive opt-in, run from the extracted `pkgroot/`:

```sh
sudo ./install.sh --dlss=yes
```

The installer downloads about 750 MB from NVIDIA's sources, verifies pinned
hashes and builds GPU kernels for the detected RTX 20/30/40/50 architecture.
Allow a few minutes and at least 2.5 GB free. This step is optional; a failure
leaves the driver installed and can be retried with the same command.
`--dlss=no` skips this step and keeps existing files; the upstream
`NULLMOTH_DLSS=1` opt-in is also accepted.

Files live under `/Library/NullMoth/dlss/`; compiled kernels are in `kernels/`.
Relaunch the game after setup. `NVMTL_DLSS=0` in `nvrm610.conf` disables the
native adapter. Super Resolution, Ray Reconstruction and Frame Generation
remain separate features whose operation depends on the game and GPU.

Wine games use the [Wine DLSS setup](../nvk/wine-macos/INSTALL.md#d3d12-and-dlss)
and their original `nvngx_dlss.dll`. These paths do not change video codecs.
