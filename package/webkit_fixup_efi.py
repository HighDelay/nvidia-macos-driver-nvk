#!/usr/bin/env python3
"""Install the optional WebKit helper in an explicitly selected OpenCore EFI."""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import uuid

NAME = 'NVWebKitFixup.kext'
IDENTITY = 'com.highdelay.NVWebKitFixup'
EFI_GUID = 'C12A7328-F81F-11D2-BA4B-00A0C93EC93B'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def plain(path, directory=False):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),
            'Expected a real directory/file, not a link: ' + str(path))


def no_links(path):
    for part in (path, *path.parents):
        require(not part.is_symlink(), 'Symlink in target path: ' + str(part))


def digest(path):
    plain(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(path):
    plain(path, True)
    result = {}
    for item in sorted(path.rglob('*')):
        plain(item, item.is_dir())
        if item.is_file():
            result[item.relative_to(path).as_posix()] = digest(item)
    return result


def command(*args):
    return subprocess.check_output([str(a) for a in args], stderr=subprocess.STDOUT)


def disk_info(target):
    return plistlib.loads(command('/usr/sbin/diskutil', 'info', '-plist', target))


def esp(info):
    require(info.get('Content') == 'EFI' or info.get('PartitionType', '').upper() == EFI_GUID,
            'Select an EFI System Partition, not a normal data volume')


@contextlib.contextmanager
def target_oc(target):
    mounted_here = None
    try:
        if re.fullmatch(r'disk[0-9]+s[0-9]+', target):
            info = disk_info(target)
            esp(info)
            if not info.get('Mounted'):
                command('/usr/sbin/diskutil', 'mount', target)
                mounted_here = target
                info = disk_info(target)
            require(info.get('MountPoint'), 'EFI partition did not mount')
            oc = Path(info['MountPoint']) / 'EFI/OC'
        else:
            oc = Path(target)
            require(oc.is_absolute(), 'Supply diskXsY or the absolute mounted EFI/OC path')
            no_links(oc)
            require(oc.parts[-2:] == ('EFI', 'OC'), 'Target must end in EFI/OC')
            info = disk_info(str(oc.parent.parent))
            esp(info)
        no_links(oc)
        plain(oc, True)
        require(Path(info['MountPoint']) / 'EFI/OC' == oc, 'EFI mount and OC path disagree')
        require(not info.get('ReadOnlyVolume', False), 'EFI volume is read-only')
        plain(oc/'OpenCore.efi')
        yield oc
    finally:
        if mounted_here:
            try:
                command('/usr/sbin/diskutil', 'unmount', mounted_here)
            except subprocess.CalledProcessError:
                print('NOTE: EFI remains mounted; eject it when convenient.', file=sys.stderr)


def version(text):
    require(isinstance(text, str) and re.fullmatch(r'[0-9]+(?:\.[0-9]+){0,2}', text),
            'Unrecognized version: ' + str(text))
    parts = tuple(int(p) for p in text.split('.'))
    return parts + (0,) * (3-len(parts))


def verify_bundle(bundle, identifier, signature=True):
    tree(bundle)
    info = plistlib.loads((bundle/'Contents/Info.plist').read_bytes())
    require(info.get('CFBundleIdentifier') == identifier, 'Unexpected bundle identity: ' + str(bundle))
    executable = info.get('CFBundleExecutable')
    require(isinstance(executable, str) and '/' not in executable and executable not in ('', '.', '..'),
            'Invalid bundle executable')
    plain(bundle/'Contents/MacOS'/executable)
    command('/usr/bin/lipo', bundle/'Contents/MacOS'/executable, '-verify_arch', 'x86_64')
    if signature:
        command('/usr/bin/codesign', '--verify', '--strict', bundle)
    return info


def prepare(oc, payload):
    config_path = oc/'config.plist'
    plain(config_path)
    original = config_path.read_bytes()
    require(len(original) <= 8*1024*1024, 'OpenCore config is unexpectedly large')
    config = plistlib.loads(original)
    require(config.get('Misc', {}).get('Security', {}).get('Vault') == 'Optional' and
            not (oc/'vault.sig').exists() and not (oc/'vault.plist').exists(),
            'Vault-protected EFI requires manual installation and re-signing')
    require(config.get('Kernel', {}).get('Quirks', {}).get('DisableLinkeditJettison') is True,
            'Lilu requires DisableLinkeditJettison; configure it manually first')
    entries = config['Kernel']['Add']
    require(isinstance(entries, list) and all(isinstance(e, dict) for e in entries), 'Invalid Kernel/Add')
    plain(oc/'Kexts', True)
    lilu = []
    for index, entry in enumerate(entries):
        if not entry.get('Enabled'):
            continue
        bundle_path = entry.get('BundlePath', '')
        require(isinstance(bundle_path, str) and bundle_path and not Path(bundle_path).is_absolute()
                and '..' not in Path(bundle_path).parts, 'Unsafe Kernel/Add bundle path')
        bundle = oc/'Kexts'/bundle_path
        no_links(bundle)
        # Match the actual bundle identifier, including renamed Lilu bundles.
        plain(bundle/'Contents/Info.plist')
        info = plistlib.loads((bundle/'Contents/Info.plist').read_bytes())
        if info.get('CFBundleIdentifier') == 'as.vit9696.Lilu':
            require(entry.get('Arch', 'Any') in ('Any', 'x86_64'), 'Lilu is not enabled for x86_64')
            require(version(info['CFBundleVersion']) >= (1, 7, 2), 'Lilu 1.7.2 or later is required')
            require(not entry.get('MinKernel') or version(entry['MinKernel']) <= (24, 0, 0),
                    'Lilu MinKernel excludes Sequoia; use manual setup')
            require(not entry.get('MaxKernel') or version(entry['MaxKernel']) >= (25, 99, 99),
                    'Lilu MaxKernel restricts the helper; use manual setup')
            # Official Lilu builds may be unsigned; OpenCore injects them.
            # Check the existing dependency without re-signing/replacing it.
            verify_bundle(bundle, 'as.vit9696.Lilu', signature=False)
            lilu.append(index)
        if info.get('CFBundleIdentifier') == IDENTITY:
            require(bundle_path == NAME, 'Existing helper uses another path; use manual setup')
    require(len(lilu) == 1, 'Exactly one enabled Lilu entry is required; Lilu is never installed or replaced here')
    matches = [e for e in entries if e.get('BundlePath') == NAME]
    require(len(matches) <= 1, 'Duplicate helper entries; use manual setup')
    if (oc/'Kexts'/NAME).exists() or (oc/'Kexts'/NAME).is_symlink():
        verify_bundle(oc/'Kexts'/NAME, IDENTITY)
    verify_bundle(payload/NAME, IDENTITY)
    plain(payload/'Kernel-Add.plist')
    addition = plistlib.loads((payload/'Kernel-Add.plist').read_bytes())
    expected = {'Arch':'x86_64', 'BundlePath':NAME, 'Enabled':True,
                'ExecutablePath':'Contents/MacOS/NVWebKitFixup', 'PlistPath':'Contents/Info.plist',
                'MinKernel':'24.0.0', 'MaxKernel':'25.99.99'}
    require(set(addition) == set(expected) | {'Comment'} and
            all(addition.get(k) == v for k, v in expected.items()), 'Unrecognized helper Kernel/Add fragment')
    # Retain every other entry and setting, inserting immediately after Lilu.
    revised = [e for e in entries if e.get('BundlePath') != NAME]
    lilu_entry = entries[lilu[0]]
    revised.insert(next(i for i, e in enumerate(revised) if e is lilu_entry)+1, addition)
    config['Kernel']['Add'] = revised
    fmt = plistlib.FMT_BINARY if original.startswith(b'bplist') else plistlib.FMT_XML
    return original, plistlib.dumps(config, fmt=fmt, sort_keys=False)


def atomic(path, data):
    temporary = path.with_name('.'+path.name+'.nvwk-'+uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def install(oc, payload):
    lock = oc/'.NVWebKitFixup.lock'
    lock.mkdir()  # Never recover or follow an unknown concurrent/stale lock.
    temporary = oc/'Kexts'/('.NVWebKitFixup-'+uuid.uuid4().hex+'.kext')
    backup = None
    moved_old = placed_new = False
    previous = None
    new_tree = None
    config_path = oc/'config.plist'
    destination = oc/'Kexts'/NAME
    try:
        original, updated = prepare(oc, payload)
        old_tree = tree(destination) if destination.exists() else None
        new_tree = tree(payload/NAME)
        backups = oc/'NVWebKitFixup-backups'
        if backups.exists() or backups.is_symlink():
            plain(backups, True)
        else:
            backups.mkdir(mode=0o700)
        backup = backups/uuid.uuid4().hex
        backup.mkdir(mode=0o700)
        atomic(backup/'config.plist', original)
        previous = {'before_config_sha256':hashlib.sha256(original).hexdigest(),
                    'after_config_sha256':hashlib.sha256(updated).hexdigest(),
                    'before_kext':old_tree, 'after_kext':new_tree}
        atomic(backup/'state.json', json.dumps(previous, indent=2).encode())
        shutil.copytree(payload/NAME, temporary, copy_function=shutil.copyfile)
        require(tree(temporary) == new_tree, 'Copied helper differs from verified source')
        require(config_path.read_bytes() == original, 'Config changed during preflight; retry')
        require((tree(destination) if destination.exists() else None) == old_tree,
                'Existing helper changed during preflight; retry')
        if old_tree is not None:
            os.rename(destination, backup/NAME)
            moved_old = True
        os.rename(temporary, destination)
        placed_new = True
        atomic(config_path, updated)  # Config is activated only after the helper is in place.
        require(config_path.read_bytes() == updated and tree(destination) == new_tree, 'Post-install verification failed')
        print('WebKit helper installed after Lilu. Reboot to activate it.')
        print('EFI backup: ' + str(backup))
        return backup
    except BaseException:
        if previous is not None:
            # Never overwrite another process's later config or replacement bundle.
            current = digest(config_path)
            require(current in (previous['before_config_sha256'], previous['after_config_sha256']),
                    'Config changed externally; recovery backup: ' + str(backup))
            if current != previous['before_config_sha256']:
                atomic(config_path, original)
            if placed_new:
                require(tree(destination) == new_tree, 'Helper changed externally; recovery backup: '+str(backup))
                shutil.rmtree(destination)
            if moved_old:
                os.rename(backup/NAME, destination)
        raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
        lock.rmdir()


def restore(oc, backup):
    require(backup.parent == oc/'NVWebKitFixup-backups', 'Backup must belong to the selected EFI/OC')
    no_links(backup)
    plain(backup, True)
    lock = oc/'.NVWebKitFixup.lock'
    lock.mkdir()
    try:
        plain(backup/'state.json')
        state = json.loads((backup/'state.json').read_text())
        require(digest(backup/'config.plist') == state['before_config_sha256'], 'Backup config changed')
        require(digest(oc/'config.plist') == state['after_config_sha256'],
                'Current config changed since installation; restore manually to preserve later edits')
        destination = oc/'Kexts'/NAME
        require(tree(destination) == state['after_kext'], 'Current helper changed; restore manually')
        if state['before_kext'] is not None:
            require(tree(backup/NAME) == state['before_kext'], 'Old helper backup changed')
        removed = backup/'restored-new.kext'
        require(not removed.exists(), 'This backup was already used')
        os.rename(destination, removed)
        try:
            if state['before_kext'] is not None:
                shutil.copytree(backup/NAME, destination, copy_function=shutil.copyfile)
            atomic(oc/'config.plist', (backup/'config.plist').read_bytes())
        except BaseException:
            if destination.exists():
                shutil.rmtree(destination)
            os.rename(removed, destination)
            raise
        print('Saved EFI config and helper restored. Reboot to apply.')
    finally:
        lock.rmdir()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'restore'))
    parser.add_argument('--target', required=True, help='Explicit EFI partition diskXsY or mounted /Volumes/.../EFI/OC')
    parser.add_argument('--payload', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--backup', help='Backup folder name printed by the automatic install')
    args = parser.parse_args()
    require(os.geteuid() == 0, 'Run with sudo')
    with target_oc(args.target) as oc:
        if args.action == 'install':
            install(oc, args.payload)
        else:
            require(args.backup and re.fullmatch(r'[0-9a-f]{32}', args.backup), 'Supply the recorded backup folder name')
            restore(oc, oc/'NVWebKitFixup-backups'/args.backup)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, plistlib.InvalidFileException, subprocess.CalledProcessError) as error:
        print('WebKit helper not completed: '+str(error), file=sys.stderr)
        sys.exit(1)
