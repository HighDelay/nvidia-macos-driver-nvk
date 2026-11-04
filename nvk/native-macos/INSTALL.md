# Minecraft native NVK runtime

For MPV and other Vulkan applications, see the [custom-library guide](APPLICATIONS.md).

This package carries separate NVAccel builds for macOS Sequoia 15 and Tahoe 26. The bundled upstream NVK renderer requires **Sequoia 15.5 or newer**, or Tahoe 26. The installer selects the matching accelerator automatically. The native Minecraft runtime is installed with the existing userland runtime at:

```text
/Library/GPUBundles/nvmtl/minecraft
```

Install the matching Fabric or NeoForge mod **0.1.2 or newer** from [HighDelay/minecraft-nvk](https://github.com/HighDelay/minecraft-nvk). It automatically detects this installed directory, so **no JVM runtime argument is required**. You can remove an existing `-Dnvkbridge.runtime=/Library/GPUBundles/nvmtl/minecraft` argument after replacing the old bridge jar.

For a custom runtime directory, set this optional Java/JVM argument:

```text
-Dnvkbridge.runtime=/absolute/path/to/nvk-runtime
```

An explicit path takes priority over automatic detection. Select **Vulkan** in Minecraft **26.3**'s graphics settings and restart the game. The startup log and F3 screen should identify native **NVK Mesa**. OpenGL launches show the mod's reminder to switch to Vulkan.

Java 25 and Intel macOS are required. Fabric Loader 0.19.5+ and NeoForge 26.3.0.99+ artifacts are provided by the mod project. Keep your existing driver settings; no system-wide Vulkan provider change is needed. To compare with the normal Minecraft provider, add `-Dnvkbridge.enabled=false` and restart.

The frontend targets macOS 15.0; the bundled private NVK renderer targets 15.5. Gameplay has been checked on RTX 2060 SUPER/Tahoe 26.7.1; Sequoia installation compatibility checks do not replace a physical Sequoia boot/gameplay test. The native runtime and driver retain their existing licenses; the separate Java mod is MIT licensed.
