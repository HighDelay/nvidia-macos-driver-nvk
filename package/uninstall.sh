#!/bin/bash
set -u
# This script runs as root and calls cp, rm, mv and kmutil by name. Without a
# fixed PATH those resolve through whatever the caller exported, and kmutil
# rebuilds the auxiliary kernel collection.
PATH=/usr/bin:/bin:/usr/sbin:/sbin; export PATH
# the NVIDIA driver only: the AMD/Intel GPU driver, the NPU driver and the audio driver are separate products with
# their own uninstallers
KEXTS="NVRM NVAccel NVRMFB NVRMAGDC NVPrimarySelector NMIntelFB"; EXT=/Library/Extensions; GB=/Library/GPUBundles
KC=/Library/KernelCollections/AuxiliaryKernelExtensions.kc
K=/System/Library/Kernels/kernel
KARG=(--allow-missing-kdk); [ -f "$K" ] && KARG+=(--kernel "$K")   # without a matching Kernel Debug Kit, kmutil needs --allow-missing-kdk
KB=/System/Library/KernelCollections/BootKernelExtensions.kc
KS=/System/Library/KernelCollections/SystemKernelExtensions.kc
REMOVING=0; RB=""; NEWKC=""; REMOVE_KC=0; CANDIDATE=""
rollback_removal() {
  local failed=0 k b
  for k in $KEXTS; do [ ! -e "$RB/$k.kext" ] || ditto "$RB/$k.kext" "$EXT/$k.kext" || failed=1; done
  for b in NVMTLDriver.bundle NVIDIAShared.bundle nvmtl NVKZinkGLDriver.bundle nvmtl-allow.txt; do [ ! -e "$RB/$b" ] || ditto "$RB/$b" "$GB/$b" || failed=1; done
  [ ! -e "$RB/kexts" ] || ditto "$RB/kexts" /Library/NullMoth/kexts || failed=1
  [ ! -d "$RB/firmware-new" ] || ditto "$RB/firmware-new" /Library/NullMoth/nvfw || failed=1
  [ ! -d "$RB/firmware-old" ] || ditto "$RB/firmware-old" /Users/Shared/nvfw || failed=1
  [ ! -e "$RB/driver-version" ] || cp -p "$RB/driver-version" /Library/NullMoth/driver-version || failed=1
  [ ! -e "$RB/update-pending" ] || cp -p "$RB/update-pending" /Library/NullMoth/update-pending || failed=1
  [ ! -e "$RB/com.nullmoth.osupdate.plist" ] || cp -p "$RB/com.nullmoth.osupdate.plist" /Library/LaunchDaemons/ || failed=1
  if [ -e "$RB/AuxiliaryKernelExtensions.kc" ]; then cp -p "$RB/AuxiliaryKernelExtensions.kc" "$KC" || failed=1
  else rm -f "$KC" || failed=1; fi
  return "$failed"
}
die() {
  echo "STOP: $*" >&2
  if [ "$REMOVING" = 1 ]; then
    if rollback_removal; then echo "Previous driver files and collection restored." >&2
    else echo "Removal rollback incomplete; backup retained at $RB." >&2; exit 1; fi
  fi
  [ -z "$RB" ] || rm -rf "$RB"
  [ -z "$NEWKC" ] || rm -f "$NEWKC"
  exit 1
}
trap 'die "uninstaller interrupted (signal HUP)"' HUP
trap 'die "uninstaller interrupted (signal INT)"' INT
trap 'die "uninstaller interrupted (signal TERM)"' TERM
[ "$(id -u)" -eq 0 ] || die "run with sudo"
# SIP's filesystem protection makes the driver's files and the kernel collection read-only even for root: removal then
# failed half-way ("Operation not permitted" on every bundle, then kmutil). The driver cannot load in that state either,
# so stop before anything is touched and say how to get out of it.
SIPS=$(csrutil status 2>/dev/null)
if printf '%s\n' "$SIPS" | grep -q 'status: enabled\.' || printf '%s\n' "$SIPS" | grep -q 'Filesystem Protections: enabled'; then
  die "System Integrity Protection is on, so macOS will not let the driver be removed (and the driver cannot load while it is on). Restart once so OpenCore applies its SIP setting again, then remove the driver again. Nothing was changed."
