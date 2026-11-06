# Security

This is HighDelay's NVK fork. The policy below describes the upstream NullMoth
driver and its signed 1401 updates. Fork packages are distributed through
[HighDelay's releases](https://github.com/HighDelay/nvidia-macos-driver-nvk/releases)
and installed with their included scripts; they do not carry NullMoth's release signature.

## Reporting a vulnerability

Report it privately through GitHub: [open a security advisory](https://github.com/nullmoth/nvidia-macos-driver/security/advisories/new).
Please do not open a public issue for a vulnerability. Include the driver version, the macOS version, and the steps that
show the problem. We answer in the advisory thread.

## Where the driver comes from

The official upstream sources are NullMoth's [releases](https://github.com/nullmoth/nvidia-macos-driver/releases),
the 1401 app (which downloads from those releases), and [nullmothsystems.com](https://nullmothsystems.com). Copies on
other sites or in other repositories are not ours. The 1401 apps check every driver package and app update against a
detached Ed25519 signature made with the NullMoth release key before they install it, and refuse anything unsigned.

## What the driver changes on your Mac

macOS will not load a GPU driver Apple did not sign unless some of its protections are relaxed, so the setup makes two
system-wide changes:

- **Boot arguments** `amfi_get_out_of_my_way=0x1` and `amfi=0x80` turn off macOS's check of app signatures and the
  entitlements they claim. A program running as you can load code into your other apps, including ones with camera,
  microphone, or Full Disk Access, and can give itself entitlements Apple would normally refuse. `ipc_control_port_options=0`
  relaxes a related protection on task ports.
- **System Integrity Protection** is partly off (`csr-active-config` 0x0A43).

Upstream's Mac app restores the settings recorded by its own setup. This fork's installation and removal scripts do not
change those OpenCore settings; `uninstall.sh` on its own does not restore them. While
these settings are on, only run software you trust, as you would on any Mac with these protections off.
