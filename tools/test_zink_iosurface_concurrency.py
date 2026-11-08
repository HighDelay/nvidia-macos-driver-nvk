#!/usr/bin/env python3
"""Exercise isolated shared CGL contexts, including a deterministic publication wait."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--runtime', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--apple', action='store_true', help='Run the stream comparison through Apple OpenGL')
    ap.add_argument('--expect-contention', action='store_true', help='Require the original lock-contention failure as a negative control')
    ap.add_argument('--repeat', type=int, default=1)
    ap.add_argument('--width', type=int, default=128)
    ap.add_argument('--height', type=int, default=72)
    ap.add_argument('--frames', type=int, default=240)
    ap.add_argument('--stress-only', action='store_true')
    args = ap.parse_args()
    if not (8 <= args.width <= 4096 and 8 <= args.height <= 2160 and 8 <= args.frames <= 10000 and 1 <= args.repeat <= 100):
        ap.error('Use dimensions 8..4096 by 8..2160, 8..10000 frames and 1..100 repeats')
    runtime = args.runtime.resolve(strict=True)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    headers = Path(os.environ.get('VULKAN_HEADERS', str(Path.home() / 'Vulkan-Headers/include')))
    shim = out / 'gld-test-lookup.dylib'
    probe = out / 'cgl_iosurface_concurrency_probe'
    flags = ['clang', '-arch', 'x86_64', '-mmacosx-version-min=15.5', '-std=c11', '-O2',
             '-Wall', '-Wextra', '-Werror', '-Wno-deprecated-declarations']
    subprocess.run(flags + ['-dynamiclib', '-DZINK_TEST_IOSURFACE_WAITS', '-I'+str(headers),
        '-Wl,-undefined,dynamic_lookup', '-Wl,-rpath,'+str(runtime), '-framework', 'CoreFoundation',
        '-framework', 'IOSurface', str(ROOT/'tools/zink_gld_test_lookup.c'), '-o', str(shim)], check=True)
    subprocess.run(flags + [f'-DPROBE_WIDTH={args.width}', f'-DPROBE_HEIGHT={args.height}', f'-DPROBE_FRAMES={args.frames}',
        str(ROOT/'tools/cgl_iosurface_concurrency_probe.c'), '-framework', 'OpenGL',
        '-framework', 'CoreFoundation', '-framework', 'IOSurface', '-o', str(probe)], check=True)
    env = dict(os.environ)
    for key in ('DYLD_INSERT_LIBRARIES', 'NVKZINK_DISABLE', 'NVKZINK_PRESENT', 'ZINK_TEST_APPLE_GLD',
                'ZINK_TEST_GLD_BUNDLE', 'ZINK_TEST_NO_FAMILY_VRAM', 'NVKZINK_OBS_COMPAT'):
        env.pop(key, None)
    env['DYLD_INSERT_LIBRARIES'] = str(shim)
    if args.apple:
        env.update(ZINK_TEST_APPLE_GLD='1', NVKZINK_DISABLE='1')
    else:
        env['ZINK_TEST_GLD_BUNDLE'] = str(runtime/'NVKZinkGLDriver.bundle')
    results = []
    modes = ['stress'] if args.apple or args.stress_only else ['delay', 'alias', 'lifetime', 'teardown', 'stress', 'timeout']
    for iteration in range(args.repeat):
        for mode in modes:
            name = f'{iteration+1:02d}-{mode}'
            started = time.monotonic()
            argv = [str(probe), str(runtime/'libnvk_cgl.dylib'), mode]
            if args.apple:
                argv.append('--apple')
            with (out/(name+'.log')).open('w') as log:
                process = subprocess.Popen(argv, env=env, stdout=log, stderr=subprocess.STDOUT)
                try:
                    code = process.wait(timeout=90)
                except subprocess.TimeoutExpired:
                    try:
                        subprocess.run(['sample', str(process.pid), '1', '1', '-file', str(out/(name+'-sample.txt'))],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                    except (OSError, subprocess.TimeoutExpired) as error:
                        (out/(name+'-sample-error.txt')).write_text(str(error)+'\n')
                    finally:
                        process.kill()
                        process.wait()
                    code = 124
            log_text = (out/(name+'.log')).read_text()
            negative = args.expect_contention and mode in ('delay', 'alias', 'lifetime', 'teardown')
            matched = code == 1 and 'progressed during held fence: 0' in log_text and 'independent_progress && dependent_blocked' in log_text
            passed = matched if negative else code == 0
            results.append(dict(case=name, returncode=code, passed=passed, negative_control=negative,
                                seconds=round(time.monotonic()-started, 3)))
            print(name, code, 'PASS' if passed else 'FAIL', flush=True)
    (out/'results.json').write_text(json.dumps(results, indent=2)+'\n')
    raise SystemExit(any(not r['passed'] for r in results))


if __name__ == '__main__':
    main()
