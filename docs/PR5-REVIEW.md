# PR #5 integration review

Reviewed [PR #5](https://github.com/HighDelay/nvidia-macos-driver-nvk/pull/5)
at `3e28b36e3cd80dc0378bc3a92fea90ea5086b675` against `b240c97` on
2026-11-02. The merge has no textual conflicts. Its existing merge of `b240c97`
retains the fork's current Photo Booth, native runtimes, video, display/cursor,
OBS and packaging repairs. The shared upstream framebuffer-fetch mask handling
is used once.

## Accepted changes

- Cache repeated graphics/compute bindings, dynamic render state, successful
  threadgroup variants and immutable shader metadata. Layout, offsets, resource
  changes and fresh command recordings invalidate the relevant caches; failed
  variants remain retryable. Compute and graphics state remain separate.
- Recycle descriptor sets only after confirmed retirement, retain their pool
  and layout identity, and disable recycling without a valid storage dummy.
  Dirty/copy masks cover all 321 binding slots, including storage slots above 255.
  Resource enumeration keeps an owned snapshot through iteration and releases
  it promptly on normal exhaustion. Metal sampler wrappers remain independent.
- Require an exact completion witness. Sequence allocation precedes the submit
  lock, so a later sequence is not proof that an earlier submission completed.
  The negative control fails against `b240c97`; the repaired verdict passes.
  Missing-witness quarantine and backend fallback remain. Ring exhaustion is
  still a separate limitation.
- Fix Minecraft's image failing to update after enlarging its window when BAR1
  cannot import the new drawable. Native Zink falls back to a GPU copy into
  aligned, bounded IOSurface system pages on the same device. It holds the write
  lock through GPU completion, publishes the seed on unlock, and retains resources
  after uncertain work. It adds no CPU pixel copy or family-VRAM tag for host pages.
  This complements the existing small-BAR buffer-allocation fix.

## Improvements made during integration

- Return immediately when draining an empty descriptor free list, avoiding
  arithmetic on an unallocated list. Exercise untouched lists in the regression.
- Replace the contributor-specific Vulkan-Headers path in the WindowServer cache
  regression with the current user's home directory.
- Refresh setup guides when assembling from an already organized release package.
  A regression reproduces the old guide-name collision; the previous guide stays
  with external build records, while the new guide goes into the installable tree.

## Independent validation

Host: RTX 2060 SUPER, macOS Tahoe 26.7.1 (25G241). These are new fork-side
checks; the contributor's RTX 2060 / 26.7.2 / 256 MiB results remain separate.

- All **40 host regression entrypoints** pass, including sanitizer fixtures,
  retained surface/lifetime tests, installer/package checks and codec policy.
- All **12 isolated NVMTL GPU probes** pass. They cover fetch on/off, compute,
  high storage bindings, sampler transitions, descriptor reuse, render state,
  host-copy coherence, 32 Core Image frames, Unknown-usage writes and eight
  padded volume-copy recordings. Photo Booth's writable backing and Zink
  selection are retained; the synthetic probe does not open a camera.
- All **36 Zink GPU cases** pass, including automatic GLD updates through repeated
  growth/shrink, worker threads, multiple windows, VSync and OBS/OS-build policy.
  Forced VRAM refusal verifies every pixel of **24 system-page GPU-copy frames**,
  plus rejection and uncertain-work quarantine. The largest actual drawable on
  this desktop was 1920×938; AppKit constrained requested 1920×1080 windows.

The plugin and Zink are rebuilt. CGL/GLD, translator, kernel, native Vulkan/Wine,
DXVK/vkd3d and video binaries retain verified release provenance. Unchanged kernel
binaries do not need another link test. Build records and validation logs stay
outside the installable payload. No application FPS increase is claimed.

This review does not establish live Minecraft resize behavior, a rebooted
WindowServer session, Sequoia execution or long-run stability. The installed
driver was not replaced. Release publication waits for the user's approval.
