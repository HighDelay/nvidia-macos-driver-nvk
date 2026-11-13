# Mesa/NVK 26.3.0 compatibility evaluation

The candidate uses official Mesa `mesa-26.3.0`, commit
`0a6fa60abb737d26ac8a316a4aad7c9ddf64f9e4`, with this fork's existing
620.70.06-compatible macOS backend. Updating Mesa does not require adopting
upstream's deferred 2.9 mapping-token, hub or power interfaces.

The previous NVK base was `17ca6174dcc6cb22059ac343bc29f8af7800f42e`
(26.3.0-devel). Driver upstream currently pins 26.3.0-rc3 at
`90a959ebba9dbb36f2ea5176091c15d6ebdd0af0`; its `v3.0.0-beta.1` tag
does not introduce a later Mesa revision. Between rc3 and stable, the only
Nouveau change is a Blackwell instruction-latency adjustment. This update is
for maintenance and correctness, with no promised frame-rate improvement.

The stable base changes NVK queue heap/SLM state management, several image
formats, NIR/SPIR-V handling and NAK fragment-interlock metadata. A clean patch
application alone cannot qualify those changes.

## Preserved contracts

- Retain the existing macOS, 620-header, command recycler, slab allocation and
  default slab patches, plus the DLSS cache lifetime repair.
- Carry the compatible upstream warning and bounded GPU-name fixes. Use upstream's equivalent CoreGraphics dependency fix in `src/vulkan/meson.build`
  for the colour-space APIs used by Mesa's Metal WSI; keep
  the unresolved-strong-symbol check enabled.
- Apply patches without fuzz. Build both `mesa_clc` and `vtn_bindgen2` from the
  same patched Mesa source, and reject existing tools with a different or
  missing source identity. Serialized NIR is not compatible across revisions.
- Keep the optional newer NAK patch disabled. One SM120 latency hunk overlaps
  the stable source; do not apply the whole optional patch without review.
- Keep the driver-owned Wine provider, matched DXVK pair, MSync and the
  descriptor-buffer workaround. Keep presentation resource reuse and its opt-out.
- Keep native Minecraft and Zink as separate runtimes. Zink's Gallium/OpenGL
  frontend retains its own validated Mesa pin and overlay: its internal resource
  ABI is independent of the Vulkan interface to the updated NVK renderer.
- Preserve the native Zink shared-context, HDR, small-BAR and presentation
  repairs. Check those paths against the new renderer in an isolated runtime.

## Build and validation

Evaluation uses signed MacPorts LLVM/Clang 22.1.8, the matching
SPIRV-LLVM-Translator 22.1.3 and SPIRV-Tools 1.4.363.0. Compiler dependencies
remain build tools; the NVK renderer must link only system libraries/frameworks.
On this host, target dependency discovery selects the SDK's zlib 1.2.12,
not the installed MacPorts zlib. Input hashes and complete commands are retained
beside the external build output.

The compatible chain is selected by default in `build/build_nvk.sh`, which now
honours `nvk/MESA_BASE` rather than overriding it with the old revision:

```sh
bash build/build_nvk.sh /path/to/mesa-git /path/to/ogkm-610.57.04 /new/output
```

Tested on **RTX 2060 SUPER / 8 GiB BAR1 / i5-11400F / Tahoe 26.7.1
(25G241)** with the installed 620.70.06 driver. The final raw signed renderer
SHA-256 is `b666af6ae86bba19fcdf6bdbb21163d50bd8e220ecf09cbe5a478076e3af0900`;
its private-runtime relocation is
`c85b5996d0301d31bde5c65f8f8446de7d234f673498ffc03fa59fb57cda91cf`.

| Check against the final renderer | Result |
| --- | --- |
| Build, license and ABI checks | Zero compiler warnings; six-crate license record matched; no unresolved strong symbols or non-system dependencies; x86_64 ad hoc signatures valid; 2,335 exports with retained native bridge/ICD exports. |
| Native frontend/SDL | 120 frames with exact diagnostic pixel checks, resize and minimize/restore. |
| Standard Vulkan loader → GPU-copy frontend | 90 pixel-checked frames, matching SDL provider, resize/restore; no normal-path CPU pixel transfers. |
| Zink/OpenGL runtime | 64/64 cases, including core/legacy, native presentation, front-buffer capture, interop, odd sizes and OBS policy. |
| Buffer mapping/uploads/small-BAR policy | 13/13 cases. This does not replace a physical small-BAR machine test. |
| HDR/SDR IOSurface transfers | 8/8 cases at 60 frames each, with padding and resize. |
| Shared-context concurrency | 18/18 delay, alias, lifetime, teardown, stress and timeout cases. |
| Wine native ABI and frontend | 1,024-word fill/readback; 120 default, 90 reuse-optout and 60 HUD frames with exact pixels. |
| Windows Vulkan | Fill/readback and 240 presents over two surfaces. |
| DXVK D3D11 | 300 default, 120 indirect and 120 indexed-indirect frames; pixels, depth, blend, resize and shared-resource checks passed. |
| vkd3d-proton D3D12 | DXIL compute/root UAV/fence with 1,024-word readback; 120 windowed frames, five render readbacks and resize passed. |
| Provider setup/rollback | 19 checks passed with final provider `nvk-wine-provider-31139083f449`. |
| Stale native-tool identity | Negative control refused mismatched tools before source export. |

Loaded-library paths are recorded in the GPU runs. Zink's unchanged frontend
and probe inputs were reused only after checking their installed source records
and hashes. DXVK, vkd3d, NVAPI and NGX binaries remain unchanged and verified.

## Minecraft without the selector mod

The [setup guide](../nvk/native-macos/INSTALL.md) now documents the standard
`-Dorg.lwjgl.vulkan.libname=…` argument, launcher steps and rollback. A Java 25 /
LWJGL 3.4.3 probe invokes the unmodified Minecraft 26.3 loader method without the
companion jar, checks the chosen frontend, verifies SDL's provider pointer and
enumerates native NVK. It passes with the argument against both the private
candidate and the installed runtime; removing it selects bundled
MoltenVK in the negative control. This establishes provider selection, rather
than a full gameplay result. The existing companion remains optional.

Upstream already has native NVK and a Metal-surface presenter. This fork retains
its existing GPU-copy frontend instead of enabling upstream's software-present
policy. MoltenVK does not translate native-path rendering; Metal still supplies
the macOS drawable. No global loader or Minecraft native-library replacement is
needed for this setup.

## Handoff and limits

Raw commands, signed MacPorts tool provenance, source/tool identities, GPU logs
and exact private binaries are retained together on the external build volume.
The user will perform actual application tests before promotion.
The installed driver and runtimes are not replaced by this evaluation. A full
driver build/release remains held; local GPU probes do not qualify a new boot,
WindowServer, other GPUs, Sequoia, or every application.
