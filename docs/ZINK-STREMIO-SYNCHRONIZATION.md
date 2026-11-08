# Stremio native Zink IOSurface repairs

The combined shared-context and HDR-export repairs are locally validated with
Zink enabled, including user-confirmed smoother Stremio playback on RTX 2060
SUPER. The RTX 5070 Laptop result remains unverified. No codec, kernel, Metal
plugin, Minecraft Vulkan provider or Wine provider changes are included.

## HDR export follow-up

The shared-lock repair alone did not resolve playback lag on either user's
machine. A second trace of the actual local application found RGBA16F
`CAOpenGLLayer` output at 1920×943, eight bytes per pixel and a 15,360-byte row.
The preceding real-libmpv test used RGBA16F worker textures but exported to a
BGRA8 IOSurface, missing the expensive half-float output path.

The live layer accumulated 479,845 ms across 13,840 GPU completion waits
(34.67 ms mean); acquiring IOSurface locks was negligible by comparison.
NVK's image-to-buffer path can perform its format/layout conversion with a
compute shader. Writing the resulting pixels directly to imported system pages
over PCIe is much slower than converting into GPU-local memory first.

The export now converts to a packed, device-local buffer, then performs a buffer
copy into the IOSurface pages. One buffer per context grows as required and is
released on normal destruction; uncertain completion retains the entire context.
`PIPE_USAGE_IMMUTABLE` keeps the staging allocation on the device-only path even
with the fork's small-BAR policy. Allocation failure retains the original copy
path. Padded rows copy only their pixel bytes, preserving padding and other
planes. Publication still waits for the complete transfer before unlocking and
advancing the surface seed. No CPU pixel transfer or asynchronous early-success
handoff is introduced.

The actual Stremio layer's mean wait fell to about 1.7 ms, and the user reported
"so much smoother" playback with Zink enabled. A separate HDR transfer probe
at the same dimensions fell from a 53.08 ms mean flush to 6.29 ms while Stremio
was also running; this is a transfer comparison under load, not application FPS.
Both measurements use the same 2.8 / 620 renderer and unchanged video codecs.
The RTX 5070 Laptop is expected to benefit from the same path; local success
does not establish its result or Apple-OpenGL performance parity.

The dedicated probe renders changing HDR values above 1.0, checks exact channel
values within half-float/UNORM precision, rejects non-finite output, verifies
orientation and untouched padding, and records both flush and CPU-visible
publication latency. It includes a large padded surface and context-local
staging growth/reuse across surface sizes:

```sh
python3 tools/test_zink_iosurface_transfers.py \
  --runtime /candidate/runtime --output /new/transfer-results
```

The original synchronization-only results below are retained as historical
evidence. They establish lock progress and pixel correctness; their BGRA8
publication timings did not qualify normal Stremio HDR presentation.

## Evidence and repair

The affected Stremio 5.1.30 sample places 425 of 448 main-thread samples in the
IOSurface publication fence wait, and 420 of 448 `mpv-render` samples in the
share-group mutex taken by texture refresh. The original implementation holds
that mutex through the GPU copy and completion wait, and ordinary
`GL_TEXTURE_2D` binds take it even though IOSurface imports support only rectangle
textures. The Zink binary in the affected 2.7.2 installation and the preceding
2.8 package is identical; upgrading that binary alone is not this repair.

Stremio's local 5.1.30 binary creates two distinct shared CGL contexts. The
render worker uses one and the layer uses the other. The bridge retains one
Mesa context per CGL context; there is no evidence requiring a global context
lock or concurrent access to a single context as the explanation for this sample.
Applications must still obey CGL context ownership and GL sharing rules.

The repair:

- Returns immediately for irrelevant texture targets before taking any
  IOSurface-group lock.
- Retains a snapshot of bindings under the short group-list lock, then performs
  transfers outside it. Snapshots keep deleted, replaced and retired bindings
  alive until their users relinquish them.
- Serializes a transfer with other transfers and refreshes of the **same
  IOSurface**, including separately imported aliases and its different planes.
  An unrelated context can upload ordinary textures, publish a different surface
  and tear down while another publication waits for completion.
- Avoids waiting on an unrelated surface merely to retire an obsolete binding
  during flush. The writer check is repeated under the surface lock.
- Retains the pre-flush before inspecting backend write generations. Zink can
  defer clear-only writes until that flush; removing it loses their publication.
- Gives uncertain copies an independent, idempotent quarantine reference. A
  snapshot can outlive removal from the normal list, so list ownership alone is
  insufficient to protect a timed-out copy's pages, lock and image.

The actual publisher still waits for confirmed GPU completion before unlocking
and advancing the surface seed. Same-surface readers wait; uncertain completion
still quarantines the screen and retains its resources until process exit.
No fence is removed, no successful external handoff is reported early, and no
per-frame CPU transfer is introduced into the driver.

## Reproducing the local checks

