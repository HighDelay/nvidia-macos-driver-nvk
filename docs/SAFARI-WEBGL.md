# Optional Safari WebGL helper

NVWebKitFixup lets Apple's sandboxed WebKit GPU process load the NVIDIA Metal
runtime. It requires Lilu 1.7.2+ in OpenCore and the matching driver from the same
package. It targets Intel Sequoia 15 and Tahoe 26 (Darwin 24/25), refusing unknown
symbols or hook instructions. The original local candidate passed Aquarium on
RTX 2060 SUPER / Tahoe 26.7.1. The broader 0.2.0 candidate has source/gate tests
and an isolated Tahoe link check; Sequoia and 0.2.0 boot validation remain open.

## Installer choices

The installer offers **Manual**, **Automatic**, or **Skip**, separately from the
Intel panel, audio and DLSS. Manual is the default, including without interactive
input. It prints the paths below and setup instructions without changing EFI.
Skip keeps any existing helper configuration.

The extracted package contains `pkgroot/Optional/NVWebKitFixup/` with
`NVWebKitFixup.kext`, `Kernel-Add.plist`, `efi.py`, this guide, and licenses.

## Manual setup

1. Back up your working EFI and config.plist somewhere accessible for recovery.
2. Mount the EFI you actually boot from. `diskutil list` lists partitions;
   `sudo diskutil mount diskXsY` mounts the one you choose. Disk0s1 is not always
   the correct partition.
3. Copy **Optional/NVWebKitFixup/NVWebKitFixup.kext** from the extracted package
   to **EFI/OC/Kexts/**. Keep your installed Lilu; this package does not replace it.
4. Add an enabled **Kernel → Add** entry immediately after Lilu. The bundled
   `Optional/NVWebKitFixup/Kernel-Add.plist` is an entry template, not a complete
   config. Use:

   | Field | Value |
   | --- | --- |
   | Arch | `x86_64` |
   | BundlePath | `NVWebKitFixup.kext` |
   | Enabled | `True` |
   | ExecutablePath | `Contents/MacOS/NVWebKitFixup` |
   | PlistPath | `Contents/Info.plist` |
   | MinKernel | `24.0.0` |
   | MaxKernel | `25.99.99` |

5. Ensure Lilu's `Kernel/Quirks/DisableLinkeditJettison=True`. Validate with
   ocvalidate from your OpenCore version, then reboot. Vaulted EFIs need their
   existing re-signing workflow. Update the old `25.6.0` kernel limits if replacing
   an earlier exact-build trial.

## Automatic setup

Select Automatic to detect EFI System Partitions using the partition types from
`diskutil list -plist`. Volume names and slice numbers are not used to guess.
The helper checks each partition for a compatible OpenCore configuration and
Lilu. It selects the only compatible candidate; if several qualify, choose your
boot EFI from the numbered list. Without interactive input, an ambiguous result
requires `--efi-target`. If none qualifies, follow the reported error or use
manual setup. Detection does not change EFI files.

```sh
sudo ./install.sh --webkit-fixup=auto
# Optional override for a specific EFI:
sudo ./install.sh --webkit-fixup=auto --efi-target diskXsY
```

The override accepts a partition identifier or an absolute mounted `EFI/OC`
path. It bypasses detection and still runs the same checks. Partitions mounted
by the helper are unmounted afterward; existing mounts remain mounted.

Automatic setup requires a writable EFI System Partition, OpenCore, enabled
compatible Lilu and an unvaulted config. It checks the bundles and backs up the
config and any existing helper to **EFI/OC/NVWebKitFixup-backups/&lt;id&gt;/**.
It copies the helper and changes only its Kernel/Add entry; other settings and
kext order remain intact. Lilu, OpenCore, boot arguments, SIP and AMFI settings
are retained. Validate the resulting config with your matching ocvalidate.
Official unsigned Lilu builds are supported: their identity, version and
architecture are checked without re-signing them. The bundled helper must pass
its signature check.

An identical kext and Kernel/Add configuration are skipped without making a
backup or rewriting either. Formatting-only differences do not count as an
update. Actual kext/configuration changes create a new recovery backup; a
configuration-only change backs up the config without duplicating the kext.
Existing recovery backups are retained.

Its transaction restores EFI changes on failure where possible. Unexpected
concurrent edits are left for manual recovery. EFI failure does not undo the
driver install. Review the reported error/backup and follow the manual guide.
Keep the backup until boot testing succeeds; it contains private OpenCore data
and must not be included in public logs. Use `--webkit-fixup=manual` for
instructions or `--webkit-fixup=skip` to skip. EFI installation requires the
Automatic choice; Manual remains the default.

## Verify and recover

After reboot, `sysctl -n debug.nvwkfixup` should report `ready`; `map` increases
when WebKit loads the runtime. `read` can stay zero with the matched userland
handle fallback. Test Safari on <https://webglsamples.org/aquarium/aquarium.html>.

Disable its Kernel/Add entry or use `-nvwkoff`, then reboot. Never unload a live
helper. Driver removal does not edit OpenCore; remove its entry and
`EFI/OC/Kexts/NVWebKitFixup.kext` separately when no longer needed.

To restore an automatic-install backup, from the extracted package:

```sh
sudo /usr/bin/python3 Optional/NVWebKitFixup/efi.py restore \
  --target diskXsY --backup <id>
```

Later config/helper edits cause restore to refuse an overwrite. Merge the saved
config manually in that case. From another boot environment, the backup's
`config.plist` and previous `NVWebKitFixup.kext` can be restored manually. If no
helper existed before installation, remove the new kext after restoring the old
config. Keep a separate working EFI for recovery.
