#!/usr/bin/env python3
"""Run the real checksum guards against complete, unlisted and escaped payloads."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'build'))
import stage_package as stage

CHECKER = ROOT / 'package/sums_complete.sh'


class ManifestCoverage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='manifest coverage ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'pkgroot'
        self.root.mkdir()
        self.put('sums_complete.sh', CHECKER.read_bytes())
        self.put('Library/runtime.bin', b'checked runtime')
        stage.write_manifest(self.root)

    def put(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def shell(self):
        return subprocess.run(['/bin/bash', str(CHECKER), str(self.root)], capture_output=True, text=True)

    def assert_clean(self):
        stage.verify_manifest(self.root)
        result = self.shell()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_complete_manifest_passes(self):
        self.assert_clean()

    def test_bare_binary_and_uppercase_records_pass(self):
        manifest = self.root / 'SHA256SUMS'
        rows = manifest.read_text().splitlines()
        manifest.write_text(''.join(row[:64].upper() + ' *' + row[68:] + '\n' for row in rows))
        self.assert_clean()

    def test_unlisted_kext_is_refused_even_when_all_listed_hashes_match(self):
        self.put('Library/Extensions/unlisted.kext/Contents/MacOS/unlisted', b'extra code')
        hashes = subprocess.run(['shasum', '-a', '256', '--strict', '-c', 'SHA256SUMS'],
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual(hashes.returncode, 0)  # negative control: digest verification alone misses it
        with self.assertRaisesRegex(ValueError, 'does not list'):
            stage.verify_manifest(self.root)
        result = self.shell()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unlisted.kext', result.stdout)

    def test_shared_prefix_does_not_cover_a_file(self):
        self.put('Library/runtime.bin.extra', b'extra')
        with self.assertRaisesRegex(ValueError, 'does not list'):
            stage.verify_manifest(self.root)
        self.assertNotEqual(self.shell().returncode, 0)

    def test_internal_symlink_needs_no_duplicate_record(self):
        (self.root / 'Library/runtime.dylib').symlink_to('runtime.bin')
        self.assert_clean()

    def test_escaped_names_match_the_existing_package_writer(self):
        for name in ['odd\\backslash', 'odd\nnewline', 'odd\rcarriage', 'normal space']:
            self.put(name, name.encode())
        stage.write_manifest(self.root)
        self.assert_clean()

    def test_unlisted_escaped_name_is_reported_without_breaking_lines(self):
        self.put('odd\nnewline', b'unlisted')
        result = self.shell()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(r'odd\nnewline', result.stdout)

    def test_missing_manifest_is_refused(self):
        (self.root / 'SHA256SUMS').unlink()
        self.assertNotEqual(self.shell().returncode, 0)

    def test_malformed_record_is_refused(self):
        with (self.root / 'SHA256SUMS').open('a') as output:
            output.write('0000  Library/runtime.bin\n')
        self.assertNotEqual(self.shell().returncode, 0)

    def test_manifest_path_cannot_escape(self):
        with (self.root / 'SHA256SUMS').open('a') as output:
            output.write('1' * 64 + '  ../outside\n')
        self.assertNotEqual(self.shell().returncode, 0)

    def test_tampered_digest_is_still_refused_by_build_guard(self):
        self.put('Library/runtime.bin', b'changed')
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            stage.verify_manifest(self.root)


class InstallerCoverage(unittest.TestCase):
    def run_integrity(self, root):
        source = (ROOT / 'package/install.sh').read_text()
        start = source.index('# --strict: without it')
        end = source.index('for target in 15 26; do', start)
        block = source[start:end]
        script = 'die() { echo "DIE $*"; exit 1; }; HERE=$1\n' + block + '\necho VERIFIED\n'
        return subprocess.run(['/bin/bash', '-c', script, 'integrity-test', str(root)],
                              capture_output=True, text=True)

    def test_real_guard_requires_covered_checker_and_complete_payload(self):
        with tempfile.TemporaryDirectory(prefix='installer coverage ') as name:
            root = Path(name).resolve()
            (root / 'sums_complete.sh').write_bytes(CHECKER.read_bytes())
            (root / 'runtime').write_bytes(b'checked')
            stage.write_manifest(root)
            self.assertEqual(self.run_integrity(root).returncode, 0)
            (root / 'unlisted').write_bytes(b'extra')
            result = self.run_integrity(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('does not list', result.stdout)

    def test_uncovered_checker_does_not_execute(self):
        with tempfile.TemporaryDirectory(prefix='installer checker ') as name:
            root = Path(name).resolve()
            (root / 'runtime').write_bytes(b'checked')
            stage.write_manifest(root)
            marker = root.parent / (root.name + '-executed')
            (root / 'sums_complete.sh').write_text('touch "' + str(marker) + '"\n')
            result = self.run_integrity(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('verified sums_complete.sh', result.stdout)
            self.assertFalse(marker.exists())

    def test_checker_hash_mismatch_stops_before_execution(self):
        with tempfile.TemporaryDirectory(prefix='installer tamper ') as name:
            root = Path(name).resolve()
            (root / 'sums_complete.sh').write_bytes(CHECKER.read_bytes())
            stage.write_manifest(root)
            (root / 'sums_complete.sh').write_text('echo UNTRUSTED_EXECUTION\n')
            result = self.run_integrity(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('mismatch', result.stdout)
            self.assertNotIn('UNTRUSTED_EXECUTION', result.stdout)


if __name__ == '__main__':
    unittest.main()
