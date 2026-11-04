#!/bin/bash
# Explicit, per-boot control for the optional NMIntelFB bridge.
set -euo pipefail
SYSCTL=/usr/sbin/sysctl
IOREG=/usr/sbin/ioreg
read_knob() { "$SYSCTL" -n "$1"; }
set_knob() { "$SYSCTL" -w "$1=$2" >/dev/null; }
panel_present() { "$IOREG" -r -c NMIntelFB -d1 | /usr/bin/grep -q 'class NMIntelFB,'; }
matched_frames() {
    "$IOREG" -r -c NMIntelFB -d1 -l | /usr/bin/grep -q '"NMIntelFBMatchedFrameABI" = 2'
}
status() {
    for name in nmintelfb_agdc nmintelfb_ram nmintelfb_flips nmintelfb_flip_late \
        nvaccelfb nvaccel_iop nvaccel_iop_direct nvaccel_iop_memcpy \
        nvaccel_iop_ok nvaccel_iop_ram_frames nvaccel_iop_quarantined; do
        value=$(read_knob "debug.$name" 2>/dev/null) || value=unavailable
        echo "$name: $value"
    done
}
enable() {
    set_knob debug.nvaccel_igpu 1
    # Re-run pending registration even if NVIDIA armed before the delayed panel.
    set_knob debug.nvaccelfb 3
    set_knob debug.nmintelfb_agdc 1
    local attempt=0
    while [ "$(read_knob debug.nmintelfb_agdc)" != 1 ]; do
        attempt=$((attempt + 1))
        [ "$attempt" -lt 40 ] || { echo "Panel AGDC did not start. Reboot to restore the previous per-boot state." >&2; return 1; }
        /bin/sleep 0.25
    done
    set_knob debug.nvaccel_iop 1
    echo "Intel-panel acceleration enabled for this boot."
}
ram_mode() {
    local desired=$1 attempt=0 current
    while ! set_knob debug.nmintelfb_ram "$desired"; do
        current=$(read_knob debug.nmintelfb_ram)
        case "$current" in -*) echo "Panel scanout is quarantined; reboot before retrying." >&2; return 1;; esac
        attempt=$((attempt + 1))
        [ "$attempt" -lt 12 ] || { echo "RAM mode was refused; leave it off until the laptop is investigated." >&2; return 1; }
        /bin/sleep 0.25
    done
}
action=${1:-status}
case "$action" in status) status; exit 0;; enable|fast|ordinary) ;;
    *) echo "Usage: $0 status|enable|fast|ordinary" >&2; exit 2;; esac
[ "$(id -u)" = 0 ] || { echo "Run this action with sudo." >&2; exit 1; }
panel_present || { echo "No active NMIntelFB panel; nothing changed." >&2; exit 1; }
# Both kernel components must provide the paired bridge, including ordinary copies.
read_knob debug.nvaccel_iop_direct >/dev/null
read_knob debug.nmintelfb_ram >/dev/null
case "$action" in
    enable) enable;;
    fast)
        enable
        attempt=0
        until matched_frames; do
            attempt=$((attempt + 1))
            [ "$attempt" -lt 20 ] || {
                echo "No frames from the matched accelerator yet. Log out/in, then retry fast mode." >&2; exit 1;
            }
            /bin/sleep 0.25
        done
        previous_direct=$(read_knob debug.nvaccel_iop_direct)
        set_knob debug.nvaccel_iop_direct 0
        if ! ram_mode 2; then set_knob debug.nvaccel_iop_direct "$previous_direct"; exit 1; fi
        set_knob debug.nvaccel_iop_memcpy 1
        set_knob debug.nvaccel_iop_direct 2
        echo "Experimental double-buffered GPU copies enabled for this boot."
        ;;
    ordinary)
        set_knob debug.nvaccel_iop_direct 0
        ram_mode 0
        set_knob debug.nvaccel_iop_memcpy 0
        set_knob debug.nvaccel_iop_blank_hold 0
        echo "Ordinary panel copies restored."
        ;;
esac
status
