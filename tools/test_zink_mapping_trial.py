#!/usr/bin/env python3
"""Check trial backup, atomic replacement failures and rollback without /Library writes."""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zink_mapping_trial as trial


class TrialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payload = self.root/'payload'; self.payload.mkdir()
        self.installed = self.root/'installed'; self.installed.mkdir()
        self.old = b'original published library'
        self.new = b'candidate mapping repair'
        (self.installed/trial.LIBRARY).write_bytes(self.old)
        (self.payload/trial.LIBRARY).write_bytes(self.new)
        for name in trial.PRIVATE: (self.installed/name).write_bytes(name.encode())
        self.record = {'installed_sha256': {p.name:trial.digest(p) for p in self.installed.iterdir()},
                       'payload_sha256': {trial.LIBRARY:trial.digest(self.payload/trial.LIBRARY)}}
        (self.payload/'TRIAL.json').write_text(json.dumps(self.record))
        for name, value in (('ROOT', self.payload), ('INSTALLED', self.installed)):
            p = patch.object(trial, name, value);p.start();self.addCleanup(p.stop)
        self.sign = patch.object(trial.subprocess, 'run');self.sign.start();self.addCleanup(self.sign.stop)

    def test_apply_is_idempotent_and_restore_is_exact(self):
        trial.apply(self.record)
        self.assertEqual((self.installed/trial.LIBRARY).read_bytes(), self.new)
        trial.apply(self.record)
        trial.restore(self.record)
        self.assertEqual((self.installed/trial.LIBRARY).read_bytes(), self.old)
        self.assertFalse((self.installed/trial.BACKUP_NAME).exists())

    def test_signing_failure_leaves_installed_library_and_no_partial_backup(self):
        with patch.object(trial.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'codesign')):
            with self.assertRaises(subprocess.CalledProcessError): trial.apply(self.record)
        self.assertEqual((self.installed/trial.LIBRARY).read_bytes(), self.old)
        self.assertFalse((self.installed/trial.BACKUP_NAME).exists())
        self.assertFalse(list(self.installed.glob('.zink-trial-*')))

    def test_restore_does_not_overwrite_a_newer_driver(self):
        trial.apply(self.record)
        (self.installed/trial.LIBRARY).write_bytes(b'a newer release')
        with self.assertRaises(RuntimeError): trial.restore(self.record)
        self.assertEqual((self.installed/trial.LIBRARY).read_bytes(), b'a newer release')

    def test_damaged_backup_is_preserved_and_never_restored(self):
        trial.apply(self.record)
        (self.installed/trial.BACKUP_NAME/trial.LIBRARY).write_bytes(b'damaged backup')
        with self.assertRaises(RuntimeError): trial.restore(self.record)
        self.assertEqual((self.installed/trial.LIBRARY).read_bytes(), self.new)
        self.assertTrue((self.installed/trial.BACKUP_NAME).exists())

    def test_damaged_payload_is_rejected(self):
        (self.payload/trial.LIBRARY).write_bytes(b'damaged payload')
        with self.assertRaises(RuntimeError): trial.manifest()
        self.assertEqual((self.installed/trial.LIBRARY).read_bytes(), self.old)

    def upgrade_record(self):
        trial.apply(self.record)
        updated = json.loads(json.dumps(self.record))
        updated['previous_trial_sha256'] = [self.record['payload_sha256'][trial.LIBRARY]]
        (self.payload/trial.LIBRARY).write_bytes(b'second candidate')
        updated['payload_sha256'][trial.LIBRARY] = trial.digest(self.payload/trial.LIBRARY)
        return updated

    def test_upgrade_preserves_original_and_can_restore(self):
        updated = self.upgrade_record()
        trial.apply(updated)
        self.assertEqual((self.installed/trial.LIBRARY).read_bytes(), b'second candidate')
        self.assertEqual((self.installed/trial.BACKUP_NAME/trial.LIBRARY).read_bytes(), self.old)
        trial.restore(updated)
        self.assertEqual((self.installed/trial.LIBRARY).read_bytes(), self.old)

    def test_failed_upgrade_can_restore_original(self):
        updated = self.upgrade_record()
        with patch.object(trial.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'codesign')):
            with self.assertRaises(subprocess.CalledProcessError): trial.apply(updated)
        self.assertEqual((self.installed/trial.LIBRARY).read_bytes(), self.new)
        trial.restore(updated)
        self.assertEqual((self.installed/trial.LIBRARY).read_bytes(), self.old)

    def test_upgrade_refuses_unknown_library(self):
        updated = self.upgrade_record()
        (self.installed/trial.LIBRARY).write_bytes(b'unknown newer driver')
        with self.assertRaises(RuntimeError): trial.apply(updated)
        self.assertEqual((self.installed/trial.LIBRARY).read_bytes(), b'unknown newer driver')


if __name__ == '__main__':
    unittest.main()
