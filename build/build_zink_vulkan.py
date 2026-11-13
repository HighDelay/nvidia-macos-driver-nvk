#!/usr/bin/env python3
"""Build Zink's private Vulkan frontend from the existing native NVK bridge."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('output', type=Path)
    ap.add_argument('--renderer', type=Path, default=Path('/Library/GPUBundles/nvmtl/libvulkan_nouveau.dylib'))
    ap.add_argument('--headers', type=Path, default=Path.home() / 'Vulkan-Headers/include')
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    renderer = args.output / 'libnvk_renderer.dylib'
    frontend = args.output / 'libnvk_zink_vulkan.dylib'
    if renderer.exists() or frontend.exists():
        raise SystemExit('Private Vulkan outputs already exist; use a new output directory')
    shutil.copyfile(args.renderer, renderer)
    renderer.chmod(0o755)
    commands = [
        ['install_name_tool','-id','@loader_path/libnvk_renderer.dylib',str(renderer)],
        ['codesign','--force','--sign','-','--timestamp=none',str(renderer)],
        ['xcrun','clang++','-arch','x86_64','-mmacosx-version-min=15.5','-dynamiclib',
         '-std=c++17','-fobjc-arc','-O2','-Wall','-Wextra','-Wno-missing-field-initializers',
         '-Werror=unguarded-availability-new','-I'+str(args.headers),str(ROOT/'nvk/native-macos/bridge.mm'),
         '-framework','Foundation','-framework','QuartzCore','-framework','Metal',
         '-framework','IOSurface','-framework','IOKit',
         '-Wl,-install_name,@rpath/libnvk_zink_vulkan.dylib','-o',str(frontend)],
        ['codesign','--force','--sign','-','--timestamp=none',str(frontend)],
    ]
    for command in commands:
        subprocess.run(command, check=True)
    for binary in (renderer,frontend):
        subprocess.run(['codesign','--verify','--strict',str(binary)],check=True)
        subprocess.run(['lipo',str(binary),'-verify_arch','x86_64'],check=True)
    record = {'frontend_source':'nvk/native-macos/bridge.mm',
              'frontend_source_sha256':digest(ROOT/'nvk/native-macos/bridge.mm'),
              'frontend_headers':{name:digest(ROOT/name) for name in ['plugin/nvmtl_fd_token.h','plugin/nvmtl_rebind_import.h','nvk/native-macos/present_worker.h']},
              'renderer_original_sha256':digest(args.renderer),
              'renderer_source':str(args.renderer),
              'files':{p.name:digest(p) for p in (renderer,frontend)},
              'commands':commands,'minimum_macos':'15.5',
              'selection':'private frontend and renderer; process Vulkan environment is unchanged'}
    (args.output/'NATIVE-BUILD.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record,indent=2))


if __name__ == '__main__':
    main()
