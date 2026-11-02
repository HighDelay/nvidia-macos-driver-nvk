# Empty shader binding metadata

This follows the address-snapshot changes at `c28bf91` on PR #5. Shaders without
constexpr samplers or embedded argument fields still queried two Objective-C
associated-object caches on every draw. A four-second sample of the isolated
uniform-change benchmark confirmed repeated metadata lookup and ARC work.

Each function now remembers that its immutable binding metadata is empty. After
the first lookup, render and compute binding skip those queries. The flag is a
monotonic atomic byte; it adds no allocation, lock, resource retention or GPU
command. Nonempty metadata continues through the existing binding path.
Argument-buffer contents are not cached by this new flag.

Both the first lookup and the fast path still call parked-sampler restoration.
Switching from a shader with its own sampler back to a shader using an app sampler
must restore the app's sampler even when the returning shader's metadata is cached.

The new GPU transition probe exposed a separate device-selection bug: constexpr
sampler creation used `MTLCreateSystemDefaultDevice()`. That can select another
GPU, and in the isolated probe it loaded the installed NVMTL plugin alongside the
candidate, producing duplicate Objective-C classes. Samplers now use `fn.device`,
consistent with the function's existing device contract. The probe checks that
exactly one NVMTL plugin remains loaded. This does not redesign the driver's
existing global device identity.

## Measurements

RTX 2060 / NVK TU106-A, macOS 26.7.2 (25G332), x86_64. Five alternating processes
per build and fetch mode, 21 measured frames after two warmups, 1,200 draws/frame.
Values are means of process medians for calling-thread recording CPU:

| Fetch | Workload | c28bf91 (ms) | Metadata cache (ms) | Change |
| --- | --- | ---: | ---: | ---: |
| Off | Stable bindings | 1.144265 | 0.529414 | -53.73% |
| Off | Uniform changes | 1.845665 | 1.329763 | -27.95% |
| Off | Texture + uniform changes | 4.880501 | 4.195903 | -14.03% |
| On | Stable bindings | 1.606515 | 0.825545 | -48.61% |
| On | Uniform changes | 2.594512 | 1.755713 | -32.33% |
| On | Texture + uniform changes | 5.535192 | 4.601683 | -16.86% |

Every frame passed all RGBA pixel checks. Background desktop load was not
controlled; texture-change runs varied substantially and their percentages are
less certain. These are isolated shaders without embedded metadata, not live
WindowServer CPU, GPU utilization, power or application FPS. WindowServer's
current default disables framebuffer fetch, so both paths are measured explicitly.
The off mode expects the dummy input attachment's zero value instead of accumulated
color. The timing comparison isolates the metadata optimization; the final build
also contains the sampler-device repair, checked separately with GPU readbacks.

Raw process medians, source and binary hashes, profile path and host results are
in [NVMTL-BINDING-METADATA-RESULTS.json](NVMTL-BINDING-METADATA-RESULTS.json).

## Validation and remaining work

- `test_binding_metadata.py` exercises the production metadata prelude and real
  parked-sampler helpers under ASan/UBSan: cold/warm lookups, shader and app sampler
  transitions, independent stages/functions, compute dictionaries, nonempty plans
  and concurrent readers. Argument-field decoding is unchanged and stubbed here.
- `test_static_sampler_device.py` checks distinct supplied devices, descriptor
  preservation, malformed metadata, failure and absence of system-default queries.
- `bench_sampler_transitions.m` checks all pixels across 12 recordings switching
  linear app sampling to constexpr nearest sampling and back, with one plugin loaded.
- The final snapshot passes 22 host scripts, plus framebuffer-fetch on/off,
  repeated/indirect/alternating compute and high storage-slot GPU probes. The two
  history-based negative controls needed access to the original Git object database
  because the isolated source archive has no `.git` directory.

The plugin targets macOS 15.0; the full bundled runtime requires 15.5 or newer.
No kext, translator, Zink, Minecraft or Wine/NVK binary changes are included.
Installation/reboot, live WindowServer, Sequoia execution, other GPUs and extended
sessions remain unqualified. The separate translator audit still needs unavailable
Cargo dependencies. This candidate remains a local test package.

A prototype that bound only changed descriptor-set ranges had noisy A/B results
without a consistent benefit, so it is excluded. Fetch barriers and completion
witnesses retain their current semantics. Concurrent descriptor-recycling edits
in the shared workspace are also outside this snapshot and its measurements.
