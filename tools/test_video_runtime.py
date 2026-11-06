#!/usr/bin/env python3
"""Codec package copies/provenance checks; signing tools are simulated here."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('stage_video_test', ROOT / 'build/stage_package.py')
STAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STAGE)


class VideoRuntime(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nvenc-package-')
        self.root = Path(self.temp.name).resolve()
        self.runtime = self.root / 'runtime'
        pin = json.loads((ROOT / 'video/nvenc-source.json').read_text())
        payload = {'libnvencvt.dylib': b'test binary', 'INSTALL.md': b'guide',
                   'source-changes.patch': (ROOT / 'video' / pin['overlay']).read_bytes(),
                   'LICENSES/nvenc-MIT.txt': (ROOT / 'video/nvenc-MIT.txt').read_bytes(),
                   'LICENSES/NVIDIA-MIT.txt': b'NVIDIA notice', 'LICENSES/WebKit-BSD.txt': b'WebKit notice'}
        for name, data in payload.items():
            path = self.runtime / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.record = {'schema_version': 1, 'kind': 'nvenc-codecs', 'source': pin['source'], 'dependencies': pin['dependencies'],
                       'registration_api': {'version': 2, 'symbol': 'NVEncVT_RegisterCodecs', 'decoder_scope': 1, 'encoder_scope': 2},
                       'overlay_sha256': STAGE.digest(ROOT / 'video' / pin['overlay']),
                       'target': {'os': 'macos', 'arch': 'x86_64', 'minimum_macos': '15.0'},
                       'turing_defaults': {'additional_decoders': ['vp08', 'vp09'], 'replace_stock_encoders': False, 'av1': False},
                       'installation': {'library': '/Library/GPUBundles/NVMTLDriver.bundle/Contents/MacOS/libnvencvt.dylib',
                                        'records': '/Library/GPUBundles/nvmtl/video'},
                       'files': {name: STAGE.digest(self.runtime / name) for name in payload}}
        self.write_record()
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def write_record(self):
        (self.runtime / 'NVENC-BUILD.json').write_text(json.dumps(self.record))

    def fake_run(self, *args, **kwargs):
        self.calls.append(tuple(map(str, args)))
        return subprocess.CompletedProcess(args, 0, 'minos 15.0\n' if args[0] == 'vtool' else '', '')

    def validate(self):
        with mock.patch.object(STAGE, 'run', self.fake_run):
            return STAGE.validate_video_runtime(self.runtime)

    def test_complete_codec_payload_is_placed_once_and_preserves_other_codecs(self):
        files = self.validate()
        package = self.root / 'pkgroot'
        binary = package / STAGE.PLUGIN / 'Contents/MacOS/libnvenc_h264_tu10x.dylib'
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'original Turing encoder')
        conf = package / STAGE.RUNTIME / 'nvrm610.conf'
        conf.parent.mkdir(parents=True)
        conf.write_text('OTHER=1\n')
        with mock.patch.object(STAGE, 'run', self.fake_run):
            STAGE.stage_video_runtime(package, self.runtime, files)
        self.assertEqual(binary.read_bytes(), b'original Turing encoder')
        self.assertEqual((binary.parent / 'libnvencvt.dylib').read_bytes(), b'test binary')
        self.assertFalse((package / STAGE.RUNTIME / 'video/libnvencvt.dylib').exists())
        self.assertEqual(conf.read_text().count('NVMTL_NVENCVT=1'), 1)
        STAGE.write_manifest(package)
        STAGE.verify_manifest(package)
        self.assertEqual(STAGE.digest(self.runtime / 'libnvencvt.dylib'), self.record['files']['libnvencvt.dylib'])
        self.assertTrue(any(call[:3] == ('codesign', '--verify', '--strict') for call in self.calls))

    def test_changed_library_is_rejected(self):
        (self.runtime / 'libnvencvt.dylib').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.validate()

    def test_unscoped_addon_is_rejected(self):
        self.record.pop('registration_api')
        self.write_record()
        with self.assertRaisesRegex(ValueError, 'scoped registration API'):
            self.validate()

    def test_stale_overlay_is_rejected(self):
        self.record['overlay_sha256'] = '0' * 64
        self.write_record()
        with self.assertRaisesRegex(ValueError, 'overlay mismatch'):
            self.validate()

    def test_turing_av1_or_encoder_takeover_is_rejected(self):
        self.record['turing_defaults']['av1'] = True
        self.write_record()
        with self.assertRaisesRegex(ValueError, 'Turing codec selection'):
            self.validate()

    def test_symlinked_library_is_rejected(self):
        library = self.runtime / 'libnvencvt.dylib'
        library.unlink()
        library.symlink_to(self.runtime / 'INSTALL.md')
        with self.assertRaisesRegex(ValueError, 'regular file'):
            self.validate()

    def test_appledouble_and_unrecorded_payload_are_rejected(self):
        (self.runtime / '._libnvencvt.dylib').write_bytes(b'metadata')
        with self.assertRaisesRegex(ValueError, 'unrecorded'):
            self.validate()

    def test_config_defaults_preserve_explicit_optouts_and_process_selection(self):
        for value in ('0', '1', 'OBS'):
            original = 'OTHER=1\nNVMTL_NVENCVT=' + value + '\n'
            self.assertEqual(STAGE.video_default_config(original), original)
        generated = STAGE.video_default_config('# NVMTL_NVENCVT=0\n')
        self.assertEqual(generated.count('\nNVMTL_NVENCVT=1\n'), 1)
        self.assertEqual(STAGE.video_default_config(generated), generated)


if __name__ == '__main__':
    unittest.main()
