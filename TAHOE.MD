# Building the Tahoe driver with the retained fork fixes

Before merging a newer upstream revision, read [Fork merge notes](docs/FORK-MERGE-NOTES.md).
Retain the Tahoe fixes, existing performance optimizations and the OpenGL/Minecraft and MoltenVK
changes, checking for conflicts and equivalent upstream implementations first.

This guide targets upstream **2.5.4 at `4677905`**, using NVIDIA **615.78.08**. It includes surface
uploads from imported pages, extended-range/sRGB rendering repairs, descriptor fallback, display/audio
and startup changes. The earlier parked-VRAM and OpenGL/Minecraft optimizations are covered upstream
and included once. The fork retains its cursor shutdown accounting, pointer safety guards and the
other behavior described in the [merge notes](docs/FORK-MERGE-NOTES.md).

The local [display rotation](docs/DISPLAY-ROTATION.md) candidate uses native CoreDisplay
rotation, enabled by default on Tahoe. WindowServer rotates into the physical raster;
the extra GPU/CPU correction pass is retired. It includes native rotated modes,
per-head registry paths and cursor geometry, while retaining cursor serialization/drain
and preserving existing scaling overrides. `nvrmrotation=0` disables it. Build matching
NVRMFB, Tahoe NVAccel and NVMTLDriver, preserving official version/dependency metadata.
The four-monitor desktop report still needs a retest with this candidate. Prior RTX
2060 Super results apply to the retired path; laptop routing, sleep/wake and hotplug
remain unqualified. The PR's separate surface-busy/wait change is excluded.

ANGLE Metal remains enabled by default for all apps loading this plugin, including Chrome, Chromium, Edge and
Electron. `NVMTL_ANGLE_METAL=0` skips the automatic workaround for an application. The user reconfirmed this
broader default on 2026-10-19.
Keep strict stencil detection, submission-copy caching, release `-O2`, OpenGL committed-resource accounting
and MoltenVK binding/sampler caches. WindowServer now uses upstream's conditional completion policy: wait until
scheduled when NVAccel accepts GPU marks, or until completed when those marks are unavailable. Surface page-off
still waits for pending GPU writes before CPU capture. Submit-ahead's benefit on this fork and the remaining
refresh-linked presentation ceiling need measurement.

NVRMFB keeps upstream's display-aware BAR1 allocation, scanout lifetime, modes, hotplug and event-driven flips.
The cursor worker retains the fork's kernel-priority ONCE serialization, cancellation/drain accounting and
stopping checks. Upstream's other framebuffer callbacks also use ONCE; this coalesces repeat entries for each
worker without imposing a global worker order. OpenGL refresh correction retains its private-layout and
timing guards. The retired experimental
small-BAR policies, scanout allocation fallbacks and borrowed-scanout extension remain removed.

Dock badge mip filling retains the fork's shared CPU/GPU format and size checks, atomic root tracking and
explicit mip-write detection, including views, resolves and storage bindings. The upstream A8 repair is separate
and is retained alongside it. The user reported working badge digits on the preceding fork; this combined build
still needs checking after installation.

## Building and testing the upstream 2.5.4 integration

The source merge alone does not update the running driver or `~/Downloads/pkgroot`. Use the official
**2.5.4 package** for its matching main RM, firmware, runtime, codecs, installer, licence texts and version metadata.
Do not combine the 615 framebuffer with the old 610 RM/firmware package. Main RM source remains
upstream's, except for the local source-path default in its build helper. The kernel still validates
firmware under `/Library/NullMoth/nvfw` and preserves the firmware-backup fallback.

Rebuild the release plugin and Tahoe NVAccel for the 2.5.4 changes, and rebuild NVRMFB for the
fork's display-rotation addition. The translator source is unchanged from the preceding fork;
reuse its fork output only after checking source and artifact identity. A full build of all
four components is also supported. Use OGKM 615.78.08 headers
(`~/ogkm615`, or set `OGKM` explicitly); the public checkout does not
include Darwin RM/KMS archives or compile metadata for a complete main RM link. Reuse the matching
official NVRM and runtime. The new main-RM parameter-table repair is not included by rebuilding the
accelerator alone. Native NVK/Mesa source is unchanged in this update.

