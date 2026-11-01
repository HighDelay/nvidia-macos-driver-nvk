# Translator regression repair after 2.6.3

The 17 failures reproduced on both this fork and untouched upstream 2.6.3 were
investigated with the same SPIRV-Tools 2025.3 validator. Passing validation alone
does not establish correct shader output: the repair also checks GPU readbacks.

## Findings and changes

| Original failures | Finding and repair |
| --- | --- |
| 4 owned-module checks | Accept the current detailed diagnostics. Keep the stricter owned logical-pointer policy independent of the external validator, and check that derivatives without their required execution mode are rejected. |
| 1 typed-pointer parser check | Reject malformed or overflowing address-space values instead of silently treating them as address space zero. Preserve qualifiers inside function-pointer parameters. |
| 5 buffer/pointer checks | Recognize guarded loads shared between call sites and integer device-address phis. Fix direct `array_ref<void>` accesses to use the driver's address table, consistently with helpers, instead of reading buffer contents as addresses. |
| 2 packed-color checks | Check the existing NaN-to-zero selection. GPU readbacks additionally exposed incorrect RGB10A2 half-way rounding; preserve the fused residual operation used to recover product rounding error. |
| 1 fast-trigonometry check | Test the default and opt-in flush policies in separate child processes, without changing the environment of concurrent tests. |
| 4 texture-binding checks | Follow the address-table slot into the sampled-texture heap instead of requiring the older fixed-binding representation. Check actual pixels as well as descriptor declarations. |

Additional coverage found that device pointers stored in local variables could
be written as zero. Preserve their 64-bit address, including a derived pointer's
offset, and recognize generic pointer-table loads. The former callback-rejection
test and its ignored positive duplicate become one checked, positive regression.

The test updates check returned data, stores, null guards and descriptor data
flow. They do not disable validation, replace missing loads with zero, or accept
silently discarded writes.

## Reproducing the checks

With `spirv-val` and `spirv-dis` on `PATH`:

```sh
cargo test --manifest-path translator/translator/Cargo.toml --lib
cargo test --manifest-path translator/translator/Cargo.toml \
  --test gl_uniform_vector_loads --test immutable_local_pointer_fields \
  --test instruction_metadata
cargo test --manifest-path translator/wrapper/Cargo.toml --lib -- \
  --skip compositor_pixel_sampler_spirv_validates --skip ca_named_modules_validate
```

Those two explicitly skipped wrapper tests require unpublished local compositor
fixtures. Two other wrapper corpus tests remain ignored by upstream; they depend
on the machine's shader cache. No passing result is claimed for these four tests.

To check a built translator through its C ABI and a matching built plugin:

```sh
python3 tools/test_gl_translation.py /path/to/libnvmtl_translate.dylib \
  --spirv-val /path/to/spirv-val --spirv-as /path/to/spirv-as
python3 tools/test_translator_readback.py \
  --plugin /path/to/NVMTLDriver --translator /path/to/libnvmtl_translate.dylib \
  --tools /path/to/spirv-tools/bin --out /path/to/readback-results
```

The readback tool installs nothing and does not restart applications. It uses
bounded offscreen compute kernels, finite inputs, mapped outputs and neighboring
canaries. Coverage includes direct and helper address-table accesses, local
pointer stores, both address-phi branches, 64-bit loads, RGB565/RGB10A2 packing,
NaN/clamp/rounding controls, 1D textures, texture/sampler slots through 127/15,
and distinct sampled/storage views. The 2.6.4 readbacks additionally distinguish
two texture-array elements (constant and runtime indexing) and nearest/linear
sampler-array elements at bindings 14/15. It records SPIR-V, disassembly and results.

After merging upstream 2.6.4, the complete library run passes **2,217 tests**,
with no failures or ignored tests. The wrapper has 43 passes, two ignored tests
and the two private-fixture exclusions above. Fifty focused integration tests
and 21 GPU readback cases (28 dispatches) pass. Offscreen readbacks are checked
on RTX 2060 SUPER/Tahoe 26.7.1. These are
correctness checks, not an FPS measurement or full application qualification.
Framebuffer, cursor, parked-VRAM and DXVK implementations remain unchanged.
The 2.6.4 release separately rebuilds the native frontends and adds the optional
Wine D3D12/DLSS components. See [merge validation](FORK-MERGE-NOTES.md#upstream-264-integration)
for package, kernel-link and hardware-test limits.

The 2.6.6 merge passes **2,227 library tests**, the same **43 wrapper tests**
(with the existing two ignores and two private-fixture exclusions), 18 focused
integration tests and all 21 GPU readback cases. The added C-ABI check requires
explicit capability bit 2 for both rate-map coordinate directions, including
sampler specialization and refusal after clearing that bit. Run it with
`python3 tools/test_raster_rate_caps.py /path/to/libnvmtl_translate.dylib`.
Tests used a separate copy whose library lock prunes obsolete workspace-only
entries; every remaining dependency version/checksum matches the tracked lock.
The production wrapper still builds with its tracked lock and `--locked`.
See [2.6.6 merge validation](FORK-MERGE-NOTES.md#upstream-266-integration).
