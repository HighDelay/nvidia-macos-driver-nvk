# Browser video decoding

Edge's GPU process crashed during VP9 playback, then disabled hardware rendering
for the rest of that browser session. The ANGLE Metal workaround was still
present: a fresh browser initialized ANGLE normally before playing the video.

The codec's private output pool copied the client's destination dictionary.
Chromium requested only `PixelFormatType`, so this pool produced heap-backed
CVPixelBuffers without IOSurfaces. Native tests had previously requested
IOSurface backing explicitly and therefore missed this browser contract.

The pool now retains the negotiated output attributes, including IOSurface
backing and Metal compatibility, while preserving client options and the
existing pool/conversion policy. ANGLE, sandbox settings, kernel code, Zink and
the other GPU codec selections are unchanged.

Validation on RTX 2060 SUPER / Tahoe 26.7.1:

- The original library fails the new minimal-attributes test with missing
  IOSurfaces. The corrected library passes all 48 sync/async VP8/VP9 sessions
  (360 frames), through 4K and VP9 10-bit, with exact libvpx-reference pixels.
- Edge **156.0.4314.58** played the supplied [YouTube VP9 test](https://www.youtube.com/watch?v=VhZtweWFtVI)
  at **3840×2160 / 60 fps** for 35 seconds, including looping. Media diagnostics
  selected `VideoToolboxVideoDecoder` with platform decoding enabled; ANGLE
  Metal, compositing, WebGL and WebGPU remained enabled, with zero GPU crashes.
- The player controls were deliberately revealed during the interactive run.
  Raw dropped-frame counts are recorded; this is not an isolated FPS benchmark.
- The original H.264/HEVC hardware encoders still pass their eight-frame smoke
  tests. Ada/Ampere/TU117 registration policy and codec packaging checks pass.

Run `tools/video/vt_codec_probe.m` with `--minimal-output` to reproduce the
browser-style destination dictionary. The probe checks IOSurface backing on
every returned frame. [Evidence and hashes](BROWSER-VIDEO-DECODING.json).

After updating, fully quit and reopen a browser that already fell back to
software rendering. Reloading the video alone does not reset that session's
failed GPU state. Other browser versions, codecs and applications need their
own playback qualification.
