# Wine / CrossOver native NVK

This runtime lets compatible x64 Wine builds send Vulkan directly to this
driver's native NVK renderer. For D3D11 games, the included DXVK DLLs translate
D3D11 to Vulkan first:

| Provider | Game rendering path |
|---|---|
| DXVK + MoltenVK | D3D11 → DXVK → Vulkan → MoltenVK → Metal → NVMTL → NVK → NVIDIA GPU |
| DXVK + native NVK | D3D11 → DXVK → Wine's Vulkan bridge → native NVK → NVIDIA GPU |

It removes MoltenVK's Vulkan-to-Metal conversion and NVMTL's Metal/AIR-to-SPIR-V
translation from game rendering. Wine, DXVK and NVK shader compilation remain.
Finished frames are copied on the GPU into a Metal drawable for macOS presentation;
this is not zero-copy, but it does not read back/upload pixels through the CPU.
Fewer translation stages can reduce conversion overhead. A controlled FPS gain
over MoltenVK has **not** been measured.

## Requirements

- This fork's driver and a matching Wine/NVK runtime, on Intel macOS **15.5+** or
  **Tahoe 26**, with a supported NVIDIA GPU.
- An **x64 Windows D3D11 or D3D12** game and a compatible macOS Wine Vulkan
  bridge. D3D12/DLSS require the matching new provider. 32-bit games, HDR and
  cross-process external GPU-handle sharing are not supported by this integration.
- The matched, repaired DXVK **3.1.1** `d3d11.dll` / `dxgi.dll` pair supplied with
  the runtime. Stock 3.1.1 can crash when a game requests an unsupported shared texture.

**Verified:** ZZZ and Honkai: Star Rail Global D3D11 gameplay on RTX 2060 SUPER /
Tahoe 26.7.1 using YAAGL's Wine 11.0-1 build, including characters, scenery,
UI and FPS overlay. Windows Vulkan readback/presentation and D3D11
depth/blend/shared-texture probes also pass.
Genshin Impact startup and in-world rendering are also user-confirmed on an
RTX 4060 Laptop GPU with **Steam Patch and Timeout fix enabled**; the screenshot
shows native NVK/Mesa 26.3.0-devel, characters, scenery and UI.
CN gameplay, commercial CrossOver releases, other Wine builds
and physical Sequoia hardware have **not** been qualified.

**D3D12/DLSS require the 2.6.4 Wine/NVK provider or newer.** Older D3D11-only
providers cannot use these features. ZZZ D3D12 and DLSS
Quality are confirmed on RTX 2060 SUPER / Tahoe 26.7.1;
the instructions below continue to use the verified D3D11 route.

## Setup with YAAGL

