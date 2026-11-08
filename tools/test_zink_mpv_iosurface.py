#!/usr/bin/env python3
"""Render local video with an existing libmpv through isolated shared CGL contexts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--runtime', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--libmpv', type=Path, required=True)
    ap.add_argument('--headers', type=Path, required=True, help='Directory containing mpv/client.h and mpv/render_gl.h')
    ap.add_argument('--video', type=Path, required=True)
    ap.add_argument('--apple', action='store_true')
    ap.add_argument('--width', type=int, default=1920)
    ap.add_argument('--height', type=int, default=1080)
    ap.add_argument('--frames', type=int, default=120)
    args = ap.parse_args()
    if not (8 <= args.width <= 4096 and 8 <= args.height <= 2160 and 8 <= args.frames <= 2000):
        ap.error('Use dimensions 8..4096 by 8..2160 and 8..2000 frames')
    runtime, libmpv, headers, video = (p.resolve(strict=True) for p in (args.runtime, args.libmpv, args.headers, args.video))
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    shim, probe = out/'gld-test-lookup.dylib', out/'cgl_mpv_iosurface_probe'
    flags = ['clang', '-arch', 'x86_64', '-mmacosx-version-min=15.5', '-std=c11', '-O2',
             '-Wall', '-Wextra', '-Werror', '-Wno-deprecated-declarations']
    subprocess.run(flags + ['-dynamiclib', '-Wl,-undefined,dynamic_lookup', '-Wl,-rpath,'+str(runtime),
        '-framework', 'CoreFoundation', str(ROOT/'tools/zink_gld_test_lookup.c'), '-o', str(shim)], check=True)
    subprocess.run(flags + ['-I'+str(headers), f'-DPROBE_WIDTH={args.width}', f'-DPROBE_HEIGHT={args.height}',
        f'-DPROBE_FRAMES={args.frames}', '-Wl,-rpath,'+str(libmpv.parent), str(ROOT/'tools/cgl_mpv_iosurface_probe.c'),
        '-framework', 'OpenGL', '-framework', 'CoreFoundation', '-framework', 'IOSurface', '-o', str(probe)], check=True)
    env = dict(os.environ)
    for key in ('DYLD_INSERT_LIBRARIES', 'NVKZINK_DISABLE', 'NVKZINK_PRESENT', 'ZINK_TEST_APPLE_GLD',
                'ZINK_TEST_GLD_BUNDLE', 'NVKZINK_OBS_COMPAT'):
        env.pop(key, None)
    env.update(DYLD_INSERT_LIBRARIES=str(shim), XDG_CACHE_HOME=str(out/'cache'),
               DYLD_LIBRARY_PATH=str(libmpv.parent))
    if args.apple:
        env.update(ZINK_TEST_APPLE_GLD='1', NVKZINK_DISABLE='1')
    else:
        env['ZINK_TEST_GLD_BUNDLE'] = str(runtime/'NVKZinkGLDriver.bundle')
    argv = [str(probe), str(runtime/'libnvk_cgl.dylib'), str(libmpv), str(video)]
    if args.apple:
        argv.append('--apple')
    started = time.monotonic()
    with (out/'playback.log').open('w') as log:
        process = subprocess.Popen(argv, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            code = process.wait(timeout=90)
        except subprocess.TimeoutExpired:
            try:
                subprocess.run(['sample', str(process.pid), '1', '1', '-file', str(out/'playback-sample.txt')],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            except (OSError, subprocess.TimeoutExpired) as error:
                (out/'sample-error.txt').write_text(str(error)+'\n')
            finally:
                process.kill()
                process.wait()
            code = 124
    result = dict(returncode=code, seconds=round(time.monotonic()-started, 3), apple=args.apple,
                  width=args.width, height=args.height, frames=args.frames, video_sha256=digest(video),
                  libmpv_sha256=digest(libmpv), zink_sha256=digest(runtime/'libnvk_zink.dylib'),
                  headers={str(p.relative_to(headers)): digest(p) for p in (headers/'mpv').glob('*.h')})
    (out/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    print((out/'playback.log').read_text(), flush=True)
    raise SystemExit(code != 0)


if __name__ == '__main__':
    main()
