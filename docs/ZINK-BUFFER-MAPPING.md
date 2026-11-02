# Zink upload-buffer mapping repair

Minecraft 26.3 on an RTX 2070 SUPER with 8 GiB VRAM and a 256 MiB BAR1
crashed during startup on Sequoia 15.8.1 / 24H32. Three JVM reports show the
same native fault: `tc_buffer_map+0x383`, writing to address `0x40` through a
null transfer. The Java stack is uploading a startup cubemap through
`glMapBufferRange`; removing the companion Vulkan mod did not change it.

The accompanying console reports `nvRes: 0x51` followed by
`ZINK: vkMapMemory failed (VK_ERROR_OUT_OF_DEVICE_MEMORY)`. NVIDIA identifies
[`0x51` as `NV_ERR_NO_MEMORY`](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/main/src/common/sdk/nvidia/inc/nvstatuscodes.h).
This records a failed mapping, not a measurement that all 8 GiB VRAM is used.
Vulkan [allows map operations to fail](https://docs.vulkan.org/refpages/latest/refpages/source/vkMapMemory.html).

Two repairs are included in the pinned Mesa overlay:

- On Darwin NVK with a small BAR, prefer ordinary system-memory types for the
  host-visible coherent upload heaps. Previously these heaps also matched
  device-local BAR memory, which NVK lists first. VRAM allocation can succeed
  even when a later CPU mapping cannot fit the aperture. Keep device-local
  rendering heaps, full-BAR preferences and GPU-copy presentation unchanged.
- When the Gallium driver returns a failed buffer map, leave the transfer
  null and restore the driver-thread state. The caller can report
  `GL_OUT_OF_MEMORY`; it must not dereference a nonexistent transfer.

The allocation policy and unchecked transfer are shared by Tahoe and Sequoia.
The reported small BAR is a plausible trigger; the supplied logs do not measure
live aperture use or prove that it was the only mapping constraint.

## Regression test

With a verified runtime built from the current source:

```sh
python3 tools/test_zink_buffer_uploads.py --runtime /path/to/zink-out \
  --output /path/to/new/mapping-results
```

The test runs a private Vulkan proxy in isolated child processes. It changes
Zink's reported BAR size and rejects selected map calls; it neither installs a
driver nor fills real VRAM/BAR1 to cause an error. It checks a 16 MiB transient
PBO upload, all pixels of six cubemap faces, failed read/write map handling,
successful uploads after a failure, and the full-BAR allocation preference.
The proxy is a developer test tool and is never included in the driver runtime.

On the preceding published runtime, the small-BAR upload case and both injected
map-failure cases reproduce a segmentation fault on Tahoe 26.7.1 with an RTX
2060 SUPER. With the repair, all four cases pass: three system-memory maps in
the constrained-BAR upload, one controlled failure followed by recovery for
each read/write test, and a retained BAR map in the full-BAR test. The
[recorded results and log hashes](ZINK-BUFFER-MAPPING-TESTS.json) identify the
tested library. Passing these controlled cases does not establish that the original
Sequoia Minecraft instance now reaches gameplay; that needs a user retest.

## Follow-up: persistent buffers and black windows

The maintainer confirmed Minecraft working with a real 256 MB BAR1 device and
approved the `v2.6.4_fix` release. A later [Sequoia log](https://mclo.gs/ZUBuDRI)
from the RTX 2070 SUPER / 15.8.1 machine shows a black window with three more
`vkMapMemory` failures. It reaches texture-atlas creation; shaders are disabled.
This instance has 146 mods. Its original log does not identify the failed
allocations' size or memory type.

An expanded regression identifies a remaining allocation hole in the published
runtime: non-coherent persistent `glBufferStorage` buffers still select the
device-local/host-visible heap. They cannot use a temporary staging fallback
because their CPU pointer must remain live. Under a simulated unavailable BAR
map, this case returns an allocation error with the published library; the
coherent persistent and transient cases pass.

The first candidate overlay places persistent buffer backing in coherent system RAM
on small-BAR Darwin NVK devices, including non-coherent OpenGL mappings.
Full-BAR preferences and GPU-only rendering resources retain their allocation
policy. It also adds allocation size, memory-type flags, heap index and heap
size to a failed-map diagnostic. These details distinguish remaining BAR maps
from genuine system-memory mapping failures. The local baseline and candidate
results are recorded in [the persistent-mapping tests](ZINK-PERSISTENT-MAPPING-TESTS.json).

The expanded test also keeps persistent buffers mapped while the GPU consumes
their data, checks coherent/non-coherent storage, preserves full-BAR placement,
and runs transient/persistent cases with the real hardware memory properties.

`tools/zink_mapping_trial.py` supports a compact experimental bundle. Its
`Test-Mapping.command` compares the installed and candidate libraries without
installation and produces a Desktop log archive. `Apply-Zink-Trial.command`
requires Minecraft to be closed, verifies the installed private renderer,
backs up the original Zink library and atomically replaces only that dylib.
`Restore-Zink.command` restores the verified original and refuses to overwrite
a newer driver. Applying or undoing this userland trial needs a Minecraft
relaunch, without a reboot.

## Follow-up: ordinary mutable buffers

The [fresh Fabric/API-only retest](https://mclo.gs/AOcQq7s) on Sequoia still
shows a completely black window. Its installed-library hash matches the first
trial, and all 18 returned probe runs pass. However, the game reports three
failed **2 MiB** mappings of memory type 1 (`DEVICE_LOCAL | HOST_VISIBLE |
HOST_COHERENT`) in the **256 MiB BAR heap**. The modpack is not required to
reproduce the problem; passing the earlier probes did not cover this path.

Two new cases reproduce the same allocation size/type failure locally:
8 KiB `GL_STATIC_DRAW` and `GL_DYNAMIC_DRAW` array buffers. They bypass both
the less-than-4-KiB CPU shadow and the >=8196-byte staging hint; their direct
mapping pins an entire 2 MiB slab. Creating these as PBOs would miss the bug
because Mesa always assigns PBOs to the staging usage class.

The second trial extends the small-BAR policy to all buffer allocations that
request CPU visibility, as well as persistent buffers. Their backing prefers
coherent system RAM; device-only allocations, textures, full-BAR placement and
GPU-copy presentation keep their existing paths. This can increase PCIe traffic
for mutable vertex/index buffers on small-BAR devices; performance has not been
benchmarked. No frame readback or CPU presentation fallback is introduced.

The expanded probe maps array-buffer storage, then uses it for six GPU cubemap
uploads and verifies every pixel. The first trial fails both new constrained-BAR
cases; the second passes all 13 cases. See [mutable-buffer test evidence](ZINK-MUTABLE-MAPPING-TESTS.json).
The new installer accepts the exact first-trial hash and preserves its original
published-library backup.

On 2026-10-31, the affected Sequoia 15.8.1 / RTX 2070 SUPER user confirmed that
trial v2 renders both menus and a single-player world. The [successful game
log](https://mclo.gs/CbC8KZ0) confirms native Zink/NVK and a 256 MiB BAR1, with
no `vkMapMemory` failures. This qualifies the reported black-window repair in
Minecraft 26.3 with Fabric/API. The remaining copy-performance warning is not
an allocation failure, and this test does not establish a performance gain.
