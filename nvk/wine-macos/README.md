# Native NVK runtime for Wine and DXVK

[User setup: Wine, CrossOver and YAAGL](INSTALL.md). This page covers source,
packaging and validation. The launcher lives in [HighDelay/yaagl-nvk](https://github.com/HighDelay/yaagl-nvk)
and the repaired DXVK pair in [HighDelay/dxvk-nvk](https://github.com/HighDelay/dxvk-nvk/tree/nvk-3.1.1).

This package lets Windows D3D11 applications use the fork's native NVK renderer
through Wine and DXVK. The modified YAAGL launcher selects the package and keeps
its Wine prefix separate from the launcher's other backends.

```text
Windows D3D11 game → DXVK → Wine Vulkan → native NVK → NVIDIA GPU
```

DXVK still translates D3D11 and shaders to Vulkan/SPIR-V, and Wine still supplies
Windows compatibility. This route bypasses MoltenVK's Vulkan-to-Metal translation
and NVMTL's Metal/AIR-to-SPIR-V translation for game rendering. Metal supplies the
macOS display drawable. A GPU copy transfers completed frames into its IOSurface;
this is not zero-copy. Normal presentation does not read or upload pixels on the
CPU, but currently waits on the CPU for each presentation copy to finish.

## Requirements and current status

- Intel x86_64 macOS, with a compatible installed NVIDIA driver and matching NVK
  IOSurface import protocol. The bundled renderer targets macOS 15.5 or newer.
- The compact provider and a compatible external Wine installation. YAAGL uses
  the installed driver provider and downloads the pinned Wine build on setup.
  The full-bundle developer builder below remains available for offline probes.
- D3D11, with optional D3D12/DLSS in the 2.6.4 Wine/NVK provider or newer.
  Older D3D11-only providers reject requests for the newer features.

On RTX 2060 SUPER / Tahoe 26.7.1, real Windows Vulkan GPU readback and window
presentation, two-window presentation, and a textured D3D11/DXVK probe passed.
The D3D11 probe completed 300 presents, 600 textured draws, depth/blend checks,
11 staging readbacks and a resize. ZZZ and Honkai: Star Rail Global D3D11
gameplay through YAAGL Wine 11.0-1 are user-confirmed, including characters,
scenery, UI and the FPS overlay. HSR 4.6.0 was confirmed with Steam Patch and
the timeout fix off, using its Jadeite launch route with the required `--`
separator. On 2026-10-31, Genshin Impact startup and in-world rendering were also
user-confirmed on an RTX 4060 Laptop GPU through native NVK/Mesa 26.3.0-devel,
with **Steam Patch and Timeout fix enabled**. The supplied photo shows characters,
scenery and UI. Its OS/Wine versions were not supplied. CN gameplay remains
unqualified.
No controlled FPS comparison has been made.
Physical Sequoia testing of Wine, commercial CrossOver releases and other configurations
remain unqualified.

## D3D12 and DLSS

D3D12 uses `D3D12 → vkd3d-proton → Wine Vulkan → native NVK → NVIDIA GPU`,
sharing the matched DXVK DXGI component. The [vkd3d-nvk fork](https://github.com/HighDelay/vkd3d-nvk)
packages unmodified upstream 3.1 DLLs with their exact source/archive identity.
The macOS changes are in this Wine frontend, provider packaging and prefix
helper. D3D11 remains the default.

The local candidate passed D3D12 device creation, DXIL compute/vertex/pixel
shaders, a 1,024-word readback, fences, 120 presentations, rendered-pixel checks
and resize. The user confirmed ZZZ world, character and UI rendering on RTX
2060 SUPER / Tahoe 26.7.1 / YAAGL Wine 11.0-1. D3D11 regression probes passed.

Optional DLSS support supplies the unmodified NVIDIA Wine NGX pair and
DXVK-NVAPI. The game supplies its original `nvngx_dlss.dll`. A real DLSS SR
probe evaluated three 320×180 → 640×360 frames and checked 4,680 colour values
with no mismatches, including a Wine child-process launch. The user confirmed
ZZZ DLSS Quality and the native HUD. DLSSG is not blanket-blocked: the game and
libraries keep their GPU checks. Frame generation, ray tracing, other GPUs,
commercial CrossOver and sustained sessions remain unqualified. No speedup
is claimed.

The D3D12 HUD is a 224×44 cached bitmap copied by the GPU. A small CPU upload
refreshes it up to four times per second; no game-frame CPU readback is added.
It reports presentation FPS and average ms/frame, including application stalls,
not GPU execution time. Full drawable pixel checks passed across 45 frames,
resize and restore. Minecraft's independent frontend is unchanged.

The **2.6.4 Wine/NVK provider and newer** include these components.
[Follow the D3D12/DLSS setup guide](INSTALL.md#d3d12-and-dlss) after installing a
matching driver. YAAGL's separate D3D12 and DLSS settings default off; its HUD
setting defaults on. Enable YAAGL's Steam Patch and Timeout fix for ZZZ and GI;
keep both off for the verified HSR route.

## Setup with YAAGL

1. Install the driver runtime, extract the YAAGL launcher package and use its Start command.
2. Open the supplied launcher and select **Settings → Wine → NVIDIA NVK + DXVK
   (local, experimental)**.
3. Let the launcher download/prep its private Wine and initialize its dedicated
   `wineprefix-nvk` prefix. It validates the installed runtime and installs its native x64
   `d3d11.dll` / `dxgi.dll` pair with a rollback journal.
4. Download the chosen game and launch through YAAGL's normal preparation flow.
   D3D11 is the default; ZZZ can explicitly select the D3D12 route described above.
5. Check the DXVK HUD and game launch logs. The underlying Vulkan device must
   identify NVIDIA/NVK, and loaded-image logs must show the installed driver's
   `libnvk_wine.dylib` and `libnvk_renderer.dylib`.

The launcher sets `WINEDLLOVERRIDES=d3d11,dxgi=n` while preserving unrelated
overrides. It refuses conflicting DLLs in the game directory instead of replacing
them. ReShade is not enabled for this backend. Runtime validation, cache paths and
graphics settings are local to the launched processes.

The tested backend also defaults to `dxvk.enableDescriptorBuffer = False` and
logs `Binding model: Legacy`. The descriptor-buffer path rendered ZZZ scenery
but lost characters and HUD text; switching to descriptor sets restored both.
The FPS/frame-time/device HUD is enabled by default. ZZZ and HSR Global gameplay
and GI in-game rendering are user-confirmed; CN gameplay and sustained
stability/performance remain unqualified. GI's packaged defaults still need both
Steam Patch and Timeout fix enabled manually after applying recommended settings.

DXVK may report an AMD PCI vendor to D3D11 applications as its normal NVIDIA/NVAPI
workaround. Check its Vulkan device/driver log and actual loaded libraries rather
than using that emulated vendor alone to identify the rendering GPU.

## Package and build

The source is in this driver repository. YAAGL integration is in
[YAAGL NVK](https://github.com/HighDelay/yaagl-nvk). DXVK and Wine
source are pinned as dependencies. Wine's binary code is unchanged. DXVK 3.1.1
needs the local shared-resource fallback repair below for ZZZ startup. Compiled
runtime archives belong in release assets.

YAAGL release archives contain neither Wine nor NVK/DXVK. They require the
driver's installed provider and download only the pinned Wine build on setup.
Updating the driver's matched pair and manifests updates the pair YAAGL installs;
there is no second DXVK release pin in the launcher.

The build needs Xcode command line tools, Python 3.12 or newer, Vulkan headers,
the matching private NVK renderer, the exact vendor archives below, and their
license/source records. Use an output directory that does not already exist.

```sh
python3 nvk/wine-macos/package.py \
  --output /absolute/new/nvk-wine-runtime \
  --renderer /absolute/minecraft-runtime/lib/libnvk_renderer.dylib \
  --headers /absolute/Vulkan-Headers/include \
  --wine-archive /absolute/wine-crossover-11.0-1-osx64-signed.tar.xz \
  --dxvk-archive /absolute/dxvk-3.1.1.tar.gz \
  --dxvk-source /absolute/dxvk-v3.1.1 \
  --mesa-license /absolute/Mesa-license.rst \
  --wine-cert-fragment /absolute/yaagl-wine-cert.inf.fragment
```

For YAAGL, the last argument is the existing launcher's Wine INF certificate
fragment, applied only to the private Wine package/prefix. Preserve its YAAGL
revision and hash. It does not install macOS certificates or modify `/etc/hosts`.
The frontend alone can be built with `build.py --output ... --renderer ...
--headers ...`; that output does not include Wine, DXVK or the setup helper.

| Dependency | Pinned identity |
|---|---|
| Wine archive | YAAGL `wine-crossover-11.0-1-signed`, SHA-256 `89fa7e90fb626523a90d5867a03c6be785d017176739c6320a3b86c7838c3a35` |
| DXVK | v3.1.1, commit `b1a1c99ab52b687cf950d62c88bc2fa316b41663` |
| DXVK archive | SHA-256 `40565b4a724aadc4433fa4e010b4b23916d9b1f1baeee64e17186db94f54e608` |
| YAAGL NVK integration | `eb42ac6f596b3b3686f3f8b58b9c46ed56937f01`, on base `4543cc5c5a66e8d1a702111fb095682019948fcb` |
| Repaired DXVK fork | `e1c9938a60e1fbdb89a265e1087b2395ca17933e`, branch `nvk-3.1.1` |

The Wine binary release does not identify its exact CrossOver patch revision.
Its archive hash and reference source are recorded; a fully reproducible Wine
source build remains unresolved. Keep that limitation explicit when preparing a
public bundle and provide the matching dependency source/license records.

The two private Wine loaders are re-signed with
`com.apple.security.get-task-allow` to satisfy their task-port exchange on the
tested Tahoe host. SDK retagging did not fix that exchange and is not used.
No boot arguments, global loader selection or installed CrossOver files change.

### Compact runtime for driver packages

After building the repaired full bundle below, extract its matched Vulkan/DXVK
libraries and rebuild the setup helper for use with an external Wine installation:

```sh
python3 nvk/wine-macos/provider.py \
  --base /absolute/repaired-runtime --output /absolute/new/wine-provider
```

To include the optional D3D12 component, add both
`--vkd3d-archive /absolute/vkd3d-proton-3.1.tar.zst` and
`--vkd3d-source /absolute/vkd3d-proton-3.1-source`. Reading the `.zst` archive
requires Python 3.14+ with Zstandard support. The builder checks upstream commit
`4522cc1a8b1347f1ab1b0f7b14a23ed9444d95d1` and release archive SHA-256
`b85ea8219b71093a3924913dd7aae6172333dc0c069983ed510816d67f02120b`.
It preserves the existing DXVK pair and adds matched `vkd3d/x64/d3d12.dll` and
`d3d12core.dll`, license notices and `VKD3D-BUILD.json`. It can also rebuild a
validated compact provider without a bundled Wine installation.

`nvk-runtime install` continues to install only D3D11/DXGI. The explicit
`install-d3d12` command installs both graphics pairs, preserving independent
original-file journals. `restore` checks both journals before restoring either
pair. YAAGL's experimental ZZZ toggle requires a provider that reports
`supports_d3d12: true`; a requested D3D12 launch on an older provider is refused.

This output contains no Wine installation or prefix. It carries the frontend,
private renderer, `libMoltenVK.dylib` alias, paired DXVK DLLs, setup helper,
licenses, source records, manifest and checksums. Use it with the existing package
stager's **`--wine-runtime /absolute/new/wine-provider`** option, alongside the
usual plugin/translator/kext and optional Minecraft arguments. The stager validates
it before and after copying to `Library/GPUBundles/nvmtl/wine`, and includes
`WINE-NVK.md` in the package. The existing installer/uninstaller manages it through
the ordinary nvmtl directory copy/removal; no system-wide provider selection changes.

`provider.py` copies the tested native libraries and DXVK DLLs byte-for-byte;
it does not rebuild NVK or qualify a different Wine/CrossOver version. Run the
setup fixtures on its rebuilt helper and retain a coherent renderer/driver pair.
Compiled bundles remain release assets rather than checked-in binaries.

## Integrity, logs and rollback

### DXVK shared-resource fallback repair

The stock DXVK 3.1.1 DLLs crash on the tested ZZZ startup when shared memory is
unavailable: a rejected sharing request still propagates its handle type into the
allocator, which calls the unavailable `vkGetMemoryWin32HandleKHR`. The local
patch keeps the allocation's default no-sharing handle type unless the image was
actually made shareable. It also refuses optional shared semaphore/keyed-mutex
creation when Win32 semaphore sharing is unavailable. Ordinary texture rendering
and DXVK's local-storage fallback remain available; external handles are not faked.

Apply [the DXVK patch](patches/dxvk-3.1.1-shared-fallback.patch) to the pinned DXVK
v3.1.1 checkout. Build its x64 `d3d11.dll` and `dxgi.dll` together with Meson/Ninja,
an x64 MinGW cross compiler, glslang and the pinned recursive DXVK submodules.
The local test used llvm-mingw 20261020, glslang 16.6.0, Meson 1.12.1 and Ninja
1.13.2, release optimization with debug symbols retained in the build directory.

```sh
git -C /absolute/dxvk-v3.1.1 apply /absolute/driver/nvk/wine-macos/patches/dxvk-3.1.1-shared-fallback.patch
meson setup /absolute/dxvk-build /absolute/dxvk-v3.1.1 \
  --cross-file /absolute/win64-cross-file.txt --buildtype release -Ddebug=true \
  -Denable_d3d8=false -Denable_d3d9=false -Denable_d3d10=false
ninja -C /absolute/dxvk-build
python3 nvk/wine-macos/replace-dxvk.py \
  --base /absolute/base-runtime --output /absolute/new/repaired-runtime \
  --dxvk-source /absolute/dxvk-v3.1.1 --dxvk-build /absolute/dxvk-build \
  --strip /absolute/llvm-mingw/bin/llvm-strip
```

The last command creates a new runtime, retains the base, strips only delivered
DLL debug sections, records source/submodules/compiler/options in `DXVK-BUILD.json`
and updates its runtime identity and checksums. Close the game and restore the
old prefix DLL journal before installing a different runtime identity.

The original DLLs reproduce an execute-at-null crash on the first shared texture.
The repaired pair passes eight GPU readbacks across feature levels 11.0/11.1 and
ordinary/shared/keyed/NT texture variants, while handle export remains refused.
The normal 300-present/600-draw test also passes. ZZZ then reached its menu/login
after enabling YAAGL's existing Steam Patch and timeout-fix options with the same
Wine 11.0-1 build. A preceding second crash was in `MHYPBase.dll`, separate from
the DXVK fault. ZZZ gameplay later rendered correctly with descriptor sets;
sustained stability and a performance comparison remain unqualified.

The patch changes DXVK code covered by its zlib license; preserve the DXVK license
and attribution when distributing it. The same repair is published on the
`nvk-3.1.1` branch of [HighDelay/dxvk-nvk](https://github.com/HighDelay/dxvk-nvk).
Use either that commit or the patch on v3.1.1, not both.

`nvk-wine-runtime.json` records the package identity and critical file hashes;
`BUILD.json` records source and build provenance; `SHA256SUMS` covers delivered
regular files. `LICENSES/` contains the component licenses. The native helper
checks hashes, architectures, signatures and the private provider alias:

```sh
/absolute/runtime/bin/nvk-runtime check /absolute/runtime
```

The Wine library lookup uses a scoped `DYLD_LIBRARY_PATH` and a package-local
`libMoltenVK.dylib` alias to `libnvk_wine.dylib`. Logs verify the actual library
loaded by the Windows process. The alias does not invoke the MoltenVK renderer.
General external-memory/semaphore sharing, HDR and unsupported WSI extensions
are not advertised. Private staged IOSurface import remains available internally.

Each game launch records `runtime.json` and DXVK logs beside the normal game log
in a `.nvk` directory. Startup library logs identify the provider; per-call Vulkan
tracing is confined to diagnostic probes. Prefixes, game data and account state
are excluded from release archives.

Close the game before changing runtime packages. Select another Wine distribution
to use its ordinary prefix again. To restore the two original DLLs in the NVK
prefix, run:

```sh
/absolute/runtime/bin/nvk-runtime restore /absolute/runtime /absolute/wineprefix-nvk
```

Rollback validates both originals before changing either file. It refuses to
overwrite unexpected user modifications. It does not require a valid runtime
manifest, reinstalling the driver, changing NVRAM or removing Minecraft's runtime.

Probe sources and rebuild commands are in [tools/wine-nvk](../../tools/wine-nvk/README.md).

## Packaging the optional components

Keep `/Library/GPUBundles/nvmtl/wine` as the single provider, with separate
`dxvk/x64`, `vkd3d/x64` and `dlss/x64` groups. YAAGL does not bundle or separately
download these libraries. Provider construction accepts an existing compact
provider and a rebuilt Wine frontend using the exact same NVK renderer:

```sh
python3 nvk/wine-macos/provider.py --base /absolute/base-provider \
  --frontend /absolute/wine-frontend-build --output /absolute/new-provider \
  --vkd3d-archive /absolute/vkd3d-proton-3.1.tar.zst \
  --vkd3d-source /absolute/clean-vkd3d-proton-3.1 \
  --nvapi-archive /absolute/dxvk-nvapi-v0.9.2.tar.gz \
  --nvapi-source /absolute/clean-dxvk-nvapi-0.9.2 \
  --ngx-archive /absolute/nvidia_driver-linux-x86_64-610.57.04-archive.tar.xz
```

All archives, revisions and individual NGX/NVAPI DLL hashes are pinned in
`provider.py`. The NVIDIA archive is downloaded from NVIDIA's redistribution
server; only its unmodified Wine DLL pair and full driver license are extracted.
DXVK-NVAPI's MIT license, vkd3d's LGPL/license/AUTHORS records and separate
`VKD3D-BUILD.json`, `DLSS-BUILD.json`, `FRONTEND-BUILD.json` are included and
covered by provider checksums. Preserve those records when updating dependencies.
The game's DLSS feature library is not redistributed by this package.

The compiled registry-aware helper passes `tools/wine-nvk/test_setup.py` and
`test_dlss_setup.py`: 19 filesystem/setup checks and three real Wine-prefix
scenarios cover absent, string and DWORD registry originals, repeat installs,
tamper refusal and independent NGX/graphics rollback. The built provider also
passes D3D11/D3D12 rendering and real DLSS evaluation on RTX 2060 SUPER/Tahoe
26.7.1. These checks use disposable prefixes; commercial CrossOver and Sequoia
hardware remain unqualified. No kernel source changed in this integration.
