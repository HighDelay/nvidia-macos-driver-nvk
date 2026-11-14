# Native NVK limited-BAR memory and presentation

Minecraft 26.3 launched with both native NVK and Zink/OpenGL using an 8 GiB BAR1.
On the same RTX 2060 SUPER / Tahoe 26.7.1 machine with a 256 MiB BAR1, native
Vulkan failed during texture uploads with `VK_ERROR_OUT_OF_DEVICE_MEMORY:
Failed to map buffer`; Zink/OpenGL continued working.

A separate LWJGL/VMA probe reproduces the failure using Minecraft's transient
buffer usage, `AUTO_PREFER_HOST`, coherent host-visible memory and sequential
host access. VMA selects the device-local, host-visible BAR memory type despite
the system-memory alternative. The third 16 MiB allocation fails to map with
NVIDIA `NV_ERR_NO_MEMORY` (`0x51`). BAR space is shared with the desktop; this
does not establish that the card's 8 GiB VRAM is exhausted.

## Policy

`nvk/nvk-small-bar-memory.patch` changes the Darwin memory-type initialization
only when BAR1 is smaller than VRAM. These devices expose device-only VRAM and
coherent, cached system memory. They no longer advertise a separate host-visible
VRAM type backed by the limited aperture. Upload allocations can then use system
RAM without requiring an application-specific allocator workaround.

When BAR1 covers VRAM, the existing memory types, heaps, allocation flags and
VRAM mapping policy are preserved. The renderer patch requires an application
restart; it does not require a kernel or EFI change.

## Drawable imports during resize

After correcting upload placement, native Minecraft could launch and render a
world with the limited aperture, but resizing failed with
`VK_ERROR_INVALID_EXTERNAL_HANDLE: Failed to present image`. Old and new drawable
surfaces can coexist during resize, increasing pressure on the shared aperture.

The native bridge now uses the same recovery as Zink: if a family-VRAM drawable
import returns that exact error on a limited-BAR device, import the IOSurface's
system-memory pages and let the GPU copy directly into them. There is no CPU
game-pixel copy. Require the host-memory extension, valid pointer alignment,
compatible coherent memory and sufficient backing size. Other import errors
retain their existing behavior.

Hold the surface lock until the GPU completion fence succeeds, issue the
transfer-to-host visibility barrier, then publish with IOSurface unlock instead
of the family-VRAM dirty mark/tag. Failed preparations release their locks and
resources; uncertain GPU work retains both. Wine's bounded resource cache
re-locks the same retained surface and verifies its backing pointer on reuse.
Native presentation remains synchronous; Wine retains async and reuse defaults
and their existing opt-outs.

Full-BAR devices skip the new capability query and internal extension addition,
and retain the original drawable import/publication path.

## Checks

The topology test compiles the actual Mesa initialization block before and after
the patch. Its 74 layouts cover limited, full, oversized and missing BARs, older
GPU classes, budget callbacks, non-Darwin and devices without VRAM. All 12
full-BAR layouts retain the original memory types and heaps exactly.

```sh
python3 tools/test_nvk_small_bar_memory.py --mesa "$PATCHED_MESA_DIR" \
  --output out/tests/native-memory-types
```

The GPU regression holds 384 MiB of staging buffers mapped simultaneously, copies
the final buffer through device-only VRAM and verifies the complete 16 MiB
readback. It uses the real runtime and LWJGL/VMA in a private Java process; it
does not change the driver or BAR configuration. An optional baseline run must
reproduce the specific failed mapping rather than an unrelated process failure.

```sh
python3 tools/test_native_mapped_memory.py --runtime "$CANDIDATE_RUNTIME" \
  --baseline-runtime "$PREVIOUS_RUNTIME" --lwjgl "$LWJGL_MAVEN_DIR" \
  --java "$JAVA_BIN" --javac "$JAVAC_BIN" --expect-system \
  --output out/tests/native-mapped-memory
```

GPU and Minecraft application qualification are recorded separately from the
topology test. A source-level full-BAR equivalence check does not substitute for
a new physical full-BAR gameplay test. Raw local logs stay with the build output,
outside the installable package and public documentation.

On the RTX 2060 SUPER / Tahoe 26.7.1 with a physical 256 MiB BAR1, the mapped
staging/readback regression passes. A test-only frontend forces drawable fd
imports to return the resize error while keeping real host imports, GPU commands,
fences and SDL surfaces. Four 90-frame cases pass complete pixel verification,
resize and minimize/restore: native, Wine async, Wine synchronous and Wine with
resource reuse disabled. Cached Wine cases reuse their recovered host imports.

```sh
python3 tools/test_native_host_present.py --runtime "$CANDIDATE_RUNTIME" \
  --sdl "$SDL3_LIBRARY" --output out/tests/native-host-present
```

ASan/UBSan host fixtures also cover capability rejection, unchanged full-BAR
extension selection, allocation/bind cleanup, visibility/publication, cached
surface locking and uncertain-completion retention. The forced-import test
does not simulate physical full BAR or qualify a complete driver boot.
The user confirmed Minecraft world rendering and repeated resize with the private
candidate after restarting Prism Launcher; its cached instance settings had
initially selected the earlier startup-only runtime. The complete staged package
still needs installation/application testing, and a new physical full-BAR run
remains pending.
