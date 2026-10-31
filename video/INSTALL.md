# Additional NVIDIA video codecs

This driver can bundle [mickys/nvenc](https://github.com/mickys/nvenc) with a
pinned Turing overlay. On RTX 20 / TU10x, it adds hardware **VP8 and VP9 decoding**
through VideoToolbox while retaining the driver's H.264/HEVC encoders and decoders.
VP9 supports 8-bit NV12 and 10-bit P010 output. Low-level 12-bit VP9 decoding is
separate from the VideoToolbox output formats.

**VP8/VP9 encoding and AV1 encode/decode are not provided by Turing's hardware.**
Use an application's software encoder for VP8/VP9. The addon cannot add absent
hardware capabilities. Ada, TU117 and Ampere retain the upstream codec selection;
the new VP8 VideoToolbox adapter is enabled for TU10x only. Ada keeps its upstream
H.264/HEVC/AV1 encoders. Ampere keeps the driver's H.264/HEVC encoders and the
addon's upstream VP9/AV1 decoders. TU117 keeps its upstream H.264/HEVC encoders.

## Installed driver

Packages built with `--video-runtime` include `libnvencvt.dylib` beside the Metal
plugin and a source/license record in `/Library/GPUBundles/nvmtl/video`.
`NVMTL_NVENCVT=1` in `/Library/GPUBundles/nvmtl/nvrm610.conf` enables the addon when
an application creates its Metal device. A pre-existing explicit setting is
preserved. Restart the application after changing this setting; no reboot is
needed for a userland codec update.

Applications must request VideoToolbox decoding for that codec. A registered
VP8 decoder does not redirect an application's private software VP8 decoder.
Container support and codec negotiation remain application-specific. Browser,
Discord, OBS and QuickTime use should be qualified separately from API tests.

For command-line tools, preload the codec before their first VideoToolbox
session. For example, with an existing compatible FFmpeg installation:

```sh
DYLD_INSERT_LIBRARIES=/Library/GPUBundles/NVMTLDriver.bundle/Contents/MacOS/libnvencvt.dylib \
  ffmpeg -hwaccel videotoolbox -i input.webm -f null -
```

To disable the addon, add this block to `nvrm610.conf` (or put the setting inside
its existing block), then restart the affected applications. These markers keep
the setting across driver upgrades:

```ini
# --- my settings, kept on update ---
NVMTL_NVENCVT=0
# --- end my settings ---
```

Built-in H.264/HEVC codecs remain available. Remove the addon library only as part
of a signed bundle update or reinstall the previous complete driver package.

## Build and package

```sh
python3 build/build_nvenc.py /Volumes/Games/nvenc-source /Volumes/Games/nvenc-runtime --fetch
```

The builder verifies the pinned source and dependency revisions, applies only
the recorded overlay, builds/signs the x86_64 macOS 15.0 library, and records its
source identity, compiler, license notices and payload hashes in
`NVENC-BUILD.json`. Existing unrelated source edits and output directories are
rejected.

Pass `--video-runtime /Volumes/Games/nvenc-runtime` to `build/stage_package.py`
alongside the normal Minecraft, Wine and driver build inputs, then complete the
normal Zink/combined package steps. Keep this runtime in future complete driver
packages. No kernel or CGL changes are required for the codec library.

## Validation

On an RTX 2060 SUPER / Tahoe 26.7.1, 24 hardware VideoToolbox sessions decoded
180 synthetic VP8/VP9 frames through 4K, with every luma/chroma sample matching
libvpx. This includes odd dimensions, VP9 10-bit output and synchronous/asynchronous
delivery. Separate checks confirmed installed-driver loading. The retained
H.264/HEVC hardware encoders each produced eight valid packets in a smoke test.

Ada, Ampere and TU117 codec registration is regression-tested with recorded OS
calls; those cards and Sequoia have not received hardware codec testing here.
See the [validation record](../docs/VIDEO-CODECS-VALIDATION.json) and the probes in
[`tools/video`](../tools/video). The probe's `installed` argument checks automatic
driver loading; a library path checks process-local registration.
