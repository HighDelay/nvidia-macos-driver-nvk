#!/usr/bin/env python3
"""Build a private Wine frontend, reusing the native Minecraft renderer/WSI."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

repo = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--renderer', type=Path, required=True)
parser.add_argument('--headers', type=Path, required=True)
args = parser.parse_args()
root = args.output.resolve()
root.mkdir(parents=True, exist_ok=False)
lib = root/'lib'
lib.mkdir()
renderer = lib/'libnvk_renderer.dylib'
shutil.copyfile(args.renderer, renderer)
renderer.chmod(0o755)
entry = lib/'libnvk_wine.dylib'
commands = [
    ['codesign', '--verify', '--strict', str(renderer)],
    ['xcrun','clang++','-arch','x86_64','-mmacosx-version-min=15.0','-dynamiclib',
     '-std=c++17','-fobjc-arc','-O2','-Wall','-Wextra','-Wno-missing-field-initializers',
     '-Werror=unguarded-availability-new','-DNVKBRIDGE_WINE=1','-I'+str(args.headers),
     str(repo/'nvk/native-macos/bridge.mm'),'-framework','Foundation','-framework','QuartzCore',
     '-framework','Metal','-framework','IOSurface','-framework','IOKit',
     '-Wl,-install_name,@loader_path/libnvk_wine.dylib','-o',str(entry)],
    ['codesign','--force','--sign','-','--timestamp=none',str(entry)],
    ['codesign','--verify','--strict',str(entry)],
]
for i,cmd in enumerate(commands):
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    (root/f'build-{i}.log').write_text(result.stdout)
    if result.returncode:
        raise SystemExit(result.stdout)
files = {str(f.relative_to(root)):hashlib.file_digest(f.open('rb'),'sha256').hexdigest() for f in lib.glob('*.dylib')}
record = {'schema_version':1, 'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),
          'source_diff':subprocess.check_output(['git','diff','--','nvk/native-macos/bridge.mm'],cwd=repo,text=True),
          'source_files':{name:hashlib.sha256((repo/name).read_bytes()).hexdigest() for name in
                          ['nvk/native-macos/bridge.mm','plugin/nvmtl_fd_token.h','plugin/nvmtl_rebind_import.h','nvk/native-macos/present_hud.h','nvk/wine-macos/build.py']},
          'platform':{'os':'macos','arch':'x86_64','minimum_macos':'15.5'},
          'entry':{'path':str(entry.relative_to(root)),'sha256':files[str(entry.relative_to(root))]},
          'renderer':{'path':str(renderer.relative_to(root)),'sha256':files[str(renderer.relative_to(root))]},
          'presentation':{'implementation':'gpu-copy','hud':'gpu-copy-cached-bitmap','wine_child_options':1},'commands':commands,
          'validation':{'wine_window':'NOT RUN','d3d11':'NOT RUN','zzz':'NOT RUN'},
          'files':files}
for key, binary in [('entry',entry),('renderer',renderer)]:
    record[key]['dependencies'] = subprocess.check_output(['otool','-L',str(binary)],text=True)
    record[key]['deployment'] = subprocess.check_output(['vtool','-show-build',str(binary)],text=True)
(root/'BUILD.json').write_text(json.dumps(record,indent=2)+'\n')
(root/'nvk-wine-runtime.json').write_text(json.dumps(record,indent=2)+'\n')
(root/'SHA256SUMS').write_text(''.join(f'{sha}  {name}\n' for name,sha in sorted(files.items())))
print(json.dumps({'root':str(root),'files':files},indent=2))
