# Repository instructions

## Fork merge instructions

Before merging upstream into this fork, read [docs/FORK-MERGE-NOTES.md](docs/FORK-MERGE-NOTES.md).
Check for conflicts and equivalent upstream implementations while retaining the fork's Tahoe fixes,
existing performance optimizations, OpenGL/Minecraft changes and MoltenVK optimizations. The notes
identify the commits, required behavior and validation limits.

## Fork releases and debugging packages

Standing user instructions, updated 2026-10-28: when completing requested implementation
updates or upstream merges for the forks below, build, validate, commit, push and prepare
the affected releases as GitHub drafts. Upload and verify the complete assets and release
notes, then give the user the draft link for review. Publish only after the user explicitly
approves that release. This approval requirement replaces the earlier automatic-publication
instruction. A later instruction to work locally, hold a release or wait for testing takes
precedence. Documentation-only changes do not require a new binary release. Update only
the repositories and artifacts that need changing.

| Fork | Release contents |
| --- | --- |
| [HighDelay/nvidia-macos-driver](https://github.com/HighDelay/nvidia-macos-driver) | Complete installable driver archive with top-level `pkgroot/`, native Minecraft runtime, compact Wine/NVK provider and matched DXVK pair; standalone compact provider archive, separate optional ARM translation scripts archive and `SHA256SUMS`, following the previous Wine/NVK release. |
| [HighDelay/yaagl-nvk](https://github.com/HighDelay/yaagl-nvk) | Separate Global and CN launcher archives containing ZZZ, GI and HSR, with Start commands, required launcher data, guides and `SHA256SUMS`. |
| [HighDelay/dxvk-nvk](https://github.com/HighDelay/dxvk-nvk) | Matched x64 `d3d11.dll` and `dxgi.dll`, license, source patch, build provenance, setup notes and `SHA256SUMS`. |
| [HighDelay/vkd3d-nvk](https://github.com/HighDelay/vkd3d-nvk) | Matched x64 `d3d12.dll` and `d3d12core.dll`, upstream license/source identity, build provenance, Wine/NVK setup notes and `SHA256SUMS`. Reuse unchanged upstream binaries only with verified provenance. |

### Publication workflow

1. Check the current fork branch, upstream changes, retained repairs and existing releases
   before choosing the next tag. Preserve the driver's merge instructions above. Build
   changed components and reuse unchanged binaries only with their recorded source identity,
   matching dependencies and verified hashes.
2. Run the appropriate build, packaging and regression checks. For driver packages, verify
   both OS accelerator variants, supported macOS requirements, userland signatures, runtime
   manifests, symlinks and checksums; run an isolated kernel-collection link check when kexts
   change. Keep AppleDouble files (`._*`) and Finder metadata out of archives and manifests.
   State hardware/testing limits accurately instead of treating host checks as a confirmed
   fix on an untested machine.
3. Keep `/Library/GPUBundles/nvmtl/wine` as the driver-owned Wine/NVK provider and DXVK source.
   Preserve its setup/rollback helper and the separate Minecraft runtime. A DXVK update must
   refresh both DLLs, provider identity/hashes, source/license records and checksums together,
   and produce a matching driver release. Coordinate a YAAGL update only when its consumer
   contract or launcher behavior changes.
4. YAAGL continues to use that installed provider by default. Its archives contain launchers
   only: Wine downloads during setup, while NVK and DXVK come from the driver. Preserve this
   arrangement instead of adding another runtime bundle or an independent DXVK download.
5. Push the relevant source commits before uploading assets. Use versioned tags pointing
   to the intended pushed commits and record the provenance of reused components. Upload
   the archives and checksums to a draft release and verify the uploaded sizes/digests.
   Present the complete draft and validation limits, then wait for the user's explicit
   approval before publishing. After approval, publish a regular public release marked
   Latest, with draft and prerelease disabled, unless the user requests otherwise.

### ARM translation release asset

Standing user instruction, updated 2026-11-04: include the separate ARM translation
scripts package in each driver release, alongside the driver and compact provider.
Use `nvidia-arm-scripts-<driver-version>.tar.gz` with top-level
`NVIDIA-ARM-Scripts/`, and include its digest in the release's shared `SHA256SUMS`.
Keep it outside `pkgroot/`; installing the driver must not install the CPU translator.
The scripts use the signed upstream runtime directly, without requiring `1401.app`
or changing the GPU driver or OpenCore.

Build with `build/package_arm_apps.py --version <driver-version>`. The scripts
package version follows the driver; the bundled runtime retains its independently
pinned upstream version. Verify its SHA-256, upstream Ed25519 signature, internal
checksums, licenses and helper signature/architecture. Run the wrapper tests and
isolated install/check/uninstall checks; record application translation results
separately and retain upstream CPU, disk-space and application-support limits.
Reuse unchanged runtime bytes only with verified provenance and hashes.

Stage this archive with the other assets under `out/`. Preserve the exact validated
local archives for the later draft rather than rebuilding solely to upload them.
Include a short setup link to [the ARM scripts guide](tools/arm-apps/README.md) in
the release notes. Verify uploaded sizes/digests and obtain explicit publication
approval for the complete draft, including this asset. Local-test and release-hold
instructions apply to the ARM package too.

### Driver release format

Standing user instruction, updated 2026-11-01: keep the installable package
focused on installation and runtime files. The top level contains one short
`READ-ME.md`, one current `PACKAGE-IDENTITY.json`, the installer/verification
tools, `VERSION`, checksums and payload directories. Group useful setup guides
under `Guides/`; keep optional-component instructions with that component.
Do not accumulate duplicate identities, `BASE-*` histories, repair/validation
reports, review notes or temporary test instructions in `pkgroot`. Preserve
those records beside the build output, outside the installable archive.
Keep licenses, signed resources and component manifests/build records at the
paths their runtime/setup/build verifiers expect. Avoid duplicate guides unless
a component contract requires its copy. Enforce this layout in the package
builder, regenerate checksums after cleanup, and verify that repackaging leaves
driver/runtime binaries unchanged. A packaging-only change reuses verified
binaries and does not require recompiling or repeating kernel link tests.

Standing user instruction, updated 2026-11-04: bundle NMXAudio under `Optional/`
when it is supplied by the verified upstream package. Audio is a separate installer
choice, default No, for Tahoe with a PCI HD Audio controller. Retain driver-managed
opt-ins on updates and their backup/rollback/removal state. Preserve independently
installed or replaced audio drivers; do not uninstall the NullMoth namespace wholesale.
Keep audio separate from the Intel-panel choice and from NVIDIA video codecs.

Standing user instruction, updated 2026-10-31: future driver releases use tags
`v<version>-nvk` and archives `nvidia-macos-driver-<version>-nvk.tar.gz`.
Keep optional NMIntelFB support in the normal complete driver package under
`Optional/`, with the installer prompt defaulting to No and existing opt-ins
retained on updates. Do not add feature-specific suffixes such as `intel-panel`
for this bundled component. Keep release-note installation filenames and
`SHA256SUMS` consistent with the archive name. If a version is already public,
use a new version/revision rather than replacing its published tag or assets
unless the user explicitly requests replacement. The already-approved
`v2.6.5-intel-panel` release is the one-time exception to this future naming rule.

Use [the Wine native NVK release](https://github.com/HighDelay/nvidia-macos-driver/releases/tag/v2.6.2-wine-nvk)
as the delivery and release-description reference. Keep the Minecraft README link at the top,
immediately followed by this Wine/CrossOver heading:

```markdown
### For Wine/Crossover native NVK, please read [this](https://github.com/HighDelay/nvidia-macos-driver#wine--crossover-native-vulkan)
```

Keep `What has been added` with relevant commit links, actual test results/hardware, the all-app
ANGLE Metal default, installation instructions, and the existing boot warning and recovery
steps. Keep entries brief and remove redundant links/details from older releases. Include
Wine/NVK and YAAGL setup links when relevant. Maintain release notes in the GitHub release
description; `~/Downloads/changelog.rtf` no longer needs updating.

Assemble public release assets in a separate clean output directory from verified build
inputs. Manually staging or replacing `~/Downloads/pkgroot` remains an on-request debugging
and local-testing workflow; it is not the normal release deliverable or a substitute for
publishing. A debug staging request alone does not request a release. Preserve explicit
local-only/test-before-publish instructions for experimental changes.
