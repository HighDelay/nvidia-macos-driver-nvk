# Safari WebGPU argument-buffer texture repair

## Reproduction and cause

On RTX 2060 SUPER with 8 GiB BAR1, Tahoe 26.7.1 (25G241), and Safari 27,
a WebGPU game renders its menu over a black car/garage scene. The user
reports that Chromium browsers render the game correctly. Safari uses WebGPU
for this session and its GPU process loads NVMTL successfully. This failure
is separate from the previous WebKit plugin-loading repair.

The repaired translator now renders the car and garage correctly after a normal
Safari relaunch. The user confirmed the fix on this host on 2026-11-13.

The site reports failed fragment pipelines including `ps_5`, `ps_19`, `ps_28`,
`ps_26`, and `ps_265`. The GPU process reports `native emitter: unknown SSA
value` for their optimized texture operands. A capture from the restarted game
contains 89 WGSL modules and 70 render-pipeline descriptors.

Metal's optimizer copies one argument-buffer texture as an integer payload:

```llvm
%field = getelementptr inbounds %Group, ptr addrspace(2) %group, i64 0, i32 1
%bytes = bitcast ptr addrspace(2) %field to ptr addrspace(2)
%payload = load i64, ptr addrspace(2) %bytes, align 8
%texture = inttoptr i64 %payload to ptr addrspace(1)
; air.sample_texture_2d consumes %texture
```

The existing recognition of this idiom happened after raw-buffer emission and
depended on buffer-device-address mode. The primary construction lost the
texture's SSA value; the raw-first sampler retry could silently replace its
sample with zero. That retry also discarded the address model needed to read
uniform buffers nested in WebGPU argument groups. Compilation success alone
therefore did not demonstrate correct rendering.

After the first repair, Safari still rendered the scene too dark. The captured
`ms-resolve` shader averages four `textureLoad` calls from a multisampled
texture. Safari returned `(0.05, 0.10, 0.15, 0.25)` from a texture cleared to
`(0.2, 0.4, 0.6, 1)`; Edge returned the full clear color. Embedded texture
discovery discarded the multisampled flag, so translation emitted an ordinary
image and interpreted sample indices as mip levels. Only sample zero contributed
to the average. Query-only argument-buffer shaders also skipped embedded
texture discovery and returned zero dimensions and default mip/sample counts.

## Retained behavior

- Recognize the known texture's sole-use i64 payload independently of the
  address model. Keep additionally observed integers on the integer path.
- Emit that payload through the normal opaque resource-load path before raw
  buffer handling. Preserve each field's descriptor mapping and texture slot.
- Retain required device addressing in the raw and raw-with-workgroup retries
  when the module loads an indirect buffer pointer. Keep the existing direct
  word-buffer and atomic-cursor paths; applying BDA to every raw module changes
  their pointer/index semantics.
- Preserve sampler-heap retry selection, descriptor limits, ordinary integer
  loads, and all existing rendering and kernel repairs.
- Preserve the embedded texture's multisampled flag in image types, reflection,
  loaded values, descriptor arrays and bindless heap metadata. Multisample
  reads require `OpImageFetch` with `Sample`, rather than `Lod`.
- Discover embedded resources for query-only shaders using the shared AIR image
  intrinsic classifier. Size, mip-count and sample-count queries require the
  real texture descriptor even when the shader never samples or writes it.

Only the shader translator requires rebuilding. The bundle must be re-signed
after replacing its translator because its resource seal covers that dylib.
Keep the matching standalone copy in `nvmtl/` synchronized. Do not change
kexts, EFI, Safari sandbox policy, or shader-cache permissions for this repair.

## Validation, 2026-11-13

The installed 2.9 translator reproduces `unknown SSA value %3` on the reduced
Metal fixture. The private candidate passes 2,266 translator unit tests and
24 focused argument-buffer, reflection, gated-descriptor, and sampler tests.
The new raw-first retry fixture carries its sampler inside an argument buffer,
matching the real shader's ABI.

