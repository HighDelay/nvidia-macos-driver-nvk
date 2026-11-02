#!/usr/bin/env python3
"""Exercise the complete uninstaller with permanent default and external kexts."""
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import unittest

import primary_install_fixture as fixture

ROOT = Path(__file__).resolve().parents[1]
KERNEL_MOCK = r'''
import json,os,sys,plistlib
from pathlib import Path
r=Path(os.environ['FIXTURE_ROOT']);a=sys.argv[1:]
with (r/'calls.jsonl').open('a') as f:f.write(json.dumps(['kmutil']+a)+'\n')
p=Path(a[a.index('-A')+1])
if a[0]=='create':
 entries=[]
 for i,arg in enumerate(a):
  if arg=='-p':
   path=Path(a[i+1]);d=plistlib.loads((path/'Contents/Info.plist').read_bytes())
   if d['CFBundleIdentifier']!=os.environ.get('FAKE_REMOVE_DROP'):
    entries.append([d['CFBundleIdentifier'],d['CFBundleVersion'],str(path)])
 p.write_text(json.dumps({'candidate':True,'entries':entries}))
elif a[0]=='inspect':
 if not p.exists():sys.exit(2)
 data=json.loads(p.read_text())
 for row in data['entries']:
  if data.get('candidate') and p==r/'Library/KernelCollections/AuxiliaryKernelExtensions.kc' and row[0]==os.environ.get('FAKE_REMOVE_LIVE_DROP'):continue
  print('\t'.join(row))
'''


class UninstallPaths(fixture.Install):
    def setUp(self):
        super().setUp()
        self.keep = self.bundle('Library/Extensions/HighPointRR.kext', 'com.highpoint-tech.kext.HighPointRR', '4.22.1')
        self.bundle('Library/Extensions/NVMeFix.kext', 'org.test.NVMeFix', '1')
        self.kc = self.root/'Library/KernelCollections/AuxiliaryKernelExtensions.kc'
        self.entries = [['com.highpoint-tech.kext.HighPointRR','4.22.1',
                         '/private/var/tmp/nullmoth-remove.deleted/repo/HighPointRR.kext']]
        self.cache()
        tool=self.bin/'kmutil';tool.write_text('#!'+sys.executable+'\n'+KERNEL_MOCK)
        tool=self.bin/'csrutil';tool.write_text('#!/bin/sh\necho "System Integrity Protection status: disabled."\n');tool.chmod(0o755)
        source=(ROOT/'package/uninstall.sh').read_text().replace('PATH=/usr/bin:/bin:/usr/sbin:/sbin; export PATH',
            'PATH='+str(self.bin)+':/usr/bin:/bin:/usr/sbin:/sbin; export PATH')
        for prefix in ['/Library/','/System/','/Users/Shared/']:
            source=source.replace(prefix,str(self.root)+prefix)
        self.uninstall=self.root/'uninstall.sh';self.uninstall.write_text(source)

    def bundle(self, relative, identifier, version):
        p=self.root/relative;(p/'Contents').mkdir(parents=True,exist_ok=True)
        (p/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':identifier,'CFBundleVersion':version}))
        return p

    def cache(self):
        self.kc.write_text(json.dumps({'entries':self.entries}));self.old[self.kc]=self.kc.read_bytes()

    def external(self):
        p=self.bundle('external drivers/External.kext','org.test.External','1')
        self.entries.append(['org.test.External','1',str(p)]);self.cache()
        return p

    def run_remove(self, **faults):
        return subprocess.run(['/bin/bash',str(self.uninstall)],env={**self.env,**faults},capture_output=True,text=True)

    def test_removal_uses_permanent_paths_after_staging_cleanup(self):
        result=self.run_remove();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        for row in json.loads(self.kc.read_text())['entries']:
            self.assertTrue(Path(row[2]).is_dir());self.assertFalse('scratch-' in row[2])
        self.assertTrue((self.keep/'Contents/Info.plist').is_file())
        self.assertFalse((self.root/'Library/Extensions/NVRM.kext').exists())
        self.assertFalse(list(self.root.glob('scratch-*')))
        for call in map(json.loads,(self.root/'calls.jsonl').read_text().splitlines()):
            if call[:2]==['kmutil','create']:
                self.assertIn(str(self.keep.resolve()),call)
                self.assertFalse(list(Path(call[call.index('--repository')+1]).glob('*.kext')))

    def test_external_extension_is_retained_in_removal_collection(self):
        external=self.external();result=self.run_remove()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        rows=json.loads(self.kc.read_text())['entries']
        self.assertIn(['org.test.External','1',str(external.resolve())],rows)
        self.assertTrue((external/'Contents/Info.plist').is_file())

    def test_dropped_external_stops_before_removal(self):
        self.external();result=self.run_remove(FAKE_REMOVE_DROP='org.test.External')
        self.assert_restored(result);self.assertIn('dropped org.test.External',result.stdout+result.stderr)

    def test_missing_external_path_stops_before_removal(self):
        self.external();shutil.rmtree(self.root/'external drivers')
        result=self.run_remove();self.assert_restored(result)
        self.assertIn('cannot preserve',result.stdout+result.stderr)

    def test_failed_published_preservation_rolls_back(self):
        self.external();result=self.run_remove(FAKE_REMOVE_LIVE_DROP='org.test.External')
        self.assert_restored(result);self.assertIn('published removal collection dropped',result.stdout+result.stderr)

    def test_check_only_preserves_installed_driver_and_cache(self):
        self.external();result=self.run_remove(CHECK='1')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('CHECK PASS',result.stdout)
        for path,data in self.old.items():self.assertEqual(path.read_bytes(),data)

    def panel(self):
        panel=self.bundle('Library/Extensions/NMIntelFB.kext','com.nullmoth.NMIntelFB','0.1')
        binary=panel/'Contents/MacOS/NMIntelFB';binary.parent.mkdir();binary.write_bytes(b'old-panel')
        for path in panel.rglob('*'):
            if path.is_file():self.old[path]=path.read_bytes()
        self.entries.append(['com.nullmoth.NMIntelFB','0.1',str(panel)]);self.cache()
        return panel

    def test_optional_intel_panel_is_removed_with_the_driver(self):
        panel=self.panel();result=self.run_remove()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertFalse(panel.exists())
        self.assertNotIn('com.nullmoth.NMIntelFB',self.kc.read_text())

    def test_failed_removal_restores_optional_panel(self):
        self.panel();self.external();result=self.run_remove(FAKE_REMOVE_LIVE_DROP='org.test.External')
        self.assert_restored(result)

    def test_check_only_preserves_optional_panel(self):
        self.panel();result=self.run_remove(CHECK='1')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        for path,data in self.old.items():self.assertEqual(path.read_bytes(),data)

    def test_external_only_collection_is_rebuilt_instead_of_removed(self):
        external=self.external();shutil.rmtree(self.keep);shutil.rmtree(self.root/'Library/Extensions/NVMeFix.kext')
        self.entries=self.entries[1:];self.cache()
        result=self.run_remove();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads(self.kc.read_text())['entries'],[['org.test.External','1',str(external.resolve())]])


if __name__=='__main__':unittest.main(verbosity=2)
