# Native OpenGL through Zink and NVK

This optional integration runs OpenGL shaders through Mesa Zink and the fork's
native NVK renderer. It is separate from Minecraft's native Vulkan bridge and
the Wine/DXVK/vkd3d runtime. Ordinary driver packages retain Apple's OpenGL
renderer unless built with the optional Zink GLD bundle.

## Rendering and presentation

| Path | Work submitted for an OpenGL application |
| --- | --- |
| Apple GL on this driver | OpenGL → Apple GL-on-Metal → Metal/NVMTL → NVK → NVIDIA GPU |
| Native Zink | OpenGL.framework dispatch → Mesa OpenGL/Zink → private NVK → NVIDIA GPU |

Zink removes the Apple GL-to-Metal and NVMTL Metal/AIR-to-SPIR-V stages from
application shader execution. Mesa still implements OpenGL, Zink generates
Vulkan work and NVK compiles GPU shaders. Apple's CGL carrier still owns native
context creation, pixel formats and drawable lifecycle; this is not a replacement
OpenGL.framework.

For an AppKit window, the default presentation mode copies on the GPU into the
actual CAMetalLayer drawable's IOSurface VRAM. It uses the same version-3
IOSurface import, completion fence, dirty notification and generation tag
protocol as the Minecraft native NVK runtime. It never shares a VkImage across
Vulkan devices. When family VRAM import is refused (for example, a full 256 MiB
BAR1 aperture), native Zink imports the drawable's system pages and copies into
them on the GPU. It holds an IOSurface write lock through GPU completion and
publishes the new seed instead of marking nonexistent VRAM current. Unsupported
host imports still fail safely; uncertain GPU work retains its resources and
quarantines the share group. A GPU scratch image handles channel order and opaque alpha;
there is no game-frame CPU readback, CPU row conversion or CPU pixel upload.
Metal still allocates the drawable and macOS composites it. This is a GPU-copy
path, not zero-copy scan-out, and it still waits for GPU completion on the CPU.

Native presentation follows the context's CGL swap interval: `0` disables
CAMetalLayer display synchronization and a nonzero interval enables it. An
application's VSync toggle takes effect on subsequent frames, independently for
each context, including render-thread clients. Intervals greater than one do not
currently add multi-refresh pacing. With VSync off, render throughput can exceed
the monitor's refresh rate; GPU copy completion is still required before a frame
is published. The display's physical refresh rate does not change.

Window swaps also accept a non-current context, as Apple's `CGLFlushDrawable`
and Cocoa `flushBuffer` do. The bridge locks and temporarily binds that context,
presents its GPU-rendered image, and restores the caller's CGL and Mesa bindings.
This covers clients such as AnyDesk that clear the context before flushing.
Ordinary swaps with the context already current keep their existing fast path.
Swaps before any drawable is attached are deferred. Automatic mode can switch
from its GPU fallback to native presentation when AppKit later attaches a view;
an explicit `gpu` selection remains in effect. AnyDesk 9.8.1 remote viewing is
user-confirmed with the default mode on RTX 2060 SUPER / Tahoe 26.7.1.

Single-buffered windows publish dirty front-buffer rendering after `glFlush`
or `glFinish`, including Apple's render-flush aliases. This uses the same
GPU-copy path without requiring a buffer swap. Explicit front-buffer draws in
double-buffered contexts also publish without rotating their back buffer;
ordinary back-buffer and offscreen flushes do not present a frame.

The private frontend and renderer live under
`/Library/GPUBundles/nvmtl/zink`. They do not change `VK_DRIVER_FILES`, the default
ICD, Minecraft's runtime, Wine's provider or their paired DXVK/vkd3d DLLs.

## Compatibility and limits

The native GLD/CGL adapter is tested on **x86_64 macOS 26.7.1, build
25G241**. It does not restrict activation to an exact macOS build number.
If Apple's private CGL layout and required exports remain compatible, an
installed runtime can continue selecting Zink after a macOS update without
rebuilding. The build recorded in the package is provenance, not an activation
whitelist. The user requested this policy and will test macOS updates.

