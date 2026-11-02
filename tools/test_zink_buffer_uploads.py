#!/usr/bin/env python3
"""Test small-BAR uploads and map failures with a process-local Vulkan proxy."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--headers', type=Path, default=Path.home()/'Vulkan-Headers/include')
    args = parser.parse_args()
    runtime = args.runtime.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    isolated = output/'runtime'
    isolated.mkdir()
    for name in ('libnvk_zink.dylib', 'libnvk_renderer.dylib'):
        shutil.copy2(runtime/name, isolated/name)
    real = isolated/'libnvk_zink_vulkan-real.dylib'
    shutil.copy2(runtime/'libnvk_zink_vulkan.dylib', real)
    proxy = isolated/'libnvk_zink_vulkan.dylib'
    probe = output/'buffer-upload-probe'
    subprocess.run(['clang++', '-arch', 'x86_64', '-mmacosx-version-min=15.5',
        '-dynamiclib', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror',
        '-I'+str(args.headers), str(ROOT/'tools/zink_memory_test_vulkan.cpp'),
        '-Wl,-install_name,@rpath/libnvk_zink_vulkan.dylib', '-o', str(proxy)], check=True)
    subprocess.run(['clang', '-arch', 'x86_64', '-mmacosx-version-min=15.5',
        '-std=c11', '-Wall', '-Wextra', '-Werror', '-I'+str(ROOT/'zink'),
        str(ROOT/'tools/zink_buffer_upload_probe.c'), '-framework', 'IOSurface',
        '-framework', 'CoreFoundation', '-o', str(probe)], check=True)
    for binary in (proxy, probe):
        subprocess.run(['codesign', '--force', '--sign', '-', '--timestamp=none', str(binary)], check=True)
    env = dict(os.environ, ZINK_TEST_REAL_VULKAN=str(real))
    for name in ('DYLD_INSERT_LIBRARIES', 'ZINK_DESCRIPTORS', 'ZINK_DEBUG', 'MESA_LOADER_DRIVER_OVERRIDE',
                 'NVK_BAR_VRAM', 'NVK_LOCAL_MAP_SYSMEM'):
        env.pop(name, None)
    results = []
    for case in ('upload', 'map-failure', 'read-failure', 'full-bar',
                 'persistent', 'persistent-coherent', 'full-bar-persistent',
                 'hardware', 'hardware-persistent', 'small-static', 'small-dynamic',
                 'full-bar-small-static', 'hardware-small-static'):
        if case.startswith('full-bar'):
            env['ZINK_TEST_FULL_BAR'] = '1'
        else:
            env.pop('ZINK_TEST_FULL_BAR', None)
        if case.startswith('hardware'):
            env['ZINK_TEST_NATIVE_MEMORY'] = '1'
        else:
            env.pop('ZINK_TEST_NATIVE_MEMORY', None)
        started = time.monotonic()
        with (output/(case+'.log')).open('w') as log:
            try:
                result = subprocess.run([str(probe), str(isolated/'libnvk_zink.dylib'),
                    str(proxy), case], env=env, stdout=log, stderr=subprocess.STDOUT, timeout=45)
                code = result.returncode
            except subprocess.TimeoutExpired:
                code = 124
        results.append({'case': case, 'returncode': code,
                        'seconds': round(time.monotonic()-started, 3)})
        print(case, code, flush=True)
    (output/'results.json').write_text(json.dumps(results, indent=2)+'\n')
    raise SystemExit(any(case['returncode'] for case in results))


if __name__ == '__main__':
    main()
