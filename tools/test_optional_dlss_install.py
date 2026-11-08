#!/usr/bin/env python3
"""Exercise the installer's DLSS choice without installing or downloading anything."""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'build'))
import stage_package

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

# Run the real guarded DLSS step with the packager's manifest, without the
# unrelated driver installation. The helper records a call instead of fetching.
dlss_step=s[s.index('# Native MetalFX DLSS is independent'):s.index('\necho; echo "Done. Reboot now:')]

class DlssPayloadTests(unittest.TestCase):
    required=('dlss_fetch.sh','dlss/dlss_cache','dlss/nm_unzstd','dlss/assertfail_trap.ptx')

    def invoke(self,*args,answer=None,manifest_format='packager',missing=None,spoof=False):
        with tempfile.TemporaryDirectory(prefix='dlss-payload-') as temp:
            folder=Path(temp).resolve();payload=folder/'pkgroot';payload.mkdir()
            for name in self.required:
                file=payload/name;file.parent.mkdir(exist_ok=True)
                file.write_text("#!/bin/bash\nprintf '%s\\n' \"$@\" > \"$DLSS_TEST_CALLED\"\n"
                                if name=='dlss_fetch.sh' else 'fixture helper\n')
            stage_package.write_manifest(payload)
            manifest=payload/'SHA256SUMS'
            lines=manifest.read_text().splitlines(keepends=True)
            if missing:
                lines=[line for line in lines if line[66:].strip()!='./'+missing]
                if spoof:
                    # A valid checksum for a different file whose name ends in
                    # the required path must not count as helper coverage.
                    alternate=payload/('unrelated '+missing)
                    alternate.parent.mkdir(parents=True,exist_ok=True)
                    alternate.write_bytes((payload/missing).read_bytes())
                    lines.append(stage_package.digest(alternate)+'  ./unrelated '+missing+'\n')
            text=''.join(lines)
            if manifest_format=='bare':text=text.replace('  ./','  ')
            elif manifest_format=='binary':text=text.replace('  ./',' *./')
            else:self.assertEqual(manifest_format,'packager')
            manifest.write_text(text)
            script=folder/'runner.sh'
            script.write_text('#!/bin/bash\nset -u\nHERE=\n'+choices+'''\n
step() { printf 'STEP %s\\n' "$*"; }
(cd "$HERE" && /usr/bin/shasum -a 256 --strict -c SHA256SUMS --quiet) || exit 9
'''+dlss_step)
            env=dict(os.environ);env.pop('NULLMOTH_DLSS',None)
            marker=folder/'fetch-called';env['DLSS_TEST_CALLED']=str(marker)
            command=['/bin/bash',str(script),'--payload',str(payload),*args]
            master=slave=None
            try:
                if answer is not None:
                    master,slave=os.openpty();os.write(master,(answer+'\n').encode())
                result=subprocess.run(command,env=env,stdin=slave if slave is not None else subprocess.DEVNULL,
                                      capture_output=True,text=True,timeout=10)
            finally:
                if master is not None:os.close(master)
                if slave is not None:os.close(slave)
            return result,marker.read_text() if marker.exists() else None

    def test_packaged_manifest_reaches_prompt_and_fetch(self):
        result,called=self.invoke(answer='y')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('Install/update native MetalFX DLSS? [y/N]',result.stdout)
        self.assertEqual(called,'--yes\n')
        self.assertNotIn('does not cover',result.stdout)

    def test_declining_keeps_default_no(self):
        result,called=self.invoke(answer='')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('[y/N]',result.stdout)
        self.assertIn('existing DLSS files are kept',result.stdout)
        self.assertIsNone(called)

    def test_noninteractive_defaults_to_no(self):
        result,called=self.invoke()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertNotIn('[y/N]',result.stdout)
        self.assertIn('existing DLSS files are kept',result.stdout)
        self.assertIsNone(called)

    def test_explicit_yes_and_no_with_packaged_manifest(self):
        for choice in ('yes','no'):
            with self.subTest(choice=choice):
                result,called=self.invoke('--dlss='+choice)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertEqual(called,'--yes\n' if choice=='yes' else None)
                self.assertNotIn('[y/N]',result.stdout)

    def test_standard_checksum_formats(self):
        for fmt in ('bare','binary'):
            with self.subTest(format=fmt):
                result,called=self.invoke(answer='y',manifest_format=fmt)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertIn('[y/N]',result.stdout)
                self.assertEqual(called,'--yes\n')

    def test_missing_helper_coverage_skips_without_prompt(self):
        for name in self.required:
            with self.subTest(helper=name):
                result,called=self.invoke('--dlss=yes',missing=name)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertIn('does not cover the DLSS step',result.stdout)
                self.assertNotIn('[y/N]',result.stdout)
                self.assertIsNone(called)

    def test_similarly_named_files_do_not_cover_helpers(self):
        for name in self.required:
            with self.subTest(helper=name):
                result,called=self.invoke('--dlss=yes',missing=name,spoof=True)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertIn('does not cover the DLSS step',result.stdout)
                self.assertIsNone(called)

if __name__=='__main__':unittest.main()