fi
BK=${1:-}
[ -z "$BK" ] || [ -d "$BK" ] || die "no backup at $BK"
BUILD=$(sw_vers -buildVersion)
printf '%s\n' "$BUILD" | grep -Eq '^[0-9]+[A-Za-z][A-Za-z0-9]+$' || die "cannot identify the current macOS build"
require_install_directory() {
  local metadata permissions entry listing grants
  [ ! -L "$1" ] && [ -d "$1" ] || die "installer parent must be a real directory (no driver changes made)"
  metadata=$(stat -f '%u %Lp' "$1" 2>/dev/null) || die "cannot verify installer parent ownership"
  case "$metadata" in
    "0 "[0-7][0-7][0-7]|"0 "[0-7][0-7][0-7][0-7]) ;;
    *) die "installer parent is not a verified root-owned directory (no driver changes made)";;
  esac
  permissions=${metadata#* }
  [ $((8#$permissions & 022)) -eq 0 ] || die "installer parent is writable by another account or group (no driver changes made)"
  listing=$(ls -lde "$1" 2>/dev/null)
  entry=$(printf '%s\n' "$listing" | head -1 | awk '{print $1}')
  [ -n "$entry" ] || die "cannot inspect installer parent access controls"
  # A deny entry only takes access away (some Macs carry "group:everyone deny delete" on /Library); an allow entry can
  # hand another account write access the mode bits above never show, so any allow entry still stops the install and
  # is named in the log.
  case "$entry" in *+*)
    grants=$(printf '%s\n' "$listing" | tail -n +2 | grep -E '^[[:space:]]*[0-9]+:.*[[:space:]]allow[[:space:]]' || true)
    [ -z "$grants" ] || die "installer parent $1 has access-control entries that allow access ($(printf '%s' "$grants" | tr -s ' \n' ' ')) (no driver changes made)"
    ;;
  esac
}

# Shared with uninstall.sh: resolve old staging references by bundle identity,
# never by filename. Existing permanent paths may contain a pending update;
# recovery of a missing/temporary path requires the recorded version too.
permanent_kext_path() {
  local wanted_id=$1 wanted_version=$2 recorded=$3 candidate resolved found="" id version
  if [ -d "$recorded" ]; then
    resolved=$(cd "$recorded" && pwd -P) || return 1
    case "$resolved" in /private/var/tmp/*|/var/tmp/*|/private/tmp/*|/tmp/*) ;;
      *)
        id=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$resolved/Contents/Info.plist" 2>/dev/null) || id=''
        if [ "$id" = "$wanted_id" ]; then printf '%s\n' "$resolved"; return 0; fi
        ;;
    esac
  fi
  for candidate in "$EXT"/*.kext /Library/Apple/System/Library/Extensions/*.kext; do
    [ -d "$candidate" ] && [ ! -L "$candidate" ] || continue
    id=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$candidate/Contents/Info.plist" 2>/dev/null) || continue
    [ "$id" = "$wanted_id" ] || continue
    version=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$candidate/Contents/Info.plist" 2>/dev/null) || continue
    [ -n "$wanted_version" ] && [ "$version" = "$wanted_version" ] || continue
    resolved=$(cd "$candidate" && pwd -P) || return 1
    if [ -n "$found" ] && [ "$found" != "$resolved" ]; then
      echo "Ambiguous installed copies of $wanted_id version $wanted_version" >&2; return 1
    fi
    found=$resolved
  done
  [ -n "$found" ] || { echo "No installed match for $wanted_id version $wanted_version (recorded at $recorded)" >&2; return 1; }
  printf '%s\n' "$found"
}

# Include only permanent installed paths in a collection that will survive this
# process. A copied temporary repository leaves broken paths after cleanup.
KEEP_KEXT_ARGS=(); KEEP_KEXT_IDS=""
keep_kext() {
  local path=$1 id=$2 parent
  printf '%s' "$id" | grep -Eq '^[A-Za-z0-9_.-]+$' || die "invalid preserved extension identity"
  case "$id" in com.nullmoth.NVRM|com.nullmoth.NVAccel|com.nullmoth.NVRMFB|com.nullmoth.NVRMAGDC|com.nullmoth.NMIntelFB|org.hackintosh.experimental.NVPrimarySelector) return;; esac
  case " $KEEP_KEXT_IDS " in *" $id "*) return;; esac
  parent=$path
  while :; do
    require_install_directory "$parent"
    [ "$parent" != / ] || break
    parent=$(dirname "$parent")
  done
  KEEP_KEXT_ARGS+=(-p "$path")
  KEEP_KEXT_IDS="$KEEP_KEXT_IDS $id"
}
for x in "$EXT"/*.kext; do
  [ -d "$x" ] || continue
  n=$(basename "$x" .kext); case " $KEXTS " in *" $n "*) continue;; esac
  id=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$x/Contents/Info.plist" 2>/dev/null) \
    || die "cannot identify installed extension $x"
  permanent_path=$(cd "$x" && pwd -P) || die "cannot resolve $x"
  keep_kext "$permanent_path" "$id"
done
RB=$(mktemp -d /var/tmp/nullmoth-remove.XXXX) && [ -n "$RB" ] || die "cannot create the removal preflight directory"
mkdir -p "$RB/repo" || die "cannot create the removal repository"
if [ -s "$KC" ]; then
  kmutil inspect -a x86_64 -A "$KC" --show-extension-info > "$RB/existing-kexts" 2>/dev/null \
    || die "cannot inspect the existing collection before preserving extensions"
  while IFS=$'\t' read -r external_id external_version external_path external_rest; do
    case "$external_path" in *.kext) ;; *) continue;; esac
    case "$external_id" in com.nullmoth.NVRM|com.nullmoth.NVAccel|com.nullmoth.NVRMFB|com.nullmoth.NVRMAGDC|com.nullmoth.NMIntelFB|org.hackintosh.experimental.NVPrimarySelector) continue;; esac
    case " $KEEP_KEXT_IDS " in *" $external_id "*) continue;; esac
    case "$external_path" in /System/*) continue;; esac
    permanent_path=$(permanent_kext_path "$external_id" "$external_version" "$external_path") \
      || die "cannot preserve existing extension $external_id from a permanent installed path"
    keep_kext "$permanent_path" "$external_id"
  done < "$RB/existing-kexts"
fi
if [ "${#KEEP_KEXT_ARGS[@]}" = 0 ]; then
  REMOVE_KC=1
else
  NEWKC="$KC.nullmoth-remove-new"; rm -f "$NEWKC" || die "cannot clear the removal candidate"
  kmutil create -n aux --volume-root / ${KARG[@]+"${KARG[@]}"} -B $KB -S $KS --repository "$RB/repo" ${KEEP_KEXT_ARGS[@]+"${KEEP_KEXT_ARGS[@]}"} -A "$NEWKC" -z >/dev/null 2>&1 || die "kmutil could not rebuild the collection"
  [ -s "$NEWKC" ] || die "kmutil produced no removal collection"
  INS=$(kmutil inspect -a x86_64 -A "$NEWKC" --show-extension-info 2>/dev/null) || die "cannot inspect the removal collection"
  printf '%s\n' "$INS" | grep -Eq 'com\.nullmoth|org\.hackintosh\.experimental\.NVPrimarySelector' && die "the removal collection still lists the NVIDIA driver or selector"
  for kept_id in $KEEP_KEXT_IDS; do
    printf '%s\n' "$INS" | awk '{print $1}' | grep -Fxq "$kept_id" || die "removal collection dropped $kept_id"
  done
  # Keep a private verified copy in case the candidate path is removed before publish.
  cp -p "$NEWKC" "$RB/removal-candidate.kc" || die "cannot keep a copy of the removal collection"
  CANDIDATE=$NEWKC
fi
# Finish all collection checks before changing installed files.
if [ "${CHECK:-0}" = 1 ]; then
  [ -z "$NEWKC" ] || rm -f "$NEWKC" || die "cannot remove the check-only collection"
  rm -rf "$RB"
  echo "CHECK PASS (nothing changed)"
  exit 0
fi
if [ -z "$RB" ]; then RB=$(mktemp -d /var/tmp/nullmoth-remove.XXXX) && [ -n "$RB" ] || die "cannot create the removal backup"; fi
for k in $KEXTS; do [ ! -e "$EXT/$k.kext" ] || ditto "$EXT/$k.kext" "$RB/$k.kext" || die "cannot back up $k"; done
for b in NVMTLDriver.bundle NVIDIAShared.bundle nvmtl NVKZinkGLDriver.bundle nvmtl-allow.txt; do [ ! -e "$GB/$b" ] || ditto "$GB/$b" "$RB/$b" || die "cannot back up $b"; done
[ ! -d /Library/NullMoth/nvfw ] || ditto /Library/NullMoth/nvfw "$RB/firmware-new" || die "cannot back up firmware"
[ ! -d /Users/Shared/nvfw ] || ditto /Users/Shared/nvfw "$RB/firmware-old" || die "cannot back up legacy firmware"
for f in /Library/NullMoth/kexts /Library/NullMoth/driver-version /Library/NullMoth/update-pending /Library/LaunchDaemons/com.nullmoth.osupdate.plist "$KC"; do
  [ ! -e "$f" ] || cp -Rp "$f" "$RB/" || die "cannot back up the removal state"
done
REMOVING=1
for k in $KEXTS; do rm -rf "$EXT/$k.kext" || die "cannot remove $k"; done
for b in NVMTLDriver.bundle NVIDIAShared.bundle nvmtl NVKZinkGLDriver.bundle nvmtl-allow.txt; do rm -rf "$GB/$b" || die "cannot remove $b"; done
rm -rf /Library/NullMoth/kexts /Library/NullMoth/nvfw /Library/NullMoth/licenses /Library/NullMoth/driver-version /Library/NullMoth/update-pending /Library/LaunchDaemons/com.nullmoth.osupdate.plist || die "cannot remove the cached driver or update switcher"
# the firmware used to live here; an install no longer writes it, so take the leftover with us
rm -rf /Users/Shared/nvfw 2>/dev/null || true
# the install's fallback job and the boot guard's counters (a removed driver has nothing to fall back to)
launchctl bootout system/com.nullmoth.lastgood 2>/dev/null || true
rm -f /Library/LaunchDaemons/com.nullmoth.lastgood.plist /Library/NullMoth/lastgood.sh || die "cannot remove the fallback job"
launchctl bootout system/com.nullmoth.bootguard 2>/dev/null || true
rm -f /Library/LaunchDaemons/com.nullmoth.bootguard.plist /Library/NullMoth/bootguard.sh || die "cannot remove the boot-guard job"
nvram -d 7C436110-AB2A-4BBB-A880-FE41995C9F82:nullmoth-nv-starts 2>/dev/null || true
nvram -d 7C436110-AB2A-4BBB-A880-FE41995C9F82:nullmoth-nv-ok 2>/dev/null || true
nvram -d 7C436110-AB2A-4BBB-A880-FE41995C9F82:nullmoth-backlight 2>/dev/null || true
if [ "$REMOVE_KC" = 1 ]; then
  rm -f "$KC" "$KC.nullmoth-install-new" "$KC.nullmoth-update-new" "$KC.nullmoth-remove-new" || die "cannot remove the auxiliary collection"
elif [ "$CANDIDATE" = "$NEWKC" ]; then
  [ -s "$NEWKC" ] || cp -p "$RB/removal-candidate.kc" "$NEWKC" || die "cannot restore the removal collection from its copy"
  mv -f "$NEWKC" "$KC" || die "cannot publish the removal collection"
else
  cp -p "$CANDIDATE" "$KC" || die "cannot publish the verified backup collection"
fi
if [ -f "$KC" ]; then
  FINAL_INS=$(kmutil inspect -a x86_64 -A "$KC" --show-extension-info 2>/dev/null) || die "cannot verify the published removal collection"
  printf '%s\n' "$FINAL_INS" | grep -Eq 'com\.nullmoth|org\.hackintosh\.experimental\.NVPrimarySelector' && die "the kernel collection still lists the NVIDIA driver or selector"
  for kept_id in $KEEP_KEXT_IDS; do
    printf '%s\n' "$FINAL_INS" | awk '{print $1}' | grep -Fxq "$kept_id" || die "published removal collection dropped $kept_id"
  done
fi
REMOVING=0
rm -rf "$RB"
echo "Driver removed. Reboot now: sudo shutdown -r now"
