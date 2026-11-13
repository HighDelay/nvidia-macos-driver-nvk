# Bounded asynchronous Wine presentation

The Wine frontend can move drawable acquisition and the presentation copy's
completion wait off the application thread. This changes the host scheduling
of the existing GPU-copy path; it does not remove GPU synchronization or
introduce a CPU pixel copy. Native Minecraft and Zink keep synchronous policy.

## Why both waits matter

The previous [ZZZ measurement](WINE-PRESENT-REUSE-20261113-RESULTS.json) reached
74.86 FPS on a 75 Hz display at 1080p High, MSync enabled. Per frame, its
`vkQueuePresentKHR` call took 13.17 ms, including 11.55 ms acquiring a Metal
drawable and 1.52 ms waiting on the rendering/copy fence. Both waits held the
device submission lock. The fence counter includes earlier rendering
dependencies; it is not a timestamp of the copy alone.

Moving only `vkWaitForFences` would leave the larger drawable wait in place.
Nor does moving both prove higher FPS at a display refresh ceiling. Worker time
can overlap application submissions or reappear as bounded queue/acquire
backpressure. Controlled frame pacing and application behavior still matter.

## Ownership and ordering

`present_worker.h` uses a fixed two-slot FIFO. Capacity includes the running job.
An additional present call waits without holding the backend submission lock.
`vkQueuePresentKHR` copies caller arrays and submits a binary-semaphore handoff
on the caller's queue before returning. The worker never retains `pResults` or
other pointers into the caller's stack.

A job retains its device, swapchains and source-image reservations. A single
worker per device handles the complete copy/wait/publish sequence, retaining the
drawable, exact IOSurface import, pool, buffer and fence until completion.
Only then does it publish the dirty/generation tag, present, park completed
imports and return source images to acquisition. The single worker also keeps
the shared coherent HUD mapping from being overwritten during GPU reads.

If drawable acquisition or preparation rejects a job before consuming its
handoff, a fenced empty submission consumes that handoff before image release.
A late surface/out-of-date error is latched for subsequent acquire/present.
Uncertain GPU completion retains GPU-owned resources, marks device loss and
wakes blocked image acquisition. Worker exceptions also mark device loss.

Present acceptance, resize, teardown and idle use a lifecycle gate distinct from
the backend submission lock. Lifecycle operations drain jobs before changing
layer settings or freeing images. Normal device teardown joins its worker;
process-exit cleanup stops remaining workers before library destructors run.

## Fallback and scope

The worker requires a copy queue reserved separately from application queues.
It never reduces or steals the queues requested by DXVK/vkd3d. If no queue is
available, or worker startup fails, presentation stays synchronous. On the local
host DXVK obtained a private queue; the D3D12 probe requested every available
queue and exercised this fallback.

`WINE_NVK_ASYNC_PRESENT=0` selects synchronous presentation after a game restart.
The Wine-specific setting is independent of MSync, resource reuse and profiling.
Native Minecraft/Zink frontends ignore it. The normal driver-owned provider
contract and matching DLL pairs remain unchanged, so YAAGL needs no source update.

In asynchronous mode, the existing total presentation CPU counter measures the
call's acceptance/backpressure cost. Drawable/fence counters measure worker time;
adding them to the call counter as sequential time would double-count overlap.

## Qualification

Private tests use RTX 2060 SUPER / 8 GiB BAR1 / Tahoe 26.7.1 (25G241) and retain
the 620.70.06 kernel interface. The first prototype was tested with the previous
Mesa renderer separately, then with official stable Mesa 26.3.0.

- AddressSanitizer/UndefinedBehaviorSanitizer and ThreadSanitizer host fixtures
  exercise real bridge scheduling with mocked GPU calls: drawable/fence delays,
  concurrent submission, queue backpressure, caller-pointer lifetime, idle and
  swapchain drain, late resize, exceptions and timeout quarantine/wakeup.
- Old-renderer prototype: 120 exact-pixel presentations with resize/restore.
- Final stable-renderer Wine candidate: 120 frames each with async default,
  synchronous opt-out, reuse disabled, and HUD enabled; native ABI fill/readback.
- Windows Vulkan: 240 presentations over two surfaces. DXVK D3D11: 120 direct,
  60 indirect and 60 indexed-indirect presentations plus shared-resource checks.
- D3D12: DXIL compute/root UAV/fence and 1,024 words; 120 presentations with five
  render readbacks and resize using synchronous fallback.
- Native frontend: 90 exact-pixel frames while the Wine async variable is set,
  confirming the native synchronous policy. Provider setup/rollback: 19 cases.
- Packaging fixture verifies that changing the worker header invalidates Zink
  frontend provenance; unchanged runtime dependencies remain pinned and verified.

The [gameplay record](WINE-ASYNC-PRESENTATION-20261114-RESULTS.json) confirms
ZZZ D3D11/DXVK loaded the private stable renderer and async frontend with MSync.
A 30-second capture averaged 73.53 FPS, with zero import/presentation errors,
2.71 ms per presentation call and no newly created import sets. A two-second
thread sample during that capture found the fence wait on the worker and
bounded-queue backpressure on DXVK's submission thread. Sampling can perturb
timing. The user reported it felt somewhat better.

The user then requested a synchronous comparison using the same provider, Mesa,
MSync, reuse and DXVK settings. Its 30-second sample averaged 65.69 FPS, with no
import/presentation errors and 14.94 ms per presentation call. The user reported
it felt about the same, then requested a final async repeat. That repeat's
separate 30-second sample averaged 61.42 FPS, with no import/presentation errors.
After returning to gameplay, the user reported **async feels better, with a few
FPS higher**, and approved pushing both updates. The scenes/cameras and capture
timing were not controlled by replay, and only the first capture included a
thread sample. These differences prevent attributing the capture FPS gaps solely
to async.

No uncapped FPS, input-latency, one-percent-low, long-session, commercial CrossOver
or other-GPU claim is established. Source promotion is approved after the final
user comparison; a full driver build/release remains held. The installed stable
renderer is separate from the private async Wine provider selected for this test.
