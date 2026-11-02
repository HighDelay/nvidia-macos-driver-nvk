#!/usr/bin/env python3
"""Run a private mapping comparison, or apply/undo one experimental Zink dylib."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parent
INSTALLED = Path('/Library/GPUBundles/nvmtl/zink')
BACKUP_NAME = '.persistent-mapping-trial'
LIBRARY = 'libnvk_zink.dylib'
PRIVATE = ('libnvk_zink_vulkan.dylib', 'libnvk_renderer.dylib')
CASES = ('upload', 'map-failure', 'read-failure', 'full-bar', 'persistent',
         'persistent-coherent', 'full-bar-persistent', 'hardware', 'hardware-persistent',
         'small-static', 'small-dynamic', 'full-bar-small-static', 'hardware-small-static')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.sha256(stream.read()).hexdigest()


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def manifest():
    record = json.loads((ROOT/'TRIAL.json').read_text())
    for name, sha in record['payload_sha256'].items():
        require('/' not in name and not (ROOT/name).is_symlink(), 'Invalid trial payload path')
        require(digest(ROOT/name) == sha, 'Damaged trial file: '+name)
    return record


def compatible(record):
    require(INSTALLED.is_dir() and not INSTALLED.is_symlink(), 'Install the published Zink driver first.')
    for name in PRIVATE:
        require(not (INSTALLED/name).is_symlink() and
                digest(INSTALLED/name) == record['installed_sha256'][name],
                'Different installed private renderer: '+name+'. This trial needs the v2.6.4_fix package.')


def replace_library(source, target, expected):
    require(not target.is_symlink(), 'Refusing a symlink at the installed library path')
    descriptor, temporary = tempfile.mkstemp(prefix='.zink-trial-', dir=target.parent)
    os.close(descriptor)
    temporary = Path(temporary)
    try:
        shutil.copyfile(source, temporary)
        temporary.chmod(0o755)
        require(digest(temporary) == expected, 'Copied library checksum mismatch')
        subprocess.run(['codesign', '--verify', '--strict', str(temporary)], check=True)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def apply(record):
    compatible(record)
    current = INSTALLED/LIBRARY
    before = record['installed_sha256'][LIBRARY]
    after = record['payload_sha256'][LIBRARY]
    backup = INSTALLED/BACKUP_NAME
    current_hash = digest(current)
    if backup.exists():
        require(backup.is_dir() and not backup.is_symlink() and
                not (backup/LIBRARY).is_symlink() and digest(backup/LIBRARY) == before,
                'Missing or damaged original-library backup')
        saved = json.loads((backup/'TRIAL.json').read_text())
        require(saved['installed_sha256'] == record['installed_sha256'],
                'The earlier trial has a different original driver')
        allowed = (before, after, *record.get('previous_trial_sha256', []))
        require(current_hash in allowed, 'A newer/different driver is installed; refusing to overwrite it.')
        if current_hash == after:
            print('The mapping trial is already installed. Relaunch Minecraft.')
            return
        # Record both candidate hashes before replacing the dylib so rollback
        # remains possible even if signing or replacement fails during upgrade.
        temporary = backup/'TRIAL.json.new'
        temporary.write_text(json.dumps(record, indent=2)+'\n')
        os.replace(temporary, backup/'TRIAL.json')
        replace_library(ROOT/LIBRARY, current, after)
        print('Mapping trial updated; the original published library backup is preserved.')
        print('Relaunch Minecraft. Use Restore-Zink.command to return to the published library.')
        return
    require(current_hash == before, 'Installed Zink differs from the published baseline; refusing to overwrite it.')
    backup.mkdir(mode=0o700)
    try:
        shutil.copyfile(current, backup/LIBRARY)
        require(digest(backup/LIBRARY) == before, 'Original-library backup checksum mismatch')
        (backup/'TRIAL.json').write_text(json.dumps(record, indent=2)+'\n')
        replace_library(ROOT/LIBRARY, current, after)
    except BaseException:
        if digest(current) == before:
            shutil.rmtree(backup)
        raise
    print('Mapping trial applied. Relaunch Minecraft; no reboot is needed.')
    print('If it stays black, export the complete game console log again.')
    print('Use Restore-Zink.command to return to the published library.')


def restore(record):
    compatible(record)
    backup = INSTALLED/BACKUP_NAME
    require(backup.is_dir() and not backup.is_symlink(), 'No mapping-trial backup exists.')
    saved = json.loads((backup/'TRIAL.json').read_text())
    before = saved['installed_sha256'][LIBRARY]
    after = saved['payload_sha256'][LIBRARY]
    require(digest(backup/LIBRARY) == before, 'Original-library backup is damaged')
    current = INSTALLED/LIBRARY
    require(digest(current) in (before, after, *saved.get('previous_trial_sha256', [])),
            'A newer/different driver is installed; refusing to restore over it.')
    replace_library(backup/LIBRARY, current, before)
    shutil.rmtree(backup)
    print('Published Zink library restored. Relaunch Minecraft; no reboot is needed.')


def test(record, output_parent=None):
    compatible(record)
    timestamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    output = Path(tempfile.mkdtemp(prefix='NVIDIA-Zink-Mapping-'+timestamp+'.',
                                  dir=output_parent or Path.home()/'Desktop'))
    results = []
    (output/'TRIAL.json').write_text(json.dumps(record, indent=2)+'\n')
    (output/'system.txt').write_text(subprocess.check_output(['sw_vers'], text=True)+
        'architecture: '+platform.machine()+'\ninstalled_zink_sha256: '+digest(INSTALLED/LIBRARY)+'\n')
    try:
        for label, source in (('installed', INSTALLED/LIBRARY), ('candidate', ROOT/LIBRARY)):
            runtime = output/label/'runtime'
            runtime.mkdir(parents=True)
            shutil.copyfile(source, runtime/LIBRARY)
            for name in PRIVATE:
                target = 'libnvk_zink_vulkan-real.dylib' if name == PRIVATE[0] else name
                shutil.copyfile(INSTALLED/name, runtime/target)
            proxy = runtime/PRIVATE[0]
            shutil.copyfile(ROOT/'map-test-vulkan.dylib', proxy)
            for case in CASES:
                print(label+': '+case, flush=True)
                env = dict(os.environ, ZINK_TEST_REAL_VULKAN=str(runtime/'libnvk_zink_vulkan-real.dylib'))
                for key in ('DYLD_INSERT_LIBRARIES', 'ZINK_TEST_FULL_BAR', 'ZINK_TEST_NATIVE_MEMORY',
                            'ZINK_DESCRIPTORS', 'ZINK_DEBUG', 'NVK_BAR_VRAM', 'NVK_LOCAL_MAP_SYSMEM'):
                    env.pop(key, None)
                if case.startswith('full-bar'): env['ZINK_TEST_FULL_BAR'] = '1'
                if case.startswith('hardware'): env['ZINK_TEST_NATIVE_MEMORY'] = '1'
                with (output/label/(case+'.log')).open('w') as log:
                    try:
                        run = subprocess.run([str(ROOT/'buffer-upload-probe'), str(runtime/LIBRARY),
                            str(proxy), case], env=env, stdout=log, stderr=subprocess.STDOUT, timeout=45)
                        code = run.returncode
                    except subprocess.TimeoutExpired:
                        code = 124
                results.append({'runtime':label, 'case':case, 'returncode':code})
                print('  result: '+str(code), flush=True)
            shutil.rmtree(runtime)
    finally:
        (output/'results.json').write_text(json.dumps(results, indent=2)+'\n')
        archive = output.with_name(output.name+'.tar.gz')
        with tarfile.open(archive, 'w:gz') as tar:
            for path in sorted(output.rglob('*')):
                if path.is_file() and 'runtime' not in path.relative_to(output).parts:
                    tar.add(path, arcname=output.name+'/'+str(path.relative_to(output)), recursive=False)
        print('\nSend this log archive back:\n'+str(archive))


def main():
    require(len(sys.argv) == 2 and sys.argv[1] in ('test', 'apply', 'restore'), 'Use the supplied .command files.')
    require(platform.machine() == 'x86_64' and sys.platform == 'darwin', 'This trial requires Intel macOS.')
    record = manifest()
    action = sys.argv[1]
    if action in ('apply', 'restore') and os.geteuid() != 0:
        print('Quit Minecraft before continuing. Administrator access updates one Zink library.', flush=True)
        subprocess.run(['sudo', sys.executable, str(Path(__file__).resolve()), action], check=True)
        return
    if action in ('apply', 'restore'):
        java = subprocess.run(['pgrep', '-x', 'java'], capture_output=True, text=True)
        for pid in java.stdout.split():
            loaded = subprocess.run(['lsof', '-t', '-a', '-p', pid, str(INSTALLED/LIBRARY)],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            require(loaded.returncode != 0, 'A Java process is using Zink. Quit Minecraft, then run this command again.')
    {'test': test, 'apply': apply, 'restore': restore}[action](record)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print('STOP: '+str(error), file=sys.stderr)
        sys.exit(1)