The staging helper below replaces the framebuffer plist only. Include the rebuilt framebuffer
executable separately, preserve the official package's dependency/version plists, and regenerate both
checksum manifests. Keep the default flip verification (`debug.nvrmfb_flip_latch=1`) for the first
comparison; upstream's mode 2 defers the check to the next present and needs separate qualification.

The release plugin uses `-O2`; userland and Tahoe kernel builds target x86_64 with a macOS 15.0 minimum.
Compilation and source-retention records are identified by `out/upstream-merge-latest.json`.
This source merge does not stage or install a package. Compilation and focused CPU checks do not
qualify runtime/GPU behavior. After installation and reboot, check icons/Settings glyphs, fresh Dock previews/badges,
Terminal redraw, window dragging, OpenGL/MoltenVK, display modes and cursor/shutdown behavior.
Gamma/hotplug, temperature reporting and sleep/wake also need runtime coverage. Earlier measurements
in [OpenGL performance](docs/OPENGL-PERFORMANCE.md) apply to their original builds.

## Historical framebuffer-resource repair

NVRMFB was already upstream's framebuffer, but the fork's NVAccel still changed how framebuffer-owned memory was borrowed, retained and mapped into the GPU. That extension, including its extra class fields and `nvaccel_scanout_borrow_*` diagnostics, has been removed. NVAccel's remaining source difference from upstream 2.3.0 is the parked-backing repair before ordinary surface dirty updates. Retained VRAM parking itself is already present upstream and stays enabled.

This restoration followed a report that a 2070 Super, believed to have a 256 MB BAR1, boots official 2.1.0 but stays at verbose output with the fork. The restored 2.1.0-based build subsequently booted on the local RTX 2060 Super; that does not establish the result on the affected 2070 Super. The new 2.3.0 merge still needs its own boot test.

## Terminal redraw regression in 2.1.0

On stock 2.1.0, Terminal could keep displaying old text until its window was resized. Its logs reported `framebuffer fetch set could not be made (mask 0x7) - this draw is skipped`: the new fetch path requested colour input slots absent from the render pass and dropped the draw.

The 2.1.0-based fork disabled framebuffer fetch specifically in Apple's `Terminal` process. Upstream 2.3.0 now handles absent colour inputs and orders overlapping triangles, so the merged fork removes that application-specific disable and uses upstream's fetch policy, including Blender's lighting support and WindowServer's existing opt-in.

For an older affected driver, launch a separate Terminal instance with the workaround:

```bash
open -n -a /System/Applications/Utilities/Terminal.app --env NVMTL_NO_FBFETCH=1
```

On RTX 2060 Super / macOS Tahoe 26.7.1, this restored normal text updates without resizing. Disabling the new WindowServer completion-wait path did not clear the symptom; its setting was restored. This hardware result validates the process-local fetch workaround. The combined fork has since booted on this setup; other GPU and OS combinations still need validation.

The former workaround was in `NVMTLDriver`. Rebuild the plugin and start a fresh Terminal after installing the 2.3.0 merge to test the upstream repair. `NVMTL_NO_FBFETCH` is a process environment variable, not a boot argument or an `nvrm610.conf` setting.

## Stencil detection in 2.1.0

The new stencil-export emulation originally treated any shader structure member at byte offset 160 as its marker. Cached shaders from the running Tahoe desktop confirmed false matches in two WindowServer glass shaders and four Chromium shaders; none had a push-constant block. This could reset normal stencil references and replay draws that did not export stencil.

The plugin now follows the actual push-constant variable to its block and checks the translator's dedicated layout: one unsigned 32-bit member at offset 160. Ordinary uniform fields no longer activate emulation. The translator's stencil-export path remains supported. Hardware testing on RTX 2060 Super / Tahoe 26.7.1 confirmed that this change alone did not restore the missing Dock window previews.

## WindowServer completion policy in 2.3.7

The user reports working Dock previews with official 2.3.7. The fork's extra unconditional wait is therefore
removed in favor of upstream's conditional wait, while retaining pending-surface marks and capture/page-off
synchronization. `NVMTL_WS_WAIT_COMPLETION` still forces completion waiting; disabling GPU marks also uses the
fallback. The combined fork needs fresh-preview, screenshot and window-drag checks after installation.
The historical A/B result below established the workaround on 2.1.0 and does not establish its necessity on 2.3.7.

## Historical Dock window previews in 2.1.0

