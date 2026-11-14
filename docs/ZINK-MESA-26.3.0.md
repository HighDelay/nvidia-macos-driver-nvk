# Zink/Gallium Mesa 26.3.0 update

The native OpenGL frontend now uses stable Mesa **26.3.0**, commit
`0a6fa60abb737d26ac8a316a4aad7c9ddf64f9e4`, instead of the development snapshot
`17ca6174dcc6cb22059ac343bc29f8af7800f42e`. This updates the compiled Zink,
Gallium, state tracker and shader compiler. The earlier
[NVK update](MESA-26.3.0-UPDATE.md) updated the Vulkan backend separately.

The OpenGL version string identifies the frontend. A newly launched application
using the updated runtime reports `Mesa 26.3.0`; a Git build may also show the
pinned revision. Applications already using the old library need to be
relaunched. A reboot is unnecessary for those applications.

## Reconciled interfaces and retained repairs

Mesa replaced the frontend's `get_param` callback with a `broken_invalidate`
boolean. The native frontend leaves the new field false, preserving its existing
explicit framebuffer invalidation after swap and resize. CGL dispatch and
capability metadata are regenerated against the pinned stable source and local
public OpenGL symbols. The existing public-loader capability filter remains:
the tested CGL path exposes OpenGL 4.1 core rather than advertising entrypoints
Apple's loader cannot supply.

Stable Mesa already includes locked submission broadcasts, predicate wait loops,
monotonic timed waits and additional fence/reset synchronization. The overlay
uses those implementations and retains the locked usage/unflushed update and
batch-generation checks in both wait loops. The Darwin barrier still provides
one serial return only after the other waiters stop using it.

The rebased overlay also retains:

- Direct private Vulkan selection and the Darwin NVK descriptor-set workaround.
- Staging/GPU texture uploads and bounded 4 KiB texture uploads on the worker.
- Coherent system-memory preference for CPU-mappable buffers on small BARs,
  failed-map handling and allocation diagnostics.
- IOSurface write-generation tracking, shared-context synchronization, HDR
  staging copies, row padding, GPU fences and uncertain-completion quarantine.
- Native GPU-copy presentation, front-buffer publication, resize and worker
  behavior, OBS compatibility and process-local opt-outs.

The minimum remains macOS 15.5. No exact OS-build activation gate is added.
The NVK renderer is reused with its verified source identity and exact digest;
kernel, NVMTL, translator, Minecraft Vulkan and Wine provider files are outside
this update. Wine's asynchronous presentation option does not change Zink's
synchronous GPU-copy policy.

## Source and binary identity

[`build/prepare_zink.py`](../build/prepare_zink.py) checks the exact source
revision, accepts only the verified stable archive and refuses unrelated edits
or an unrecognized overlay. The archive's 12,923 file contents were checked
against the pinned Git tree, with the mirror's 33 CSV CRLF checkout conversions
accounted for. Its SHA-256 is
`cf65daa7cb857010df1b56169426350cc11d8481def906ca3653fa2ccd2cd0d4`.

`ZINK-BUILD.json` and `NATIVE-BUILD.json` record component files, source hashes,
the Mesa revision and the matched Vulkan frontend/renderer. Private raw build
records remain beside the build output. Candidate library paths are made local
to the runtime and signatures are regenerated before GPU qualification.

## Validation

Qualification uses an RTX 2060 SUPER, 8 GiB BAR1, i5-11400F and macOS Tahoe
26.7.1 (25G241), with the installed 620.70.06 driver. Diagnostic windows and
process-local loader shims exercise the private candidate without changing
system OpenGL selection during qualification.

| Check on the final candidate | Result |
| --- | --- |
| OpenGL, CGL and GLD runtime | 64/64 cases: core/legacy contexts, native GPU copies, front buffers, resize, worker threads, VSync, capability filtering, OBS policy and quarantine. |
| Buffer mapping and uploads | 13/13 cases, including persistent/coherent maps, ordinary mutable buffers, injected failures and process-local small-BAR policy. |
| HDR/SDR IOSurface transfers | 8/8 cases at 60 frames each, including padding and resize. |
| Shared-context concurrency | 18/18 delay, alias, lifetime, teardown, stress and timeout cases across three repeats. |
| Source guards | 6/6 checks, including archive verification, idempotent preparation and refusal of old revisions or altered inputs. |
| Package guards | 34/34 tests. |
| IOSurface lifetime | ASan/UBSan and TSan passed 64,003 binding lifetimes each; the missing-quarantine-retain negative control was rejected. |
| Installer and rollback | 8/8 fixture checks, including interruption recovery and refusal to overwrite a later update. |

All component file and producer hashes match the checked source. Loaded-library
records confirm the final candidate supplied Zink and its Vulkan frontend.
The CGL core probe reports
`4.1 (Core Profile) Mesa 26.3.0 (git-0a6fa60abb)`. The reused NVK renderer digest
remains `c85b5996d0301d31bde5c65f8f8446de7d234f673498ffc03fa59fb57cda91cf`.
Signed x86_64 libraries retain the macOS 15.5 target and use system dependencies.

## Limits and local testing

The qualified userland candidate is installed for local testing. Thirteen Zink
files were replaced with their owner, mode and restricted flags preserved;
119 unrelated files and three provider symlinks were verified unchanged.
Original files and a guarded rollback helper remain in a root-owned backup.

Seven fresh installed-path checks passed without a loader shim: automatic
system core and legacy contexts, IOSurface interop, native odd-size resize,
front-buffer flush, noncurrent presentation and Mesa synchronization. The normal
system OpenGL path reports the stable frontend version. The final installed
hashes and metadata match the qualified candidate.

Automated checks do not establish Minecraft gameplay, Stremio playback,
Sequoia, physical small-BAR hardware, Blackwell, WindowServer or reboot
compatibility for this new frontend. Earlier application results apply to their
recorded binaries. No frame-rate improvement is claimed.

The full-driver build and release remain held. The exact candidate binaries,
source records and local rollback material are retained for testing and a later
release.
