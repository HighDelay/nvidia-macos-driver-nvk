#!/usr/bin/env python3
"""Exercise optional panel install, removal and rollback in a private filesystem."""
import hashlib
import os
import plistlib
import pty
import subprocess
import unittest
from primary_install_fixture import Install


class IntelPanelInstall(Install):
    name = 'NMIntelFB'
    option = '--intel-panel'

    def add_panel(self, root, name):
        panel = root/f'{name}.kext'
        binary = panel/'Contents/MacOS'/name
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'new-panel')
        (panel/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':f'com.nullmoth.{name}'}))
        return panel

    def setUp(self):
        super().setUp()
        self.panel = self.add_panel(self.payload/'Optional', self.name)
        sign = self.bin/'codesign'
        sign.write_text('#!/bin/bash\n[ "${PANEL_SIGN_FAIL:-0}" != 1 ]\n')
        sign.chmod(0o755)
        km = self.bin/'kmutil'
        code = km.read_text().replace("for k in ['NVRM','NVAccel','NVRMFB','NVRMAGDC']:",
            f"if os.environ.get('EXPECT_PANEL') and not os.environ.get('MISSING_PANEL'):print('com.nullmoth.{self.name} 0.1')\n  for k in ['NVRM','NVAccel','NVRMFB','NVRMAGDC']:")
        km.write_text(code)
        self.refresh_manifest()
        self.live = self.root/'Library/Extensions'/f'{self.name}.kext'

    def refresh_manifest(self):
        rows = [hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(self.payload))
                for p in self.payload.rglob('*') if p.is_file() and p.name!='SHA256SUMS']
        (self.payload/'SHA256SUMS').write_text('\n'.join(rows)+'\n')

    def existing(self):
        import shutil
        shutil.copytree(self.panel,self.live)
        (self.live/'Contents/MacOS'/self.name).write_bytes(b'old-panel')
        for p in self.live.rglob('*'):
            if p.is_file(): self.old[p]=p.read_bytes()

    def run_panel(self, choice=None, answer=None, options=(), **env):
        cmd=['/bin/bash',str(self.script),*options]
        if choice: cmd.append(self.option+'='+choice)
        if answer is None:
            return subprocess.run(cmd,env=dict(self.env,**env),capture_output=True,text=True,timeout=30)
        master,slave=pty.openpty()
        try:
            os.write(master,(answer+'\n').encode())
            return subprocess.run(cmd,env=dict(self.env,**env),stdin=slave,
                                  stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=30)
        finally:
            os.close(master);os.close(slave)

    def test_noninteractive_default_skips_panel(self):
        r=self.run_panel();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertFalse(self.live.exists())
        self.assertIn('No interactive input',r.stdout)

    def test_prompt_default_no(self):
        r=self.run_panel(answer='');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertIn('[y/N]',r.stdout);self.assertFalse(self.live.exists())

    def test_prompt_yes_installs_and_fallback_covers_panel(self):
        r=self.run_panel(answer='yes',EXPECT_PANEL='1');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual((self.live/'Contents/MacOS'/self.name).read_bytes(),b'new-panel')
        self.assertIn(self.name+';', (self.root/'Library/NullMoth/lastgood.sh').read_text())

    def test_existing_opt_in_survives_update_without_prompt(self):
        self.existing();r=self.run_panel(EXPECT_PANEL='1');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertNotIn('[y/N]',r.stdout)
        self.assertEqual((self.live/'Contents/MacOS'/self.name).read_bytes(),b'new-panel')

    def test_explicit_no_removes_previous_panel(self):
        self.existing();r=self.run_panel('no');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertFalse(self.live.exists())
        self.assertIn(self.name+';', (self.root/'Library/NullMoth/lastgood.sh').read_text())

    def test_failed_fresh_install_removes_new_panel(self):
        r=self.run_panel('yes',EXPECT_PANEL='1',FAKE_LIVE_FAIL='1');self.assert_restored(r)
        self.assertFalse(self.live.exists())

    def test_failed_update_restores_previous_panel(self):
        self.existing();r=self.run_panel(EXPECT_PANEL='1',FAKE_LIVE_FAIL='1');self.assert_restored(r)

    def test_failed_removal_restores_previous_panel(self):
        self.existing();r=self.run_panel('no',FAKE_LIVE_FAIL='1');self.assert_restored(r)

    def test_removal_rejects_collection_still_containing_panel(self):
        self.existing();r=self.run_panel('no',EXPECT_PANEL='1');self.assert_restored(r)
        self.assertIn('retained deselected '+self.name,r.stderr);self.assert_creates(1)

    def test_preflight_missing_panel_stops_before_live_changes(self):
        r=self.run_panel('yes',EXPECT_PANEL='1',MISSING_PANEL='1');self.assert_restored(r)
        self.assertFalse(self.live.exists());self.assert_creates(1)

    def test_check_only_does_not_install_panel(self):
        r=self.run_panel('yes',EXPECT_PANEL='1',CHECK='1');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertFalse(self.live.exists());self.assert_creates(1)

    def test_invalid_signature_stops_before_collection_build(self):
        r=self.run_panel('yes',PANEL_SIGN_FAIL='1');self.assert_restored(r)
        self.assertFalse((self.root/'creates').exists())

    def test_missing_optional_payload_refuses_explicit_yes(self):
        import shutil
        shutil.rmtree(self.panel);self.refresh_manifest()
        r=self.run_panel('yes');self.assert_restored(r)
        self.assertIn('lacks the optional',r.stderr)


