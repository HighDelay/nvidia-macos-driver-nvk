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

    def test_reinstall_does_not_backup_copy_or_rewrite(self):
        first=efi.install(self.oc,self.payload)
        paths=[self.oc/'config.plist',self.oc/'Kexts'/efi.NAME/'Contents/MacOS/NVWebKitFixup']
        before={p:(p.read_bytes(),p.stat().st_ino,p.stat().st_mtime_ns) for p in paths}
        with patch.object(efi,'atomic') as writes,patch.object(efi.shutil,'copytree') as copies:
            for _ in range(3):self.assertIsNone(efi.install(self.oc,self.payload))
        writes.assert_not_called();copies.assert_not_called()
        self.assertEqual(list((self.oc/'NVWebKitFixup-backups').iterdir()),[first])
        self.assertEqual(before,{p:(p.read_bytes(),p.stat().st_ino,p.stat().st_mtime_ns) for p in paths})

    def test_manual_matching_xml_keeps_comments_and_needs_no_backup_folder(self):
        original,updated=efi.prepare(self.oc,self.payload)
        (self.oc/'config.plist').write_bytes(updated.replace(b'<dict>',b'<!-- user formatting -->\n<dict>',1))
        efi.shutil.copytree(self.payload/efi.NAME,self.oc/'Kexts'/efi.NAME)
        before=(self.oc/'config.plist').read_bytes()
        self.assertIsNone(efi.install(self.oc,self.payload))
        self.assertEqual((self.oc/'config.plist').read_bytes(),before)
        self.assertFalse((self.oc/'NVWebKitFixup-backups').exists())

    def test_real_kext_update_backs_up_once_and_retains_config(self):
        first=efi.install(self.oc,self.payload)
        before=(self.oc/'config.plist').read_bytes();previous=efi.tree(self.oc/'Kexts'/efi.NAME)
        info=self.oc/'config.plist';inode=info.stat().st_ino
        (self.payload/efi.NAME/'Contents/MacOS/NVWebKitFixup').write_bytes(b'next helper')
        second=efi.install(self.oc,self.payload)
        self.assertNotEqual(first,second);self.assertEqual(efi.tree(second/efi.NAME),previous)
        self.assertEqual(info.read_bytes(),before);self.assertEqual(info.stat().st_ino,inode)
        self.assertIsNone(efi.install(self.oc,self.payload))
        self.assertEqual(set((self.oc/'NVWebKitFixup-backups').iterdir()),{first,second})
        efi.restore(self.oc,second)
        self.assertEqual(efi.tree(self.oc/'Kexts'/efi.NAME),previous)

    def test_config_only_update_backs_up_without_kext_duplication_and_restores(self):
        first=efi.install(self.oc,self.payload)
        config=plistlib.loads((self.oc/'config.plist').read_bytes())
        config['Kernel']['Add'][1]['Enabled']=False
        before=plistlib.dumps(config);(self.oc/'config.plist').write_bytes(before)
        binary=self.oc/'Kexts'/efi.NAME/'Contents/MacOS/NVWebKitFixup';inode=binary.stat().st_ino
        second=efi.install(self.oc,self.payload)
        self.assertFalse((second/efi.NAME).exists())
        self.assertFalse(json.loads((second/'state.json').read_text())['kext_changed'])
        self.assertEqual(binary.stat().st_ino,inode)
        efi.restore(self.oc,second)
        self.assertEqual((self.oc/'config.plist').read_bytes(),before)
        self.assertEqual(binary.stat().st_ino,inode)

    def test_legacy_backup_without_change_flag_remains_restorable(self):
        self.old_helper();before=efi.tree(self.oc/'Kexts'/efi.NAME)
        backup=efi.install(self.oc,self.payload)
        state=json.loads((backup/'state.json').read_text());state.pop('kext_changed')
        (backup/'state.json').write_text(json.dumps(state))
        efi.restore(self.oc,backup)
        self.assertEqual(efi.tree(self.oc/'Kexts'/efi.NAME),before)

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

    def test_already_mounted_partition_is_not_unmounted(self):
        # Real diskutil plist output uses MountPoint and may omit Mounted.
        info={'Content':'EFI','MountPoint':str(self.root),'Writable':True}
        with patch.object(efi,'disk_info',return_value=info),patch.object(efi,'command') as commands:
            with efi.target_oc('disk7s1') as oc:self.assertEqual(oc,self.oc)
        commands.assert_not_called()

    def test_readonly_mounted_partition_refused(self):
        for key,value in (('VolumeReadOnly',True),('MediaReadOnly',True),('Writable',False),('WritableMedia',False),('WritableVolume',False)):
            info={'Content':'EFI','MountPoint':str(self.root),key:value}
            with self.subTest(key=key),patch.object(efi,'disk_info',return_value=info):
                with self.assertRaisesRegex(ValueError,'read-only'):
                    with efi.target_oc('disk7s1'):pass

    def test_existing_unsigned_lilu_allowed_but_our_payload_must_verify(self):
        with patch.object(efi,'command',return_value=b'') as commands:
            efi.prepare(self.oc,self.payload)
        signatures=[c.args[-1] for c in commands.call_args_list if c.args[0]=='/usr/bin/codesign']
        self.assertNotIn(self.oc/'Kexts/Lilu.kext',signatures)
        self.assertIn(self.payload/efi.NAME,signatures)

    def disk_commands(self, disks, listing=None):
        """Model diskutil mount state, including ESPs whose names are misleading."""
        if listing is None:
            listing = {'AllDisksAndPartitions':[{'Content':'GUID_partition_scheme', 'Partitions':[
                {'DeviceIdentifier':name, 'Content':info.get('Content', ''),
                 'PartitionType':info.get('PartitionType', '')} for name,info in disks.items()]}]}
        def run(*args):
            if args[0] != '/usr/sbin/diskutil':
                return b''  # lipo and codesign: the same fixture contract as the transaction tests
            if args[1:] == ('list','-plist'):
                return plistlib.dumps(listing)
            action = args[1]; target = args[-1]; info = disks[target]
            if action == 'info':
                return plistlib.dumps({k:v for k,v in info.items() if k != '_mount'})
            if action == 'mount':
                info['MountPoint'] = info['_mount']
            elif action == 'unmount':
                info.pop('MountPoint', None)
            else:
                self.fail('Unexpected diskutil action: ' + action)
            return b''
        return run

    def test_detection_uses_partition_type_and_restores_mount_state(self):
        disks = {'disk0s1':{'Content':'EFI','_mount':str(self.root)},
                 'disk0s4':{'Content':'Apple_APFS','VolumeName':'EFI','_mount':'unused'},
                 'disk9s2':{'PartitionType':efi.EFI_GUID.lower(),'MountPoint':str(self.root)}}
        before = efi.tree(self.oc)
        with patch.object(efi,'command',side_effect=self.disk_commands(disks)) as commands:
            self.assertEqual(efi.efi_partitions(),['disk0s1','disk9s2'])
            # Rejecting an ambiguous target cannot create a backup or change a config.
            with patch.object(efi.sys.stdin,'isatty',return_value=False):
                with self.assertRaisesRegex(ValueError,'ambiguous'):efi.detect_target(self.payload)
        self.assertEqual(before,efi.tree(self.oc))
        self.assertNotIn('MountPoint',disks['disk0s1'])
        self.assertEqual(disks['disk9s2']['MountPoint'],str(self.root))
        disk_calls = [c.args for c in commands.call_args_list if c.args[0]=='/usr/sbin/diskutil']
        self.assertNotIn(('/usr/sbin/diskutil','info','-plist','disk0s4'),disk_calls)
        self.assertEqual([c for c in disk_calls if c[1] in ('mount','unmount')],
                         [('/usr/sbin/diskutil','mount','disk0s1'),('/usr/sbin/diskutil','unmount','disk0s1')])

    def test_unique_compatible_opencore_selected_and_empty_efi_skipped(self):
        empty=self.root/'empty';empty.mkdir()
        disks={'disk7s1':{'Content':'EFI','_mount':str(empty)},
               'disk0s1':{'Content':'EFI','_mount':str(self.root)}}
        with patch.object(efi,'command',side_effect=self.disk_commands(disks)):
            self.assertEqual(efi.detect_target(self.payload),'disk0s1')
        self.assertTrue(all('MountPoint' not in d for d in disks.values()))
        self.assertFalse((self.oc/'NVWebKitFixup-backups').exists())

    def test_detection_refuses_vault_lilu_and_readonly_failures(self):
        for reason in ('vault','lilu','readonly'):
            config=copy.deepcopy(self.config)
            if reason=='vault':config['Misc']['Security']['Vault']='Secure'
            if reason=='lilu':config['Kernel']['Add'][0]['Enabled']=False
            (self.oc/'config.plist').write_bytes(plistlib.dumps(config))
            disks={'disk0s1':{'Content':'EFI','_mount':str(self.root),'ReadOnlyVolume':reason=='readonly'}}
            with self.subTest(reason=reason),patch.object(efi,'command',side_effect=self.disk_commands(disks)):
                with self.assertRaisesRegex(ValueError,'No compatible OpenCore EFI'):efi.detect_target(self.payload)
            self.assertNotIn('MountPoint',disks['disk0s1'])
            self.assertFalse((self.oc/'Kexts'/efi.NAME).exists())

    def test_multiple_candidates_require_valid_interactive_selection(self):
        disks={'disk0s1':{'Content':'EFI','MountPoint':str(self.root)},
               'disk8s3':{'Content':'EFI','MountPoint':str(self.root)}}
        with patch.object(efi,'command',side_effect=self.disk_commands(disks)), \
             patch.object(efi.sys.stdin,'isatty',return_value=True):
            with patch('builtins.input',return_value='2'):
                self.assertEqual(efi.detect_target(self.payload),'disk8s3')
            for answer in ('','0','3','disk0s1'):
                with self.subTest(answer=answer),patch('builtins.input',return_value=answer):
                    with self.assertRaisesRegex(ValueError,'No EFI selected'):efi.detect_target(self.payload)
        self.assertFalse((self.oc/'NVWebKitFixup-backups').exists())

    def test_explicit_target_bypasses_detection_and_restore_needs_target(self):
        disks={'disk0s1':{'Content':'EFI','MountPoint':str(self.root)}}
        with patch.object(efi.os,'geteuid',return_value=0),patch.object(efi,'detect_target') as detect:
            with patch.object(efi.sys,'argv',['efi.py','install','--target','disk0s1']), \
                 patch.object(efi,'command',side_effect=self.disk_commands(disks)),patch.object(efi,'install') as install:
                efi.main();install.assert_called_once()
            with patch.object(efi.sys,'argv',['efi.py','restore','--backup','a'*32]):
                with self.assertRaisesRegex(ValueError,'original --target'):efi.main()
            detect.assert_not_called()

    def test_no_efi_and_malformed_identifiers_are_not_guessed(self):
        for listing in ({'AllDisksAndPartitions':[]},
                        {'AllDisksAndPartitions':[{'Content':'EFI','DeviceIdentifier':'disk0'}]},
                        {'AllDisks':['disk0s1']}):
            with self.subTest(listing=listing),patch.object(efi,'command',return_value=plistlib.dumps(listing)):
                with self.assertRaises(ValueError):efi.detect_target(self.payload)


if __name__ == '__main__':
    unittest.main()
