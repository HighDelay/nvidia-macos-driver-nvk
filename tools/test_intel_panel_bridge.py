#!/usr/bin/env python3
"""Run actual scanout code and NVAccel's frame lease with fake MMIO under sanitizers."""
import argparse
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path)
args = parser.parse_args()

def run(output):
    output.mkdir(parents=True, exist_ok=True)
    source = (ROOT/'kexts/NVRM/accel/nvrm-accel.cpp').read_text()
    start = source.index('class NVIntelFrameLease {')
    end = source.index('enum NVIntelCopyResult', start)
    (output/'intel_frame_lease.inc').write_text(source[start:end])
    panel = (ROOT/'kexts/NMIntelFB/intelfb.cpp').read_text()
    start = panel.index('void NMIntelFB::activateBridge()')
    end = panel.index('void NMIntelFB::drainWorkers()', start)
    (output/'intel_auto_bridge.inc').write_text(panel[start:end])
    subprocess.run(['xcrun', 'clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-g',
                    '-I'+str(ROOT/'kexts/NMIntelFB'), '-I'+str(output),
                    str(ROOT/'tools/test_intel_panel_scanout.cpp'), '-o', str(output/'panel-tests')], check=True)
    subprocess.run([str(output/'panel-tests')], check=True)

if args.output:
    run(args.output)
else:
    with tempfile.TemporaryDirectory(prefix='intel-panel-test-') as d:
        run(Path(d))
