#!/bin/bash
# Root LaunchDaemon for the opt-in NVRMFB panel brightness record.
set -eu
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
umask 077

state=/Library/NullMoth
record="$state/backlight.level"
helper="$state/backlight_keeper.sh"
label=com.nullmoth.backlight
plist="/Library/LaunchDaemons/$label.plist"
oid=debug.nvrmfb_backlight_disk

[ "$(id -u)" = 0 ] || { echo "Run with sudo." >&2; exit 1; }
[ -d "$state" ] && [ ! -L "$state" ] || { echo "Install the NVIDIA driver first." >&2; exit 1; }
meta=$(stat -f '%u %Lp' "$state")
[ "${meta%% *}" = 0 ] && [ "$((8#${meta#* } & 022))" = 0 ] || exit 1

valid_level() {
    [[ "$1" =~ ^(0|[1-9][0-9]{0,4})$ ]] && [ "$1" -le 65535 ]
}

case "${1:-install}" in
    install)
        launchctl bootout "system/$label" 2>/dev/null || true
        src=$(mktemp "$state/.backlight-script.XXXXXX")
        cfg=$(mktemp /Library/LaunchDaemons/.nullmoth-backlight.XXXXXX)
        trap 'rm -f "$src" "$cfg"' EXIT
        cp "$0" "$src"
        chmod 755 "$src"
        cat > "$cfg" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>$label</string>
<key>ProgramArguments</key><array><string>/bin/bash</string><string>$helper</string><string>service</string></array>
<key>RunAtLoad</key><true/>
</dict></plist>
EOF
        /usr/bin/plutil -lint "$cfg"
        chmod 644 "$cfg"
        mv -f "$src" "$helper"
        mv -f "$cfg" "$plist"
        launchctl bootstrap system "$plist"
        ;;
    remove)
        launchctl bootout "system/$label" 2>/dev/null || true
        rm -f "$helper" "$plist" "$record"
        ;;
    service)
        current=
        for ((attempt = 0; attempt < 60; ++attempt)); do
            if current=$(sysctl -n "$oid" 2>/dev/null); then break; fi
            sleep 2
        done
        [ -n "$current" ] || exit 0
        saved=
        if [ -f "$record" ] && [ ! -L "$record" ]; then saved=$(cat "$record"); fi
        if [ "$current" = -1 ] && valid_level "$saved"; then
            sysctl -w "$oid=$saved" >/dev/null 2>&1 || true
        fi
        save() {
            local value tmp
            value=$(sysctl -n "$oid" 2>/dev/null) || return 0
            valid_level "$value" && [ "$value" != "$saved" ] || return 0
            tmp=$(mktemp "$state/.backlight-level.XXXXXX") || return 0
            if printf '%s\n' "$value" > "$tmp" && mv -f "$tmp" "$record"; then
                saved=$value
            else
                rm -f "$tmp"
            fi
        }
        trap 'save; exit 0' TERM INT
        while :; do
            save
            sleep 15 & wait $!
        done
        ;;
    *)
        echo "Use install or remove." >&2
        exit 2
        ;;
esac
