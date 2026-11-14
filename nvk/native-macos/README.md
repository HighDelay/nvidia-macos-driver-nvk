# Native Vulkan presentation for Minecraft 26.3

This local runtime adds macOS window presentation to the existing NVK renderer. Minecraft submits Vulkan rendering commands directly to NVK. A Vulkan image-to-buffer copy writes each completed frame into the CAMetalLayer drawable's IOSurface, then the drawable is presented. Metal supplies the display surface; MoltenVK and Metal shader translation are absent from this rendering path.

The renderer is copied from the installed driver into a private runtime directory and given its own install name. Application Vulkan handles remain separate from the NVK instance used internally by NVMTL. Installed drivers and the system Vulkan selection are unchanged.

## Build

Requires Intel macOS, a compatible NVIDIA driver, Xcode command line tools and Vulkan headers. The tested renderer is the fork's 2.5.5 library, SHA-256 `a3a6451e7a193cbe2e71b5a6d769d14f8fd75b8728412bc28af944646d0af914`.

```sh
python3 nvk/native-macos/build.py --output out/minecraft-native-runtime
```

The output includes two signed dylibs under `lib/`, the NVK Bridge manifest, source and binary hashes, and build commands. Keep the entire directory together. Set `-Dorg.lwjgl.vulkan.libname=/absolute/path/to/runtime/lib/libnvk_minecraft.dylib` in the launcher's JVM arguments, select Minecraft's Vulkan backend and restart. No companion mod is required. Vanilla retains its feature checks and SDL provider matching. See the [launcher setup guide](INSTALL.md).

This reuses the renderer binary rather than rebuilding Mesa. Its IOSurface import protocol and surface-dirty entry points must match the installed driver. Other applications keep using their existing libraries.

## Other applications

The frontend also exports the Vulkan loader/ICD interface and the builder emits
`nvk_bridge_icd.json`, selecting `lib/libnvk_minecraft.dylib` for presentation.
See [MPV and custom Vulkan-library setup](APPLICATIONS.md). This support requires
a runtime built with these changes; the `v2.7.1-nvk` binaries are unchanged.

## Include in a driver package

The frontend targets macOS 15.0; the bundled upstream NVK renderer targets 15.5, making the package minimum **Sequoia 15.5**. Tahoe 26 is also supported. Build both accelerator variants with `build/accel_build_all.sh`, then pass `--accel-15`, `--accel-26` and `--minecraft-runtime` to `build/stage_package.py` along with the matching plugin, translator and framebuffer. The helper checks both runtime libraries against macOS 15.5, verifies the entry hash and signatures, and regenerates the replaced accelerators' nested checksums and the root manifest.

The package installer already selects the accelerator for its running OS. It copies the bridge under `/Library/GPUBundles/nvmtl/minecraft` with the existing userland runtime, and uninstall removes it through that same directory. Users select it with the JVM argument in [the packaged Minecraft setup instructions](INSTALL.md); the companion selector mod remains optional. This packaging does not enable Tahoe rotation on Sequoia or change the default Vulkan provider. Physical Sequoia boot/gameplay testing remains unqualified.

## Presentation and synchronization

- Rendering functions and actual device features come from NVK. The frontend implements Metal surfaces and swapchains; missing rendering features are not spoofed.
- Swapchain images have stable native NVK allocations. Two to eight application images are supported independently of CAMetalLayer's two or three drawable slots.
- Presentation imports the drawable on the application's native Vulkan device, waits for render semaphores, copies using the drawable pitch, waits for GPU completion, marks the VRAM dirty and publishes its generation tag before presenting.
- A private queue is requested from an available family. Transfer-only is preferred. The tested renderer exposes one graphics/compute/transfer family, so the copy can use NVK's compute implementation; a dedicated copy engine is not established.
- Queue submission and hidden presentation work share a device lock. Acquisition reserves images, honors exhaustion and timeouts, and releases reservations after presentation. Uncertain GPU work is quarantined rather than freed while still referenced.
- Normal presentation performs no CPU pixel readback or upload. GPU-copy is the presentation mode, not zero-copy. The optional test observer performs separate diagnostic readbacks and is disabled during gameplay.

