# Address-table snapshot optimization

This follows the [framebuffer-fetch descriptor-mask repair](NVMTL-FRAMEBUFFER-FETCH-MASKS.md)
on PR #5. It reduces repeated CPU work while constructing immutable GPU address
tables, without changing bindings, barriers, descriptor layouts or completion.

Each snapshot previously queried the same dummy buffer's address for every
unbound buffer slot, normally 31 calls on a one-buffer draw. It now queries once.
It also rebuilt 128 sampled-texture indices even when only uniform-buffer
offsets changed. A per-recording, per-stage cache now holds that texture portion
and copies it into each new snapshot. Buffer addresses and self-pointers are
still rebuilt for the current page and offset. Sampled view, texel view, null
binding and resource-kind transitions invalidate the affected stage's cache.

The cache adds two 128-entry uint64 arrays and two validity bytes to the existing
bind-table allocation (about 2 KiB per recording). It adds no separate heap
allocation, GPU allocation, lock or shared global cache. Command retirement frees
it with the bind table; in-flight snapshots never point into the mutable cache.

## Evidence and validation

A four-second sample of the isolated fetch-mask build found address-table
construction on the recording thread, including repeated
`vk_common_GetBufferDeviceAddress` calls. This profile is from the benchmark,
not live WindowServer. Raw sample: `/Volumes/App/nvmtl-followup-20261031/validation/fetch-profile.txt`.

RTX 2060 / TU106-A, macOS 26.7.2 (25G332), x86_64, five alternating processes per
build, 15 measured frames after two warmups, 1,200 draws per frame:

| Workload | Fetch-mask build CPU ms | Address-cache build CPU ms | Change |
| --- | ---: | ---: | ---: |
| Stable fetch bindings | 1.226161 | 1.229859 | +0.30% |
| Uniform bytes change each draw | 2.103388 | 1.892394 | -10.03% |
| Texture and uniform change each draw | 4.346120 | 4.222442 | -2.85% |

These are means of process medians for calling-thread recording CPU. Every frame
passed all RGBA pixel checks. The small stable/texture differences are treated as
noise, not claimed improvements. This comparison isolates the additional address
cache from the preceding descriptor-mask optimization; neither benchmark measures
live WindowServer CPU or application frame rate. Source, hashes, raw results and
test names are in `NVMTL-ADDRESS-SNAPSHOT-RESULTS.json`.

`tools/test_address_snapshots.py` compiles the production bind table, binding
functions and snapshot builder under ASan/UBSan. It checks the complete table
layout, 64-bit addresses and offsets, independent stages, texture/texel/null
transitions, unchanged earlier snapshots, page rollover and reuse, and allocation
failure. It verifies that an empty snapshot performs one dummy-address query.
The existing OpenGL descriptor tests cover mask fallback, dynamic offsets and
resource changes. Surrounding compute, graphics, resource-lifetime, sampler,
surface-publication and witness-policy regressions are retained.

Boundary testing also reproduced a pre-existing dirty-bitmap overrun: the 321
binding slots were tracked in four 64-bit words, so storage bindings 256–320
wrote outside the bitmap. The adjacent texture generation field changed
incorrectly, and UBSan reported the out-of-bounds index. Dirty and copy masks now
cover every binding. High storage changes cannot take the buffer-offset-only
reuse path, and copying initialized high storage descriptors stays in bounds.
The four-word shader masks remain sufficient for the separate base-binding
range; storage descriptors continue to use their explicit high-water mark.

Host tests cover every binding from 255 through 320 in both stages, selective
reuse and all 128 storage-copy bits. `tools/bench_storage_binding_bounds.m` also
passed GPU readbacks at storage slots 62, 63 and 127 (bindings 255, 256 and 320),
alternating between two targets within each command recording. This is a
correctness repair, not a performance claim.

The plugin targets macOS 15.0; the bundled runtime requires 15.5 or newer. The
local package retains both accelerator variants, the separate Minecraft runtime
and driver-owned Wine/NVK provider with the matched DXVK pair. No kernel,
translator, Zink or frontend binaries are changed by these optimizations.

Live WindowServer, installation/reboot, extended capture sessions and other GPUs
still need testing. The separate translator audit cannot run in this checkout
without its Cargo dependencies; no full translator-suite pass is claimed.

## Other paths inspected

- Sparse descriptor iteration could avoid testing unused bits, but it was a much
  smaller cost in this profile and has not been changed.
- Surface wrapping already has weak-wrapper and bounded released-import caches.
  Broader reuse would need to preserve surface identity, mutable metadata and GPU
  ownership; this pass does not relax those constraints.
- The upstream completion-witness later-lap change remains deferred for the
  submission-order review described in the fetch-mask document.
- The upstream separate presentation queue and trace-ID lock changes address
  specific HUD deadlocks. They are not established causes of the supplied
  WindowServer CPU results and are outside this performance candidate.