class AMDPanelInstall(IntelPanelInstall):
    name = 'NMGopFB'
    option = '--amd-panel'

    def test_interrupted_first_install_is_rolled_back_before_retaining_panel_choice(self):
        self.existing()
        state = self.root/'Library/NullMoth'
        backup = state/'backup-before-amd'
        backup.mkdir()
        (backup/'amd-panel-touched').touch()
        lock = state/'install.lock'
        lock.mkdir()
        (lock/'owner').write_text(f'pid=1\nboot=0\nbk={backup}\n')
        (lock/'installing').touch()
        stat = self.bin/'stat'
        stat.write_text(stat.read_text().replace(" elif a[1]=='%d:%i':", " elif a[1]=='%u':print('0')\n elif a[1]=='%d:%i':"))

        r = self.run_panel()
        self.assertEqual(r.returncode, 0, r.stdout+r.stderr)
        self.assertIn('interrupted install was rolled back', r.stdout)
        self.assertFalse(self.live.exists())

    def test_second_prompt_can_install_amd_after_declining_intel(self):
        self.add_panel(self.payload/'Optional', 'NMIntelFB')
        self.refresh_manifest()
        r = self.run_panel(answer='\nyes', EXPECT_PANEL='1')
        self.assertEqual(r.returncode, 0, r.stdout+r.stderr)
        self.assertIn('Install NMIntelFB', r.stdout)
        self.assertIn('Install NMGopFB', r.stdout)
        self.assertTrue(self.live.is_dir())
        self.assertFalse((self.root/'Library/Extensions/NMIntelFB.kext').exists())

    def test_foreign_panel_is_kept_and_cannot_be_replaced(self):
        self.existing()
        info = self.live/'Contents/Info.plist'
        info.write_bytes(plistlib.dumps({'CFBundleIdentifier': 'org.test.ForeignPanel'}))
        foreign = info.read_bytes()
        r = self.run_panel('yes')
        self.assertNotEqual(r.returncode, 0, r.stdout+r.stderr)
        self.assertIn('cannot identify the existing NMGopFB.kext', r.stderr)
        r = self.run_panel()
        self.assertEqual(r.returncode, 0, r.stdout+r.stderr)
        self.assertEqual(info.read_bytes(), foreign)

    def test_explicit_two_panel_choices_fail_before_preflight(self):
        self.add_panel(self.payload/'Optional', 'NMIntelFB')
        self.refresh_manifest()
        r = self.run_panel('yes', options=('--intel-panel=yes',))
        self.assert_restored(r)
        self.assertIn('share one slot', r.stderr)
        self.assertFalse((self.root/'creates').exists())

    def test_explicit_switch_replaces_intel_with_amd(self):
        intel = self.add_panel(self.root/'Library/Extensions', 'NMIntelFB')
        r = self.run_panel('yes', options=('--intel-panel=no',), EXPECT_PANEL='1')
        self.assertEqual(r.returncode, 0, r.stdout+r.stderr)
        self.assertTrue(self.live.is_dir())
        self.assertFalse(intel.exists())

    def test_failed_switch_restores_intel_and_removes_amd(self):
        intel = self.add_panel(self.root/'Library/Extensions', 'NMIntelFB')
        for path in intel.rglob('*'):
            if path.is_file():
                self.old[path] = path.read_bytes()
        r = self.run_panel('yes', options=('--intel-panel=no',), EXPECT_PANEL='1', FAKE_LIVE_FAIL='1')
        self.assert_restored(r)
        self.assertFalse(self.live.exists())


if __name__=='__main__':unittest.main(verbosity=2)
