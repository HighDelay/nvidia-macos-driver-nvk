#!/usr/bin/env python3
"""Exercise EFI transactions on fixtures; never mount or change a real EFI."""
import copy
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('efi', ROOT/'package/webkit_fixup_efi.py')
efi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(efi)


class EfiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='webkit EFI fixture ')
        self.root = Path(self.temp.name).resolve()
        self.oc = self.root/'EFI/OC'
        self.oc.mkdir(parents=True)
        (self.oc/'OpenCore.efi').write_bytes(b'unchanged bootloader')
        self.payload = self.root/'payload'
        self.bundle(self.payload/efi.NAME, efi.IDENTITY, '0.2.0', b'new helper')
        (self.payload/'Kernel-Add.plist').write_bytes((ROOT/'kexts/NVWebKitFixup/Kernel-Add.plist').read_bytes())
        self.bundle(self.oc/'Kexts/Lilu.kext', 'as.vit9696.Lilu', '1.7.3', b'keep Lilu')
        self.bundle(self.oc/'Kexts/Other.kext', 'test.other', '1.0.0', b'keep other')
        self.config = {'Misc':{'Security':{'Vault':'Optional'}}, 'NVRAM':{'Private':'retained'},
                       'Kernel':{'Quirks':{'DisableLinkeditJettison':True},
                                 'Add':[{'BundlePath':'Lilu.kext','Enabled':True,'Arch':'Any'},
                                        {'BundlePath':'Other.kext','Enabled':True}]}}
        self.save()
        self.native = patch.object(efi, 'command', return_value=b'')
        self.native.start()

    def tearDown(self):
        self.native.stop()
        self.temp.cleanup()

    def bundle(self, path, identifier, version, binary):
        executable = path.stem
        (path/'Contents/MacOS').mkdir(parents=True)
        (path/'Contents/MacOS'/executable).write_bytes(binary)
        (path/'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier':identifier, 'CFBundleExecutable':executable, 'CFBundleVersion':version}))

    def save(self, fmt=plistlib.FMT_XML):
        self.before = plistlib.dumps(self.config, fmt=fmt)
        (self.oc/'config.plist').write_bytes(self.before)

    def old_helper(self):
        self.bundle(self.oc/'Kexts'/efi.NAME, efi.IDENTITY, '0.1.0', b'old helper')
        self.config['Kernel']['Add'].append({'BundlePath':efi.NAME,'Enabled':False})
        self.save()

    def test_install_and_restore_without_previous_helper(self):
        before_lilu = efi.tree(self.oc/'Kexts/Lilu.kext')
        backup = efi.install(self.oc, self.payload)
        updated = plistlib.loads((self.oc/'config.plist').read_bytes())
        entries = updated['Kernel']['Add']
        self.assertEqual([e['BundlePath'] for e in entries], ['Lilu.kext',efi.NAME,'Other.kext'])
        self.assertTrue(entries[1]['Enabled'])
        expected = copy.deepcopy(self.config);expected['Kernel']['Add'] = entries
        self.assertEqual(updated, expected)
        self.assertEqual(efi.tree(self.oc/'Kexts/Lilu.kext'), before_lilu)
        self.assertEqual((backup/'config.plist').read_bytes(), self.before)
        self.assertEqual(efi.tree(self.oc/'Kexts'/efi.NAME), efi.tree(self.payload/efi.NAME))
        efi.restore(self.oc, backup)
        self.assertEqual((self.oc/'config.plist').read_bytes(), self.before)
        self.assertFalse((self.oc/'Kexts'/efi.NAME).exists())

    def test_update_and_restore_previous_helper(self):
        self.old_helper();previous = efi.tree(self.oc/'Kexts'/efi.NAME)
        backup = efi.install(self.oc, self.payload)
        self.assertEqual(efi.tree(backup/efi.NAME), previous)
        efi.restore(self.oc, backup)
        self.assertEqual(efi.tree(self.oc/'Kexts'/efi.NAME), previous)
        self.assertEqual((self.oc/'config.plist').read_bytes(), self.before)

    def test_binary_plist_preserved(self):
        self.save(plistlib.FMT_BINARY)
        efi.install(self.oc, self.payload)
        self.assertTrue((self.oc/'config.plist').read_bytes().startswith(b'bplist'))

    def test_copy_failure_keeps_original(self):
        self.old_helper();previous = efi.tree(self.oc/'Kexts'/efi.NAME)
        with patch.object(efi.shutil, 'copytree', side_effect=OSError('copy failed')):
            with self.assertRaises(OSError):efi.install(self.oc, self.payload)
        self.assertEqual((self.oc/'config.plist').read_bytes(), self.before)
        self.assertEqual(efi.tree(self.oc/'Kexts'/efi.NAME), previous)
        self.assertFalse((self.oc/'.NVWebKitFixup.lock').exists())

    def test_config_failure_restores_old_bundle(self):
        self.old_helper();previous = efi.tree(self.oc/'Kexts'/efi.NAME)
        atomic = efi.atomic
        def fail(path, data):
            if path == self.oc/'config.plist':raise OSError('config write failed')
            atomic(path, data)
        with patch.object(efi, 'atomic', side_effect=fail):
            with self.assertRaises(OSError):efi.install(self.oc, self.payload)
        self.assertEqual((self.oc/'config.plist').read_bytes(), self.before)
        self.assertEqual(efi.tree(self.oc/'Kexts'/efi.NAME), previous)

    def test_guarded_restore_refuses_later_edits(self):
        backup = efi.install(self.oc, self.payload)
        (self.oc/'config.plist').write_bytes(b'later user edit')
        with self.assertRaisesRegex(ValueError, 'Current config changed'):efi.restore(self.oc, backup)
        self.assertEqual((self.oc/'config.plist').read_bytes(), b'later user edit')
        self.assertTrue((self.oc/'Kexts'/efi.NAME).exists())

    def test_preflight_race_preserves_external_config(self):
        copytree=efi.shutil.copytree
        def changed(*args,**kwargs):
            result=copytree(*args,**kwargs)
            (self.oc/'config.plist').write_bytes(b'external edit')
            return result
        with patch.object(efi.shutil,'copytree',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'changed externally'):efi.install(self.oc,self.payload)
        self.assertEqual((self.oc/'config.plist').read_bytes(),b'external edit')
        self.assertFalse((self.oc/'Kexts'/efi.NAME).exists())

    def test_restore_copy_failure_retains_new_installation(self):
        self.old_helper();backup=efi.install(self.oc,self.payload)
        updated=(self.oc/'config.plist').read_bytes();current=efi.tree(self.oc/'Kexts'/efi.NAME)
        with patch.object(efi.shutil,'copytree',side_effect=OSError('restore copy failed')):
            with self.assertRaises(OSError):efi.restore(self.oc,backup)
        self.assertEqual((self.oc/'config.plist').read_bytes(),updated)
        self.assertEqual(efi.tree(self.oc/'Kexts'/efi.NAME),current)

    def test_vault_and_missing_disabled_old_lilu_refused(self):
        for setting in ('vault','missing','disabled','old','quirk'):
            with self.subTest(setting=setting):
                config = copy.deepcopy(self.config)
                if setting=='vault':config['Misc']['Security']['Vault']='Secure'
                if setting=='missing':config['Kernel']['Add']=config['Kernel']['Add'][1:]
                if setting=='disabled':config['Kernel']['Add'][0]['Enabled']=False
                if setting=='old':config['Kernel']['Add'][0]['MaxKernel']='23.99.99'
                if setting=='quirk':config['Kernel']['Quirks']['DisableLinkeditJettison']=False
                (self.oc/'config.plist').write_bytes(plistlib.dumps(config))
                with self.assertRaises(ValueError):efi.install(self.oc, self.payload)
                self.assertEqual(plistlib.loads((self.oc/'config.plist').read_bytes()),config)
                self.assertFalse((self.oc/'Kexts'/efi.NAME).exists())

    def test_symlink_and_concurrent_lock_refused(self):
        target=self.oc/'Kexts'/efi.NAME;target.symlink_to(self.payload/efi.NAME)
        with self.assertRaises(ValueError):efi.install(self.oc,self.payload)
        target.unlink();(self.oc/'.NVWebKitFixup.lock').mkdir()
        with self.assertRaises(FileExistsError):efi.install(self.oc,self.payload)
        self.assertEqual((self.oc/'config.plist').read_bytes(),self.before)

    def test_explicit_partition_mounted_and_unmounted(self):
        info={'Content':'EFI','Mounted':False}
        mounted={'Content':'EFI','Mounted':True,'MountPoint':str(self.root),'ReadOnlyVolume':False}
        with patch.object(efi,'disk_info',side_effect=[info,mounted]),patch.object(efi,'command') as commands:
            with efi.target_oc('disk7s1') as oc:self.assertEqual(oc,self.oc)
        self.assertEqual([c.args for c in commands.call_args_list],
                         [('/usr/sbin/diskutil','mount','disk7s1'),('/usr/sbin/diskutil','unmount','disk7s1')])

    def test_normal_volume_refused_without_mount(self):
        with patch.object(efi,'disk_info',return_value={'Content':'Apple_APFS'}),patch.object(efi,'command') as commands:
            with self.assertRaises(ValueError):
                with efi.target_oc('disk7s1'):pass
        commands.assert_not_called()

    def test_existing_unsigned_lilu_allowed_but_our_payload_must_verify(self):
        with patch.object(efi,'command',return_value=b'') as commands:
            efi.prepare(self.oc,self.payload)
        signatures=[c.args[-1] for c in commands.call_args_list if c.args[0]=='/usr/bin/codesign']
        self.assertNotIn(self.oc/'Kexts/Lilu.kext',signatures)
        self.assertIn(self.payload/efi.NAME,signatures)


if __name__ == '__main__':
    unittest.main()
