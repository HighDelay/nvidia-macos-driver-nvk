#!/usr/bin/env python3
"""Optional ARM app translator commands, separate from the NVIDIA driver."""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
SYSTEM_PREFIX = Path('/Library/NullMoth/arm')
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_PAYLOAD_BYTES = 512 * 1024 * 1024


def require(ok, message):
    if not ok:
        raise ValueError(message)


def load_pin():
    pin = json.loads((HERE / 'source.json').read_text())
    require(pin.get('schema_version') == 1 and pin.get('version') == '2.7.0', 'unknown ARM package pin')
    require(pin.get('archive') == 'nullmoth-arm-2.7.0.tar.gz', 'unexpected ARM package name')
    require(pin.get('sha256') == 'eb4331ba373e0dccc3cf241ef2115ec1b8f88b38ecf3762a3d70a5312b5fa81c', 'ARM package pin changed; update the reviewed helper too')
    return pin


def verified_archive():
    """Hash and authenticate one buffer before anything is unpacked or run."""
    pin = load_pin()
    archive = HERE / 'upstream' / pin['archive']
    require(archive.is_file() and not archive.is_symlink(), 'bundled ARM archive is missing')
    require(archive.stat().st_size <= MAX_ARCHIVE_BYTES, 'ARM archive exceeds the size limit')
    data = archive.read_bytes()
    require(hashlib.sha256(data).hexdigest() == pin['sha256'], 'ARM archive checksum mismatch')
    signature = (archive.with_name(archive.name + '.sig')).read_text().strip()
    require(len(base64.b64decode(signature, validate=True)) == 64, 'invalid release signature')
    verifier = HERE / 'verify-signature'
    require(verifier.is_file() and os.access(verifier, os.X_OK), 'signature checker missing; use the complete scripts package')
    result = subprocess.run([str(verifier), signature], input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    require(result.returncode == 0, 'upstream ARM signature verification failed')
    return data


def unpack(data, destination):
    """The signed package contains files/directories only; reject links/devices."""
    destination = Path(destination)
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        members = archive.getmembers()
        require(len(members) <= 4096, 'too many archive entries')
        require(sum(m.size for m in members) <= MAX_PAYLOAD_BYTES, 'unpacked ARM payload exceeds the size limit')
        seen = set()
        for entry in members:
            path = PurePosixPath(entry.name)
            require(not path.is_absolute() and path.parts and path.parts[0] == 'pkgroot'
                    and '..' not in path.parts and '\\' not in entry.name, 'archive path escapes pkgroot')
            require(entry.isfile() or entry.isdir(), 'archive links and special files are refused')
            require(path not in seen, 'duplicate archive path')
            require(not any(part.startswith('._') or part == '.DS_Store' for part in path.parts), 'archive contains Finder metadata')
            seen.add(path)
        # A fresh private directory and the no-link policy prevent ancestor redirects.
        for entry in members:
            path = destination.joinpath(*PurePosixPath(entry.name).parts)
            if entry.isdir():
                path.mkdir(parents=True, exist_ok=True, mode=0o755)
            else:
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                stream = archive.extractfile(entry)
                require(stream is not None, 'archive file has no contents')
                with stream, path.open('xb') as output:
                    shutil.copyfileobj(stream, output)
                path.chmod(0o755 if entry.mode & 0o111 else 0o644)
    payload = destination / 'pkgroot'
    for name in ('install.sh', 'uninstall.sh', 'SHA256SUMS', 'bin/nm-arm', 'llvm/bin/llc', 'LICENSE'):
        require((payload / name).is_file(), 'incomplete ARM payload: ' + name)
    return payload


def check_system_path(path, missing_ok=False):
    """Privileged scripts may live only under real, protected root directories."""
    path = Path(path)
    for item in (path, *path.parents):
        if not item.exists():
            require(missing_ok, 'missing system path: ' + str(item))
            continue
        info = item.lstat()
        require(not stat.S_ISLNK(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                'untrusted system path: ' + str(item))


# All privileged instructions are fixed arguments to the system shell. Root
# never imports these user-owned Python sources or executes the downloaded
# signature helper. It copies the already-authenticated archive into its own
# private folder and checks the exact pinned digest before extracting/running it.
SYSTEM_INSTALL = r"""
set -eu
PATH=/usr/bin:/bin:/usr/sbin:/sbin; export PATH
umask 077
d=$(/usr/bin/mktemp -d /private/var/root/nvidia-arm.XXXXXXXX)
trap '/bin/rm -rf "$d"' EXIT
/bin/chmod 700 "$d"
/bin/cp -X "$1" "$d/package.tar.gz"
h=$(/usr/bin/shasum -a 256 "$d/package.tar.gz"); h=${h%% *}
[ "$h" = "$2" ] || { echo 'STOP: ARM archive changed after authentication' >&2; exit 1; }
for p in /Library /Library/NullMoth /Library/NullMoth/arm; do
  [ ! -L "$p" ] || { echo 'STOP: system runtime parent is a symlink' >&2; exit 1; }
  [ ! -e "$p" ] || {
    [ -d "$p" ] || { echo 'STOP: system runtime parent is not a directory' >&2; exit 1; }
    meta=$(/usr/bin/stat -f '%u %Lp' "$p"); owner=${meta%% *}; mode=${meta#* }
    [ "$owner" = 0 ] && [ $((8#$mode & 022)) = 0 ] || { echo 'STOP: untrusted system runtime parent' >&2; exit 1; }
  }
done
/bin/mkdir "$d/unpacked"
/usr/bin/tar -xzf "$d/package.tar.gz" -C "$d/unpacked"
(umask 022; /bin/bash "$d/unpacked/pkgroot/install.sh")
/bin/chmod 755 /Library/NullMoth /Library/NullMoth/arm
"""


def install(prefix):
    require(os.geteuid() != 0, 'launch the ARM scripts as your normal user; installation asks for sudo itself')
    data = verified_archive()
    system = prefix == SYSTEM_PREFIX
    if system:
        check_system_path(SYSTEM_PREFIX, missing_ok=True)
    with tempfile.TemporaryDirectory(prefix='nvidia-arm-') as temporary:
        Path(temporary).chmod(0o700)
        payload = unpack(data, Path(temporary) / 'unpacked')
        if system:
            pin = load_pin()
            result = subprocess.call(['/usr/bin/sudo', '/bin/bash', '-c', SYSTEM_INSTALL, 'nvidia-arm-install',
                                      str(HERE / 'upstream' / pin['archive']), pin['sha256']])
            if result:
                return result
        else:
            env = dict(os.environ, PATH='/usr/bin:/bin:/usr/sbin:/sbin')
            subprocess.run(['/bin/bash', str(payload / 'install.sh'), '--prefix', str(prefix)],
                           env=env, check=True, preexec_fn=lambda: os.umask(0o022))
    print('ARM translator installed. NVIDIA driver and OpenCore were not changed.')
    return 0


def uninstall(prefix):
    require(os.geteuid() != 0, 'launch the ARM scripts as your normal user; removal asks for sudo itself')
    script = prefix / 'uninstall.sh'
    require(script.is_file(), 'ARM translator uninstaller is missing')
    command = ['/bin/bash', str(script), '--prefix', str(prefix)]
    if prefix == SYSTEM_PREFIX:
        check_system_path(script)
        command.insert(0, '/usr/bin/sudo')
    # Root removes only the protected runtime. Do not parse a user's writable
    # translated-app manifest as root; use nm-arm remove as the user instead.
    subprocess.run(command, check=True, env=dict(os.environ, PATH='/usr/bin:/bin:/usr/sbin:/sbin'))
    print('ARM runtime removed. Translated copies and logs were kept.')
    return 0


def run_cli(command, arguments, prefix):
    require(os.geteuid() != 0, 'run translation and app commands as your normal user, without sudo')
    cli = prefix / 'bin/nm-arm'
    require(cli.is_file() and os.access(cli, os.X_OK), 'ARM translator is not installed; run Install-ARM.command first')
    # Process arguments are passed directly; no shell interpretation of app paths.
    return subprocess.call([str(cli), command, *arguments])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, default=SYSTEM_PREFIX, help='optional user-owned runtime instead of /Library/NullMoth/arm')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('install', help='install the signed bundled translator (sudo for the system prefix only)')
    commands.add_parser('uninstall', help='remove the runtime; keep translated copies and logs')
    for command in ('check', 'list', 'logs', 'version'):
        commands.add_parser(command)
    translate = commands.add_parser('translate', help='write a translated copy under ~/Applications/NullMoth ARM')
    translate.add_argument('app', type=Path)
    translate.add_argument('--jobs', type=int)
    remove = commands.add_parser('remove', help='remove one translated copy as the user')
    remove.add_argument('name')
    args = parser.parse_args(argv)
    prefix = args.prefix.expanduser().absolute()
    require('..' not in prefix.parts and str(prefix) not in ('/', '/Library', '/Library/NullMoth', '/Applications', '/Users', '/System', '/usr', '/usr/local', '/bin', '/sbin'), 'unsafe installation prefix')
    if args.command == 'install':
        return install(prefix)
    if args.command == 'uninstall':
        return uninstall(prefix)
    forwarded = []
    if args.command == 'translate':
        app = args.app.expanduser().absolute()
        require(app.is_dir() and app.suffix.lower() == '.app', 'choose an existing .app bundle')
        forwarded.append(str(app))
        if args.jobs is not None:
            require(1 <= args.jobs <= 64, '--jobs must be between 1 and 64')
            forwarded += ['--jobs', str(args.jobs)]
    if args.command == 'remove':
        require(args.name and '/' not in args.name and args.name not in ('.', '..') and not args.name.startswith('-'), 'invalid translated-app name')
        forwarded.append(args.name)
    return run_cli(args.command, forwarded, prefix)


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:] or ['--help']))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print('STOP: ' + str(error), file=sys.stderr)
        sys.exit(1)
