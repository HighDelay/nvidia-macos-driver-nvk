#!/usr/bin/env python3
"""Record and stage the matched, separately built optional OpenCore helper."""
import argparse
import json
from pathlib import Path
import plistlib
import shutil
import subprocess

import stage_package as integrity

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'kexts/NVWebKitFixup'
SOURCES = tuple('kexts/NVWebKitFixup/'+name for name in
                ('nvwebkitfixup.cpp', 'policy.hpp', 'compatibility.hpp', 'Info.plist', 'build.sh'))
BUNDLE = 'NVWebKitFixup.kext'


def files(root):
    integrity.validate_links(root)
    result = {}
    for path in sorted(root.rglob('*')):
        integrity.require(not path.is_symlink(), 'Helper bundles must not contain symlinks')
        integrity.require(path.name != '.DS_Store' and not path.name.startswith('._'), 'Metadata in helper bundle')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = integrity.digest(path)
    return result


def record(output, lilu_sdk, kernel_sdk):
    bundle = output/BUNDLE
    subprocess.run(['codesign', '--verify', '--strict', str(bundle)], check=True)
    info = plistlib.loads((bundle/'Contents/Info.plist').read_bytes())
    data = {'version': info['CFBundleVersion'], 'files': files(bundle),
            'source_sha256': {p: integrity.digest(ROOT/p) for p in SOURCES},
            'lilu_sha256': integrity.digest(lilu_sdk/'Contents/MacOS/Lilu'),
            'lilu_sdk_sha256': {str(p.relative_to(lilu_sdk)):integrity.digest(p)
                for name in ('Headers', 'Library')
                for p in sorted((lilu_sdk/'Contents/Resources'/name).rglob('*'))
                if p.is_file() and not p.is_symlink()},
            'kernel_sdk_libraries_sha256': {str(p.relative_to(kernel_sdk)):integrity.digest(p)
                for p in sorted((kernel_sdk/'Library/x86_64').glob('lib*')) if p.is_file()},
            'kernel_sdk_headers_sha256': {str(p.relative_to(kernel_sdk)):integrity.digest(p)
                for p in sorted((kernel_sdk/'Headers').rglob('*')) if p.is_file() and not p.is_symlink()}}
    (output/'BUILD.json').write_text(json.dumps(data, indent=2)+'\n')


def stage(payload, build, run):
    integrity.require(build is not None, 'Supply --webkit-fixup-build with a matched NVWebKitFixup build')
    build = Path(build).resolve(strict=True)
    integrity.regular(build/'BUILD.json')
    identity = json.loads((build/'BUILD.json').read_text())
    bundle = build/BUNDLE
    integrity.bundle(bundle, 'NVWebKitFixup')
    expected = plistlib.loads((SOURCE/'Info.plist').read_bytes())
    info = plistlib.loads((bundle/'Contents/Info.plist').read_bytes())
    integrity.require(info == expected and identity.get('version') == expected['CFBundleVersion'], 'Stale helper bundle metadata')
    integrity.require(identity.get('files') == files(bundle), 'Helper build file hashes do not match')
    integrity.require(identity.get('source_sha256') == {p: integrity.digest(ROOT/p) for p in SOURCES},
                      'Helper sources changed since building')
    run('lipo', bundle/'Contents/MacOS/NVWebKitFixup', '-verify_arch', 'x86_64')
    run('codesign', '--verify', '--strict', bundle)
    destination = payload/'Optional/NVWebKitFixup'
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    shutil.copytree(bundle, destination/BUNDLE, copy_function=shutil.copyfile)
    for src, name in [(ROOT/'package/webkit_fixup_efi.py', 'efi.py'),
                      (SOURCE/'Kernel-Add.plist', 'Kernel-Add.plist'),
                      (ROOT/'docs/SAFARI-WEBGL.md', 'README.md'), (SOURCE/'LICENSE', 'LICENSE')]:
        shutil.copyfile(src, destination/name)
        (destination/name).chmod(0o644)
    licenses = destination/'LICENSES'
    licenses.mkdir()
    for name in ('Lilu-BSD3.txt', 'MacKernelSDK-APSL-2.0.txt'):
        shutil.copyfile(ROOT/'licenses'/name, licenses/name)
    return identity


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--lilu-sdk', type=Path, required=True)
    parser.add_argument('--kernel-sdk', type=Path, required=True)
    args = parser.parse_args()
    record(args.output, args.lilu_sdk, args.kernel_sdk)