1. Install the matching driver and extract [YAAGL NVK](https://github.com/HighDelay/yaagl-nvk/releases).
2. Open the game's supplied Start command; use the NVIDIA NVK backend.
3. For ZZZ, Settings → Game exposes **D3D12 (vkd3d-proton)** and **DLSS support**
   separately. Both default off. Enabling DLSS requires D3D12 and an RTX GPU;
   select the actual DLSS quality inside the game. The launcher installs the
   matching components in its private prefix and registers NGX with backups.
4. Settings → General → **DXVK / NVK HUD** controls the overlay on the next
   game launch. D3D11 uses DXVK's device/FPS/frame-time HUD; D3D12 uses the
   native presentation FPS/ms box.
5. Enable **Steam Patch** and **Timeout fix** for ZZZ and GI; keep both off for
   the verified HSR configuration. GI currently defaults to off, so enable both
   manually after applying recommended settings. Launch through YAAGL's
   full patch/preparation flow. A direct `wine steam.exe Game.exe` shortcut is
   not equivalent and crashed during testing. GI/HSR retain their D3D11 routes.

YAAGL takes NVK, DXVK, vkd3d and NGX/NVAPI from the installed driver provider.
Its archives contain no graphics runtime or Wine. Wine downloads on first setup.
Old driver providers continue to work for D3D11, but requested new features fail
with an update message before prefix DLLs are changed.

## Standalone Wine

Install a driver release that includes the Wine/NVK runtime, or extract a matching
standalone runtime. Driver packages install the compact provider at
`/Library/GPUBundles/nvmtl/wine`. It contains the libraries and DXVK pair, **not
Wine itself**. YAAGL downloads its Wine installation separately on first setup.
Close applications in the prefix before installing or restoring the DLL pair.

Choose a compatible Wine executable and create a dedicated 64-bit prefix:

```sh
nvk_runtime=/Library/GPUBundles/nvmtl/wine
nvk_wine=/absolute/path/to/wine
nvk_prefix="$HOME/Library/Application Support/Wine/NVK"
"$nvk_runtime/bin/nvk-runtime" check "$nvk_runtime"
env WINEPREFIX="$nvk_prefix" WINEARCH=win64 "$nvk_wine" wineboot.exe --init
"$nvk_runtime/bin/nvk-runtime" install "$nvk_runtime" "$nvk_prefix"
```

For the verified Wine build, you can use YAAGL's prepared
`launcher-data/wine-nvk/bin/wine` after first setup. Install any game prerequisites
in this same prefix. Launch a
D3D11 executable with the runtime selected **for this process only**:

```sh
cd /absolute/path/to/game
env WINEPREFIX="$nvk_prefix" \
  DYLD_LIBRARY_PATH="$nvk_runtime/lib" \
  WINEDLLOVERRIDES='d3d11,dxgi=n' \
  DXVK_CONFIG='dxvk.enableDescriptorBuffer = False' \
  DXVK_HUD='devinfo,fps,frametimes,scale=1.25' \
  DXVK_LOG_LEVEL=info DXVK_LOG_PATH="$nvk_prefix" \
  "$nvk_wine" ./Game.exe
```

Select the game's D3D11 renderer if it offers several backends. Append unrelated
DLL overrides to `WINEDLLOVERRIDES` if your game needs them. Remove game-local
`d3d11.dll` / `dxgi.dll` overrides from this test setup, or they may supersede the
matched system32 pair. Do not mix DXMT/D3DMetal/ReShade DLLs into this NVK setup.

Keep `dxvk.enableDescriptorBuffer = False`: the tested descriptor-buffer path
rendered scenery but lost ZZZ characters and HUD text; descriptor sets fixed both.
Use `DXVK_HUD=0` to hide the overlay. Do not export `DYLD_LIBRARY_PATH` globally or
replace Wine's/CrossOver's installed MoltenVK library.

## CrossOver — opt-in compatibility test

Commercial CrossOver is **not tested**. Its Wine version, library validation or
Vulkan loader may differ; these steps describe a scoped test, not guaranteed
compatibility with every release. If it still loads MoltenVK or rejects the
provider, use the verified YAAGL Wine package rather than modifying CrossOver.app.

1. Duplicate the game's **64-bit** bottle for the NVK test. Close its applications.
2. Select **Wine** as its graphics backend (or disable the DXVK/DXMT/D3DMetal
   toggles on older releases), so CrossOver does not install a different DLL pair.
3. From the bottle, use **Open Shell**. CodeWeavers documents this under
   [Bottle Commands](https://support.codeweavers.com/en_US/crossover-mac-user-guide).
   Keep that shell's CrossOver environment and use the actual bottle directory
   shown by CrossOver if your bottles are stored elsewhere.
4. In that shell, install the runtime DLLs and launch through CrossOver's Wine:

```sh
nvk_runtime=/Library/GPUBundles/nvmtl/wine
nvk_bottle='My Game NVK'
nvk_prefix="$HOME/Library/Application Support/CrossOver/Bottles/$nvk_bottle"
nvk_cx_wine='/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin/wine'
"$nvk_runtime/bin/nvk-runtime" install "$nvk_runtime" "$nvk_prefix"
cd "$nvk_prefix/drive_c/path/to/game"
env WINEPREFIX="$nvk_prefix" \
  DYLD_LIBRARY_PATH="$nvk_runtime/lib" \
  WINEDLLOVERRIDES='d3d11,dxgi=n' \
  DXVK_CONFIG='dxvk.enableDescriptorBuffer = False' \
  DXVK_HUD='devinfo,fps,frametimes,scale=1.25' \
  DXVK_LOG_LEVEL=info DXVK_LOG_PATH="$nvk_prefix" \
  "$nvk_cx_wine" --bottle "$nvk_bottle" --cx-app ./Game.exe
```

Change the app path if CrossOver is installed elsewhere. Continue using this
scoped command for the NVK test; an ordinary GUI launch does not select this
provider automatically. No CrossOver installation files are replaced.

## D3D12 and DLSS

First complete [Standalone Wine](#standalone-wine) or the
[CrossOver setup](#crossover--opt-in-compatibility-test) above, including prefix
initialization, the matched DXVK pair and the native-provider launch environment.
This section adds optional D3D12/DLSS components; it does not replace that setup.

These commands require a driver provider that reports `supports_d3d12: true`
and, for DLSS, `supports_dlss: true` (2.6.4 Wine/NVK or newer). A launcher update
alone cannot add them to an older provider. Use a separate initialized
64-bit prefix or duplicate bottle and close its applications before setup.

### D3D12

Keep the `nvk_runtime`, `nvk_prefix` and `nvk_wine` variables from the Wine setup
above. Install both the existing DXGI dependency and the matched D3D12 pair:

```sh
"$nvk_runtime/bin/nvk-runtime" install-d3d12 "$nvk_runtime" "$nvk_prefix"
cd /absolute/path/to/game
env WINEPREFIX="$nvk_prefix" \
  DYLD_LIBRARY_PATH="$nvk_runtime/lib" \
  WINEDLLOVERRIDES='d3d11,dxgi,d3d12,d3d12core=n' \
  VKD3D_CONFIG=nodxr WINE_NVK_HUD=fps,frametimes DXVK_HUD=0 \
  "$nvk_wine" ./Game.exe
```

Choose the game's D3D12 backend and its own launch arguments. ZZZ's verified
YAAGL route uses `-use-d3d12` and removes `-force-d3d11`. These generic commands
supply graphics components; they do not replace a game's required launcher,
patches or other setup. Ray tracing is disabled for this tested configuration.

### Optional DLSS Super Resolution

The provider includes the unmodified NVIDIA Wine NGX pair (`nvngx.dll` and
`_nvngx.dll`) and DXVK-NVAPI (`nvapi64.dll`). Keep the game's original
`nvngx_dlss.dll`. The helper registers the core library in this prefix and backs
up its exact original `NGXCore\FullPath` value and DLLs. Supply the Wine
executable used by this prefix:

```sh
"$nvk_runtime/bin/nvk-runtime" install-dlss "$nvk_runtime" "$nvk_prefix" "$nvk_wine"
cd /absolute/path/to/game
env WINEPREFIX="$nvk_prefix" \
  DYLD_LIBRARY_PATH="$nvk_runtime/lib" \
  WINEDLLOVERRIDES='d3d11,dxgi,d3d12,d3d12core,nvapi64,nvngx,_nvngx=n' \
  DXVK_ENABLE_NVAPI=1 WINE_NVK_EXPERIMENTAL=dlss \
  VKD3D_CONFIG=nodxr WINE_NVK_HUD=fps,frametimes DXVK_HUD=0 \
  "$nvk_wine" ./Game.exe
```

Then enable DLSS and choose its quality in the game. `WINE_NVK_EXPERIMENTAL`
reaches Wine child processes and is forwarded to Mesa before it initializes.
Setting `NVK_EXPERIMENTAL=dlss` alone was lost in the tested child launch path.
This exposes experimental precompiled GPU support; it does not fabricate GPU
capabilities. DLSS SR is verified on RTX 2060 SUPER. DLSSG is left to normal
hardware checks, with no blanket block; frame generation is **not tested**.

Turn off DLSS inside the game before returning to D3D11 or launching without
NGX. To remove only NGX/NVAPI and restore the original registry value, leaving
the graphics DLLs installed:

```sh
"$nvk_runtime/bin/nvk-runtime" restore-dlss "$nvk_runtime" "$nvk_prefix" "$nvk_wine"
```

### CrossOver D3D12 / DLSS

Use the duplicate bottle and Open Shell described above. Pass CrossOver's
matching bottle arguments to registry-aware setup/restore, for example:

```sh
"$nvk_runtime/bin/nvk-runtime" install-dlss "$nvk_runtime" "$nvk_prefix" \
  "$nvk_cx_wine" --bottle "$nvk_bottle"
```

For D3D12 without DLSS, use `install-d3d12` instead; it needs no Wine argument.
Launch with the corresponding D3D12 or DLSS environment above, substituting
`"$nvk_cx_wine" --bottle "$nvk_bottle" --cx-app ./Game.exe` for the last line.
Keep other bottle arguments/environment from Open Shell. The helper verifies
Wine's `C:\windows\system32` resolves to the requested prefix before changing
NGX. No files inside CrossOver.app are replaced. Commercial CrossOver remains
untested; use the verified YAAGL Wine if its loader rejects the native provider.

## Verify, update and restore

The DXVK HUD/log should identify **NVK / NVIDIA** and the log should say
`Binding model: Legacy`. A D3D11 app may see an emulated AMD PCI vendor because
of DXVK's normal NVAPI workaround; that alone does not identify the Vulkan GPU.
For provider diagnostics, add `DYLD_PRINT_LIBRARIES=1` to the same launch command
and capture stderr. The Windows process must load this runtime's
`libnvk_wine.dylib` and `libnvk_renderer.dylib`. Its local `libMoltenVK.dylib`
is an alias to the NVK frontend, not the MoltenVK renderer. A generic
`VK_ICD_FILENAMES` setting alone does not select it in the verified Wine build.

The helper validates hashes, signatures and architectures. DXVK, vkd3d and
NGX/NVAPI have independent original-file backups and rollback journals. To switch runtime
versions that change the DLL pair, close the prefix's applications, restore first,
then install the new pair. An identical DLL pair can adopt a new provider identity
without replacing DLLs or losing its original-file backups:

```sh
"$nvk_runtime/bin/nvk-runtime" restore "$nvk_runtime" "$nvk_prefix" "$nvk_wine"
```

For CrossOver DLSS restore, pass `"$nvk_cx_wine" --bottle "$nvk_bottle"` instead
of `"$nvk_wine"`. If DLSS was never installed, Wine arguments may be omitted.
Restore also works after the runtime was updated or its manifest became invalid.
Unexpected changes to installed DLLs or backups are refused before either DLL is
replaced. After restoring, use your normal Wine/CrossOver launch again. Minecraft's
runtime and the system's default Vulkan provider are independent of this setup.
