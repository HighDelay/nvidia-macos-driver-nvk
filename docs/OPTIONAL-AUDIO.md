# Optional HD Audio (Tahoe)

The complete driver package carries upstream NMXAudio under `Optional/`. The
installer asks separately and defaults to **No**. Skip it if your audio already
works. It requires macOS 26 and a PCI HD Audio controller; it is not a USB audio
or NVIDIA video codec driver.

For noninteractive installs, use `sudo bash install.sh --audio=yes` to opt in,
or `--audio=no` to remove audio previously installed by this fork. Updates retain
that opt-in. The independent Intel-panel prompt is unchanged.

An independently installed or subsequently replaced NMXAudio is preserved.
Remove it through its own installer before selecting the bundled copy. Audio
installed by this fork participates in kernel preflight, backup, interrupted
installation recovery, boot fallback and uninstall. No hardware audio test has
been performed for this integration.

The payload comes unchanged from the verified upstream driver release. Its
FreeBSD HD Audio license remains under `licenses/` in the package.
