# NVK Bridge for MPV and other Vulkan applications

NVK Bridge routes Vulkan rendering directly to native NVK. Its presentation
adapter copies frames on the GPU into Metal drawables for macOS composition.
The `minecraft` directory name is historical; this frontend can also serve
other applications using `VK_EXT_metal_surface`.

Requires this fork's driver on **x86_64 Sequoia 15.5+ or Tahoe**, an NVIDIA
Turing-or-newer GPU, and a runtime containing the loader interface added here.
**The published `v2.7.1-nvk` runtime predates this change.** Updating source alone
does not update installed libraries.

## Choose the runtime

For a driver package containing this update:

```sh
NVK_BRIDGE_RUNTIME="/Library/GPUBundles/nvmtl/minecraft"
ls "$NVK_BRIDGE_RUNTIME/nvk_bridge_icd.json" \
   "$NVK_BRIDGE_RUNTIME/lib/libnvk_minecraft.dylib" \
   "$NVK_BRIDGE_RUNTIME/lib/libnvk_renderer.dylib"
```

Keep these files together. A custom build uses the same layout; set
`NVK_BRIDGE_RUNTIME` to its absolute directory instead. The existing
[runtime builder](build.py) creates it with
`python3 nvk/native-macos/build.py --output out/nvk-app-runtime`.

## MPV

Use an x86_64 MPV build with Vulkan-loader support, `gpu-next`, and the `macvk`
context. Check its available contexts with `mpv --gpu-context=help`.

Start with software decoding to check the Vulkan rendering path:

```sh
VK_DRIVER_FILES="$NVK_BRIDGE_RUNTIME/nvk_bridge_icd.json" \
mpv --no-config --vo=gpu-next --gpu-api=vulkan --gpu-context=macvk \
    --hwdec=no --log-file="$HOME/Desktop/mpv-nvk.log" \
    -- "$HOME/Movies/example.mp4"
```

Check the log for the NVIDIA/NVK device. After playback works, try
`--hwdec=videotoolbox-copy` in place of `--hwdec=no`. Decoder availability comes
from VideoToolbox and the installed codecs; this copy mode can transfer decoded
frames through system memory. The bridge does not add hardware codecs or
guarantee direct decoder-surface sharing.

See MPV's [Vulkan and GPU options](https://mpv.io/manual/master/#video-output-drivers)
and [hardware decoding options](https://mpv.io/manual/master/#options-hwdec).

## Other applications

Choose the application's Vulkan renderer and the integration it supports:

| Application setting | Use |
| --- | --- |
| Vulkan ICD / driver manifest | `$NVK_BRIDGE_RUNTIME/nvk_bridge_icd.json` |
| Custom provider loaded through `vkGetInstanceProcAddr` | `$NVK_BRIDGE_RUNTIME/lib/libnvk_minecraft.dylib` |
| Full Vulkan **loader** library | `/Library/GPUBundles/nvmtl/libvulkan.1.dylib`, plus the `VK_DRIVER_FILES` setting below |

For an application using the standard Vulkan loader, select the bridge for
that launch:

```sh
VK_DRIVER_FILES="$NVK_BRIDGE_RUNTIME/nvk_bridge_icd.json" /path/to/application
```

For a GUI app, fully quit it first and pass the environment through LaunchServices:

```sh
open --env "VK_DRIVER_FILES=$NVK_BRIDGE_RUNTIME/nvk_bridge_icd.json" \
     -a "/Applications/Example.app"
```

Use absolute library paths in app settings; settings files may not expand shell
variables. A provider exposing `vkGetInstanceProcAddr` is different from a full
loader exporting every core Vulkan symbol. Apps loading symbols such as
`vkCreateInstance` directly need the loader route. The raw
`/Library/GPUBundles/nvmtl/libvulkan_nouveau.dylib` is the rendering backend;
use the bridge frontend for this window-presentation path.

These examples select a provider per process. To return to the app's usual
provider, remove its custom-library setting and launch without the override.

## Troubleshooting

- **Missing manifest or ICD exports:** the installed runtime is too old. The
  frontend needs `vk_icdGetInstanceProcAddr`, `vk_icdGetPhysicalDeviceProcAddr`
  and `vk_icdNegotiateLoaderICDInterfaceVersion`, alongside its manifest.
- **MoltenVK still appears:** check which library the app actually loads. A
  build linked directly to MoltenVK cannot be redirected by an ICD selector.
- **`macvk` or `gpu-next` unavailable:** use an MPV build containing those backends.
- **Loader discovery problems:** add `VK_LOADER_DEBUG=all` to the same launch.
  Older loaders use `VK_ICD_FILENAMES` instead of `VK_DRIVER_FILES`.
- **Hardware decode fails:** retry the software-decoding command above before
  diagnosing decoder interoperability separately.

Loader selection follows the [Khronos loader/driver interface](https://github.com/KhronosGroup/Vulkan-Loader/blob/main/docs/LoaderDriverInterface.md#overriding-the-default-driver-discovery).
Required Vulkan features still come from NVK; application compatibility needs
testing with each application's own rendering and presentation requirements.
