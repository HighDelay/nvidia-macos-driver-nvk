#!/usr/bin/env python3
"""Run the real prompt/private-payload step without touching EFI or the driver."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'build'))
import stage_package

source=(ROOT/'package/install.sh').read_text()
options=source[source.index('INTEL_PANEL_CHOICE=ask'):source.index('\nKEXTS=')]
step=source[source.index('# Optional WebKit helper lives'):source.index('\necho; echo "Done. Reboot now:')]


class WebkitInstallerTests(unittest.TestCase):
    def invoke(self,*args,answer=None,missing=None):
        with tempfile.TemporaryDirectory(prefix='webkit prompt fixture ') as temporary:
            root=Path(temporary).resolve();payload=root/'private package';component=payload/'Optional/NVWebKitFixup'
            files=['efi.py','Kernel-Add.plist','NVWebKitFixup.kext/Contents/Info.plist',
                   'NVWebKitFixup.kext/Contents/MacOS/NVWebKitFixup','NVWebKitFixup.kext/Contents/_CodeSignature/CodeResources']
            for name in files:
                path=component/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture')
            (component/'efi.py').write_text("import os,json,sys\nfrom pathlib import Path\nPath(os.environ['EFI_CALLED_LOG']).write_text(json.dumps(sys.argv[1:]))\n")
            stage_package.write_manifest(payload)
            manifest=payload/'SHA256SUMS'
            if missing:
                manifest.write_text(''.join(line+'\n' for line in manifest.read_text().splitlines()
                                           if not line.endswith('/'+missing)))
            script=root/'runner.sh'
            script.write_text('#!/bin/bash\nset -u\nHERE=""\n'+options+'''
SOURCE_PACKAGE="/original extracted package"
(cd "$HERE" && shasum -a 256 --strict -c SHA256SUMS --quiet) || exit 8
'''+step)
            marker=root/'called.json';env=dict(os.environ,EFI_CALLED_LOG=str(marker))
            master=slave=None
            try:
                if answer is not None:
                    master,slave=os.openpty();os.write(master,(answer+'\n').encode())
                result=subprocess.run(['/bin/bash',str(script),'--payload',str(payload),*args],env=env,
                                      stdin=slave if slave is not None else subprocess.DEVNULL,
                                      capture_output=True,text=True,timeout=10)
            finally:
                if master is not None:os.close(master)
                if slave is not None:os.close(slave)
            return result,json.loads(marker.read_text()) if marker.exists() else None

    def test_noninteractive_default_prints_original_package_and_entry(self):
        result,called=self.invoke();self.assertEqual(result.returncode,0,result.stderr);self.assertIsNone(called)
        self.assertIn('/original extracted package/Optional/NVWebKitFixup/NVWebKitFixup.kext',result.stdout)
        self.assertIn('EFI/OC/Kexts/',result.stdout)
        self.assertIn('MinKernel=24.0.0; MaxKernel=25.99.99',result.stdout)
        self.assertNotIn('private package',result.stdout)

    def test_terminal_default_manual_and_skip(self):
        for answer in ('','m','invalid'):
            result,called=self.invoke(answer=answer);self.assertEqual(result.returncode,0,result.stderr)
            self.assertIsNone(called);self.assertIn('Manual Safari WebGL helper setup',result.stdout)
        result,called=self.invoke(answer='s');self.assertIsNone(called)
        self.assertIn('helper skipped',result.stdout);self.assertNotIn('Manual Safari',result.stdout)

    def test_automatic_detection_and_explicit_target_preserve_spaces(self):
        for choice in (['--webkit-fixup=auto'],[]):
            result,called=self.invoke(*choice,'--efi-target','/Volumes/My EFI/EFI/OC',answer='a' if not choice else None)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(called,['install','--target','/Volumes/My EFI/EFI/OC'])
        result,called=self.invoke('--webkit-fixup=auto')
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(called,['install'])
        result,called=self.invoke(answer='a')
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(called,['install'])

    def test_uncovered_helper_cannot_execute(self):
        for name in ('efi.py','NVWebKitFixup.kext/Contents/MacOS/NVWebKitFixup','NVWebKitFixup.kext/Contents/_CodeSignature/CodeResources'):
            result,called=self.invoke('--webkit-fixup=auto','--efi-target','disk7s1',missing=name)
            self.assertEqual(result.returncode,0,result.stderr);self.assertIsNone(called)
            self.assertIn('checksums do not cover',result.stdout)

    def test_explicit_skip_and_invalid_options(self):
        result,called=self.invoke('--webkit-fixup=skip');self.assertIsNone(called);self.assertEqual(result.returncode,0)
        result,called=self.invoke('--webkit-fixup=maybe');self.assertIsNone(called);self.assertEqual(result.returncode,2)


if __name__=='__main__':unittest.main()
