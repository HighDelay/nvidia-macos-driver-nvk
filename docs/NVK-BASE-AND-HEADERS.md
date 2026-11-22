# NVK base revision and NVRM header provenance

## Mesa base

`nvk/MESA_BASE` pins official Mesa **26.3.1** at
`fec8830e9c94b1d67f3d8469467d5529fb2978cd`. The default `3.0` profile uses the
capability-negotiated mapping ABI with NVIDIA 620.70.06 and retains the fork's
BAR-aware memory layout and GPU-copy frontends. Native Zink uses the same Mesa
revision with its own source check and Gallium overlay. The two components
communicate through Vulkan. [Zink build instructions](ZINK-OPENGL.md#build-commands)

Mesa 26.3.1 corrects Zink shader packing, client-pointer uploads, render
attachment state and batch memory accounting. It also contains shared NIR,
SPIR-V and Vulkan runtime fixes. All 22 patches in the default NVK chain
applied without fuzz, and the signed renderer retained all 2,335 exports from
the 26.3.0 build. [Mesa 26.3.1 release notes](https://docs.mesa3d.org/relnotes/26.3.1.html)

On RTX 2060 SUPER with Tahoe 26.7.1 and 8 GiB BAR1, the private candidate passed
70 Zink checks, Java/LWJGL rendering and resize, native Vulkan resize and a
384 MiB mapped upload/readback, and Wine DXVK and VKD3D rendering and resize.
The user also confirmed the private build worked on the same GPU with both
256 MiB and 8 GiB BAR1. Full-package testing and qualification on other
hardware remain pending.

The native `mesa_clc` and `vtn_bindgen2` tools must come from the exact patched
Mesa source used for the renderer. The builder records their source inputs and
rejects stale or unidentified tools. Target dependencies must resolve to system
libraries/frameworks; MacPorts LLVM and SPIR-V packages are build dependencies.

The optional `nvk/nvk-upstream-nak.patch` requires reconciling an overlapping
SM120 latency hunk and revalidating the compiler before use with stable Mesa.
The earlier qualification records cover the previous 26.3.0 baseline.
[NVK 26.3.0 evaluation](MESA-26.3.0-UPDATE.md) · [Zink 26.3.0 evaluation](ZINK-MESA-26.3.0.md)

## NVRM headers

NVK is compiled against the 610.57.04 open kernel module headers with the NVOS change in `nvk/ogkm-nvos-620.patch`; the
kernel module is built from 620.70.06. `tools/nvk_header_abi.py` compares, between module versions, every struct and
constant in the 135 headers the NVRM backend preprocesses to, restricted to the 198 names the backend sources use.

| compared with 620.70.06 | structs differing | structs missing | defines differing | defines missing |
|---|---|---|---|---|
| 610.57.04 + NVOS change (shipped) | 0 | 0 | 0 | 0 |
| 615.78.08 (the headers 2.8 was built with) | 0 | 0 | 0 | 0 |

Control: the same tool without the restriction (`--all`) reports 13 structs differing, 7 missing, 20 constants differing and
126 missing between the shipped headers and 620.70.06, so the comparison does see changes when they exist; none of them
is a name the backend uses. The header list is produced by preprocessing the backend sources with `-MM` and keeping the
files that resolve into the module tree.
