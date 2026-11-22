# Upstream backports

The fork retains Intel macOS 15.5+ and 26, Mesa **26.3.0 stable** and NVIDIA
**620.70.06**. The kernel and NVK use the capability-negotiated mapping ABI.
The NVK build keeps the GPU-copy frontends, uncached streams and BAR-aware
memory types. Hardware ray queries remain opt-in with `NVK_HWRT_QUERY=1`.
Legacy macOS and software Metal WSI are excluded at the user's request.

## Behavior

- NVK mapping hubs serialize the pending RM mapping, the IOKit map and token
  retirement. Negotiate support before using the ABI and retain the per-map
  fallback. Busy renderers renew a short GPU boost lease. `NVRM_610_BOOST=hold`
  restores the previous lifetime boost, and `NVRM_610_BOOST=0` disables it.
  Rated-TDP and GPS power caps require explicit settings.
- Failed adapter starts release their interrupt resources before retrying.
  Firmware probing waits for a bounded interval. `nvrmaspm=1` requests PCIe L1
  on links with compatible latency. `nvrmshutdown=standby` or
  `nvrmshutdown=full` restores PCI BARs after successful RM quiescence. With
  open RM clients, full shutdown uses standby.
- NVKMS can retry a rejected HDMI mode using hardware YUV420. Digital DVI
  retains RGB8 for validation and modesetting. Derived refresh modes go
  through NVKMS validation before publication. `nvfbvramwindow=1` opts into a
  bounded VRAM range.
- Use `nvfbvrr=1` for VRR on a supported display and `nvrmfbddc=1` for
  external-display DDC/CI. `nvfb10bit=1` enables the 30-bit scanout copy path
  on non-DVI mode changes. `nvfbhdr=1` additionally advertises HDR10 to PQ
  displays and uses the PQ output mode when macOS selects an HDR10 timing.
  The copy path accepts BGRA and l10r. HDR metadata and half-float scanout
  require separate qualification. `nvfbbpc`, `nvfbformat` and `nvfbrange`
  apply explicit output preferences at mode changes.
- `nvfbedpbl=1` allows DPCD brightness on an internal panel where NVKMS has
  no backlight control and the firmware already uses AUX. The driver verifies
  exact transfer lengths and attempts to restore the prior level after a
  partial write. The optional `nvfbedpbl=2` mode switches a capable PWM
  panel to AUX after setting a brightness floor. The
  [brightness helper](NVRMFB-BACKLIGHT-POWER.md) saves applied levels on disk
  when firmware drops the NVRAM setting.
- Large IOSurface readbacks can use 4 MiB contiguous staging chunks. A failed
  later copy waits for earlier DMA before releasing staging. A timeout
  quarantines the request's destinations and disables subsequent chunk
  staging for that boot.
- Metal command buffers obtained after Vulkan device loss finish with
  `MTLCommandBufferErrorDeviceRemoved`. The Vulkan dispatch and outstanding
  work keep their existing lifetime rules. Shadowed IOSurface attachments
  publish earlier writes before a native framebuffer-fetch draw reads them.
- The [optional system-wide boost daemon](GPU-BOOST.md) ships in
  `Optional/GPU-Boost/`. Default installation leaves the optional setup off.

NVRM/KMS uses the upstream 3.0 display and power sources on the fork's retained
interfaces. Startup retry, firmware wait and bounded VRAM mapping follow
[upstream lib-v3](https://github.com/nullmoth/nvidia-macos-driver/tree/lib-v3).
The large-readback path adapts
[upstream 1e14008](https://github.com/nullmoth/nvidia-macos-driver/commit/1e14008).

## Display and power additions

- `nvrtd3=1 nvrtd3res=<ACPI path>` enables runtime D3cold for a headless
  GPU with a validated ACPI power resource. A 60-second idle period, no
  framebuffer or rendering clients and successful RM standby are required
  before `_OFF`. Client and framebuffer opens wait for the transition and
  require successful `_ON` before RM resume. [Hybrid power](HYBRID-LAPTOPS.md#idle-power-down)
- `nvfbsurround=<mask>` groups two to four NVIDIA outputs into a wide SDR
  display when their timings and available scanout memory permit it. For
  example, `nvfbheads=3 nvfbsurround=7` selects the first three framebuffer
  indices. A grouped commit uses one surface with a slice per head and waits
  for every participating head. Members keep their own display-audio
  identities. Losing a member returns the available displays to their
  individual modes.
- A monitor connected after a headless start can rematch an idle NVIDIA
  framebuffer. Boot retries are bounded. Periodic recovery scans require
  `nvfbhotplugpoll=<seconds>`. [Hot-plug settings](NVRMFB-HOTPLUG-LIFECYCLE.md)
- The optional Intel panel adds firmware-mode replay on wake, pinned DMC
  firmware and Type-C diagnostics. The test output displays color bars.
  [Intel panel settings](INTEL-PANEL.md#experimental-wake-and-type-c)
- During a verbose boot, the console renders into RAM when the driver can
  verify the firmware framebuffer belongs to this GPU. The copier pauses
  while RM rebinds BAR1. A confirmed RM reservation or the boot head's owned
  scanout provides the next destination. WindowServer's connection ends the
  copy.

## Runtime validation

Private RTX 2060 SUPER and Tahoe 26.7.1 checks cover 43 Zink cases, native
Vulkan and Wine GPU-copy presentation through resize and restore, Wine Vulkan
readback, 120 Metal render-feedback cases and injected post-loss command
buffer completion. The installed kernel exercises the fallback mapping path.
The new shared-token ABI, Sequoia runtime behavior, physical Intel/AMD laptop
panels, Surround and connector detection still need live hardware testing.