A user reports that Minecraft continued launching with Zink after upgrading
from macOS 26.7.1 to 26.7.2 without rebuilding the CGL adapter. This records
Minecraft compatibility for that update; local diagnostics below use 25G241.

Apple's private ABI is still an assumption: a layout change can require an
adapter update and can break OpenGL before a clean fallback is possible. Existing
symbol/profile/device checks remain; they do not prove full layout compatibility.
The package selects native Zink for **Intel Sequoia 15.5+ and Tahoe 26**.
The [Sequoia ABI review](ZINK-SEQUOIA-ABI.json) compares a real **15.8.1 / 24H32**
collection with Tahoe: all 1,236 observed public dispatch entries and 131 GLD
exports match, together with the private profile/context/dispatch/share-group
offsets used by the adapter. The initialization loop has equivalent table
bounds; the Apple renderer's internal dispatch code differs and remains
delegated to Apple. This supports one shared adapter rather than a separate
binary or an exact-build whitelist.

**Sequoia hardware status:** the RTX 2070 SUPER machine on **15.8.1 / 24H32**
with **256 MiB BAR1** passes all **25 isolated native Zink tests**, including
core/legacy contexts, GL sharing, native GPU-copy pixel checks, opacity, resize,
worker threads, multiple windows, VSync toggles and failed-work quarantine.
This validates the adapter's complete lifecycle, including the GLD state-to-GLI
relation, in those probe processes. A subsequent Minecraft startup selected
Zink successfully but exposed a separate upload-buffer mapping crash. Small-BAR
CPU-visible buffers now prefer system memory, and failed maps return an OpenGL memory
error instead of dereferencing an unset transfer. This repair applies to both
Sequoia and Tahoe; see the [mapping investigation](ZINK-BUFFER-MAPPING.md).
On 2026-10-31, the same user confirmed menus and single-player world rendering
with the completed repair; the [game log](https://mclo.gs/CbC8KZ0) has no failed
Vulkan maps. The local Tahoe machine currently has 8 GiB BAR1; the maintainer
also previously confirmed Minecraft with a 256 MiB BAR1 configuration.

The adapter covers core 3.2/4.1 and legacy compatibility CGL contexts through the
entrypoints exposed by the current Apple SDK/framework. CGL reports the version
and extensions those entrypoints actually cover: **4.1 core and 3.2 compatibility**
on the tested framework. A build-time check uses the pinned Khronos registry
and recovered dispatch map; unsupported functions and empty Apple stubs cannot
qualify an extension. Version, GLSL and extension queries agree. The standalone
Mesa API retains its own capabilities. This prevents Skia rejecting an interface
that advertises unavailable 4.2+ functions.
RGB/RGBA8 windows, opaque/transparent alpha, ordinary CGL swaps, resize, sharing
and thread migration have focused tests. Stereo, multisampled/float drawables,
pbuffers, full-screen transitions, sandboxed clients, other GPUs and multiple
identical NVIDIA cards are unqualified. An ambiguous Metal/NVK GPU match is
refused. Apps must obey CGL context ownership. The GLD attaches shared contexts
automatically; developer tests using explicit attachment must attach every
shared context before creating GL objects.

The default `NVKZINK_PRESENT=auto` chooses direct native drawable presentation
when an AppKit view and matching NVIDIA drawable are available. Otherwise it
uses a GPU copy into shared IOSurface pages and Apple's presentation blit.
It never switches silently to CPU frame transfer. These modes are scoped to
the launched application:

| Value | Presentation |
| --- | --- |
| `auto` (default) | Native drawable when available; GPU IOSurface/Apple presentation for other CGL clients |
| `native` | Require a direct drawable GPU copy, into family VRAM or imported system pages; report unsupported targets as errors |
| `gpu` | Require shared host-page import plus Apple GL presentation; useful for comparison |
| `cpu` | Explicit readback/upload diagnostic; slow and not the normal rendering path |

For example, after installing the optional GLD package:

```sh
NVKZINK_PRESENT=native /path/to/application
```

Use the application's executable or launcher, rather than setting this for all
processes with `launchctl`. No boot argument or Minecraft companion mod selects
Zink. `NVKZINK_REQUIRE_GPU=1` makes the standalone probe reject missing GPU-copy
support; `NVKZINK_RGBX=1` tests alpha-less drawables. These names intentionally
survive the installed Metal plugin's filtering of internal `NVK_` variables.

### Native IOSurface textures

`CGLTexImageIOSurface2D` now binds rectangle textures to IOSurface storage while
rendering stays on Zink. GPU copies import and publish shared host pages; there
is no CPU pixel conversion or frame readback. Supported formats include BGRA8,
RGBA8/16F/32F, normalized and packed color formats, and individual R/RG planes
such as NV12's R8/RG8 planes, subject to the renderer's format support. This does
not perform YUV color conversion or import depth/compressed/multisample textures.

Producer flush/finish and GL fences publish writes before another client reads
them. After an external producer flushes or unlocks a changed surface, rebinding
its texture or attached framebuffer refreshes the image. Keep normal CGL sharing
and synchronization rules; concurrent unsynchronized writers are rejected.
Deleted textures retain their surface through queued writes, covering Avalonia's
delete-before-flush order. Failed imports leave the previous binding intact;
uncertain GPU completion retains resources and stops the affected Zink screen.

The [Stremio synchronization trial](ZINK-STREMIO-SYNCHRONIZATION.md) narrows
publication locking to the affected IOSurface, retaining aliases and binding
lifetimes while unrelated shared contexts continue uploading and rendering.
External publication still waits for GPU completion. The affected Blackwell
laptop's playback behavior is not yet confirmed, and this trial is not a release.

The CGL bridge clones a validated per-context integer callback vector and restores
the original vector during Apple presentation and teardown. It does not replace
OpenGL.framework, globally interpose `dlsym`, or opt Ryujinx out of Zink. Ryujinx
1.3.3's UI renders and responds on RTX 2060 SUPER / Tahoe 26.7.1 after a normal
launch. The user also sees its remaining UI quirks with Apple's renderer; game
emulation and this new interop path on Sequoia/other GPUs are not validated here.

### OBS display capture compatibility

OBS 33.0.1 retains its previously tested compatibility mode. Before native
IOSurface texture support, its Zink capture imports failed. Its ScreenCaptureKit
source also requests RGBA16F frames, which fail to start with “The stream is nil” even
with Apple's OpenGL renderer. Separate diagnostics receive BGRA8 frames and
verify Apple's BGRA8/RGBA16F texture imports successfully.

After installing a driver build with this repair, open OBS normally. The mode
is **enabled by default for OBS**, with no preference or launch argument needed.
Add **macOS Screen Capture**, explicitly select the display, and click **OK**.
The mode keeps OBS's entire OpenGL context in Apple's
renderer and changes its RGBA16F capture request to **8-bit BGRA SDR**. It is
checked only for the `com.obsproject.obs-studio` application; other applications
continue to select Zink. OBS 33.0.1's moving desktop preview is user-confirmed on
RTX 2060 SUPER / Tahoe 26.7.1. HDR capture, encoding and streaming need separate
testing. The compatibility mode continues using Apple's renderer independently
of the new native import support; OBS capture through Zink needs separate testing.

To turn the mode off, quit OBS and run:

```sh
defaults write com.obsproject.obs-studio NVKZinkCaptureCompatibility -bool false
```

Delete that preference to restore the automatic default:

```sh
defaults delete com.obsproject.obs-studio NVKZinkCaptureCompatibility
```

For a single launch, `NVKZINK_OBS_COMPAT=0 /Applications/OBS.app/Contents/MacOS/obs`
disables the mode; `NVKZINK_OBS_COMPAT=1` enables it even with a saved opt-out.
Explicit `0`/`1` environment values take priority over a saved preference.
For other CGL interoperability diagnostics,
`NVKZINK_DISABLE=1 /path/to/application` keeps automatic contexts in Apple's
renderer for that process. It does not alter ScreenCaptureKit formats.

Copy/import failures do not publish partial frames. Uncertain completion stops
the affected share group and retains its GPU resources until process exit.
Normal context teardown drains accepted work before freeing imports. One private
Zink device and its compiler cache are reused for the process; contexts without
CGL sharing still have separate GL objects. The renderer
currently caches its scratch image but creates copy/import objects per native
frame; reducing that overhead and pipelining presentation remain future work.
No controlled application FPS improvement is claimed.

## Building locally

### Collecting Sequoia CGL evidence

Give an **Intel Sequoia 15.5+** tester the standalone
[NVIDIA-Sequoia-CGL-Collect.command](../tools/NVIDIA-Sequoia-CGL-Collect.command).
They can run it as their logged-in desktop user, without `sudo`:

```sh
bash ~/Downloads/NVIDIA-Sequoia-CGL-Collect.command
```

It creates a `NVIDIA-CGL-*.tar.gz` archive on the Desktop to send back. Apple's
Command Line Tools are needed for the full collection. If they are missing,
the archive explains how to install them and rerun; the script does not install
or download anything itself. The collector records the actual OS/build, renderer
and loaded-module UUIDs, selected accelerator properties, GLD exports, static
CGL/GLEngine disassembly and public GL dispatch offsets. LLDB can resolve cached
modules even when their logical paths are absent from the filesystem; no Apple
binary or complete dyld cache is copied. It opens only its own disposable,
offscreen CGL contexts and never attaches to another process.

The supplied 24H32 collection supports the shared adapter described above.
Future evidence should still compare public dispatch and private context,
share-group and GLD layouts before activation on a new major OS. GPU-copy
presentation, context sharing, resize and teardown need testing on that OS.
Building directly on Sequoia is another option; a matching SDK alone does not
establish the private runtime ABI.

The temporary Sequoia test download and packaged launcher were retired after
the 25-case run succeeded. Developers can still use `tools/test_zink_runtime.py`
with a runtime containing its GLD bundle, as described below. After installing
the complete package and rebooting, F3 or the application startup log should
show `zink` and `NVK` without any Minecraft companion mod or JVM argument.

### Build commands

Use a separate, clean Mesa checkout at
[`17ca6174dcc6cb22059ac343bc29f8af7800f42e`](https://gitlab.freedesktop.org/mesa/mesa/-/commit/17ca6174dcc6cb22059ac343bc29f8af7800f42e).
Do not apply this overlay to the Mesa tree producing the installed NVMTL plugin.
The builder checks the source identity before applying its small Darwin/Zink
patch. Dispatch generation uses the local Apple symbols/slots and refuses
unrecognized conventions; it does not whitelist a particular OS build. It retains
descriptor sets for Darwin NVK, staging/GPU texture uploads
instead of host image copies, and bounded inline texture uploads on the render
worker. Other drivers retain their existing upload threshold.

Install Xcode command-line tools, Vulkan-Headers, Python, Meson, Ninja, Mako,
PyYAML, packaging and Bison 3.x. Bison 2.3 from macOS cannot parse this Mesa tree.
With this fork's driver already running and a matching verified NVK renderer:

```sh
python3 build/build_zink_vulkan.py /path/to/zink-out \
  --renderer /path/to/verified/pkgroot/Library/GPUBundles/nvmtl/libvulkan_nouveau.dylib
bash build/build_zink.sh /path/to/mesa /path/to/zink-build /path/to/zink-out
bash build/build_cgl_zink.sh /path/to/zink-out /path/to/cgl-build
bash build/build_gld_bridge.sh /path/to/zink-out /path/to/gld-build
python3 tools/test_zink_runtime.py --runtime /path/to/zink-out \
  --output /path/to/new/test-results
```

`VULKAN_HEADERS` can override the default `~/Vulkan-Headers/include`.
For a checkout extracted from the pinned mirror archive, set
`ZINK_MESA_ARCHIVE=/path/to/mesa-source.tar.gz`. The accepted archive SHA-256 is
`1328a4aaba2260c005ff46c88d572403f0b677cd87580050f4d1bfafe56388f0`;
its URL is `https://codeload.github.com/chaotic-cx/mesa-mirror/tar.gz/17ca6174dcc6cb22059ac343bc29f8af7800f42e`.
GitHub's regenerated archive with CSV CRLF checkout rules is also accepted:
`79c8a65a5a5cf6968368e0b7338726465fc21444cc5d5583716eaf685ce879cf`.
Its 12,909 file contents match the same pinned Git tree after normalizing those
33 CSV files' line endings. The builder records the actual archive hash and
checks extracted files against it. Source/build hashes,
Mesa identity and the private renderer pairing are recorded in `ZINK-BUILD.json`
and `NATIVE-BUILD.json`; packaging rejects stale or mismatched components.

Add `--front-buffer` to test composited single-buffer windows, render-flush
aliases, explicit front-buffer drawing, back-buffer isolation, non-current
CGL/Cocoa swaps and binding/state preservation in both native and IOSurface
presentation modes. This captures
only the probe's windows and requires screen-recording permission already
granted to the test process; the probe exits 77 if permission is unavailable.

The test script briefly opens diagnostic windows. It tests explicit CGL
attachment and the GLD lifecycle through a **process-local lookup shim**.
The shim substitutes the test bundle path/executable only in the probe process;
it does not install a driver or establish that macOS trusts a newly installed
bundle. Never use this shim for WindowServer or register it system-wide.
The native probe reads a separate diagnostic GPU buffer to verify pixels; that
readback is not part of gameplay presentation.

The native resize checks also cover odd/tiny windows, overlapping scissor clears,
transparent output and forced host-page fallback. AppKit may constrain a requested
large window to an odd height. Three earlier reported resize failures came from
an off-by-one expected scissor boundary in this probe; they pass after correcting
the expected image, with the installed driver libraries unchanged. Pixel checks
remain exact, and presentation dimensions must match the rendered frame.

## Packaging and installation

Start with a checksum-verified **complete fork package**, including Minecraft,
Wine, both accelerator variants and matching firmware. The normal
`build/stage_package.py` retains responsibility for assembling that base.
Arbitrary overlays of installed `/Library` contents are rejected.

```sh
python3 build/package_zink.py --base /path/to/verified/pkgroot \
  --zink /path/to/zink-out --stage /path/to/new/pkgroot \
  --webkit-fixup-build /path/to/verified/webkit-fixup-build \
  --archive /path/to/new/nvidia-macos-driver-2.6.6-nvk.tar.gz \
  --build-records /path/to/new/build-records --native-gld
```

The archive has one top-level `pkgroot/`. It preserves existing runtimes and
version metadata, adds Zink in its own subdirectory and selects `NVKZinkGLDriver`
for both Sequoia and Tahoe accelerators. `--native-gld` is the default. `--explicit-cgl-only` is a developer option
for attachment tests without system OpenGL selection.

The WebKit helper build must include `BUILD.json` from
`kexts/NVWebKitFixup/build.sh`; packaging verifies its source and bundle hashes.
It is bundled under `Optional/NVWebKitFixup/` with a separate installer choice.

The package has a short `READ-ME.md`, one current `PACKAGE-IDENTITY.json` and
setup guides in `Guides/`. Detailed build identities, validation reports and
review notes go to `--build-records`, outside the archive. If omitted, that
directory defaults to the stage path with `-build-records` appended. Runtime
manifests, licenses and signed resources retain their required locations.

Read the packaged [local installation guide](COMBINED-INSTALL.md) first. Run
`Verify.command` with the kernel driver already active, then install using
`Install.command` when ready for a boot test. The bundle uses the normal
`/Library/GPUBundles` search path; no System snapshot modification or
`authenticated-root` change is needed. The installer checks trusted bundle
lookup, preserves external kext sources such as macFUSE, and keeps rollback
and the existing boot fallback. A normal driver package can restore the Apple
OpenGL selection. Runtime GPU tests do not qualify registration or a reboot.

The current review and measured test coverage are in
[ZINK-PR2-REVIEW.md](ZINK-PR2-REVIEW.md). PR #2 was contributed by
[Dino-VN](https://github.com/Dino-VN); its original MIT attribution is retained.
Its RLCraft and upload-benchmark observations are author-reported and have not
been reproduced by this integration review.
