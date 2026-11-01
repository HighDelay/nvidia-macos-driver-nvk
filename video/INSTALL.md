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
Edge 156 VP9 YouTube playback at **2160p60** is verified on RTX 2060 SUPER / Tahoe
with VideoToolbox decoding and ANGLE enabled. If an earlier GPU crash forced
software rendering, fully quit and reopen the browser after updating.
[Browser repair and validation](../docs/BROWSER-VIDEO-DECODING.md).

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

From the repository root, using sibling directories of your choice:

```sh
python3 build/build_nvenc.py ../nvenc-source ../nvenc-runtime --fetch
```

The builder verifies the pinned source and dependency revisions, applies only
the recorded overlay, builds/signs the x86_64 macOS 15.0 library, and records its
source identity, compiler, license notices and payload hashes in
`NVENC-BUILD.json`. Existing unrelated source edits and output directories are
rejected.

Pass `--video-runtime ../nvenc-runtime` to `build/stage_package.py`
alongside the normal Minecraft, Wine and driver build inputs, then complete the
normal Zink/combined package steps. Keep this runtime in future complete driver
packages. No kernel or CGL changes are required for the codec library.

## Validation

On an RTX 2060 SUPER / Tahoe 26.7.1, 48 hardware VideoToolbox sessions decoded
360 synthetic VP8/VP9 frames through 4K, with every luma/chroma sample matching
libvpx. This includes odd dimensions, VP9 10-bit output, synchronous/asynchronous
delivery and minimal browser output requests. Every frame has IOSurface backing.
Separate checks confirmed installed-driver loading. The retained
H.264/HEVC hardware encoders each produced eight valid packets in a smoke test.

Ada, Ampere and TU117 codec registration is regression-tested with recorded OS
calls; those cards and Sequoia have not received hardware codec testing here.
See the [initial validation](../docs/VIDEO-CODECS-VALIDATION.json),
[browser follow-up](../docs/BROWSER-VIDEO-DECODING.json) and the probes in
[`tools/video`](../tools/video). The probe's `installed` argument checks automatic
driver loading; a library path checks process-local registration.