The 2.1.0 plugin changed WindowServer's `commitAndWaitUntilSubmitted` to return after scheduling when NVAccel accepted GPU completion marks. NVAccel waits for those marks before display flips, but that does not order offscreen snapshot consumers. Minimized windows could therefore animate correctly and then show an empty thumbnail with only the app badge.

On RTX 2060 Super / Tahoe 26.7.1, a live A/B check restored the previews with `debug.nvaccel_surface_gpu_on=0`, which makes the plugin wait for completion again. Restoring the value to `1` made a fresh window's preview invisible again. The same plugin stayed loaded throughout; no reboot or cache clearing separated these observations.

The 2.1.0-based fork restored GPU completion waiting in WindowServer's submit-and-wait call. Other asynchronous submissions retained the existing kernel swap marks. That workaround could reduce CPU/GPU overlap in WindowServer. It is superseded by the 2.3.7 policy above; submission copy caching, release `-O2` and parked-VRAM restoration remain enabled.

After installing and rebooting with the rebuilt plugin, fresh minimized-window previews worked with `debug.nvaccel_surface_gpu_on=1`. The user reported little perceived performance loss. A 20-second counter check recorded no additional completion timeouts (the count stayed at four from earlier in the boot). Comparative frame delivery has not been measured; this result covers RTX 2060 Super / Tahoe 26.7.1.

A temporary workaround on the stock 2.1.0 driver is:

```bash
sudo sysctl debug.nvaccel_surface_gpu_on=0
```

Restore and minimize the affected window to regenerate its thumbnail. This setting lasts for the current boot. Setting it back to `1` restores the upstream completion behavior.

## Components and package base

| Component | Purpose | Build output |
|---|---|---|
| `NVMTLDriver` | Retained icon rendering, PR #171 Vulkan features, PR #136 MSAA sample positions, and optional PR #147 surface scheduling | `out/plugin-<commit>-release/NVMTLDriver` |
| `libnvmtl_translate.dylib` | Retained Settings glyph fix and PR #171 shader translation repairs | `out/translator/x86_64-apple-darwin/release/libnvmtl_translate.dylib` |
| `NVAccel.kext` | Tahoe accelerator built from the same source as NVRMFB, keeping their VRAM interface compatible | `out/tahoe/NVAccel.kext` |
| `NVRMFB.kext/Contents/Info.plist` | PR #131: restrict the framebuffer claim to NVIDIA display-class PCI devices | `kexts/NVRMFB/fb/Info.plist` |

None of the four additional PRs changes the main `NVRM.kext` binary. PR #171 needs the plugin and translator rebuilt; PRs #136 and #147 need the plugin rebuilt. PR #131 changes framebuffer bundle metadata and requires reinstalling that bundle and refreshing the Auxiliary Kernel Collection, rather than recompiling its binary. The kexts in this Tahoe test workflow do not require code signing; signing would not grant kext loading approval.

Use the official **2.5.4 package** as the staging base for its matching RM, runtime, codecs, installer, licence texts and 615.78.08 firmware. Rebuild the fork plugin, Tahoe accelerator and rotation-capable framebuffer; retain the verified fork translator or rebuild it from the unchanged source. The commands below do not build the main RM, framebuffer binary or NVK. Build the framebuffer using the [rotation guide](docs/DISPLAY-ROTATION.md) and include its executable separately. Preserve the 2.5.4 package's dependency/version plists instead of replacing them with the source templates' `0.1` versions. The prebuilt runtime includes upstream's [NVK changes in 2.0.5](nvk/2.0.5/README.md).

PR #136 was adapted to preserve upstream's standard sample-position fallback when programmable positions are unavailable, while treating 1x rendering as a fixed center. Both upstream framebuffer plist copies retain the NVIDIA display-class claim and the `NVRMBootFB` personality.

PR #147 remains **off by default**. To test it in a particular application, launch that application's executable with `NVMTL_SCHEDULE_AFTER_SURFACE_WRITES=1` in its environment. It delays the scheduled notification for command buffers that write shared surfaces until completion. This is a process environment variable, not a boot argument or an `nvrm610.conf` setting.

Run the build commands in **macOS Terminal**. The plugin and translator can be built on a working Sequoia installation for use on Tahoe. The translator command below explicitly targets Intel macOS, including when the build machine uses Apple silicon. The earlier combined 2.1.0 package booted on RTX 2060 Super / Tahoe 26.7.1. Those results do not validate the newly merged 2.3.0 plugin and accelerator.

