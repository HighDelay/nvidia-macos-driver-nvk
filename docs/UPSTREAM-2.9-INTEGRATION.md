# Fork 2.9 integration candidate

Integrate upstream `7412cdde81447f78da0ebb64216dc258d8beaedb` onto fork
`a66b0f344b4598b2a4982f8dd56e6801c0866af1` on a separate integration branch.
This follows the initial [audit](UPSTREAM-2.9-REVIEW.md) and selective backports.
The user subsequently approved a repaired integration and local test packages;
publication waits for their testing and approval. Preserve the validated archives
for the later release.

## Incorporated and repaired

- AIR 2.9 atomics, sampler tables/selection, stage-in layout, pull interpolation
  and the associated translator changes and fixtures. Keep the fork's active
  callback-store repair, guarded loads, NaN checks and isolated environment tests.
- Framed shader-cache integrity, writable-directory probing and the open-source
  `nvmtl-air-dis` fallback using the bundled LLVM runtime. Remove the old `air-opt`
  executable from the installable package. Keep existing cache byte limits and
  the fork's collision-safe lowering cache.
- Background translation with checked allocation/key ownership, a 256-entry
  single-flight limit, a 64-job queue limit, and rejection when no worker can
  service a submission. A refused acquisition compiles synchronously without
  publishing or retiring another caller's entry. Bound failure memoization too.
- Function-constant warm-up admits at most eight queued jobs and two workers.
  Foreground requests atomically withdraw queued variants and compile immediately;
  a running variant gets at most two milliseconds before foreground compilation
  proceeds independently. Keep atomic cache publication. Cache hits do not start
  speculative work. No foreground request waits for the speculative queue to drain.
- Additive Metal/Vulkan functionality retains the existing binding and resource
  caches. Route new ICB and packed-422 internal compute dispatches through the
  fork's tracked pipeline bind helper so the next application dispatch rebinds
  correctly. Preserve the A8 attachment mask when adding multiview fields.
- Port H.264/HEVC/AV1 parser checks into the existing pinned codec overlay.
  Refuse oversized Exp-Golomb values, check cumulative and predicted HEVC POC
  arithmetic, and reject unrepresentable decoder reference positions. Accept a
  valid minimum transform size equal to the minimum coding-block size. Keep the
  scoped registrar, generation decisions, Turing encoders and VP8/VP9 repairs.

## Retained hardware and desktop paths

Use the matched NVIDIA **620.70.06** core, the five existing NVIDIA patches and
the previously verified Mesa/NVK renderer identified by the component build
records. The experimental 2.9 Mesa rebase and new mapping-token/power/hybrid
interfaces are not substituted into this candidate. Additional upstream NVK
patches and experimental headers in the source tree are not a statement that
they were applied to the delivered renderer. Component provenance records the
actual source identities and reused hashes.

Keep the fork framebuffer, both accelerators, optional Intel-panel implementation,
shutdown/lifetime behavior and PCI sizing policy. Do not install the new shutdown
BAR restoration worker or automatic eDP AUX takeover: retaining the existing
paths excludes the demonstrated timeout/partial-write hazards. Those replacements
need a separately qualified implementation before adoption. New boost, keyboard,
SMU and hybrid installation helpers are not added to this package's installer.

Keep the complete surface publication/import protocol, protected asynchronous
handoff, cursor/per-head concurrency, PR #5 optimizations, byte-accounted caches,
half-float mip ownership, A8/Unknown-usage and Managed IOSurface repairs. Keep
Photo Booth, Safari video/filtered icons, NVWebKitFixup and the all-app ANGLE
default. The weaker process-name scheduling rule and alternative import cache
do not replace these paths. Keep Zink/Minecraft resize/BAR behavior, Stremio HDR
DMA transfer, Wine/NVK/DXVK/vkd3d and optional DLSS contracts.

## Qualification

Keep test commands, source hashes, binary hashes, component provenance, package
checks and hardware results beside the archives under `out/`, outside `pkgroot/`.
Tests requiring unavailable private shader fixtures must be identified explicitly;
never convert them or a missing validator into a pass. Existing host checks and
isolated GPU probes do not qualify a cold boot, login, Sequoia execution or other
GPUs. Local installation and boot/desktop testing remain the user's next step.
