# NVK base revision and NVRM header provenance

## Mesa base

`nvk/MESA_BASE` pins official Mesa **26.3.0** at
`0a6fa60abb737d26ac8a316a4aad7c9ddf64f9e4`. The builder uses the retained
**2.8-compatible macOS/kernel interface**, rather than importing upstream's
2.9 mapping-token and power-management chain. See the
[stable update and private validation record](MESA-26.3.0-UPDATE.md).

The previous fork renderer used `17ca6174` (26.3.0-devel). Driver upstream
`7412cdd`, also tagged `v3.0.0-beta.1`, pins `90a959eb` (26.3.0-rc3). A newer
Mesa source revision does not require changing this fork's kernel protocol.
The existing [2.9 integration decisions](UPSTREAM-2.9-INTEGRATION.md) remain
applicable to those deferred interfaces and to previously assembled packages.

The native `mesa_clc` and `vtn_bindgen2` tools must come from the exact patched
Mesa source used for the renderer. The builder records their source inputs and
rejects stale or unidentified tools. Target dependencies must resolve to system
libraries/frameworks; MacPorts LLVM and SPIR-V packages are build dependencies.

The optional `nvk/nvk-upstream-nak.patch` remains disabled. Its earlier offline
results applied to the older Mesa base. One SM120 latency hunk overlaps stable
26.3.0, so do not append the entire patch to the new base without reconciling
it and testing the resulting compiler on the target GPU. Native Zink keeps its
separate Gallium/OpenGL Mesa pin and validated internal ABI; it calls the new
NVK renderer through Vulkan.

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
