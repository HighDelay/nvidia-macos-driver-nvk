# NVWebKitFixup — Sequoia and Tahoe compatibility candidate

This optional x86_64 Lilu plugin lets Apple's normal WebKit GPU service load
the installed NVIDIA Metal runtime while retaining other Sandbox and AMFI checks.
It is a prototype, not a generally qualified Safari WebGL repair.
Use it with the matching NVMTL page-query and owned-handle fixes from this
branch; an older driver package may lack the userland changes it needs.

Version 0.2.0 targets Intel macOS 15 Sequoia (Darwin 24) and macOS 26 Tahoe
(Darwin 25). It checks the required kernel symbols and both Sandbox function
prefixes before routing either callback. Only the reviewed frame-setup and
register-save instructions are accepted; stack-frame sizes may vary within
validated bounds. Lilu's instruction decoder must agree on the complete span.
Calls, branches, RIP-relative operands, truncated/out-of-image targets, unknown
layouts and other OS families leave its permission exceptions inactive. There
is no beta option that overrides these checks.

Apple's published Sequoia and Tahoe sources retain the relevant MAC callback
and signing-query signatures. The runtime gates permit matching builds without
pinning one kernel version string; they do not guarantee that every maintenance
release has the same private Sandbox code. An unsupported layout requires review.

The caller must have the kernel signing identity `com.apple.WebKit.GPU`, a valid
signature and platform-binary status. Executable mappings are limited to six
named NVMTL/NVK/translator/LLVM runtime paths. Each target must be a regular,
root-owned, restricted file without group/other write permission. Only private
X/RX mappings are admitted; writable executable, shared, anonymous and JIT
mappings retain their denial. Existing maximum protections are never raised.
The only open exception is read-only `/dev/null`, verified as root-owned device
3,2. Writes and other paths keep the original result.

The helper changes only the two in-memory Sandbox callbacks. It does not edit
Apple's files, disable the sandbox, alter AMFI, change IOKit message filters,
enable local video codecs, or change the confirmed Safari video/icon policies.
The matched NVMTL build also fixes the ICD's page/memory query fallback without
requesting additional sysctl permissions. When both a fresh null-device open
and `pipe()` are denied, its handle allocator duplicates WebKit's inherited
read-only null stdin. It validates the owned duplicate against `/dev/null`, sets
close-on-exec, and never consumes or closes the original stdin. Other inherited
objects and writable handles are refused.

## Build

Use the DEBUG Lilu SDK and a pinned MacKernelSDK checkout:

```sh
bash kexts/NVWebKitFixup/build.sh /path/to/Lilu.kext /path/to/MacKernelSDK /absolute/output/path
python3 tools/test_webkit_fixup_policy.py
python3 tools/test_webkit_fixup_hooks.py
python3 tools/test_webkit_fixup_compatibility.py
python3 tools/test_nvmtl_sandbox_memory.py
python3 tools/test_icd_fd_token.py
```

Build dependencies are for compilation only. Keep the installed Lilu rather
than replacing it with the DEBUG build used as an SDK. The prototype was linked
in an isolated Tahoe kernel collection against the host's Lilu 1.7.3. The
original 0.1.0 candidate booted and passed the local Safari tests below; the
broader 0.2.0 candidate requires a separate boot test.

## Local boot test

1. Retain an accessible copy of the original EFI and OpenCore configuration.
2. Put `NVWebKitFixup.kext` in `EFI/OC/Kexts/`.
3. Add [the Kernel/Add entry](Kernel-Add.plist) after Lilu. Its architecture is
   `x86_64`, executable is `Contents/MacOS/NVWebKitFixup`, plist is
   `Contents/Info.plist`, and kernel limits are `24.0.0` through `25.99.99`.
   Updating an earlier local trial also requires widening its old `25.6.0`
   limits; replacing the kext alone does not change OpenCore's load restrictions.
4. Install the matched NVMTL userland candidate and reboot.
5. Read `sysctl -n debug.nvwkfixup`, then test normal Safari on
   <https://webglsamples.org/aquarium/aquarium.html>.

The status should report readiness and increment `map` when the GPU service
loads the runtime. `read` may remain zero when the owned-handle fallback is used.
Refusal counters distinguish caller signing, file trust and protection checks.
An absent or inactive status is not a pass. Retain the existing Facebook icon
fallback while testing.

## Recorded local result

On 2026-11-10, version 0.1.0 booted with Lilu 1.7.3 on RTX 2060 SUPER / Tahoe
26.7.1, Darwin 25.6.0, `xnu-12377.161.15.700.19~2/RELEASE_X86_64`.
A real sandboxed WebKit GPU process created
a WebGL context, compiled shaders and returned the expected pixel
`[64,127,191,255]` without a GL error. Normal Safari then rendered Aquarium with
500 fish; its page counter reported approximately 73–75 FPS on a 75 Hz display,
and the user confirmed visible, moving fish. No private message-filter override
was applied to Safari. This is a short local result, not a general benchmark or
qualification of other GPUs, kernel builds, WebGL applications or extended use.
Future driver packages bundle it as an optional OpenCore component with Manual
(default), Automatic and Skip choices. See [the setup guide](../../docs/SAFARI-WEBGL.md).
Automatic detects EFI partitions by type and selects the sole compatible
OpenCore installation. Multiple compatible installations require a choice;
`--efi-target` overrides detection. Lilu, vault, backup and rollback checks apply
to both detected and explicit targets.
It is never installed in the system kext repositories or activated by default.

The 0.2.0 extension passes OS/symbol/instruction/routing refusal tests, the
unchanged permission/callback tests, compilation and an isolated Tahoe kernel
link check. Sequoia has source/interface validation and simulated gate tests,
without a Sequoia boot or kernel-collection link result. The earlier Tahoe
0.1.0 result should not be presented as a 0.2.0 boot test.

Interface review used 13 Apple XNU tags from
[`xnu-11215.1.10`](https://github.com/apple-oss-distributions/xnu/blob/xnu-11215.1.10/security/mac_policy.h)
through the published Sequoia
[`xnu-11417.140.69`](https://github.com/apple-oss-distributions/xnu/blob/xnu-11417.140.69/security/mac_policy.h)
and Tahoe
[`xnu-12377.121.6`](https://github.com/apple-oss-distributions/xnu/blob/xnu-12377.121.6/security/mac_policy.h)
sources. This establishes declaration consistency, not private-hook or hardware
correctness on every build.

## Disable and recover

Use `-nvwkoff` or disable the helper's `Kernel/Add` entry and reboot. A started
Lilu plugin cannot safely be unloaded while its callbacks are installed.
Restore the saved configuration from another boot environment if necessary;
the prepared local installer also retains an on-EFI configuration backup.
Keep the matched userland backup for a complete rollback.

The permission-hook mechanism was informed by
[WhateverXe](https://github.com/b00t0x/WhateverXe/blob/master/WhateverXe/kern_xe_data_access.cpp).
Its implementation is not treated as a verified drop-in repair. NVWebKitFixup
adds its own exact kernel/prologue gates, file-trust checks, permission bounds,
diagnostics and tests.