Build the pinned Mesa overlay and adapters using the normal
[Zink build instructions](ZINK-OPENGL.md#build-commands). The trial uses Mesa
`17ca6174dcc6cb22059ac343bc29f8af7800f42e` and the verified matched 620 frontend
and renderer from the existing 2.8 package. Keep the source tree isolated from
the Mesa tree used by the Metal plugin. On this build host, dependency discovery
must select system zlib rather than the MacPorts library built for macOS 26.

```sh
python3 tools/test_zink_iosurface_lifetime.py --output /new/lifetime-results
python3 tools/test_zink_iosurface_concurrency.py \
  --runtime /candidate/runtime --output /new/concurrency-results --repeat 10
python3 tools/test_zink_iosurface_concurrency.py \
  --runtime /verified/preceding-runtime --output /new/negative-control \
  --expect-contention
python3 tools/test_zink_runtime.py \
  --runtime /candidate/runtime --output /new/runtime-results --front-buffer
python3 tools/test_zink_buffer_uploads.py \
  --runtime /candidate/runtime --output /new/buffer-results
```

The concurrency tests hold the real publication's Vulkan wait in a
**probe-only** shim. They require unrelated uploads/publication to finish before
that wait is released, and a same-surface reader to stay blocked. Modes cover a
shared texture, separately imported aliases, delete-before-flush/replacement,
unrelated-context destruction, ordered video-style frames with timer queries
and fences, and timeout quarantine. The original binary must fail the unrelated
progress assertions as a negative control. The lifetime sanitizer fixture also
rejects a deliberately omitted quarantine retain.

`--front-buffer` captures only diagnostic windows and requires existing
screen-recording permission. A permission refusal is not a passing compositor
test. The process-local GLD lookup and wait shims are test tools, not installation
components or application launchers. Never register them globally or use them to
launch Stremio or WindowServer.

The real-libmpv probe accepts an already installed libmpv, public mpv headers and
a local test video. It renders on a worker into two shared RGBA16F textures,
then blits to an IOSurface on the other context. It checks four exact pixel
samples per frame against the worker's GPU output and requires changing frames:

```sh
python3 tools/test_zink_mpv_iosurface.py \
  --runtime /candidate/runtime --output /new/mpv-results \
  --libmpv /Applications/Stremio.app/Contents/MacOS/libmpv.2.dylib \
  --headers /path/containing/mpv-headers --video /local/animated-test.mp4 \
  --width 1920 --height 1080 --frames 120
```

The input fixture must actually animate the sampled colors. Locally it is
`testsrc2` with a continuously rotating hue, encoded as H.264 or HEVC. This
probe deliberately uses software decode to isolate the GL rendering path; it
does **not** change Stremio's settings or qualify NVDEC. It uses the real bundled
libmpv but not Stremio's complete UI, `CAOpenGLLayer` or the display compositor.

## Results and limits

Local hardware is RTX 2060 SUPER, 8 GiB BAR1, i5-11400F, Tahoe 26.7.1 / 25G241.
Loaded RM/framebuffer/accelerator versions were checked as 2.8 before GPU probes.
Logs name the candidate's private renderer path, not an assumed installed copy.

The final candidate passes 64 native/runtime/compositor cases, 13 retained
buffer-allocation/upload cases and 60 concurrency cases (ten repetitions of six
modes). ASan/UBSan and TSan each pass 64,003 binding lifetimes, including the
retired-snapshot quarantine case; the missing-retain negative control is rejected.
All four delayed-progress cases fail as expected on the original binary. In the
forced-wait shared-texture case, unrelated work takes 1.19–3.67 ms across the ten
candidate runs (median 1.82 ms), versus 1,108.88 ms with the held fence on the
original library. This measures removed lock contention, not application FPS.

The bundled `mpv v0.41.0-3-g512025b92` renders 120 exact-sample-checked frames per
clip: H.264 at 1920×1080 and HEVC at 2560×1600, with 116 and 117 changing frames
respectively. Mean publication times are 0.91 and 1.93 ms in these bounded runs;
maximums are 1.24 and 16.42 ms. These include a synthetic IOSurface handoff, not
normal Stremio display scheduling. Cold render startup and concurrent diagnostic
activity preclude a controlled performance claim.

The capability probe's old blanket rejection of `GL_ARB_texture_storage` was
stale after the preceding deliberate texture-storage exposure. It now requires
that extension while retaining rejection of the unavailable APIs. With this
corrected fixture, the preceding runtime and candidate both pass all 21 interop
cases in both core profiles. Pixel assertions are unchanged.

The final result record is kept beside the candidate under `out/`, outside any
installable driver package. It records exact binary/source hashes, test outputs
and limitations. The final library is signed x86_64, targets macOS 15.5 and uses
system zlib; all producer/binary hashes and candidate load paths are checked.
Preserve the existing 2.8 release archives unchanged.

A separate Apple-OpenGL/libmpv comparison fails this CPU-IOSurface pixel oracle,
even with an explicit `glFinish` before the CPU check. No tolerance is loosened
and no Apple-performance-parity claim is made. The failure is not attributed to
the Zink repair. Small synthetic Apple shared-context frames pass, but are not a
substitute for the affected application's playback behavior.

The historical query/descriptor crashes have not been reproduced or proved
fixed by these tests. The full-BAR host and process-local small-BAR allocation
checks do not qualify physical small-BAR hardware, Sequoia or Blackwell. Playback
smoothness and artifacts on the RTX 5070 Laptop / Tahoe 26.7.2 remain for the
tester to verify after a separately coordinated, reversible installation.

The compact local candidate contains matched **2.8 / 620** runtime inputs. It is
not a standalone upgrade for a laptop still running 2.7.2, and it has no automatic
installer. No draft, tag, publication, reboot or user-application termination is
part of this trial.
