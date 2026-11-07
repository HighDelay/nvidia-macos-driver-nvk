#!/usr/bin/env python3
"""Build an isolated native Vulkan runtime. Never installs a system library."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

repo = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--renderer', type=Path, default=Path('/Library/GPUBundles/nvmtl/libvulkan_nouveau.dylib'))
parser.add_argument('--headers', type=Path, default=Path.home() / 'Vulkan-Headers/include')
args = parser.parse_args()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=False)
lib = out / 'lib'
lib.mkdir()
renderer = lib / 'libnvk_renderer.dylib'
shutil.copyfile(args.renderer, renderer)
renderer.chmod(0o755)
commands = [
    ['install_name_tool', '-id', '@loader_path/libnvk_renderer.dylib', str(renderer)],
    ['codesign', '--force', '--sign', '-', '--timestamp=none', str(renderer)],
    ['xcrun', 'clang++', '-arch', 'x86_64', '-mmacosx-version-min=15.0', '-dynamiclib',
     '-std=c++17', '-fobjc-arc', '-O2', '-Wall', '-Wextra', '-Wno-missing-field-initializers',
     '-Werror=unguarded-availability-new',
     '-I' + str(args.headers), str(repo / 'nvk/native-macos/bridge.mm'),
     '-framework', 'Foundation', '-framework', 'QuartzCore', '-framework', 'Metal',
     '-framework', 'IOSurface', '-framework', 'IOKit',
     '-Wl,-install_name,@loader_path/libnvk_minecraft.dylib',
     '-o', str(lib / 'libnvk_minecraft.dylib')],
    ['codesign', '--force', '--sign', '-', '--timestamp=none', str(lib / 'libnvk_minecraft.dylib')],
]
(out / 'COMMANDS.json').write_text(json.dumps(commands, indent=2) + '\n')
for index, cmd in enumerate(commands):
    with (out / ('build-' + str(index) + '.log')).open('w') as log:
        result = subprocess.run(cmd, cwd=repo, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        print((out / ('build-' + str(index) + '.log')).read_text()[-9000:])
        raise SystemExit(result.returncode)
for binary in lib.glob('*.dylib'):
    subprocess.run(['codesign', '--verify', '--strict', str(binary)], check=True)
    subprocess.run(['lipo', str(binary), '-verify_arch', 'x86_64'], check=True)
entry = lib / 'libnvk_minecraft.dylib'
digest = hashlib.sha256(entry.read_bytes()).hexdigest()
manifest = {
    'schema.version': '1', 'target.os': 'macos', 'target.arch': 'x86_64',
    'minecraft.version': '26.3', 'runtime.id': 'nvk-macos-gpu-copy-' + digest[:12],
    'library.path': 'lib/libnvk_minecraft.dylib', 'library.sha256': digest,
    'presentation': 'gpu-copy',
}
(out / 'nvk-bridge-runtime.properties').write_text(
    '\n'.join(k + '=' + v for k, v in manifest.items()) + '\n')
# The same frontend is a complete ICD: it owns Metal surfaces and swapchains,
# which the raw renderer does not. Standard Vulkan apps (mpv, vkconfig) can use
# this manifest instead of the surface-less nvk_icd.json.
icd = {
    'file_format_version': '1.0.0',
    'ICD': {'library_path': './lib/libnvk_minecraft.dylib', 'api_version': '1.4.363'},
}
(out / 'nvk_bridge_icd.json').write_text(json.dumps(icd, indent=2) + '\n')
source_names = ['nvk/native-macos/bridge.mm','plugin/nvmtl_fd_token.h','plugin/nvmtl_rebind_import.h', 'nvk/native-macos/present_hud.h', 'nvk/native-macos/build.py']
record = {
    'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip(),
    'source_uncommitted': bool(subprocess.check_output(
        ['git', 'status', '--porcelain', '--untracked-files=no', '--', *source_names], cwd=repo, text=True).strip()),
    'presentation': 'gpu-copy',
    'renderer_source': str(args.renderer),
    'renderer_original_sha256': hashlib.sha256(args.renderer.read_bytes()).hexdigest(),
    'files': {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (*lib.glob('*.dylib'), out / 'nvk_bridge_icd.json')},
    'source_files': {name: hashlib.sha256((repo / name).read_bytes()).hexdigest() for name in source_names},
}
(out / 'BUILD.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record, indent=2))