`tools/test_integer_texture_handle_gpu.m` privately loads the selected NVMTL
bundle and renders `tools/fixtures/integer_texture_handle.metal`. It checks all
pixels in four offscreen frames against CPU-computed references, swapping two
different textures and then enabling nonzero fog uniforms. The narrowed
candidate matches all four references:

| Texture order | Fog alpha | Expected RGBA |
| --- | --- | --- |
| Original | 0 | (0.350000, 0.400000, 0.700000, 1) |
| Swapped | 0 | (0.315625, 0.431250, 0.362500, 1) |
| Original | 0.5 | (0.256250, 0.325000, 0.550000, 1) |
| Swapped | 0.5 | (0.234766, 0.344531, 0.339063, 1) |

An earlier candidate passed shader compilation but failed these readbacks;
the texture-swap and uniform checks are required regression coverage.
The unrelated wrapped C-array integration expectation also fails with the
installed baseline, which emits a runtime bindless array rather than the
fixed array expected by that test. It is not counted as a passing check.

The clean candidate is built locally with Rust 1.99.0 for x86_64, a macOS 15.0
deployment target, remapped source paths, an `@rpath` install name, stripped
debug symbols, and ad-hoc signatures. Earlier 2.9 binaries used Rust 1.98.1;
record the actual compiler and source hashes when packaging this candidate.

These results establish the reduced shader repair on this GPU. Following the
user's approval, the signed candidate was installed in both translator paths,
with the complete original bundle/signatures and standalone library retained
in a root-owned system backup. The guarded updater
passed isolated installation, identical reapplication, complete/partial
rollback, forced mid-update recovery, and changed-input/symlink refusal checks.

A normal Safari relaunch loaded the candidate identified by SHA-256
`566c96ddd9e04e50b52521b77d4b0e1cc01d70eae55634bc24102a262e153abb`.
The original shader-translation errors are gone, but the car/garage remains
black or too dark; the user confirmed the remaining failure. An isolated Edge
session renders the complete car and garage clearly. Safari continues to
submit frames. The first candidate therefore establishes only the integer
texture-handle repair; the multisample repair below is also required.

The second candidate additionally passes 2,265 translator unit tests and 27
focused integration checks, including the nine new integer-resource and image
query/resolve regressions. Three checks in
`reflection_describes_the_image_it_binds` fail identically in an isolated copy
of unchanged HEAD; these are recorded as baseline failures, not passes. Earlier
debug-build attempts exhausted disk space and are not counted as test results.

`tools/test_webgpu_msaa_gpu.m` renders the multisample and query fixture through
both reflected and descriptor-built argument encoders. All six readbacks pass
with the second candidate; all six fail with the first candidate:

| Check | Expected RGBA |
| --- | --- |
| Four distinct samples, averaged | (0.3125, 0.4375, 0.5625, 1) |
| Multisample width, height and sample count | (4, 4, 4, 1) |
| Mip width, height and level count | (4, 2, 5, 1) |

The four texture-swap/fog readbacks also pass with this candidate. Its SHA-256
is `95d5e2cdeabf86a265949db897c9a05f2bf6c7e74f2e2235576983269f68b387`.
The second guarded updater passes isolated install/reapply, rollback, recovery
and refusal checks. It is installed with its immediately preceding candidate
retained in a separate root-owned system backup.
To return to the released translator, roll back the second trial before the
first trial.

After a normal Safari relaunch, its fresh GPU process loads the second
candidate. The original WebGPU resolve probe now returns
`(0.2, 0.4, 0.6, 1)` and the dimensions/sample query returns `(4, 4, 4, 1)`
for both BGRA8 and RGBA8 multisample inputs, with no compilation or validation
errors. A canvas capture shows the textured green car and lit garage, and the
user confirms that the issue is fixed. Other GPUs, macOS 15, and full gameplay
remain untested.

At the user's request, retain the tested signed translator and build provenance
for a later release. Hold the complete driver build and release preparation;
no version/tag, release archive or GitHub release is created for this local test.

Local capture, AIR, SPIR-V, readbacks, build logs, and source/hash records are
retained under `out/`. The retained build's rollback guide records the exact
system backup paths and guarded restoration commands.
