# NVMTL command recording caches

NVMTL now reuses three pieces of command-recording state:

- Compute pipeline and descriptor bindings are emitted only when their actual
  handles, layout, set count or dynamic offsets change. Graphics and compute
  retain separate binding state. Internal occlusion reductions update the same
  compute state; entering the direct SASS hook invalidates it, including failures.
- Each command recording remembers eight successful compute threadgroup variants.
  Hits avoid the global variant mutex and linear search. Misses keep the existing
  synchronized compilation path; failed builds remain retryable. Recorded
  resources retain the owning pipelines and their variants through completion.
- Framebuffer-fetch descriptor bindings are reused for the same set and layout.
  Binding a different lower-set layout invalidates the higher fetch binding;
  changing fetch layouts invalidates incompatible lower bindings. Fetch barriers,
  push constants, draws and dispatches are still emitted normally.

All three caches are bounded, require no heap allocation or extra mutex, and reset
when a new command recording begins, including event segments. They do not reuse
in-flight descriptor storage or extend GPU resource lifetimes.

The accompanying resource-set repair enumerates a stable snapshot under concurrent
mutation, with a valid mutation sentinel and ARC-managed snapshot lifetime. Normal
exhaustion releases the snapshot's resources immediately; early break, return or
exception releases them when the surrounding autorelease pool drains. Backend
VkSampler interning remains in use; independent Metal sampler wrappers preserve
caller-specific state.

## Validation

`tools/test_compute_bindings.py` and `tools/test_graphics_bindings.py` compile the
production helpers with AddressSanitizer and UndefinedBehaviorSanitizer. They cover
every binding-cache input, independent recordings, variant eviction, retry after
failure, tail workgroups, indirect dispatch, internal occlusion reduction, SASS
invalidation and framebuffer-fetch layout disturbance. A sequence of 10,000
identical compute dispatches performs one pipeline bind, one descriptor bind and
one synchronized variant lookup, while keeping every dispatch and push-constant
update. Repeated fetch binding likewise emits just one bind.

`tools/bench_command_bindings.m` loads an explicitly named plugin and creates an
isolated NVMTL device without changing the installed driver. It checks repeated,
indirect, and alternating pipeline/offset/tail compute readbacks plus three
framebuffer-fetch targets. Pass `fetch-off` as the last argument to exercise the
dummy descriptor path. CPU measurements cover command recording on the calling
thread; they are not application FPS or whole-system CPU measurements.

See `NVMTL-COMMAND-CACHE-RESULTS.json` for the recorded host checks, GPU measurements,
build identity and remaining qualification limits.

WindowServer-specific dynamic-state and IOSurface metadata caching is documented in
`NVMTL-WINDOWSERVER-CACHES.md`, with its isolated compositor-like measurements in
`NVMTL-WINDOWSERVER-CACHE-RESULTS.json`.
