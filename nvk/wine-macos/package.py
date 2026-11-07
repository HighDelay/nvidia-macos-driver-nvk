#!/usr/bin/env python3
"""Assemble the pinned, app-local x64 Wine/DXVK/NVK candidate."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tarfile

WINE_SHA='89fa7e90fb626523a90d5867a03c6be785d017176739c6320a3b86c7838c3a35'
DXVK_SHA='40565b4a724aadc4433fa4e010b4b23916d9b1f1baeee64e17186db94f54e608'
DXVK_COMMIT='b1a1c99ab52b687cf950d62c88bc2fa316b41663'
repo=Path(__file__).resolve().parents[2]
parser=argparse.ArgumentParser(description=__doc__)
for name in ['output','renderer','headers','wine-archive','dxvk-archive','dxvk-source','mesa-license']:
    parser.add_argument('--'+name,type=Path,required=True)
parser.add_argument('--wine-cert-fragment',type=Path)
args=parser.parse_args()
def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
for path,expected in [(args.wine_archive,WINE_SHA),(args.dxvk_archive,DXVK_SHA)]:
    if digest(path)!=expected:raise SystemExit('Unrecognized release archive: '+str(path))
if subprocess.check_output(['git','rev-parse','HEAD'],cwd=args.dxvk_source,text=True).strip()!=DXVK_COMMIT:
    raise SystemExit('DXVK license/source must match v3.1.1')
root=args.output.resolve()
subprocess.run([sys.executable,str(repo/'nvk/wine-macos/build.py'),'--output',str(root),'--renderer',str(args.renderer.resolve()),'--headers',str(args.headers.resolve())],check=True)
commands=[]
def run(command):
    commands.append(command)
    r=subprocess.run(command,cwd=root,capture_output=True,text=True)
    (root/f'package-{len(commands)}.log').write_text(r.stdout+r.stderr)
    if r.returncode:raise SystemExit(r.stdout+r.stderr)
stage=root/'.vendor-stage'
stage.mkdir()
with tarfile.open(args.wine_archive) as archive:archive.extractall(stage,filter='data')
shutil.move(stage/'wine',root/'wine')
shutil.rmtree(stage)
prerequisites={}
if args.wine_cert_fragment:
    path=root/'wine/share/wine/wine.inf'
    lines=path.read_text().splitlines()
    start=next(i for i,line in enumerate(lines) if line.strip()=='; URL Associations')
    end=next(i for i in range(start+1,len(lines)) if not lines[i].strip())
    fragment=args.wine_cert_fragment.read_text().strip().splitlines()
    path.write_text('\n'.join(lines[:end]+['']+fragment+lines[end:])+'\n')
    prerequisites={'wine_inf_cert_fragment_sha256':digest(args.wine_cert_fragment),
                   'scope':'Windows registry in each private prefix; no macOS certificate/hosts modification'}
dxvk=root/'dxvk/x64';dxvk.mkdir(parents=True)
with tarfile.open(args.dxvk_archive) as archive:
    for name in ['d3d11.dll','dxgi.dll']:
        with archive.extractfile('dxvk-3.1.1/x64/'+name) as source:
            (dxvk/name).write_bytes(source.read())
entitlements=root/'wine-task-port.entitlements'
entitlements.write_bytes(plistlib.dumps({'com.apple.security.get-task-allow':True}))
signing=[]
for rel in ['wine/bin/wine','wine/lib/wine/x86_64-unix/wine']:
    path=root/rel;before=digest(path)
    run(['codesign','--force','--sign','-','--timestamp=none','--entitlements',str(entitlements),str(path)])
    signing.append({'path':rel,'original_sha256':before,'final_sha256':digest(path)})
(root/'bin').mkdir()
helper=root/'bin/nvk-runtime'
run(['xcrun','clang++','-arch','x86_64','-mmacosx-version-min=15.0','-std=c++17','-fobjc-arc','-O2','-Wall','-Wextra','-Werror',
     str(repo/'nvk/wine-macos/setup.mm'),'-framework','Foundation','-o',str(helper)])
run(['codesign','--force','--sign','-','--timestamp=none',str(helper)])
(root/'lib/libMoltenVK.dylib').symlink_to('libnvk_wine.dylib')
licenses=root/'LICENSES';licenses.mkdir()
shutil.copyfile(repo/'LICENSE',licenses/'frontend-PolyForm-Noncommercial-1.0.0.txt')
shutil.copyfile(args.dxvk_source/'LICENSE',licenses/'DXVK-zlib.txt')
shutil.copyfile(args.mesa_license,licenses/'Mesa-license.rst')
run(['curl','-fLsS','https://raw.githubusercontent.com/wine-mirror/wine/wine-11.0/COPYING.LIB','-o',str(licenses/'Wine-LGPL-2.1.txt')])
critical=['lib/libnvk_wine.dylib','lib/libnvk_renderer.dylib','bin/nvk-runtime','dxvk/x64/d3d11.dll','dxvk/x64/dxgi.dll','wine/share/wine/wine.inf',
          'wine/bin/wine','wine/bin/wineserver','wine/lib/wine/x86_64-unix/wine']
critical += ['wine/lib/wine/x86_64-unix/'+name+'.so' for name in ['ntdll','win32u','winemac','winevulkan']]
critical += ['wine/lib/wine/x86_64-windows/'+name+'.dll' for name in ['ntdll','kernel32','kernelbase','win32u','vulkan-1','winevulkan']]
files={name:digest(root/name) for name in critical}
runtime_id='nvk-wine-dxvk-3.1.1-'+hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()[:12]
base=json.loads((root/'BUILD.json').read_text())
manifest={'schema_version':1,'runtime_id':runtime_id,'platform':base['platform'],
          'entry':base['entry'],'renderer':base['renderer'],'files':files,
          'wine':{'executable':'wine/bin/wine','version':'wine-11.0 (YAAGL crossover 11.0-1)',
                  'archive_sha256':WINE_SHA,'source_reference':'https://github.com/riverfog7/macports-wine',
                  'source_limit':'Exact CrossOver patch revision is not specified by the binary release; archive pinned and actual loader disassembly inspected.',
                  'host_loader_mode':'direct','selection_mechanism':'Scoped DYLD_LIBRARY_PATH to lib; libMoltenVK alias resolves to libnvk_wine, verified in Windows process dyld logs.',
                  'task_port_compatibility':signing},
          'dxvk':{'version':'3.1.1','source_revision':DXVK_COMMIT,'archive_sha256':DXVK_SHA,'modified':False},
          'presentation':{'implementation':'gpu-copy','public_external_memory_sharing':False,'hdr':False},
          'validation':{'wine_window':'NOT RUN on final package','d3d11':'NOT RUN on final package','zzz':'NOT RUN'}}
manifest['wine']['prerequisites']=prerequisites
(root/'nvk-wine-runtime.json').write_text(json.dumps(manifest,indent=2)+'\n')
base['package_commands']=commands;base['package_signing']=signing;base['package_files']=files
base['source_files']={str(path.relative_to(repo)):digest(path) for path in [repo/'nvk/native-macos/bridge.mm',repo/'plugin/nvmtl_fd_token.h',repo/'plugin/nvmtl_rebind_import.h',repo/'nvk/wine-macos/setup.mm',Path(__file__).resolve(),repo/'nvk/wine-macos/build.py']}
(root/'BUILD.json').write_text(json.dumps(base,indent=2)+'\n')
run([str(helper),'check',str(root)])
# Full delivery checksums are separate from the fast critical-file launch check.
with (root/'SHA256SUMS').open('w') as sums:
    for file in sorted(root.rglob('*')):
        if file.is_file() and not file.is_symlink() and file.name!='SHA256SUMS':
            sums.write(f'{digest(file)}  {file.relative_to(root)}\n')
print(json.dumps({'root':str(root),'runtime_id':runtime_id,'critical_files':len(files)},indent=2))
