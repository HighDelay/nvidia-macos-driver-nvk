# ARM app translation without 1401

This separate scripts package installs upstream **NullMoth ARM 2.7.0** and uses
its `nm-arm` command. It does not install the NVIDIA driver or change OpenCore.
The original signed archive is bundled; installation verifies its pinned SHA-256
and upstream Ed25519 signature before running its installer.

1. Install Apple's Command Line Tools (`xcode-select --install`) if needed.
2. Double-click **Install-ARM.command**. Only runtime installation needs sudo.
3. Double-click **Translate-App.command** and choose an app, or run:

```sh
./Translate-App.command "/Applications/Example.app"
./ARM-Apps.command check
./ARM-Apps.command list
```

Open the translated copy under `~/Applications/NullMoth ARM`. Originals stay
unchanged. Translation runs as your normal user and can take a long time.

```sh
./ARM-Apps.command remove "Example"
./ARM-Apps.command logs
./ARM-Apps.command uninstall
```

`remove` deletes one translated copy. `uninstall` removes only the translator
runtime, preserving translated copies and logs; remove copies first if desired.
No user-controlled app manifest is processed as root.

An optional user-owned runtime needs no sudo:

```sh
./ARM-Apps.command --prefix "$HOME/Library/Application Support/NVIDIA ARM" install
./ARM-Apps.command --prefix "$HOME/Library/Application Support/NVIDIA ARM" check
```

Upstream requires an Intel/AMD x86-64 CPU with AES, PCLMULQDQ, SSE4.2 and POPCNT.
Keep at least **8 GB free** for translation; large apps need more.
Command-line tools are the supported starting point. Windowed apps remain
experimental; upstream currently excludes Go, Flutter, Java/Electron, SVE video
codecs and games from its support claim. Translation is not a promise that an
Apple-silicon-only app or its frameworks will work.

Source: [1401 ARM integration](https://github.com/nullmoth/1401/blob/470db400ae18b02093c7d93daf4ed3e43a28a45d/mac/Sources/ArmApps.swift),
[upstream 2.7 release notes](https://github.com/nullmoth/nvidia-macos-driver/releases/tag/v2.7.1).
The CPU translator is separate from the driver's Metal AIR/SPIR-V translator.
Upstream and LLVM licenses remain in the unchanged runtime archive; these helper
scripts use the repository's PolyForm Noncommercial license.

To build this separate package from the driver source checkout:

```sh
python3 build/package_arm_apps.py --output "$HOME/Downloads/NVIDIA-ARM-Scripts-build"
```
