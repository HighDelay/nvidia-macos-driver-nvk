#!/usr/bin/env python3
"""Validate pinned Mesa sources, then apply the native Zink overlay."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
from opengl_api import requirements

MESA_COMMIT = '17ca6174dcc6cb22059ac343bc29f8af7800f42e'
ARCHIVE_SHA256 = '1328a4aaba2260c005ff46c88d572403f0b677cd87580050f4d1bfafe56388f0'
# GitHub regenerated the archive with .gitattributes CSV CRLF checkout rules.
# All 12,909 blobs were checked against the same pinned Git tree; only those
# line endings differ. Keep accepting existing verified build inputs too.
ARCHIVE_SHA256_CRLF = '79c8a65a5a5cf6968368e0b7338726465fc21444cc5d5583716eaf685ce879cf'
ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ('nvk_zink.c', 'nvk_zink.h', 'nvk_zink_private.h', 'nvk_iosurface.c',
            'nvk_present.c', 'nvk_present.h', 'meson.build', 'zink_sync_probe.c')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_changed(path,data):
    if not path.is_file() or path.read_bytes() != data:
        path.write_bytes(data)


def prepare(source, archive=None):
    patch = ROOT / 'zink/mesa-zink-nvk.patch'
    changed = re.findall(r'^\+\+\+ b/(.+)$', patch.read_text(), re.M)
    identity = {'commit': MESA_COMMIT}
    expected = {}
    # Internal Zink resources are accessed directly by the presentation bridge.
    # Patch applicability alone cannot establish a matching Mesa ABI.
    if archive:
        archive_digest = digest(archive.read_bytes())
        if archive_digest not in (ARCHIVE_SHA256, ARCHIVE_SHA256_CRLF):
            raise ValueError('Mesa archive does not match the pinned source digest')
        identity['archive_sha256'] = archive_digest
        with tarfile.open(archive) as tar:
            for member in tar:
                name = Path(*Path(member.name).parts[1:])
                if not name.parts: continue
                target = source / name
                if member.issym():
                    if not target.is_symlink() or os.readlink(target) != member.linkname:
                        raise ValueError('Modified Mesa source link: '+str(name))
                elif member.isfile():
                    original = tar.extractfile(member).read()
                    if str(name) in changed: expected[str(name)] = original
                    elif not target.is_file() or digest(target.read_bytes()) != digest(original):
                        raise ValueError('Modified Mesa source: '+str(name))
    else:
        commit = subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()
        if commit != MESA_COMMIT:
            raise ValueError('Use a separate Mesa checkout at '+MESA_COMMIT)
        dirty = subprocess.check_output(['git','diff','--name-only','HEAD'],cwd=source,text=True).splitlines()
        if set(dirty)-set(changed):
            raise ValueError('Unrelated changes in Mesa source: '+', '.join(sorted(set(dirty)-set(changed))))
        expected = {name:subprocess.check_output(['git','show','HEAD:'+name],cwd=source) for name in changed}
    with tempfile.TemporaryDirectory(prefix='zink-overlay-') as temp:
        clean = Path(temp)
        for name, data in expected.items():
            path = clean/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data)
        subprocess.run(['git','apply',str(patch)],cwd=clean,check=True)
        for name in changed:
            actual = (source/name).read_bytes()
            if actual not in (expected[name], (clean/name).read_bytes()):
                raise ValueError('Unrecognized edits in Mesa overlay source: '+name)
        # Overwrite only the verified overlay files. No unrelated tree is patched.
        for name in changed: write_changed(source/name,(clean/name).read_bytes())
    target = source / 'src/gallium/targets/nvk-zink'
    target.mkdir(parents=True,exist_ok=True)
    for name in FRONTEND:
        directory = 'tools' if name == 'zink_sync_probe.c' else 'zink'
        write_changed(target/name,(ROOT/directory/name).read_bytes())
    registry = source/'src/mesa/glapi/glapi/registry/gl.xml'
    contract = requirements(registry)
    contract.update(mesa_commit=MESA_COMMIT, registry_sha256=digest(registry.read_bytes()),
                    registry_license='Apache-2.0', registry_copyright='Copyright 2013-2020 The Khronos Group Inc.')
    write_changed(target/'cgl-api-requirements.json', (json.dumps(contract,sort_keys=True)+'\n').encode())
    identity['overlay_sha256'] = digest(patch.read_bytes())
    identity['frontend_sha256'] = {name:digest((target/name).read_bytes()) for name in FRONTEND}
    (target/'MESA-SOURCE.json').write_text(json.dumps(identity,indent=2)+'\n')
    print('Pinned native Zink overlay ready: '+str(source))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source',type=Path)
    ap.add_argument('--archive',type=Path,help='Verified 17ca6174 source archive for a checkout without .git')
    args = ap.parse_args()
    prepare(args.source.resolve(),args.archive)


if __name__ == '__main__':
    main()