Safari and Messages Dock icons were reported missing their foregrounds with the official 1.14.0 package even after clearing the rendered-icon caches. The user confirmed that this fork's retained fixes resolve that issue on their RTX 2060 Super Hackintosh. That icon result applies to the earlier tested build; the combined 2.1.0 boot and Dock snapshot results are described above.

The merge keeps specialized-name linking, linked stages for pixel-sampler variants, swizzled sampled views and the buffer-offset repair for CoreImage resize helpers. It also keeps upstream's HDR storage-texture handling and newer stage-output repair. The fallback for missing NVIDIA Darwin build metadata remains in `build/accel_build.sh`.

## 1. Use the committed merge

The plugin build script requires a clean Git working tree. Use the merged commit in your macOS checkout and check its identity before building:

```bash
git log -1 --oneline
git status --short
```

If transferring through GitHub, the merge must first be pushed to the fork. Pulling before that still retrieves the previous published commit. The clone and pull commands below assume the merged commit is available there.

## 2. Prepare the macOS toolchain

Check that Apple's compiler and macOS SDK are available:

```bash
xcrun clang --version
xcrun --show-sdk-path
```

If they are missing, install the Command Line Tools and wait for installation to finish:

```bash
xcode-select --install
```

The PR #79 author tested the plugin with **Command Line Tools 16.4 / macOS 15.5 SDK**. NVAccel also requires the macOS SDK's kernel headers and libraries.

