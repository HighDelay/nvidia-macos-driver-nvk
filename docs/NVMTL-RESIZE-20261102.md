# Native Zink presentation after a large window resize

Minecraft 26.3 continued accepting input after enlarging its window, but its
image stopped updating. The installed PR #5 / upstream 2.6.6 plugin was
`5fa57f51477c94298cb3c0a319d57d5e5c62ccf1df51460f8d3eab2621fb156d`.
The process used SDL3 and native Zink on NVK TU106-A.

On RTX 2060 / macOS 26.7.2 (25G332), kernel counters showed 241,762,304 bytes
mapped against a 242,155,520-byte family BAR1 budget. The card has 256 MiB BAR1
and 6 GiB VRAM. The kernel repeatedly refused a 7,573,504-byte drawable backing;
Java samples showed continuing render/presentation calls and failed
`nvRmApiSurfShare` imports. A separate native-window probe reproduced a safe
handoff failure after growing from 128×128. Depending on available BAR1 space,
854×480 or the larger drawable was the first failure. Buffer requirements
matched the IOSurface payload, so no capacity guard was weakened.

The native Zink presenter now falls back after `VK_ERROR_INVALID_EXTERNAL_HANDLE`
from a family-VRAM allocation. It imports the drawable's aligned system pages
with `VK_EXT_external_memory_host` on the same Vulkan device and copies the
scratch image there on the GPU. Bounds include the full aligned allocation,
not just visible pixels. The IOSurface write lock stays held through confirmed
GPU completion; unlocking publishes its seed. This path does not set a family
VRAM dirty notification or generation tag. Both paths keep safe pre-submit
cleanup and retain all resources after uncertain GPU work. No CPU game-frame
readback, row conversion or pixel copy was added.

`tools/test_zink_runtime.py` passes all 36 cases. Large-resize tests change color
each frame and verify all pixels after repeated growth and shrink, including
854×480, requested 1920×960/1080 and return to 640×480. AppKit may constrain the
actual size to the screen; 1920×954 was verified on this desktop. The automatic
GLD test relies on AppKit updates without calling the bridge resize API. A
process-local shim refuses only Zink's family imports before submission: all
24 frames must then use system-page GPU copies. Rejection and uncertain-work
quarantine pass under the same injection. Core/legacy GL, sharing, opacity,
render threads, multiple windows, VSync, OBS policy and OS-build policy also pass.

Focused host checks for Zink mapping, package assembly and host-page bounds pass.
The CGL/GLD adapters, plugin, translator, kernel, Minecraft Vulkan/Wine runtimes,
DXVK and optional panel binaries are reused from the verified local 2.6.6 base.
Only Zink and affected diagnostics are rebuilt; CGL's existing 25G241 build
profile and paired GLD remain unchanged. Build identities, final package
validation and archive digests accompany the local test package outside pkgroot.

This is native probe verification on the user's GPU, not a confirmed fix in the
running Minecraft session. Capturing the probe's composited window with
`screencapture -l` failed (`could not create image from window`), so screen pixels
were not independently verified. No candidate installation/reboot, extended
Minecraft session, Sequoia runtime, other GPU or sleep/wake test was performed.
Kernel binaries are unchanged, so kernel-collection linking was not repeated.
Release publication remains held for local testing.
