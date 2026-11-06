#!/usr/bin/env python3
"""Build the pinned mickys codec library with the fork's Turing overlay."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PIN = json.loads((ROOT / 'video/nvenc-source.json').read_text())


def run(*args, cwd=None):
    return subprocess.check_output(list(map(str, args)), cwd=cwd, stderr=subprocess.STDOUT, text=True)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checkout(path, identity, fetch):
    if not path.exists():
        if not fetch:
            raise ValueError('Missing checkout; use --fetch: ' + str(path))
        path.mkdir(parents=True)
        run('git', 'init', '--quiet', path)
        run('git', 'remote', 'add', 'origin', identity['repository'], cwd=path)
        if identity.get('sparse'):
            run('git', 'sparse-checkout', 'set', *identity['sparse'], cwd=path)
        run('git', 'fetch', '--quiet', '--depth', '1', '--filter=blob:none', 'origin', identity['commit'], cwd=path)
        run('git', 'checkout', '--quiet', '--detach', 'FETCH_HEAD', cwd=path)
    if run('git', 'rev-parse', 'HEAD', cwd=path).strip() != identity['commit']:
        raise ValueError('Unexpected source revision: ' + str(path))


def prepare(source, fetch=False):
    checkout(source, PIN['source'], fetch)
    patch = ROOT / 'video' / PIN['overlay']
    names = re.findall(r'^\+\+\+ b/(.+)$', patch.read_text(), re.M)
    if not names or any(Path(n).is_absolute() or '..' in Path(n).parts for n in names):
        raise ValueError('Invalid codec overlay paths')
    changed = set(run('git', 'diff', '--name-only', 'HEAD', cwd=source).splitlines())
    if changed - set(names):
        raise ValueError('Unrelated edits in codec checkout: ' + ', '.join(sorted(changed - set(names))))
    with tempfile.TemporaryDirectory(prefix='nvenc-overlay-') as directory:
        clean = Path(directory)
        originals = {}
        for name in names:
            original = subprocess.check_output(['git', 'show', 'HEAD:' + name], cwd=source)
            originals[name] = original
            target = clean / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(original)
        run('git', 'apply', patch, cwd=clean)
        for name in names:
            target = source / name
            if target.is_symlink() or target.read_bytes() not in (originals[name], (clean / name).read_bytes()):
                raise ValueError('Unrecognized codec source edits: ' + name)
        for name in names:
            (source / name).write_bytes((clean / name).read_bytes())
    for name in ('open-gpu-kernel-modules', 'open-gpu-doc'):
        dependency = source / 'vendor' / name
        checkout(dependency, PIN['dependencies'][name], fetch)
        if run('git', 'status', '--porcelain', '--untracked-files=all', cwd=dependency).strip():
            raise ValueError('Modified dependency checkout: ' + str(dependency))
    spi = PIN['dependencies']['webkit-spi']
    for name, expected in spi['files'].items():
        target = source / 'vendor/webkit-spi' / name
        if not target.exists() and fetch:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(urllib.request.urlopen(spi['url'] + name, timeout=60).read())
        if target.is_symlink() or not target.is_file() or sha(target) != expected:
            raise ValueError('Unexpected WebKit SPI header: ' + name)
    return patch


def build(source, output, fetch=False):
    source = source.resolve()
    output = output.resolve(strict=False)
    if output.exists() or output.is_symlink() or source == output or source in output.parents:
        raise ValueError('Use a new output directory outside the source checkout')
    patch = prepare(source, fetch)
    # Compile only files present in the pinned Git tree, never extra local *.c.
    tracked = run('git', 'ls-tree', '-r', '--name-only', 'HEAD', 'src', 'vt', cwd=source).splitlines()
    units = ['vt/nvencvt.c', 'vt/nvencvt_dec.c'] + sorted(n for n in tracked if n.startswith('src/') and n.endswith('.c'))
    inputs = {n: sha(source / n) for n in tracked if Path(n).suffix in ('.c', '.h')}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.nvenc-build-', dir=output.parent) as directory:
        work = Path(directory) / 'runtime'
        work.mkdir()
        binary = work / 'libnvencvt.dylib'
        version = PIN['source']['commit'][:7] + '+nvk-' + sha(patch)[:12]
        command = ['xcrun', 'clang', '-arch', 'x86_64', '-mmacosx-version-min=15.0', '-O2', '-Wall',
                   '-dynamiclib', '-install_name', '@rpath/libnvencvt.dylib',
                   '-DNVENC_VERSION="' + version + '"', '-o', str(binary)] + units
        includes = ['-I' + include for include in
                    ('src', 'vendor/webkit-spi', 'vendor/open-gpu-kernel-modules/src/common/sdk/nvidia/inc',
                     'vendor/open-gpu-kernel-modules/src/nvidia/arch/nvalloc/unix/include', 'vendor/open-gpu-doc/classes/video')]
        command += includes
        for framework in ('IOKit', 'CoreFoundation', 'CoreMedia', 'CoreVideo', 'VideoToolbox', 'Accelerate', 'IOSurface'):
            command += ['-framework', framework]
        log = run(*command, cwd=source)
        run('codesign', '--force', '--sign', '-', '--timestamp=none', binary)
        run('codesign', '--verify', '--strict', binary)
        run('lipo', binary, '-verify_arch', 'x86_64')
        if '_NVEncVT_RegisterCodecs' not in run('nm', '-gU', binary):
            raise ValueError('Codec library lacks scoped registration')
        platform = run('vtool', '-show-build', binary)
        if not re.search(r'\bminos 15\.0\b', platform):
            raise ValueError('Unexpected codec deployment target')
        licenses = work / 'LICENSES'
        licenses.mkdir()
        shutil.copyfile(source / 'LICENSE', licenses / 'nvenc-MIT.txt')
        # Keep each distinct notice from the headers actually included, with
        # their original copyright ranges rather than one representative file.
        dependencies = run('xcrun', 'clang', '-arch', 'x86_64', '-mmacosx-version-min=15.0',
                           '-MM', '-MT', 'codec-dependencies', *includes, *units, cwd=source)
        notices = {'NVIDIA-MIT.txt': set(), 'WebKit-BSD.txt': set()}
        for name in sorted(set(shlex.split(dependencies.replace('\\\n', ' ')))):
            header = Path(name)
            if header.suffix != '.h' or not header.parts or header.parts[0] != 'vendor':
                continue
            text = (source / header).read_text()
            notice = text.split('*/', 1)[0] + '*/\n'
            nvidia = header.parts[1] in ('open-gpu-doc', 'open-gpu-kernel-modules')
            if nvidia and 'Permission is hereby granted' not in notice:
                raise ValueError('Unrecognized NVIDIA header license: ' + name)
            if not nvidia and (header.parts[1] != 'webkit-spi' or 'Redistribution and use' not in notice):
                raise ValueError('Unrecognized SPI header license: ' + name)
            notices['NVIDIA-MIT.txt' if nvidia else 'WebKit-BSD.txt'].add(notice)
        for name, texts in notices.items():
            if not texts:
                raise ValueError('Missing codec dependency notices: ' + name)
            (licenses / name).write_text('\n'.join(sorted(texts)))
        shutil.copyfile(patch, work / 'source-changes.patch')
        shutil.copyfile(ROOT / 'video/INSTALL.md', work / 'INSTALL.md')
        record = {'schema_version': 1, 'kind': 'nvenc-codecs', 'source': PIN['source'], 'dependencies': PIN['dependencies'],
                  'overlay_sha256': sha(patch), 'source_files': inputs, 'version': version,
                  'registration_api': {'version': 2, 'symbol': 'NVEncVT_RegisterCodecs',
                                       'decoder_scope': 1, 'encoder_scope': 2},
                  'target': {'os': 'macos', 'arch': 'x86_64', 'minimum_macos': '15.0'},
                  'installation': {'library': '/Library/GPUBundles/NVMTLDriver.bundle/Contents/MacOS/libnvencvt.dylib',
                                   'records': '/Library/GPUBundles/nvmtl/video'},
                  'compiler': run('xcrun', 'clang', '--version').splitlines()[0],
                  'sdk': run('xcrun', '--sdk', 'macosx', '--show-sdk-version').strip(),
                  'turing_defaults': {'additional_decoders': ['vp08', 'vp09'], 'replace_stock_encoders': False, 'av1': False},
                  'files': {p.relative_to(work).as_posix(): sha(p) for p in sorted(work.rglob('*')) if p.is_file()}}
        (work / 'NVENC-BUILD.json').write_text(json.dumps(record, indent=2) + '\n')
        output_log = output.parent / (output.name + '.build.log')
        if output_log.exists():
            raise ValueError('Build log already exists: ' + str(output_log))
        output_log.write_text(log)
        work.rename(output)
    print('Codec runtime ready: ' + str(output))
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--fetch', action='store_true', help='fetch missing pinned sources and headers into source/vendor')
    args = parser.parse_args()
    try:
        build(args.source, args.output, args.fetch)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, 'STOP: ' + str(error) + '\n' + getattr(error, 'output', ''))


if __name__ == '__main__':
    main()
