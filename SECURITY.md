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
