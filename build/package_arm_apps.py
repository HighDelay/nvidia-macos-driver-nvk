#!/usr/bin/env python3
"""Package optional ARM scripts and the pinned signed upstream runtime separately."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'tools/arm-apps'


def run(*args):
    return subprocess.check_output([str(a) for a in args], text=True, stderr=subprocess.STDOUT)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fetch(url, path):
    context = ssl.create_default_context(cafile='/etc/ssl/cert.pem' if Path('/etc/ssl/cert.pem').is_file() else None)
    with urllib.request.urlopen(url, context=context, timeout=120) as response, path.open('xb') as output:
        count = 0
        while True:
            data = response.read(1024 * 1024)
            if not data:
                break
            count += len(data)
            if count > 128 * 1024 * 1024:
                raise ValueError('ARM download exceeds the size limit')
            output.write(data)


def build(output, version, archive=None, signature=None):
    if sys.platform != 'darwin' or not re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}', version):
        raise ValueError('build on macOS with a numeric package version')
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('output directory must be new')
    if (archive is None) != (signature is None):
        raise ValueError('provide both upstream archive and signature, or neither')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.arm-scripts-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'output'
        package = stage / 'NVIDIA-ARM-Scripts'
        package.mkdir(parents=True)
        for name in ('arm_apps.py', 'source.json', 'verify-signature.swift', 'README.md', 'ARM-Apps.command', 'Install-ARM.command', 'Translate-App.command'):
            shutil.copyfile(SCRIPTS / name, package / name)
            (package / name).chmod(0o755 if name.endswith('.command') else 0o644)
        shutil.copyfile(ROOT / 'LICENSE', package / 'LICENSE')
        verifier = package / 'verify-signature'
        run('xcrun', 'swiftc', '-O', '-target', 'x86_64-apple-macos15.0', package / 'verify-signature.swift', '-o', verifier)
        run('codesign', '-f', '-s', '-', verifier)
        run('codesign', '--verify', '--strict', verifier)
        run('lipo', verifier, '-verify_arch', 'x86_64')
        pin = json.loads((package / 'source.json').read_text())
        vendor = package / 'upstream'; vendor.mkdir()
        archived = vendor / pin['archive']; sig = vendor / (pin['archive'] + '.sig')
        if archive:
            shutil.copyfile(archive, archived); shutil.copyfile(signature, sig)
        else:
            fetch(pin['url'], archived); fetch(pin['signature_url'], sig)
        spec = importlib.util.spec_from_file_location('packaged_arm_apps', package / 'arm_apps.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        data = module.verified_archive()
        with tempfile.TemporaryDirectory(prefix='arm-input-check-', dir=temporary) as unpacked:
            payload = module.unpack(data, unpacked)
            result = subprocess.run(['/usr/bin/shasum', '-a', '256', '--strict', '-c', 'SHA256SUMS'], cwd=payload,
                                    capture_output=True, text=True)
            if result.returncode:
                raise ValueError('upstream internal checksums failed: ' + result.stderr)
        identity = {
            'schema_version': 1, 'package_version': version, 'kind': 'optional-arm-scripts',
            'source_commit': run('git', '-C', ROOT, 'rev-parse', 'HEAD').strip(),
            'source_dirty': bool(run('git', '-C', ROOT, 'status', '--porcelain', '--untracked-files=no').strip()),
            'runtime': pin, 'runtime_signature_verified': True, 'signature_checker_sha256': digest(verifier),
            'installation': '/Library/NullMoth/arm', 'modifies_driver': False, 'modifies_opencore': False,
        }
        (package / 'PACKAGE-IDENTITY.json').write_text(json.dumps(identity, indent=2) + '\n')
        (package / 'SHA256SUMS').write_text(''.join(digest(p) + '  ' + p.relative_to(package).as_posix() + '\n'
            for p in sorted(package.rglob('*')) if p.is_file() and p.name != 'SHA256SUMS'))
        name = f'nvidia-arm-scripts-{version}.tar.gz'
        artifact = stage / name
        with tarfile.open(artifact, 'w:gz', format=tarfile.PAX_FORMAT) as tar:
            for path in [package, *sorted(package.rglob('*'))]:
                if path.name == '.DS_Store' or path.name.startswith('._') or path.is_symlink():
                    raise ValueError('unexpected metadata or symlink in scripts package')
                info = tar.gettarinfo(str(path), path.relative_to(stage).as_posix())
                info.uid = info.gid = 0; info.uname = info.gname = 'root'; info.mtime = 0
                info.pax_headers = {}
                if path.is_file():
                    with path.open('rb') as data:
                        tar.addfile(info, data)
                else:
                    tar.addfile(info)
        (stage / 'SHA256SUMS').write_text(digest(artifact) + '  ' + name + '\n')
        stage.rename(output)
        return output / name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--version', default='2.7.1')
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--signature', type=Path)
    args = parser.parse_args()
    print(build(args.output, args.version, args.archive, args.signature))


if __name__ == '__main__':
    main()