The translator requires **Rust 1.87 or newer**. Install it through [rustup](https://rustup.rs/) if needed, then check:

```bash
rustc --version
cargo --version
```

## 3. Prepare the checkout and Vulkan headers

`build/build_plugin.sh` expects the repository at `~/nvmtl-build/LIVE` and Vulkan headers at `~/Vulkan-Headers`.

For a fresh build checkout:

```bash
mkdir -p "$HOME/nvmtl-build"

git clone https://github.com/HighDelay/nvidia-macos-driver.git \
  "$HOME/nvmtl-build/LIVE"

git clone --depth 1 https://github.com/KhronosGroup/Vulkan-Headers.git \
  "$HOME/Vulkan-Headers"

cd "$HOME/nvmtl-build/LIVE"
```

If either checkout already exists, skip its clone command. To update an existing driver checkout:

```bash
cd "$HOME/nvmtl-build/LIVE"
git pull --ff-only
```

Exclude generated output locally so it does not cause the next plugin build to reject the working tree:

```bash
printf '\n/out/\n' >> .git/info/exclude
git status --short
```

`git status --short` should print nothing before building. Commit any intended source changes first.

## 4. Build the icon-fix plugin

From the repository root:

```bash
cd "$HOME/nvmtl-build/LIVE"
RELEASE=1 bash build/build_plugin.sh
```

A successful build prints `markers-missing 0` and the path to the executable. Verify the output in the same checkout:

```bash
plugin_out="$HOME/nvmtl-build/LIVE/out/plugin-$(git rev-parse --short HEAD)-release"

file "$plugin_out/NVMTLDriver"
```

Expect an **x86_64 Mach-O bundle**. This produces the plugin executable, rather than an entire `.bundle` directory or installer.

If compilation fails, inspect the full compiler output:

```bash
cat "$plugin_out/NVMTLDriver.err"
```

PRs #79, #136, #147 and #171 affect the plugin; PRs #112 and #171 affect the translator. Build both for this verification. These PRs do not themselves change Mesa, but current upstream main includes an NVK connection-pool repair beyond the released 1.14.7 runtime.

## 5. Build the System Settings glyph-fix translator

PR #112 changes the Rust translator under `translator/translator/`. Build its C-compatible wrapper, which produces the dynamic library loaded by the plugin:

```bash
cd "$HOME/nvmtl-build/LIVE"
rustup target add x86_64-apple-darwin

cargo build --release \
  --manifest-path translator/wrapper/Cargo.toml \
  --target x86_64-apple-darwin \
  --target-dir out/translator
```

This direct Cargo command uses the translator in this checkout. The repository's `build/build_xlate.sh` defaults to a separate `~/nvmtl-build/bindless` tree and also expects release-check files outside this public checkout, so use the command above for this guide.

Set the library's install name and inspect the output:

```bash
translator_out="$HOME/nvmtl-build/LIVE/out/translator/x86_64-apple-darwin/release/libnvmtl_translate.dylib"

install_name_tool -id @rpath/libnvmtl_translate.dylib "$translator_out"
codesign --force --sign - "$translator_out"
file "$translator_out"
```

Expect an **x86_64 Mach-O dynamic library**. WindowServer requires an ad-hoc signature on this userland library. Sign it after setting its install name; the staging helper preserves the finalized bytes. The build uses the Rust sources directly; Apple's private AIR fixtures are not required to build the release library. The full translator test suite does reference unpublished `validation/fixtures`, so a missing-fixture test failure is distinct from a release-library build failure.

## 6. Build NVAccel.kext for Tahoe

NVAccel requires NVIDIA's **open-gpu-kernel-modules 620.70.06** source headers. The default location is `~/ogkm620`. If that checkout is not already present:

```bash
git clone --depth 1 --branch 620.70.06 \
  https://github.com/NVIDIA/open-gpu-kernel-modules.git \
  "$HOME/ogkm620"
```

Build from the driver repository root:

```bash
cd "$HOME/nvmtl-build/LIVE"
NM_TAHOE=1 bash build/accel_build.sh kexts/NVRM out/tahoe
```

If the NVIDIA source tree is elsewhere, specify it explicitly:

```bash
OGKM="/path/to/open-gpu-kernel-modules" \
NM_TAHOE=1 bash build/accel_build.sh kexts/NVRM out/tahoe
```

This fork's build script tolerates the absence of:

```text
src/nvidia/_out/Darwin_x86_64/compile_cmds.sh
```

With a fresh NVIDIA checkout it warns that it is compiling without NVIDIA's Darwin RM defines. The metadata fallback still requires the NVIDIA headers; it does not establish runtime compatibility by itself.

The result is:

```text
out/tahoe/NVAccel.kext
```

Check the executable's architecture:

```bash
file out/tahoe/NVAccel.kext/Contents/MacOS/NVAccel
```

It should report an x86_64 Mach-O binary. Compiler and linker diagnostics are saved separately:

```bash
cat out/tahoe/accel.err
cat out/tahoe/accelld.err
```

To build both Sequoia and Tahoe accelerators from this same checkout, upstream 1.14 adds:

```bash
bash build/accel_build_all.sh kexts/NVRM out/accel
```

This produces `out/accel/15/NVAccel.kext` and `out/accel/26/NVAccel.kext`. It calls this fork's `accel_build.sh`, including the metadata fallback. The script clears each OS-specific output directory before building; use a directory reserved for generated output.

## 7. Stage the Tahoe PR outputs in an upstream package

Extract the official 2.5.4 prebuilt driver archive into a separate directory. Use its unmodified `pkgroot` as the base; the helper verifies its existing `SHA256SUMS` before copying anything. Run on macOS with Python 3 and Apple's command line tools installed:

```bash
cd "$HOME/nvmtl-build/LIVE"
python3 build/stage_package.py "/path/to/upstream/pkgroot" \
  "$HOME/nvmtl-build/tahoe-package/pkgroot" \
  --framebuffer-plist "/path/to/upstream/pkgroot/Library/Extensions/NVRMFB.kext/Contents/Info.plist"
```

The output directory must be new and outside the input package. The helper creates its parent directories and publishes the output only after all copies and checksum checks succeed. The upstream package and original build outputs remain intact.

By default, this stages the Tahoe PR changes using these exact paths in the checkout:

| Built artifact | Destination relative to the output `pkgroot` |
|---|---|
| `out/plugin-<current-commit>-release/NVMTLDriver` | `Library/GPUBundles/NVMTLDriver.bundle/Contents/MacOS/NVMTLDriver` |
| `out/translator/x86_64-apple-darwin/release/libnvmtl_translate.dylib` | `Library/GPUBundles/nvmtl/libnvmtl_translate.dylib` and the plugin's separate translator copy, if present |
| `kexts/NVRMFB/fb/Info.plist` | `Library/Extensions/NVRMFB.kext/Contents/Info.plist` |

The loader prefers a translator beside the plugin executable. The helper updates that separate copy, or preserves its relative symlink to the canonical `nvmtl` translator, so an older copy cannot take precedence. The helper retains plugin resources, NVRM/NVRMAGDC, NVK, firmware and the Sequoia accelerator from the base package. By default it keeps the framebuffer executable too. For display rotation, pass `--framebuffer` with the complete rebuilt NVRMFB.kext so its executable and matching plist are copied together; the helper removes the older bundle and regenerates all affected checksums.

The four historical PRs do not themselves require rebuilding NVAccel. Testing the current fork uses the matching 2.5.4 RM, rebuilt framebuffer and Tahoe accelerator with upstream's parked-backing repair and plane bounds, plus the fork's rotation path. Preserve the package version in the accelerator's plist, then include it explicitly:

```bash
python3 build/stage_package.py "/path/to/upstream/pkgroot" \
  "$HOME/nvmtl-build/tahoe-package/pkgroot" \
  --framebuffer out/rotation-fb/NVRMFB.kext \
  --accel-26 out/tahoe/NVAccel.kext
```

This replaces `Library/NullMoth/kexts/26/NVAccel.kext`, mirrors it into `Library/Extensions/NVAccel.kext` when that active copy exists, and refreshes the nested `SHA256SUMS`. With `accel_build_all.sh`, the corresponding input is `out/accel/26/NVAccel.kext`. Both prebuilt OS variants must be present because upstream's installer requires them; this helper updates only the Tahoe accelerator when requested.

For outputs in other locations, use `--plugin /path/to/NVMTLDriver`, `--translator /path/to/libnvmtl_translate.dylib`, `--framebuffer /path/to/NVRMFB.kext`, and optionally `--framebuffer-plist /path/to/Info.plist`. The complete framebuffer supplies its own matching plist unless explicitly overridden. The helper checks that replacement binaries contain x86_64 code. It copies finalized binaries byte-for-byte and performs no signing or install-name edits. Complete the translator build steps in section 5 first.

The package's upstream `install.sh`, `uninstall.sh`, supporting tools, and `VERSION` are preserved. The helper makes the two scripts executable, regenerates the root `SHA256SUMS` over the final package, and verifies every generated checksum. These are packaging checks; they do not establish compatibility between a newer source build and an older prebuilt framebuffer or NVK. This stages the Tahoe PR outputs, not every component changed by the upstream merge described at the top of this guide.

This workflow uses the SIP and Secure Boot settings in [README.md](README.md#wiring-into-an-existing-opencore-setup). [Apple documents unsigned development kexts](https://developer.apple.com/library/archive/documentation/Security/Conceptual/System_Integrity_Protection_Guide/KernelExtensions/KernelExtensions.html) with the relevant SIP restrictions disabled. A correct package checksum is separate from kernel approval and loading.

Install the resulting package **while running Tahoe**:

```bash
cd "$HOME/nvmtl-build/tahoe-package/pkgroot"
shasum -a 256 -c SHA256SUMS --quiet
sudo bash ./install.sh
```

After the installer reports success, reboot. It selects the Tahoe accelerator and rebuilds the Auxiliary Kernel Collection. Verify after reboot:

```bash
kmutil showloaded --list-only | grep nullmoth
```

Expect `NVRM`, `NVRMFB`, `NVAccel`, and `NVRMAGDC`. The preserved uninstaller is available as `sudo bash ./uninstall.sh` from the same package directory. Use these tools on the installed OS, not in Recovery against another mounted volume.

For distribution, create the archive outside `pkgroot`:

```bash
tar -czf "$HOME/nvmtl-build/nullmoth-tahoe-custom.tar.gz" \
  -C "$HOME/nvmtl-build/tahoe-package" pkgroot
```

The plugin and translator builds retain the icon and glyph fixes; building the accelerators together keeps the framebuffer interface consistent. The plugin keys its shader cache by translator content, so a changed library gets a new shader-cache identity after the process restarts. An identical translator binary does not invalidate cached images.

After installing, verify that the active files match the outputs you built. Each pair below should have the same SHA-256:

```bash
shasum -a 256 "$plugin_out/NVMTLDriver" \
  /Library/GPUBundles/NVMTLDriver.bundle/Contents/MacOS/NVMTLDriver
shasum -a 256 "$translator_out" \
  /Library/GPUBundles/nvmtl/libnvmtl_translate.dylib

bundled_translator=/Library/GPUBundles/NVMTLDriver.bundle/Contents/MacOS/libnvmtl_translate.dylib
if [ -e "$bundled_translator" ]; then
  shasum -a 256 "$translator_out" "$bundled_translator"
fi
```

For comparison, the plugin extracted from the official 1.14.0 package during this audit had SHA-256 `97dec4756bbf404dde6532e949237de9bafc8835e0640503b933001c753bb85b`. Its bundled translator had SHA-256 `808c76cfe0ab76cc28c5542b59ecdf9b49ad92e01741da66af2b8080baeec1ba`. These identify the audited binaries, not a known-good rendering result.

After installing and rebooting, quit and reopen System Settings. If old rendered icons remain, run this as the desktop user to back up the IconServices and Dock caches:

```bash
cache_stamp="$(date +%Y%m%d-%H%M%S)"
user_cache="$(getconf DARWIN_USER_CACHE_DIR)"
if [ -n "$user_cache" ] && [ -d "$user_cache" ]; then
  for cache_name in com.apple.iconservices com.apple.dock.iconcache; do
    icon_cache="$user_cache/$cache_name"
    if [ -e "$icon_cache" ]; then
      mv "$icon_cache" "$icon_cache.bak-$cache_stamp"
    fi
  done
fi
system_icon_cache="/Library/Caches/com.apple.iconservices.store"
if [ -e "$system_icon_cache" ]; then
  sudo mv "$system_icon_cache" "$system_icon_cache.bak-$cache_stamp"
fi
```

Reboot after moving the caches so IconServices and the desktop regenerate their images with the new translator. The first redraw can take longer. These rendered-icon caches are separate from the translator's shader cache.

The official-package regression persisted after this cache reset, while the user confirmed the fork's retained fixes work. If it recurs with a later build, retain the installed component hashes and the OS build number with the reproduction to distinguish that build from the known-working fork.

PR #112 reports restored System Settings sidebar glyphs on RTX 5070 Ti / macOS 26.6. Ghostty's incorrectly sized foreground remains outside these fixes. These patches are not a claim of complete Tahoe support.

## References

- [Upstream 1.15.0 snapshot merged into this fork](https://github.com/nullmoth/nvidia-macos-driver/commit/56bc4f9ae3105df052eefd3ea094c782e56853c3).
- [Upstream 1.14.7 release](https://github.com/nullmoth/nvidia-macos-driver/releases/tag/v1.14.7).
- [PR #171: WoW Classic shader translation and Vulkan feature fixes](https://github.com/nullmoth/nvidia-macos-driver/pull/171) — revision `47e3e08192d36e6aef67cf433adb24c4dce9eddf`.
- [PR #136: MSAA without programmable 1x sample positions](https://github.com/nullmoth/nvidia-macos-driver/pull/136) — revision `a4d77592b2bca750a346f20475dce462cf120bd1`, adapted to retain upstream's default-position fallback.
- [PR #147: optional scheduled notification after surface writes](https://github.com/nullmoth/nvidia-macos-driver/pull/147) — revision `648df70fe27f302634045611ad5042fdc88764bc`.
- [PR #131: NVIDIA-only framebuffer claim](https://github.com/nullmoth/nvidia-macos-driver/pull/131) — revision `1f10026e780c104b90a3aa011ac93c2208bbd439`, with its plist-equality test adapted.
- [Upstream 1.14.2 release](https://github.com/nullmoth/nvidia-macos-driver/releases/tag/v1.14.2).
- [Upstream 1.14.1 release](https://github.com/nullmoth/nvidia-macos-driver/releases/tag/v1.14.1).
- [Upstream 1.14.0 release](https://github.com/nullmoth/nvidia-macos-driver/releases/tag/v1.14.0).
- [Build both accelerators from the same source](https://github.com/nullmoth/nvidia-macos-driver/commit/8c9429c8984f5c8f0c18fb9ffafbf81b299cd14d).
- [PR #79: Tahoe icon rendering fixes](https://github.com/nullmoth/nvidia-macos-driver/pull/79) — the patch applied here is revision `74c0dc30ab8e58139707fb603948329a1cede67f`.
- [PR #112: CoreImage resize and System Settings glyph fix](https://github.com/nullmoth/nvidia-macos-driver/pull/112) — the patch applied here is revision `a6c38cd52ee23e335e08af89824b72aca4f9fcdb`.
- [Issue #51: Tahoe NVAccel VRAM ABI mismatch](https://github.com/nullmoth/nvidia-macos-driver/issues/51).
- [Test build 2: matching 1.13.0 components and hardware results](https://github.com/modesttao/nvidia-macos-driver/releases/tag/icons-test2).
- [Apple: installing command-line developer tools](https://developer.apple.com/library/archive/technotes/tn2339/_index.html).
