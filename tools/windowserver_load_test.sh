#!/bin/bash
# Measure WindowServer CPU/RSS and GPU utilization while running scripted desktop workloads.
# Usage:
#   tools/windowserver_load_test.sh [-d seconds_per_phase] [-o out_dir] [-p "idle move drag ..."]
#   tools/windowserver_load_test.sh --compare run_a run_b
# Phases: idle move drag switch mission windows blur layers scroll redraw churn alpha idle_after
# "drag" moves the real mouse and needs Accessibility for the terminal; do not touch the mouse during a run.
set -u
here=$(cd "$(dirname "$0")" && pwd)

summary() {  # summary <samples.csv> -> per-phase table
    awk -F, 'NR > 1 {
        p = $2; if (!(p in n)) order[++k] = p
        n[p]++; cpu[p] += $3; if ($3 > cmax[p]) cmax[p] = $3
        gpu[p] += $5; if ($5 > gmax[p]) gmax[p] = $5
        if (!(p in r0)) r0[p] = $4; r1[p] = $4
    } END {
        printf "%-11s %8s %8s %8s %8s %9s %9s %4s\n", "phase", "ws_cpu%", "max", "gpu%", "max", "rss_MB", "rss_dMB", "n"
        for (i = 1; i <= k; i++) { p = order[i]
            printf "%-11s %8.1f %8.1f %8.1f %8.0f %9.1f %+9.1f %4d\n", p, cpu[p]/n[p], cmax[p], gpu[p]/n[p], gmax[p], r1[p]/1024, (r1[p]-r0[p])/1024, n[p] }
    }' "$1"
}

if [ "${1:-}" = "--compare" ]; then
    [ $# -eq 3 ] || { echo "usage: $0 --compare run_a run_b" >&2; exit 2; }
    summary "$2/samples.csv" > /tmp/ws_cmp_a.$$; summary "$3/samples.csv" > /tmp/ws_cmp_b.$$
    awk 'FNR == 1 { next } NR == FNR { a[$1] = $2; ag[$1] = $4; next }
         FNR == 2 { printf "%-11s %9s %9s %8s %9s %9s\n", "phase", "cpu_A", "cpu_B", "delta%", "gpu_A", "gpu_B" }
         ($1 in a) { d = a[$1] > 0 ? ($2 - a[$1]) / a[$1] * 100 : 0
                     printf "%-11s %9.1f %9.1f %+8.1f %9.1f %9.1f\n", $1, a[$1], $2, d, ag[$1], $4 }' /tmp/ws_cmp_a.$$ /tmp/ws_cmp_b.$$
    rm -f /tmp/ws_cmp_a.$$ /tmp/ws_cmp_b.$$
    exit 0
fi

dur=20
out="$HOME/Downloads/windowserver-load-$(date +%Y%m%d-%H%M%S)"
phases="idle move drag switch mission windows blur layers scroll redraw churn alpha idle_after"
while getopts "d:o:p:" opt; do
    case $opt in d) dur=$OPTARG ;; o) out=$OPTARG ;; p) phases=$OPTARG ;; *) exit 2 ;; esac
done

ws=$(pgrep -x WindowServer) || { echo "WindowServer not found" >&2; exit 1; }
mkdir -p "$out"
helper="$out/windowserver_workload"
clang -fobjc-arc -O2 "$here/windowserver_workload.m" -framework Cocoa -framework QuartzCore -framework ApplicationServices \
    -o "$helper" || exit 1

{
    echo "date: $(date)"
    echo "macOS: $(sw_vers -productVersion) ($(sw_vers -buildVersion))"
    echo "commit: $(git -C "$here/.." rev-parse --short HEAD 2>/dev/null)"
    echo "accelerator: $(ioreg -r -d 1 -c IOAccelerator | awk '/^\+-o/ { print $2; exit }')"
    system_profiler SPDisplaysDataType 2>/dev/null | grep -E 'Chipset Model|Resolution|VRAM|Metal'
    echo "phases: $phases ($dur s each)"
} > "$out/info.txt"

phase_file="$out/.phase"
echo "start" > "$phase_file"

secs() {  # ps cputime ([H:]M:SS.ss) -> seconds
    awk -F: '{ s = 0; for (i = 1; i <= NF; i++) s = s * 60 + $i; print s }' <<< "$1"
}
now() { perl -MTime::HiRes=time -e 'printf "%.3f\n", time'; }

sampler() {
    echo "t,phase,ws_cpu_pct,ws_rss_kb,gpu_util_pct,gpu_temp_c" > "$out/samples.csv"
    local t0 c0 t1 c1 line rss stats gpu temp
    t0=$(now); c0=$(secs "$(ps -o time= -p "$ws" | tr -d ' ')")
    while [ -f "$phase_file" ]; do
        sleep 1
        t1=$(now); line=$(ps -o time=,rss= -p "$ws") || break
        c1=$(secs "$(awk '{ print $1 }' <<< "$line")"); rss=$(awk '{ print $2 }' <<< "$line")
        stats=$(ioreg -r -d 1 -c IOAccelerator | grep -m1 -o '"PerformanceStatistics" = {[^}]*}')
        gpu=$(grep -o '"Device Utilization %"=[0-9]*' <<< "$stats" | cut -d= -f2)
        temp=$(grep -o '"Temperature(C)"=[0-9]*' <<< "$stats" | cut -d= -f2)
        awk -v t="$t1" -v ts="$START" -v p="$(cat "$phase_file" 2>/dev/null)" -v c0="$c0" -v c1="$c1" -v w0="$t0" \
            -v rss="$rss" -v g="${gpu:-0}" -v tc="${temp:-}" \
            'BEGIN { printf "%.1f,%s,%.1f,%s,%s,%s\n", t - ts, p, (c1 - c0) / (t - w0) * 100, rss, g, tc }' >> "$out/samples.csv"
        t0=$t1; c0=$c1
    done
}

START=$(now)
sampler &
spid=$!
trap 'rm -f "$phase_file"; kill $spid 2>/dev/null; pkill -f "$helper" 2>/dev/null' EXIT INT TERM

echo "Output: $out  (do not use the mouse/keyboard until it finishes)"
for p in $phases; do
    echo "[$p] ${dur}s"
    echo "$p" > "$phase_file"
    case $p in
        idle|idle_after) sleep "$dur" ;;
        mission) n=$(( dur * 2 / 3 / 2 * 2 )); [ "$n" -lt 2 ] && n=2
                 for _ in $(seq "$n"); do open -a "Mission Control"; sleep 1.5; done ;;
        *) "$helper" "$p" "$dur" 2>> "$out/helper.log"; grep -E '^(drag|switch):' "$out/helper.log" | tail -n +1 > "$out/.notes"
           grep -E "^$p:" "$out/.notes" | sed 's/^/    /'; rm -f "$out/.notes" ;;
    esac
    echo "settle" > "$phase_file"; sleep 3
done
rm -f "$phase_file"
wait "$spid" 2>/dev/null

grep -Ev ',(settle|start)?,' "$out/samples.csv" > "$out/.s" && summary "$out/.s" | tee "$out/summary.txt"
rm -f "$out/.s"
echo "Samples: $out/samples.csv"
