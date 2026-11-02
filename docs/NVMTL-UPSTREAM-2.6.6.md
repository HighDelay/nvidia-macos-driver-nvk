# PR #5 on upstream 2.6.6

On 2026-11-02, merge HighDelay `main` at `b240c97` into the optimization
branch based on `b88230b`. HighDelay already includes NullMoth `470e391`,
the latest upstream checked at integration time. PR #5 remains open for
review; this work does not merge it into main.

The combined plugin preserves the branch's descriptor, binding, metadata,
address-table, render-state and resource-lifetime optimizations. The two
textual conflicts retain the bulk 3D upload with upstream diagnostics and
adapt the descriptor-mask fixture to the common parser. New upstream copy
fixtures now include the volume implementations and stub transfer barriers.
The missing-image guard remains ahead of every buffer/image Vulkan copy.

The one deliberate exception is upstream's later-lap completion inference.
Sequence allocation precedes the submission lock and recursive pre-submit
work can also invert submission order. This branch continues to require an
exact witness before recycling a pool. The new production-code fixture
rejects one/two-lap reordered values; unmodified `b240c97` fails its first
assertion. Missing witnesses retain the existing quarantine and backend
fallback. Submission locking and witness-ring exhaustion are not changed.

## Validation

Test host: RTX 2060 / TU106-A, 6 GB VRAM, 256 MB BAR1, macOS 26.7.2
(25G332). The release plugin builds for x86_64, `-O2`, ARC, and macOS 15.0.

- All 39 host regression scripts pass, including sanitizer fixtures for
  descriptors, binding metadata, snapshots, completion ordering, compressed
  mips and missing images; installer, surface, codec and package checks pass.
  The reused translator's rate-map C ABI passes both directions, normal and
  specialized translation, capability clearing and SPIR-V validation.
- Twelve GPU probe invocations pass: framebuffer fetch on/off, compute and
  attachment/dummy fetch bindings, high storage slots, sampler transitions,
  descriptor recycling, render state, host-copy coherence, Core Image,
  Unknown-usage storage writes and chained volume blits.
- Core Image checks all pixels across 32 color/orientation frames with
  Unknown/ShaderRead descriptors, RGBA/BGRA and shared/private backing. The
  process-local lookup shim prevents loading the installed plugin, and the
  probe verifies exactly one plugin image. No camera is opened.
- Volume copies check eight recordings with padded rows/planes, mip 0/1,
  nonzero XYZ/buffer offsets and untouched padding after a fill/copy chain.
- Package checks verify all 42 userland Mach-O signatures, x86_64, minimum
  OS requirements, both accelerator variants, optional NMIntelFB, runtime
  manifests, matched Wine/DXVK contents and symlinks. The complete package
  still requires macOS 15.5+.

Only the plugin is replaced in the verified `v2.6.6-nvk` archive. Translator,
kext, installer, Zink, Minecraft, Wine and video source trees match `b240c97`;
all reused package files retain their original hashes and modes. Existing
kernel-link and translator-suite results belong to that base release; they
are not rerun or claimed as new results here.

[Machine-readable results](NVMTL-UPSTREAM-2.6.6-RESULTS.json) record the tested
plugin hash, individual checks and base identity. Complete local logs and
archive verification accompany `~/Downloads/nvmtl-upstream-test-20261102/`,
outside `pkgroot`. The package retains one current identity and setup guides
under `Guides/`.

Installation/reboot, live WindowServer performance, sleep/wake, extended
sessions, Sequoia execution and other GPUs remain untested. The short probe
timings establish no new CPU/GPU/FPS improvement. Source is prepared for
PR #5 and the binary remains a local test candidate; publication is held.