## Validation on 2026-10-26

Tested on Tahoe 26.7.1, RTX 2060 SUPER, 8 GiB BAR1, with fork 2.5.5 installed:

| Check | Result |
|---|---|
| ASan/UBSan host tests with mocked submissions | Bounds, acquire exhaustion/timeouts, submission rollback, handoff consumption, command scope and C ABI exception handling passed. |
| Native Vulkan feature probe | Required Minecraft features/extensions supported; real NVIDIA device created. |
| SDL presentation, resize and minimize/restore | 120 frames with three images; all pixels checked by separate diagnostic readback. |
| Final runtime relocation, four images/three drawable slots | 24 frames; all pixels checked; zero import failures. |
| Mod + LWJGL 3.4.3 + SDL | Manifest, provider path, Metal surface creation and provider pointer equality passed. |
| Fabric 0.19.5, Minecraft 26.3, Essential | Menu and multiplayer rendering worked, confirmed by the user. Screenshot shows about 148 FPS in one scene. |
| NeoForge 26.3.0.99, Minecraft 26.3 | Game session worked, confirmed by the user. The session log identifies Vulkan 1.4.363 NVK Mesa 26.3.0-devel. |

The companion mod repository records [integration results](https://github.com/HighDelay/minecraft-nvk/blob/main/docs/TEST-RESULTS.md), runtime identity and validation limits. Raw test artifacts remain local and are not part of the source repository.

## Limits

This is a local Minecraft runtime, not a generally qualified Vulkan WSI implementation. Testing covers this NVIDIA desktop with Fabric Essential and NeoForge 26.3.0.99. Other GPUs, multiple GPUs/windows, device loss recovery and long sessions remain unqualified. Ambiguous matches between identical NVIDIA GPUs are rejected.

Presentation waits on the host for each GPU copy and allocates/imports presentation resources per frame. These remain candidates for measurement and resource reuse. The screenshot is not an A/B benchmark. No speedup over MoltenVK or removal of the earlier frame-rate ceiling has been established. VSync, drawable availability and compositor scheduling still affect presentation.

## Test tools

`host_test.mm` includes the frontend with mocked submissions for AddressSanitizer/UndefinedBehaviorSanitizer. `smoke.mm` exercises the actual runtime and SDL3:

```sh
xcrun clang++ -arch x86_64 -mmacosx-version-min=15.0 -std=c++17 -fobjc-arc -O2 \
  -I "$HOME/Vulkan-Headers/include" nvk/native-macos/smoke.mm \
  -framework Foundation -o out/native-vulkan-smoke
out/native-vulkan-smoke /absolute/runtime/lib/libnvk_minecraft.dylib /absolute/libSDL3.dylib 4 24
```

Set `NVKBRIDGE_VERIFY_PIXELS=1` only for the diagnostic readback test. Its cost is not representative of gameplay.

For limited-BAR upload placement and drawable-import recovery, see the
[memory and presentation checks](../../docs/NATIVE-NVK-SMALL-BAR.md). Full-BAR
memory placement and the original drawable import path are preserved.

To test the standard Vulkan loader route, set `VK_DRIVER_FILES` to the runtime's
`nvk_bridge_icd.json`, pass `libvulkan.1.dylib` as the smoke entry, and set
`NVKBRIDGE_TEST_FRONTEND` to the absolute frontend path. The probe requires that
the loader has already loaded that exact frontend before attaching its observer.

`ModFreeProbe.java` invokes the unmodified Minecraft 26.3
`NativeLibrariesBootstrap.tryLoadingVulkan` method, checks the provider selected
by `-Dorg.lwjgl.vulkan.libname`, verifies SDL's provider pointer, and enumerates
the native device. Compile with Java 25 and LWJGL 3.4.3 core/Vulkan/SDL jars;
run with `-XstartOnFirstThread`, the game's logging dependencies, its client jar
and LWJGL macOS native jars on the classpath. Pass the expected absolute frontend
path as its one program argument. No companion jar is used. This probe does
not start Minecraft or establish gameplay compatibility.
