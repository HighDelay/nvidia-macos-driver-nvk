#!/usr/bin/env python3
"""Test the ARM wrapper's archive verification and privilege/argument boundaries."""
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('arm_apps',ROOT/'tools/arm-apps/arm_apps.py')
ARM = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(ARM)
PIN = json.loads((ROOT/'tools/arm-apps/source.json').read_text())


def archive(extra=(), omit=()):
    data=io.BytesIO()
    with tarfile.open(fileobj=data,mode='w:gz') as tar:
        for name in ('install.sh','uninstall.sh','SHA256SUMS','bin/nm-arm','llvm/bin/llc','LICENSE'):
            if name in omit:continue
            info=tarfile.TarInfo('pkgroot/'+name);body=b'fixture';info.size=len(body);info.mode=0o755 if name.endswith('.sh') else 0o644
            tar.addfile(info,io.BytesIO(body))
        for name,kind,link in extra:
            info=tarfile.TarInfo(name);info.type=kind;info.linkname=link
            tar.addfile(info,io.BytesIO(b''))
    return data.getvalue()


class ArmScripts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.here=patch.object(ARM,'HERE',self.root);self.here.start()
        (self.root/'source.json').write_text(json.dumps(PIN))

    def tearDown(self):self.here.stop();self.temp.cleanup()

    def test_actual_pin_matches_reviewed_version(self):self.assertEqual(ARM.load_pin(),PIN)

    def test_changed_pin_is_refused(self):
        pin={**PIN,'sha256':'0'*64};(self.root/'source.json').write_text(json.dumps(pin))
        with self.assertRaisesRegex(ValueError,'pin changed'):ARM.load_pin()

    def verification_fixture(self):
        (self.root/'upstream').mkdir()
        p=self.root/'upstream'/PIN['archive'];p.write_bytes(b'checked bytes')
        (p.with_name(p.name+'.sig')).write_text(base64.b64encode(b's'*64).decode())
        checker=self.root/'verify-signature';checker.write_text('fixture');checker.chmod(0o755)
        return p,{**PIN,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}

    def test_signature_receives_same_buffer_as_hash(self):
        p,pin=self.verification_fixture()
        with patch.object(ARM,'load_pin',return_value=pin),patch.object(ARM.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(ARM.verified_archive(),b'checked bytes')
            self.assertEqual(run.call_args.kwargs['input'],b'checked bytes')

    def test_changed_archive_fails_before_verifier(self):
        p,pin=self.verification_fixture();p.write_bytes(b'tampered')
        with patch.object(ARM,'load_pin',return_value=pin),patch.object(ARM.subprocess,'run') as run:
            with self.assertRaisesRegex(ValueError,'checksum'):ARM.verified_archive()
            run.assert_not_called()

    def test_wrong_signature_refused(self):
        p,pin=self.verification_fixture()
        with patch.object(ARM,'load_pin',return_value=pin),patch.object(ARM.subprocess,'run',return_value=SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(ValueError,'signature verification'):ARM.verified_archive()

    def test_truncated_signature_refused(self):
        p,pin=self.verification_fixture();p.with_name(p.name+'.sig').write_text('abcd')
        with patch.object(ARM,'load_pin',return_value=pin):
            with self.assertRaisesRegex(ValueError,'signature'):ARM.verified_archive()

    def test_archive_link_refused(self):
        p,pin=self.verification_fixture();q=p.with_suffix('.target');p.rename(q);p.symlink_to(q)
        with patch.object(ARM,'load_pin',return_value=pin):
            with self.assertRaisesRegex(ValueError,'missing'):ARM.verified_archive()

    def test_complete_archive_unpacks_without_execution(self):
        payload=ARM.unpack(archive(),self.root/'x')
        self.assertEqual((payload/'install.sh').read_bytes(),b'fixture')
        self.assertTrue((payload/'install.sh').stat().st_mode & 0o100)

    def reject(self,extra):
        with self.assertRaises(ValueError):ARM.unpack(archive(extra),self.root/'x')
        self.assertFalse((self.root/'x').exists())

    def test_parent_traversal_refused(self):self.reject([('pkgroot/../escape',tarfile.REGTYPE,'')])
    def test_absolute_path_refused(self):self.reject([('/private/tmp/escape',tarfile.REGTYPE,'')])
    def test_foreign_root_refused(self):self.reject([('other/install.sh',tarfile.REGTYPE,'')])
    def test_symlink_refused(self):self.reject([('pkgroot/link',tarfile.SYMTYPE,'/Library')])
    def test_hardlink_refused(self):self.reject([('pkgroot/link',tarfile.LNKTYPE,'pkgroot/install.sh')])
    def test_device_refused(self):self.reject([('pkgroot/device',tarfile.CHRTYPE,'')])
    def test_duplicate_path_refused(self):self.reject([('pkgroot/install.sh',tarfile.REGTYPE,'')])
    def test_appledouble_refused(self):self.reject([('pkgroot/._install.sh',tarfile.REGTYPE,'')])
    def test_finder_metadata_refused(self):self.reject([('pkgroot/.DS_Store',tarfile.REGTYPE,'')])

    def test_incomplete_payload_refused(self):
        with self.assertRaisesRegex(ValueError,'incomplete'):ARM.unpack(archive(omit=['bin/nm-arm']),self.root/'x')

    def test_protected_parent_checks(self):
        with patch.object(Path,'exists',return_value=True),patch.object(Path,'lstat',return_value=SimpleNamespace(st_uid=0,st_mode=stat.S_IFDIR|0o755)):
            ARM.check_system_path(ARM.SYSTEM_PREFIX)

    def test_writable_parent_refused(self):
        with patch.object(Path,'exists',return_value=True),patch.object(Path,'lstat',return_value=SimpleNamespace(st_uid=0,st_mode=stat.S_IFDIR|0o777)):
            with self.assertRaisesRegex(ValueError,'untrusted'):ARM.check_system_path(ARM.SYSTEM_PREFIX)

    def test_symlink_parent_refused(self):
        with patch.object(Path,'exists',return_value=True),patch.object(Path,'lstat',return_value=SimpleNamespace(st_uid=0,st_mode=stat.S_IFLNK|0o755)):
            with self.assertRaisesRegex(ValueError,'untrusted'):ARM.check_system_path(ARM.SYSTEM_PREFIX)

    def test_install_requests_only_install_privilege(self):
        payload=self.root/'verified';payload.mkdir()
        with patch.object(ARM,'verified_archive',return_value=b'ok'),patch.object(ARM,'unpack',return_value=payload),patch.object(ARM,'check_system_path'),patch.object(ARM.os,'geteuid',return_value=501),patch.object(ARM.subprocess,'call',return_value=0) as call:
            self.assertEqual(ARM.install(ARM.SYSTEM_PREFIX),0)
            args=call.call_args.args[0]
            self.assertEqual(args[:3],['/usr/bin/sudo','/bin/bash','-c'])
            self.assertEqual(args[3],ARM.SYSTEM_INSTALL)
            self.assertEqual(args[-1],PIN['sha256'])
            self.assertNotIn(str(ARM.HERE/'verify-signature'),args)

    def test_tamper_never_requests_sudo(self):
        with patch.object(ARM,'verified_archive',side_effect=ValueError('bad signature')),patch.object(ARM.os,'geteuid',return_value=501),patch.object(ARM.subprocess,'call') as call:
            with self.assertRaises(ValueError):ARM.install(ARM.SYSTEM_PREFIX)
            call.assert_not_called()

    def test_custom_prefix_cannot_run_as_root(self):
        with patch.object(ARM.os,'geteuid',return_value=0):
            with self.assertRaisesRegex(ValueError,'normal user'):ARM.install(self.root/'runtime')

    def test_translation_never_runs_as_root(self):
        with patch.object(ARM.os,'geteuid',return_value=0),patch.object(ARM.subprocess,'call') as call:
            with self.assertRaisesRegex(ValueError,'without sudo'):ARM.run_cli('translate',['app'],ARM.SYSTEM_PREFIX)
            call.assert_not_called()

    def test_path_is_passed_without_shell(self):
        cli=self.root/'bin/nm-arm';cli.parent.mkdir();cli.write_text('fixture');cli.chmod(0o755)
        value='/Applications/Spaces $(touch bad) "quoted".app'
        with patch.object(ARM.os,'geteuid',return_value=501),patch.object(ARM.subprocess,'call',return_value=0) as call:
            ARM.run_cli('translate',[value],self.root)
            self.assertEqual(call.call_args.args[0],[str(cli),'translate',value]);self.assertNotIn('shell',call.call_args.kwargs)

    def test_runtime_removal_never_reads_user_manifests_as_root(self):
        p=self.root/'uninstall.sh';p.write_text('fixture')
        with patch.object(ARM.os,'geteuid',return_value=501),patch.object(ARM.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as run:
            ARM.uninstall(self.root)
            self.assertEqual(run.call_args.args[0],['/bin/bash',str(p),'--prefix',str(self.root)])
            self.assertNotIn('--user',run.call_args.args[0])

    def test_unsafe_prefix_refused(self):
        for p in ['/', '/Applications','/Library/NullMoth','/usr/local','/tmp/../Library']:
            with self.subTest(prefix=p),self.assertRaisesRegex(ValueError,'prefix'):ARM.main(['--prefix',p,'install'])

    def test_bad_remove_name_refused(self):
        for name in ['..','a/b','-option']:
            with self.subTest(name=name),self.assertRaises((ValueError,SystemExit)):ARM.main(['remove',name])

if __name__=='__main__':unittest.main()
