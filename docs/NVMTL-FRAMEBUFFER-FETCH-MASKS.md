# Framebuffer-fetch descriptor masks: local test

This candidate applies upstream `4a377d91f065f79c9cabd504aa76f13c1f2471cb`
and its fallback repair `8784c0b0d8900c89c0045ceaf2d5f93b95b4bec9` to
the `2f758b1` WindowServer optimization branch. Both are in nullmoth v2.6.5.
It is a local test build on the existing 2.6.4 fork, not a complete v2.6.5 merge.
The subsequent PR #5 follow-up is documented in
[address-table snapshots](NVMTL-ADDRESS-SNAPSHOTS.md); the measurements below
remain those of the original fetch-mask-only test package.

Framebuffer-fetch descriptors use set 3 and are bound separately. Counting their
binding 192 in a stage mask invalidated that mask, making affected shaders use
the conservative all-descriptor path. The new masks exclude both the heap and
fetch sets. The descriptor snapshot cache then updates the bindings actually
used by each shader and can reuse a set when only its address-table offset
changes. Pipelines without the new masks retain the older valid masks; unknown
bindings still fall back conservatively. `NVMTL_DESC_MASK=0` selects the older
masks for process-local comparison.

The existing fetch barriers, attachment bindings, compute and graphics binding
caches, resource ownership, capture publication, Tahoe fixes, all-app ANGLE
default, OpenGL, Minecraft and Wine/NVK runtimes are retained. No kernel or
translator code changes are included.

## Evidence

The user's live WindowServer runs identify the upstream and fork builds by report.
Their recorded checkout commit is the same (`2f758b1`); the tool did not record
the installed binary hash. Churn averaged 127.9% CPU upstream versus 171.1% on
the fork, and alpha 76.3% versus 87.6%. Several other phases favored the fork.
The fork's move phase has only eight samples versus eighteen upstream. These
single runs, without completed-frame counts, do not establish the cause of the
differences or an FPS result.

The previous `bench_windowserver_state.m` explicitly disables framebuffer fetch
and therefore could not measure this missing optimization. The new
`tools/bench_framebuffer_fetch.m` keeps it enabled, creates a separate device
from the explicitly named plugin, and checks all RGBA pixels after every frame.
It covers fixed bindings, changing uniform bytes, alternating textures, and new
render targets/command recordings. The red channel accumulates each fetch;
green and blue verify the most recent texture and uniform values.

On RTX 2060 / TU106-A, macOS 26.7.2 (25G332), x86_64, five alternating processes
per build, nine measured frames per process after two warmups, 1,200 draws:

| Workload | Existing 2f758b1 CPU ms | Candidate CPU ms | Change |
| --- | ---: | ---: | ---: |
| Stable fetch bindings | 1.224609 | 1.254187 | +2.42% |
| Uniform bytes change each draw | 8.276284 | 2.156014 | -73.95% |
| Texture and uniform change each draw | 8.667505 | 4.467402 | -48.46% |

These are means of process medians for calling-thread command recording, not
WindowServer-wide CPU or frame rate. All pixel checks passed. Disabling the new
mask on the same candidate restored the slower behavior (8.646449 ms uniform,
8.921309 ms texture). Stable bindings show no improvement. Raw logs and results:
`/Volumes/App/nvmtl-fetch-20261031/validation/bench-fetch.json`.

`tools/test_opengl_perf.py` runs the production parser and snapshot-cache logic
under ASan/UBSan with masks both enabled and disabled. It covers fetch/heap
exclusion regardless of decoration order, malformed/unknown binding refusal,
graphics/compute masks, older-mask fallback, table-page rollover, changed
resources, recycled descriptor sets and GL dynamic uniform offsets.

Sixteen host regression scripts passed. The broader OpenGL audit passed its
descriptor and cursor checks but could not run its separate translator section:
the configured Cargo dependency directory lacks `rspirv`. The translator is
unchanged and reused from the verified base package; this is not a full-suite pass.

## Deliberately deferred upstream behavior

The v2.6.5 completion-witness change (`9c1c123`) is not part of this candidate.
Its later-lap argument assumes sequence allocation follows submission order.
Here `nvmtl_wit_record` allocates a sequence before `nvmtl_submit_mutex` is held;
concurrent recording can reorder submissions. Further synchronization review and
concurrency tests are required before changing that reuse guarantee. The supplied
load logs do not establish that this issue occurred during the user's test.

The new plugin has not been installed or reboot-tested. Repeat the live load
test after installing and rebooting, using the same display, apps and workload;
record the installed plugin SHA-256 and allow shader caches to warm. Keep the
per-phase results and sample counts, preferably for three runs. Capture, desktop
visuals, long sessions, other GPUs and Sequoia hardware remain to be tested.
