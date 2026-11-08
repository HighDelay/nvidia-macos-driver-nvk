#!/usr/bin/env python3
"""Check real SDR/HDR IOSurface publication and record transfer latency."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--runtime', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--apple', action='store_true')
    ap.add_argument('--frames', type=int, default=60)
    args = ap.parse_args()
    if not 8 <= args.frames <= 10000:
        ap.error('--frames must be between 8 and 10000')
    runtime = args.runtime.resolve(strict=True)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    shim, probe = out/'gld-test-lookup.dylib', out/'cgl_iosurface_transfer_probe'
    flags = ['clang', '-arch', 'x86_64', '-mmacosx-version-min=15.5', '-std=c11', '-O2',
             '-Wall', '-Wextra', '-Werror', '-Wno-deprecated-declarations']
    subprocess.run(flags + ['-dynamiclib', '-Wl,-undefined,dynamic_lookup', '-Wl,-rpath,'+str(runtime),
        '-framework', 'CoreFoundation', str(ROOT/'tools/zink_gld_test_lookup.c'), '-o', str(shim)], check=True)
    subprocess.run(flags + [str(ROOT/'tools/cgl_iosurface_transfer_probe.c'), '-framework', 'OpenGL',
        '-framework', 'CoreFoundation', '-framework', 'CoreVideo', '-framework', 'IOSurface', '-o', str(probe)], check=True)
    env = dict(os.environ)
    for key in ('DYLD_INSERT_LIBRARIES', 'NVKZINK_DISABLE', 'NVKZINK_PRESENT', 'ZINK_TEST_APPLE_GLD',
                'ZINK_TEST_GLD_BUNDLE', 'ZINK_TEST_NO_FAMILY_VRAM', 'NVKZINK_OBS_COMPAT'):
        env.pop(key, None)
    env['DYLD_INSERT_LIBRARIES'] = str(shim)
    if args.apple:
        env.update(ZINK_TEST_APPLE_GLD='1', NVKZINK_DISABLE='1')
    else:
        env.update(ZINK_TEST_GLD_BUNDLE=str(runtime/'NVKZinkGLDriver.bundle'), NVKZINK_DISABLE='0')
    cases = [('hdr-stremio', 1920, 943, ['--half']),
             ('hdr-1600p', 2560, 1600, ['--half']),
             ('hdr-padded', 257, 135, ['--half', '--padded']),
             ('hdr-padded-large', 1919, 1079, ['--half', '--padded']),
             ('hdr-resize', 1920, 943, ['--half', '--resize']),
             ('hdr-core32', 257, 135, ['--half', '--padded', '--core32']),
             ('sdr-1080p', 1920, 1080, []),
             ('sdr-padded', 257, 135, ['--padded'])]
    results = []
    for name, width, height, options in cases:
        started = time.monotonic()
        argv = [str(probe), str(runtime/'libnvk_cgl.dylib'), str(width), str(height), str(args.frames)] + options
        if args.apple:
            argv.append('--apple')
        with (out/(name+'.log')).open('w') as log:
            try:
                code = subprocess.run(argv, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=90).returncode
            except subprocess.TimeoutExpired:
                code = 124
        log_text = (out/(name+'.log')).read_text()
        measurements = [line for line in log_text.splitlines() if line.startswith('PASS renderer=')]
        target = next((line for line in measurements if f' size={width}x{height} ' in line), '')
        metrics = {key: float(value) for key, value in re.findall(r'(import_ms|flush_\w+_ms|publish_\w+_ms)=([\d.]+)', target)}
        passed = (code == 0 and len(measurements) == (3 if '--resize' in options else 1)
                  and all('HDR_pixels_and_padding=correct' in line for line in measurements) and bool(target))
        result = dict(case=name, returncode=code, passed=passed, width=width, height=height,
                      frames=args.frames, seconds=round(time.monotonic()-started, 3), measurements=measurements, **metrics)
        results.append(result)
        print(json.dumps(result), flush=True)
    (out/'results.json').write_text(json.dumps(results, indent=2)+'\n')
    raise SystemExit(any(not row['passed'] for row in results))


if __name__ == '__main__':
    main()
