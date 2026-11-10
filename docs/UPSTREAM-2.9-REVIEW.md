# Selective upstream 2.9 backports

The review compares fork `2e62ecc72379b0ad8d164e9edd0d1a12fa56eaac`
with upstream `7412cdde81447f78da0ebb64216dc258d8beaedb`, from the
shared 2.8 ancestor `60fb7255e18c31fe0747091fd9a961f30c1cd82a`.
The full range has 530 commits, including merges, and a merge preview identifies
27 conflicting paths. This fork deliberately does not merge that whole range.

## Included

- [BAR preflight, `3889d54`](https://github.com/nullmoth/nvidia-macos-driver/commit/3889d5469ef6bf01a55181c9ef6d7ec54db31da4):
  clip placement to the CPU/IOKit address limit and allocate/insert the complete
  replacement aperture list before PCI writes. Refused allocation leaves the
  hardware alone. Match stale BAR1 apertures using the full 64-bit address.
  The fork's existing sizing, startup and display policies remain in use.
- [Package completeness, `28afa39`](https://github.com/nullmoth/nvidia-macos-driver/commit/28afa39):
  after checking hashes, reject any regular payload file absent from SHA256SUMS.
  Verify the checker itself before executing it in the root-owned private copy.
  The fork adaptation preserves escaped filenames and the Wine provider's
  existing verified-copy allowlist. The build verifier also checks completeness.

NVRM can now be rebuilt from the pinned NVIDIA 620.70.06 source using the
[build recipe](NVRM-BUILD.md). Two old patch contexts are updated for 620:
flip-notifier behavior is unchanged, and NVIDIA already includes the FSP poll
pointer clear; the fork's remaining OS-timer clear is still applied.

## Retained implementations

Keep every retention rule in [FORK-MERGE-NOTES.md](FORK-MERGE-NOTES.md).
The selective update makes no source change to NVMTL, the translator, Zink/CGL,
the native Vulkan bridge, Wine/DXVK/vkd3d/DLSS, codec registration, NVRMFB,
NVAccel, the Intel panel or NVWebKitFixup. In particular, preserve PR #5's
binding/resource optimizations, bounded caches, icon and transparency repairs,
per-surface handoff, Photo Booth and Safari fixes, and multi-head/cursor lifetime.

The fork fixes already on main since the public 2.8 archive also remain:
Safari filtered icons, the WebKit userland memory/descriptor repairs, and
optional NVWebKitFixup 0.2.0 with guarded, idempotent EFI installation.
Those remain separate from these two upstream backports. This source update
does not change the release version or prepare a new package, tag or release.

## Deferred or excluded

| Upstream change | Review decision |
| --- | --- |
| Default function-constant warm-up | Exclude. A foreground request can join a queued sibling for up to 60 seconds; an isolated production-code probe confirms the queue dependency. This exceeds the 40-second WindowServer watchdog discussed by upstream. |
| Single-flight/pretranslation helper | Exclude. Failure injection reproduces unchecked calloc/strdup crashes and a successful queue submission with no worker after pthread_create failure. Single-flight is used on ordinary cache misses even when background pretranslation is off. |
| Surface scheduling replacement | Keep ours. Upstream's process-name policy misses unprotected outputs and refused WindowServer early-dirty marks; our per-surface protocol preserves the protected asynchronous path. |
| Shared icon, storage, mip, framebuffer-fetch and shader changes | Defer. These overlap the fork's working desktop repairs, even when their commit titles describe other applications. |
| Shutdown BAR restoration | Exclude. The timeout path rewrites PCI mappings while its asynchronous RM worker may still be running; the production-handler host probe demonstrates that ordering. |
| Default eDP AUX backlight fallback | Exclude. A partial write can leave the panel switched to AUX with a zero level. Startup also assumes the AUX level represents the current brightness. Hardware attribution of the new dim-panel report is not established. |
| Codec registrar/library replacement | Keep ours. Preserve synchronous role-scoped registration, Turing support and sandbox delegation. Parser bounds improvements are suitable for separate work; an existing cumulative HEVC POC overflow remains in both codec bases and needs checked arithmetic. |
| Safari Preboot plugin copy | Skip. It is unproven, does not establish access to the separate Vulkan runtime, and its destination-containment check misses an intervening symlink. Keep our tested helper path. |
| Mesa rebase, boost policy, mapping-token interface, high-refresh deferral, hybrid/color/power/RT features and additional app/EFI packages | Defer until independently needed and qualified. Features disabled by default are not evidence of a default desktop regression. |

The static-sampler device-ownership fix is already covered by the fork. Do not
replace our stronger checks with a duplicate. The fork also retains the active
callback-table store regression test that upstream ignores, and the corrected
SPIR-V compute-derivative validator expectation.

The audit's host checks and focused new translator fixtures do not qualify an
integrated 2.9 renderer. Without affected-machine logs, the exact causes of the
reported missing Dock icons, transparency and login failures remain unproven.
The identified defects justify excluding the unsafe paths; they are not a
claim that one defect explains every reported symptom.

Do not mark the deferred range merged using an `ours` merge. Record each future
backport's upstream identity and dependency decisions separately.

## Backport validation

The BAR boundary and production staging tests pass, including descriptor and
insertion failures before PCI writes, cleanup and removal of the full old
64-bit BAR1 address. The manifest guard passes 14 coverage tests, with an
unlisted-kext negative control that ordinary digest verification accepts.
The retained installer, optional-component and package-builder checks pass.

NVRM rebuilds against the pinned 620 core and passes isolated kernel-collection
link checks alone and with the optional Intel-panel/audio components on the
Tahoe host. The optional WebKit helper also links with Lilu. The 622 protected
source files are unchanged from the fork snapshot. These are build and host
checks, not a new boot, other-GPU test or Sequoia runtime qualification; the
installed driver and EFI are unchanged.
