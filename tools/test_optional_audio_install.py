#!/usr/bin/env python3
"""Exercise opt-in audio installation, external ownership and rollback privately."""
import hashlib
import os
from pathlib import Path
import plistlib
import pty
import shutil
import subprocess
import unittest
from primary_install_fixture import Install

class OptionalAudio(Install):
    def setUp(self):
        super().setUp()
        self.env['FAKE_OS_VERSION']='26.7.1'
        self.audio=self.payload/'Optional/NMXAudio.kext'
        p=self.audio/'Contents/MacOS/NMXAudio';p.parent.mkdir(parents=True);p.write_bytes(b'new-audio')
        (self.audio/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'com.nullmoth.NMXAudio','CFBundleExecutable':'NMXAudio'}))
        self.live=self.root/'Library/Extensions/NMXAudio.kext'
        self.marker=self.root/'Library/NullMoth/nvidia-audio.sha256'
        io=self.bin/'ioreg';s=io.read_text().replace("elif name=='ioreg':print(","elif name=='ioreg':\n print('    \\\"class-code\\\" = <00030400>' if not os.environ.get('NO_HDA') else '')\n print(")
        io.write_text(s)
        km=self.bin/'kmutil';s=km.read_text().replace("for k in ['NVRM','NVAccel','NVRMFB','NVRMAGDC']:",
            "if os.environ.get('EXPECT_AUDIO') and not os.environ.get('MISSING_AUDIO'):print('com.nullmoth.NMXAudio 1.0.0')\n  for k in ['NVRM','NVAccel','NVRMFB','NVRMAGDC']:")
        km.write_text(s)
        self.refresh()

    def refresh(self):
        (self.payload/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(self.payload))+'\n' for p in self.payload.rglob('*') if p.is_file() and p.name!='SHA256SUMS'))

    def existing(self, managed=True):
        shutil.copytree(self.audio,self.live)
        p=self.live/'Contents/MacOS/NMXAudio';p.write_bytes(b'old-audio');self.old[p]=p.read_bytes()
        if managed:
            self.marker.write_text(hashlib.sha256(p.read_bytes()).hexdigest()+'\n');self.marker.chmod(0o600)
            self.old[self.marker]=self.marker.read_bytes()

    def run_audio(self, choice=None, answer=None, **env):
        cmd=['/bin/bash',str(self.script)]
        if choice:cmd+=['--audio='+choice]
        if answer is None:return subprocess.run(cmd,env={**self.env,**env},capture_output=True,text=True,timeout=30)
        master,slave=pty.openpty()
        try:
            os.write(master,(answer+'\n').encode())
            return subprocess.run(cmd,env={**self.env,**env},stdin=slave,capture_output=True,text=True,timeout=30)
        finally:os.close(master);os.close(slave)

    def test_noninteractive_default_no(self):
        r=self.run_audio();self.assertEqual(r.returncode,0,r.stdout+r.stderr);self.assertFalse(self.live.exists());self.assertFalse(self.marker.exists())

    def test_prompt_default_no(self):
        r=self.run_audio(answer='');self.assertEqual(r.returncode,0,r.stdout+r.stderr);self.assertIn('[y/N]',r.stdout);self.assertFalse(self.live.exists())

    def test_prompt_yes_records_matching_ownership(self):
        r=self.run_audio(answer='yes',EXPECT_AUDIO='1');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual((self.live/'Contents/MacOS/NMXAudio').read_bytes(),b'new-audio')
        self.assertEqual(self.marker.read_text().strip(),hashlib.sha256(b'new-audio').hexdigest())
        self.assertIn('NMXAudio', (self.root/'Library/NullMoth/lastgood.sh').read_text())
        self.assertIn('nvidia-audio.sha256', (self.root/'Library/NullMoth/lastgood.sh').read_text())

    def test_managed_update_keeps_opt_in(self):
        self.existing();r=self.run_audio(EXPECT_AUDIO='1');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertNotIn('[y/N]',r.stdout);self.assertEqual(self.marker.read_text().strip(),hashlib.sha256(b'new-audio').hexdigest())

    def test_explicit_no_removes_managed_only(self):
        self.existing();r=self.run_audio('no');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertFalse(self.live.exists());self.assertFalse(self.marker.exists())

    def test_external_audio_is_preserved_by_default(self):
        self.existing(False);r=self.run_audio();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual((self.live/'Contents/MacOS/NMXAudio').read_bytes(),b'old-audio');self.assertFalse(self.marker.exists())

    def test_explicit_no_preserves_external(self):
        self.existing(False);r=self.run_audio('no');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual((self.live/'Contents/MacOS/NMXAudio').read_bytes(),b'old-audio')

    def test_refuses_to_overwrite_external(self):
        self.existing(False);r=self.run_audio('yes');self.assert_restored(r);self.assertIn('independently',r.stderr)

    def test_replaced_audio_is_no_longer_owned(self):
        self.existing();(self.live/'Contents/MacOS/NMXAudio').write_bytes(b'replaced-audio')
        r=self.run_audio('no');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual((self.live/'Contents/MacOS/NMXAudio').read_bytes(),b'replaced-audio')

    def test_missing_selected_audio_fails_preflight(self):
        r=self.run_audio('yes',EXPECT_AUDIO='1',MISSING_AUDIO='1');self.assert_restored(r);self.assertFalse(self.live.exists())

    def test_failed_fresh_install_removes_new_audio_and_record(self):
        r=self.run_audio('yes',EXPECT_AUDIO='1',FAKE_LIVE_FAIL='1');self.assert_restored(r)
        self.assertFalse(self.live.exists());self.assertFalse(self.marker.exists())

    def test_failed_update_restores_matching_record(self):
        self.existing();r=self.run_audio(EXPECT_AUDIO='1',FAKE_LIVE_FAIL='1');self.assert_restored(r)

    def test_failed_removal_restores_audio_and_record(self):
        self.existing();r=self.run_audio('no',FAKE_LIVE_FAIL='1');self.assert_restored(r)

    def test_check_only_installs_nothing(self):
        r=self.run_audio('yes',EXPECT_AUDIO='1',CHECK='1');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertFalse(self.live.exists());self.assertFalse(self.marker.exists())

    def test_sequoia_refuses_explicit_audio(self):
        r=self.run_audio('yes',FAKE_OS_VERSION='15.8.1');self.assert_restored(r);self.assertIn('Tahoe',r.stderr)

    def test_no_hda_refuses_audio(self):
        r=self.run_audio('yes',NO_HDA='1');self.assert_restored(r);self.assertIn('controller',r.stderr)

if __name__=='__main__':unittest.main()
