#!/usr/bin/env python3
"""Exercise the installer's DLSS choice without installing or downloading anything."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
s=(ROOT/'package/install.sh').read_text()
choices=s[s.index('INTEL_PANEL_CHOICE=ask'):s.index('\nKEXTS=')]
function=re.search(r'^choose_native_dlss\(\) \{\n.*?^\}',s,re.M|re.S).group(0)

class DlssChoiceTests(unittest.TestCase):
    def invoke(self,*args,answer=None,env_opt=None):
        with tempfile.TemporaryDirectory(prefix='dlss-choice-') as temp:
            script=Path(temp)/'choice.sh'
            script.write_text('#!/bin/bash\nset -u\n'+choices+'\n'+function+'''\n
if choose_native_dlss; then echo SELECTED=yes; else echo SELECTED=no; fi
printf 'INTEL=%s AUDIO=%s\\n' "$INTEL_PANEL_CHOICE" "$AUDIO_CHOICE"
''')
            env=dict(os.environ);env.pop('NULLMOTH_DLSS',None)
            if env_opt is not None:env['NULLMOTH_DLSS']=env_opt
            if answer is None:
                return subprocess.run(['/bin/bash',str(script),*args],env=env,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=5)
            master,slave=os.openpty()
            try:
                os.write(master,(answer+'\n').encode())
                return subprocess.run(['/bin/bash',str(script),*args],env=env,stdin=slave,capture_output=True,text=True,timeout=5)
            finally:
                os.close(master);os.close(slave)

    def test_default_is_no_without_terminal(self):
        r=self.invoke();self.assertEqual(r.returncode,0);self.assertIn('SELECTED=no',r.stdout)
        self.assertNotIn('[y/N]',r.stdout)

    def test_terminal_prompt_defaults_to_no(self):
        for answer in ('','n','N','no','NO','invalid'):
            with self.subTest(answer=answer):
                r=self.invoke(answer=answer);self.assertEqual(r.returncode,0)
                self.assertIn('[y/N]',r.stdout);self.assertIn('SELECTED=no',r.stdout)

    def test_terminal_yes(self):
        for answer in ('y','Y','yes','YES'):
            with self.subTest(answer=answer):
                self.assertIn('SELECTED=yes',self.invoke(answer=answer).stdout)

    def test_explicit_options_and_upstream_environment(self):
        for args,env,want in [(('--dlss=yes',),None,'yes'),(('--dlss=no',),None,'no'),
                              ((), '1','yes'),((), '0','no'),((), 'true','no'),
                              (('--dlss=no',),'1','no'),(('--dlss=yes',),'0','yes')]:
            with self.subTest(args=args,env=env):
                r=self.invoke(*args,env_opt=env);self.assertEqual(r.returncode,0)
                self.assertIn('SELECTED='+want,r.stdout);self.assertNotIn('[y/N]',r.stdout)

    def test_component_choices_stay_independent(self):
        r=self.invoke('--dlss=no','--intel-panel=yes','--audio=yes')
        self.assertIn('SELECTED=no',r.stdout);self.assertIn('INTEL=yes AUDIO=yes',r.stdout)
        r=self.invoke('--dlss=yes','--intel-panel=no','--audio=no')
        self.assertIn('SELECTED=yes',r.stdout);self.assertIn('INTEL=no AUDIO=no',r.stdout)

    def test_invalid_argument_is_rejected(self):
        self.assertEqual(self.invoke('--dlss=maybe').returncode,2)

    def test_prompt_explains_native_and_wine_paths(self):
        text=self.invoke(answer='').stdout
        for detail in ('native Mac games','MetalFX','750 MB','GPU kernels','2.5 GB','Wine/CrossOver','Wine/NVK','nvngx_dlss.dll'):
            self.assertIn(detail.lower(),text.lower())

    def test_installer_runs_choice_only_in_covered_private_payload(self):
        step=s.index('if choose_native_dlss; then')
        self.assertGreater(step,s.index('SHA256SUMS does not cover the DLSS step'))
        self.assertIn('/bin/bash "$HERE/dlss_fetch.sh" --yes',s[step:])
        self.assertIn('existing DLSS files are kept',s[step:])

if __name__=='__main__':unittest.main()
