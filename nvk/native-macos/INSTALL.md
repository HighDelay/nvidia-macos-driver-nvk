# Minecraft native NVK runtime

Minecraft **26.3** has its own Vulkan renderer. Select this driver's native NVK
provider with one saved JVM argument; **the NVK Bridge mod and a mod loader are
not required**. Rendering goes directly to NVK, with GPU-copy presentation into
a macOS drawable. MoltenVK is bypassed for this launch.

Requires **Intel Java 25**, a supported NVIDIA GPU and this fork's complete
driver/runtime on **Sequoia 15.5+ or Tahoe 26**. The installer selects the matching
OS accelerator. The runtime is installed at:

```text
/Library/GPUBundles/nvmtl/minecraft
```

## Add the JVM argument

Append this single argument to the launcher's existing **Java/JVM arguments**:

```text
-Dorg.lwjgl.vulkan.libname=/Library/GPUBundles/nvmtl/minecraft/lib/libnvk_minecraft.dylib
```

Keep existing memory and other Java options. This is a JVM argument, not a game
argument, terminal command or environment variable.

- **Prism Launcher:** right-click the instance → **Edit** → **Settings** →
  **Java**. Enable the instance's Java arguments override, retain the existing
  arguments, and append the line above. Check that its selected Java is Intel
  Java 25.
- **Minecraft Launcher:** **Installations** → the installation's **Edit** menu →
  **More Options** → **JVM Arguments**. Append the line and save.
- **Other launchers:** use the instance/profile's JVM arguments field.

Select **Vulkan** in Minecraft's graphics settings and fully restart the game.
The startup log and F3 screen should identify **NVK Mesa**. The bridge log also
reports `NVK Bridge: native device=…`. The library setting does not switch an
OpenGL profile to Vulkan by itself.

Minecraft's LWJGL 3.4.3 macOS startup tries bundled MoltenVK first when no custom
library is set. An ICD manifest alone does not change that preference. The
argument selects the native frontend before Vulkan initialization; vanilla then
passes the same provider path to SDL and retains its device/feature checks.

## Existing NVK Bridge mod users

Add the JVM argument above, then disable or remove the `nvk-bridge` jar from the
instance's `mods/` directory. Remove old `-Dnvkbridge.runtime=…` and
`-Dnvkbridge.enabled=…` options; those belong to the companion mod. Other mods
and Fabric/NeoForge can remain if you use them.

The [companion selector mod](https://github.com/HighDelay/minecraft-nvk) remains
an optional alternative: version 0.1.2+ automatically detects the installed
runtime. Existing users can keep that arrangement instead of adding the JVM
argument. It is no longer a prerequisite for the native renderer.

## Custom or private trial runtime

Use the absolute path to that runtime's frontend, for example:

```text
-Dorg.lwjgl.vulkan.libname=/absolute/path/to/runtime/lib/libnvk_minecraft.dylib
```

If the path contains spaces, quote the whole argument in launchers that split
the JVM arguments field on spaces:

```text
"-Dorg.lwjgl.vulkan.libname=/Volumes/External Drive/runtime/lib/libnvk_minecraft.dylib"
```

Keep the complete runtime together, including its matched private
`lib/libnvk_renderer.dylib`, manifest, licenses and build records. Select the
frontend above, rather than the raw renderer or the Wine-specific frontend.
Updating source alone does not update installed libraries.

## Return to the previous provider

Remove the `-Dorg.lwjgl.vulkan.libname=…` argument and fully restart Minecraft.
Without the companion mod, the game returns to its normal provider selection.
To restore the mod's automatic selection, re-enable its jar. A failed custom
library load does not promise a same-process MoltenVK retry.

The 26.3 vanilla loader bootstrap, JVM selection and SDL alignment are checked
separately from gameplay. Earlier gameplay checks with the companion selector
cover RTX 2060 SUPER/Tahoe 26.7.1. The new Mesa candidate still needs application
testing; automated GPU probes do not establish mod-free gameplay on every GPU.
The package retains its minimum Sequoia 15.5 requirement.

For MPV and other Vulkan applications, see the [custom-library guide](APPLICATIONS.md).
