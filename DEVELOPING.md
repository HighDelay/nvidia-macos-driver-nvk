# Developing the driver

This is the guide for working on the driver's source. To report a bug, see `CONTRIBUTING.md`. For how the parts fit
together, read `docs/HOW-IT-WORKS.md` first.

## How changes get in

The fork maintainer reviews and merges changes. You work in your own fork and send a pull request
to `main`; nothing lands without review. A small pull request with a clear test is merged much faster than a big one.

1. Fork the repository and clone your fork with full history (`git clone` without `--depth`): the tests' negative
   controls read earlier revisions of the source and fail on a shallow clone.
2. Make one change on its own branch.
3. Run the applicable host tests (below) before opening the pull request.
4. Sign off every commit (`git commit -s`, see "Sign-off" below) and open the pull request with the template filled in.

## What you need

| Part | Folder | Needs |
|---|---|---|
| Metal plugin (`NVMTLDriver.bundle`) | `plugin/` | Xcode 16 |
| Shader translator (`libnvmtl_translate.dylib`) | `translator/` | Rust (stable) |
| Vulkan layer (NVK, as patches on Mesa) | `nvk/` | Meson, Ninja, Rust, Mesa at the commit in `nvk/MESA_BASE` |
| Kernel drivers (NVRM, NVRMFB, NVAccel, NVRMAGDC and the smaller ones) | `kexts/` | Xcode 16, NVIDIA's `open-gpu-kernel-modules` at tag `620.70.06` |
| Native OpenGL (Zink) | `zink/` | see `docs/ZINK-OPENGL.md` |
| DLSS support | `dlss/` | see `dlss/README.md` |
| Installer, uninstaller and what goes in the package | `package/` | a shell |
| Build scripts | `build/` | |
| Tests and developer tools | `tools/` | Python 3.12 or newer, the Xcode command line tools |
| Design notes | `docs/` | |

The host tests need only a Mac with Python and the Xcode command line tools. They need no NVIDIA card.

## Tests

```bash
python3 tools/run_all_tests.py -j 3 test_device_refusal.py test_stream_uncached.py test_webkit_fixup_efi.py
python3 tools/rotation/native_attributes_test.py
```

The runner accepts test filenames and prints failures with the end of their output.
The workflow in `.github/workflows/tests.yml` lists the modern integration checks
and retained fork regressions that run on pull requests. Add the relevant tests
when changing another component.

Running without filenames discovers all top-level tests, including historical
upstream fixtures for deferred kernel/power implementations and probes requiring
explicit runtime arguments. Those failures are not a qualification result for
this fork. See `docs/UPSTREAM-3.0-INTEGRATION.md` for the baseline comparison and
scope; never count an unavailable fixture or an excluded feature as a pass.

Each fix comes with a test in `tools/` that fails on the code before the fix (a negative control) and passes after it.
Read two existing ones before writing yours, for example `tools/test_stream_uncached.py` and
`tools/test_nvrmfb_connect_owed.py`.

## Building

`README.md` ("Build from source") lists the build commands in order, and `docs/HOW-IT-WORKS.md` explains what each
part is. `build/build_release_inputs.sh` builds everything a release package is made from; its header lists the tools
and source trees it expects and the environment variables that point at them.

Rebuild both accelerators whenever `nvrm_vram_abi.h` or `nvrm_gpuva_abi.h` changes: an accelerator built against an
older ABI loads, but the framebuffer driver refuses all its memory requests and the desktop stays black.

## Testing on real hardware

- A bad kernel driver stops the Mac from starting. Before you install a build of your own, make sure you can get
  back: keep the package's `uninstall.sh` and the backup folder the installer names, and know how to start with the
  driver off (`README.md`, "Uninstall").
- Change one component at a time (the plugin, the Vulkan layer, or the kernel drivers), restart, and check that the
  desktop comes up before you change the next.
- Never test on a machine whose data you cannot lose.
- State kernel compilation, isolated kernel-collection checks and actual boot tests separately. Record the GPU,
  macOS version and observed results; a source merge or link check does not qualify a new boot.
- A fix is proven on the application or display that showed the problem, not only on a small test program.

## Rules for changes

- No Apple code. Nothing written by Apple goes into this repository: no binaries, no files copied from macOS, and
  nothing generated from private headers.
- No NVIDIA code that NVIDIA has not published as open source. The kernel side builds on NVIDIA's
  `open-gpu-kernel-modules`; GPU firmware is downloaded on the user's machine under NVIDIA's redistribution licence and
  is not stored here.
- Other people's code keeps its licence. If you add or update third-party code, say where it comes from, pin the
  exact version, and add its licence text to `licenses/` and a line to `NOTICE`.
- General fixes only. A change fixes the Metal, Vulkan or display behaviour that is wrong, for every card the code
  supports. A special case for one application or one machine is not accepted.
- Find the cause first. Say in the pull request what you measured and how it shows the cause. A change that makes a
  symptom go away without a known cause is not accepted.
- A machine that works today must still work. A behaviour change sits behind the condition that identifies the
  broken case.
- No secrets, keys or personal data in code, tests or logs.
- Fail loud. If a step cannot do what it says, it stops and says why.
- Keep comments plain: explain why the code does what it does.

## Sign-off

Every commit must be signed off: `git commit -s` adds a `Signed-off-by:` line with your name and email. It certifies
that you wrote the change, or have the right to submit it, under this repository's licence, as set out in the
Developer Certificate of Origin (<https://developercertificate.org>). Pull requests with unsigned commits cannot be
merged.

## Where to start

Issues labelled `help wanted` are open for anyone, and `CONTRIBUTING.md` lists the areas with the most open problems.
If you own a card or laptop named in an open issue, the most useful thing is often the measurement the issue asks
for. Ask in the issue before starting something large, so two people do not build the same thing.
