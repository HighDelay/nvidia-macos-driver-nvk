#!/usr/bin/env python3
"""Exercise atomic staging, integrity coverage and firmware pairing on real files."""
from pathlib import Path
import hashlib
import json
import plistlib
import os
import subprocess
import sys
import tempfile
import unittest
import tarfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'build'))
import package_zink as pkg
from record_zink_runtime import record


class PackageZinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='zink package test ')
        self.root = Path(self.temp.name).resolve()
        self.base = self.root / 'base'
        self.zink = self.root / 'zink'
        self.out = self.root / 'output'
        self.commands = []
        self.firmware = '/Library/NullMoth/nvfw/nvidia/615.78.08/gsp_tu10x.bin\n'
        for name in ('install.sh', 'uninstall.sh', 'nullmoth-runtime-check.sh', 'nullmoth-runtime-check', 'VERSION'):
            self.put(self.base / name, b'base')
        for name in ('NVRM', 'NVRMFB', 'NVRMAGDC', 'NVAccel'):
            self.bundle(self.base / 'Library/Extensions' / (name + '.kext'), name)
        for major in ('15', '26'):
            self.bundle(self.base / 'Library/NullMoth/kexts' / major / 'NVAccel.kext', 'NVAccel')
        for name in ('NVMTLDriver', 'NVIDIAShared'):
            self.bundle(self.base / 'Library/GPUBundles' / (name + '.bundle'), name)
        for name in ('libvulkan_nouveau.dylib', 'libvulkan.1.4.362.dylib'):
            self.put(self.base / pkg.BASE_RUNTIME / name, b'base runtime')
        self.put(self.base / pkg.BASE_RUNTIME / 'nvk_icd.json', json.dumps({'ICD': {'library_path': '/Library/GPUBundles/nvmtl/libvulkan_nouveau.dylib'}}).encode())
        self.put(self.base / 'Users/Shared/nvfw/nvidia/615.78.08/gsp_tu10x.bin', b'firmware')
        self.put(self.base / 'licenses/base-notice.txt', b'keep this notice')
        for name in pkg.ZINK_FILES:
            self.put(self.zink / name, b'25G241\n' if name == 'cgl-os-build.txt' else b'zink ' + name.encode())
        native = {'renderer_original_sha256':pkg.base_tools.digest(self.base/pkg.BASE_RUNTIME/'libvulkan_nouveau.dylib'),
                  'frontend_source_sha256':pkg.base_tools.digest(ROOT/'nvk/native-macos/bridge.mm'),
                  'files':{name:pkg.base_tools.digest(self.zink/name) for name in
                           ['libnvk_renderer.dylib','libnvk_zink_vulkan.dylib']}}
        (self.zink/'NATIVE-BUILD.json').write_text(json.dumps(native))
        (self.zink/'ZINK-BUILD.json').unlink()
        mesa = self.root/'MESA-SOURCE.json'
        mesa.write_text(json.dumps({'commit':pkg.MESA_COMMIT,
            'overlay_sha256':pkg.base_tools.digest(ROOT/'zink/mesa-zink-nvk.patch')}))
        record(self.zink,'zink',mesa); record(self.zink,'cgl')
        pkg.base_tools.write_manifest(self.base)
        self.original = self.snapshot(self.base)
        self.zink_original = self.snapshot(self.zink)

    def tearDown(self):
        self.temp.cleanup()

    def put(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def bundle(self, path, name):
        self.put(path / 'Contents/Info.plist', plistlib.dumps({'CFBundleExecutable': name}))
        self.put(path / 'Contents/MacOS' / name, b'base executable ' + name.encode())

    @staticmethod
    def snapshot(root):
        return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}

    def fake_run(self, *args):
        self.commands.append(tuple(str(a) for a in args))
        if args[0] == '/bin/bash' and str(args[1]).endswith('build_intel_panel.sh'):
            self.bundle(Path(args[2])/'NMIntelFB.kext', 'NMIntelFB')
        if args[0] == 'strings': return self.firmware
        if args[0] == 'git': return 'test-commit\n'
        if args[0] == 'otool': return 'cmd LC_RPATH\n cmdsize 40\n path /Library/GPUBundles/nvmtl (offset 12)\n'
        if args[0] == 'sw_vers': return '26.7.1\n'
        return ''

    def stage(self, native_gld=False):
        with patch.object(pkg, 'run', side_effect=self.fake_run):
            return pkg.stage(self.base, self.zink, self.out, native_gld=native_gld)

    def unchanged(self):
        self.assertEqual(self.original, self.snapshot(self.base))
        self.assertEqual(self.zink_original, self.snapshot(self.zink))
        self.assertFalse(list(self.root.glob('.zink-package-*')))

    def test_complete_stage_preserves_payload_and_covers_every_regular_file(self):
        self.stage()
        self.unchanged()
        for name in pkg.ZINK_FILES:
            self.assertEqual((self.out / pkg.RUNTIME / name).read_bytes(), (self.zink / name).read_bytes())
        self.assertEqual((self.out / 'licenses/base-notice.txt').read_bytes(), b'keep this notice')
        self.assertEqual(json.loads((self.out / pkg.RUNTIME / 'nvk_icd.json').read_text())['ICD']['library_path'], './libnvk_renderer.dylib')
        self.assertEqual((self.out / pkg.BASE_RUNTIME / 'nvk_icd.json').read_bytes(), (self.base / pkg.BASE_RUNTIME / 'nvk_icd.json').read_bytes())
        self.assertTrue((self.out/'Optional/NMIntelFB.kext/Contents/MacOS/NMIntelFB').is_file())
        self.assertFalse((self.out/'Library/Extensions/NMIntelFB.kext').exists())
        self.assertFalse(list(self.out.rglob('NMIntelFBVirtual.kext')))
        identity = json.loads((self.out/'Optional/INTEL-PANEL-BUILD.json').read_text())
        for name, sha in identity['files'].items():
            self.assertEqual(pkg.base_tools.digest(self.out/'Optional/NMIntelFB.kext'/name), sha)
        for command in ('Install.command', 'Verify.command', 'Uninstall.command'):
            self.assertTrue((self.out / command).stat().st_mode & 0o111)
        entries = {}
        for row in (self.out / 'SHA256SUMS').read_text().splitlines():
            digest, name = row.split('  ', 1)
            entries[name] = digest
            self.assertEqual(hashlib.sha256((self.out / name).read_bytes()).hexdigest(), digest)
        expected = {'./' + p.relative_to(self.out).as_posix() for p in self.out.rglob('*') if p.is_file() and p.name != 'SHA256SUMS'}
        expected.update('./Library/NullMoth/kexts/' + m + '/SHA256SUMS' for m in ('15', '26'))
        self.assertEqual(set(entries), expected)
        self.assertFalse(list(self.out.rglob('NVKZinkGLDriver.bundle')))

    def test_tampered_base_is_rejected_without_output(self):
        self.put(self.base / 'VERSION', b'tampered')
        with self.assertRaisesRegex(ValueError, 'SHA256SUMS mismatch'):
            self.stage()
        self.assertFalse(self.out.exists())

    def test_signed_shader_resources_survive_transient_cache_filter(self):
        signed = Path('Library/GPUBundles/NVMTLDriver.bundle/Contents/Resources/sass/seed.bin')
        framework = Path('Library/GPUBundles/Signed.framework/Versions/A/Resources/aircache/seed.bin')
        transient = pkg.BASE_RUNTIME / 'spvcache/temporary.bin'
        for relative in (signed, framework, transient):
            self.put(self.base / relative, b'signed seed' if relative != transient else b'transient')
        pkg.base_tools.write_manifest(self.base)
        self.original = self.snapshot(self.base)
        self.stage()
        self.unchanged()
        self.assertEqual((self.out / signed).read_bytes(), b'signed seed')
        self.assertEqual((self.out / framework).read_bytes(), b'signed seed')
        self.assertFalse((self.out / transient).exists())
        self.assertTrue(any(command[:3] == ('codesign', '--verify', '--strict')
                            and command[-1].endswith('/NVMTLDriver.bundle') for command in self.commands))

    def test_missing_firmware_version_cannot_publish_a_package(self):
        self.firmware = self.firmware.replace('615.78.08', '999.0')
        with self.assertRaisesRegex(ValueError, 'missing regular file'):
            self.stage()
        self.assertFalse(self.out.exists())
        self.unchanged()

    def test_missing_adapter_cannot_publish_partial_installation(self):
        (self.zink / 'libnvk_cgl.dylib').unlink()
        with self.assertRaisesRegex(ValueError, 'missing regular file'):
            self.stage()
        self.assertFalse(self.out.exists())

    def test_existing_output_is_not_overwritten(self):
        self.put(self.out / 'keep', b'existing output')
        with self.assertRaisesRegex(ValueError, 'Output must be new'):
            self.stage()
        self.assertEqual((self.out / 'keep').read_bytes(), b'existing output')
        self.unchanged()

    def test_diagnostic_gld_selection_is_rejected(self):
        p = self.base / 'Library/NullMoth/kexts/26/NVAccel.kext/Contents/Info.plist'
        data = plistlib.loads(p.read_bytes())
        data['IOKitPersonalities'] = {'NVAccel': {'IOGLBundleName': 'NVKZinkGLDriver'}}
        p.write_bytes(plistlib.dumps(data))
        pkg.base_tools.write_manifest(self.base)
        with self.assertRaisesRegex(ValueError, 'diagnostic GLD'):
            self.stage()
        self.assertFalse(self.out.exists())

    def native_bundle(self, build='25G241', diagnostic=False):
        bundle = self.zink / 'NVKZinkGLDriver.bundle'
        self.bundle(bundle, 'NVKZinkGLDriver')
        info = bundle / 'Contents/Info.plist'
        data = plistlib.loads(info.read_bytes())
        data.update(NVKZinkExperimentalGLD=True, NVKZinkBuildOS=build,
                    NVKZinkDiagnosticOnly=diagnostic)
        info.write_bytes(plistlib.dumps(data))
        self.put(self.zink / 'gld_bundle_probe', b'lookup checker')
        record(self.zink,'gld')
        self.zink_original = self.snapshot(self.zink)

    def test_native_stage_selects_both_os_accelerators(self):
        self.native_bundle()
        self.put(self.base/'Test-Zink.command',b'old Sequoia test launcher')
        self.put(self.base/pkg.RUNTIME/'zink-tests/old.py',b'old packaged runner')
        pkg.base_tools.write_manifest(self.base)
        self.original = self.snapshot(self.base)
        self.stage(native_gld=True)
        self.unchanged()
        for relative in ('Library/Extensions/NVAccel.kext', 'Library/NullMoth/kexts/15/NVAccel.kext',
                         'Library/NullMoth/kexts/26/NVAccel.kext'):
            info = plistlib.loads((self.out / relative / 'Contents/Info.plist').read_bytes())
            self.assertEqual(info['IOKitPersonalities']['NVAccel']['IOGLBundleName'], 'NVKZinkGLDriver')
        self.assertEqual(json.loads((self.out/'PACKAGE-IDENTITY.json').read_text())['native_cgl_macos_majors'], ['15','26'])
        self.assertFalse((self.out/'Test-Zink.command').exists())
        self.assertFalse((self.out/pkg.RUNTIME/'zink-tests').exists())
        self.assertTrue((self.out / 'Library/GPUBundles/NVKZinkGLDriver.bundle/Contents/MacOS/NVKZinkGLDriver').is_file())
        pkg.base_tools.verify_manifest(self.out)

    def test_native_stage_rejects_diagnostic_bundle(self):
        self.native_bundle(diagnostic=True)
        with self.assertRaisesRegex(ValueError, 'diagnostic GLD'):
            self.stage(native_gld=True)
        self.assertFalse(self.out.exists())
        self.unchanged()

    def test_native_stage_rejects_mismatched_component_provenance(self):
        self.native_bundle(build='25H999')
        with self.assertRaisesRegex(ValueError, 'build provenance differ'):
            self.stage(native_gld=True)
        self.assertFalse(self.out.exists())
        self.unchanged()

    def test_native_stage_accepts_components_generated_on_a_new_build(self):
        (self.zink/'cgl-os-build.txt').write_text('25G242\n')
        record(self.zink,'cgl')
        self.native_bundle(build='25G242')
        self.stage(native_gld=True)
        self.unchanged()

    def test_installer_command_passes_payload_flag_and_preserves_spaces(self):
        # Execute the actual wrapper with a harmless installer and simulated
        # root identity. This never invokes sudo or modifies system paths.
        folder = self.root / 'installer with spaces'
        folder.mkdir()
        self.put(folder / 'Install.command', (ROOT / 'package/Install.command').read_bytes())
        self.put(folder / 'install.sh', b'printf "%s\\n" "$@"\n')
        identity = self.root / 'id'
        self.put(identity, b'#!/bin/sh\nprintf "0\\n"\n')
        identity.chmod(0o755)
        result = subprocess.run(['/bin/bash', str(folder / 'Install.command')],
                                env=dict(os.environ, PATH=str(self.root) + ':/usr/bin:/bin'),
                                check=True, capture_output=True, text=True)
        self.assertEqual(result.stdout.splitlines(), ['--payload', str(folder)])

    def test_private_renderer_pairing_is_checked(self):
        data = json.loads((self.zink/'NATIVE-BUILD.json').read_text())
        data['renderer_original_sha256'] = '0'*64
        (self.zink/'NATIVE-BUILD.json').write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'does not match the verified driver'):
            self.stage()
        self.assertFalse(self.out.exists())

    def test_modified_component_is_rejected(self):
        (self.zink/'libnvk_cgl.dylib').write_bytes(b'modified after building')
        with self.assertRaisesRegex(ValueError,'component hash mismatch'):
            self.stage()
        self.assertFalse(self.out.exists())

    def test_existing_minecraft_and_wine_payloads_survive(self):
        for name in ['minecraft','wine']:
            self.put(self.base/pkg.BASE_RUNTIME/name/'keep',name.encode())
        pkg.base_tools.write_manifest(self.base)
        self.original = self.snapshot(self.base)
        self.stage(); self.unchanged()
        for name in ['minecraft','wine']:
            self.assertEqual((self.out/pkg.BASE_RUNTIME/name/'keep').read_bytes(),name.encode())

    def test_framework_directory_links_survive_and_metadata_is_excluded(self):
        framework = self.base/pkg.BASE_RUNTIME/'fixture.framework'
        self.put(framework/'Versions/A/Resources/keep',b'resource')
        (framework/'Versions/Current').symlink_to('A',target_is_directory=True)
        (framework/'Resources').symlink_to('Versions/Current/Resources',target_is_directory=True)
        self.put(self.base/pkg.BASE_RUNTIME/'._metadata',b'appledouble')
        self.put(self.base/pkg.BASE_RUNTIME/'.DS_Store',b'finder')
        pkg.base_tools.write_manifest(self.base)
        self.original = self.snapshot(self.base)
        self.stage(); self.unchanged()
        copied = self.out/pkg.BASE_RUNTIME/'fixture.framework'
        self.assertTrue((copied/'Resources').is_symlink())
        self.assertEqual((copied/'Resources/keep').read_bytes(),b'resource')
        self.assertFalse(list(self.out.rglob('._*')))
        self.assertFalse(list(self.out.rglob('.DS_Store')))

    def test_arbitrary_component_overlay_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'Stage matched driver components'):
            pkg.stage(self.base,self.zink,self.out,components=self.root)
        self.assertFalse(self.out.exists())

    def test_archive_uses_pkgroot_and_normalized_ownership(self):
        self.native_bundle()
        archive = self.root/'local-test.tar.gz'
        argv = ['package_zink.py','--base',str(self.base),'--zink',str(self.zink),
                '--stage',str(self.out),'--archive',str(archive)]
        with patch.object(sys,'argv',argv),patch.object(pkg,'run',side_effect=self.fake_run):
            pkg.main()
        with tarfile.open(archive) as stream:
            for info in stream.getmembers():
                self.assertTrue(info.name == 'pkgroot' or info.name.startswith('pkgroot/'))
                self.assertEqual((info.uid,info.gid,info.uname,info.gname),(0,0,'root','root'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
