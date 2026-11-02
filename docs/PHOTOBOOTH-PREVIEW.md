# Photo Booth preview repair

The 2026-10-31 local test fixes two separate problems on an RTX 2060 SUPER,
Tahoe 26.7.1 (25G241), using a Google Pixel USB webcam:

- **Black preview:** upstream PR #433 widens texture usage only when a storage
  view exists. Shared-VRAM imports created from read-only drawable descriptors
  still lacked that view. Request it before import; retain shared VRAM when
  supported and use the private-image fallback when a linear import drops it.
- **Upside-down preview:** Photo Booth's legacy Core Image call writes
  bottom-first rows to its top-first Metal preview. Reflect the CIImage within
  the requested render bounds only for actual NVMTL CAMetalLayer drawables in
  `com.apple.PhotoBooth`. The user confirmed the normal application launch is
  upright with Zink loaded. Camera buffers, offscreen exports, saved photos,
  OBS and generic Core Image destination orientation keep their existing paths.

This is a Photo Booth compatibility adapter, not a global Metal coordinate
change. It checks method signatures, keeps inherited superclass methods intact,
and pins its runtime callbacks. No application binary or Zink selection is
modified. To disable only the orientation adapter for diagnosis, quit Photo
Booth, run `defaults write com.apple.PhotoBooth NVMTLPhotoBoothPreviewFlip -bool false`,
then reopen it. Delete that preference to restore the default.

Validation:

- Release-mode x86_64 plugin build targeting macOS 15.0; strict bundle signature
  verification. Kernel, CGL/Zink and runtime binaries were unchanged.
- Sixteen Core Image frames: RGBA/BGRA, shared/private backing, repeated
  wrappers, both vertical orientations and every output pixel checked.
- Twelve NV12 GPU cases across sizes, command buffers and queues; zero luma or
  chroma mismatches.
- Host ASan/UBSan fixtures cover usage policy, cleanup after storage fallback,
  process identity, drawable marking, nonzero crop origins, invalid rectangles,
  pass-through arguments and inherited-method isolation.
- Direct Photo Booth test: preview visible and upright; missing-ShaderWrite and
  nil-destination errors gone. The final launch loaded Zink and the installed
  plugin without the temporary diagnostic injection.

Run `python3 tools/test_surface_drawable_usage.py` and
`python3 tools/test_photobooth_preview.py` for host checks. The synthetic GPU
probe is `tools/photobooth_surface_probe.m`. When testing an uninstalled plugin,
use the process-local `tools/metal_test_lookup.c` shim to prevent Core Image
from loading a second installed driver; the probe requires exactly one image.

This was a local userland update, not a new boot, Sequoia qualification or
release. No release draft was requested. Camera images are not included in
the source or validation artifacts.

## Default-descriptor regression after 2.6.6

The merged Unknown-usage early return left the fork's backing/view repair in
place but bypassed its reported usage flags. Photo Booth can receive a default
descriptor: `_usage` stayed zero and the public getter returned ShaderRead.
Core Image rejected that drawable for lacking ShaderWrite and produced a nil
render destination, even though a writable view existed.

Preserve zero only when no real storage view exists, allowing upstream's
optimal-tiled storage twin. Eligible app surfaces with actual writable backing
still expose explicit read/write/render-target usage. WindowServer and FBO
policy, fallback cleanup and preview orientation remain unchanged.

The original Core Image probe used explicit ShaderRead and missed this case.
It now checks 32 frames across default/explicit usage, RGBA/BGRA, shared/private
backing and both orientations. The new case fails on the initial merged build
and passes with the repair. Unknown RGBA16Unorm GPU writes and the host usage/
lifetime regressions also pass. The user confirmed the normal Photo Booth
preview is visible, moving and upright on Tahoe 26.7.1 after the plugin-only
update, with Zink retained. No camera image was saved for these checks.
