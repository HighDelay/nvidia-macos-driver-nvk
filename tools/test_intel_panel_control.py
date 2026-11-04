#!/usr/bin/env python3
"""Test per-boot panel controls with private sysctl/ioreg substitutes."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
STUB = r'''import json,os,sys
from pathlib import Path
p=Path(os.environ['PANEL_STATE']);s=json.loads(p.read_text());a=sys.argv[1:]
if Path(sys.argv[0]).name=='ioreg':
 if s.get('panel',True):
  print('+-o NMIntelFB <class NMIntelFB, id 1>')
  if s.get('matched',True):print('"NMIntelFBMatchedFrameABI" = 2')
 sys.exit(0)
if a[0]=='-n':
 if a[1] not in s['knobs']:sys.exit(1)
 print(s['knobs'][a[1]]);sys.exit(0)
key,value=a[1].split('=');value=int(value);s['writes'].append([key,value])
if key=='debug.nmintelfb_ram' and value==2:
 if s.get('refuse') or s.get('busy',0):
  s['busy']=max(0,s.get('busy',0)-1);p.write_text(json.dumps(s));sys.exit(1)
s['knobs'][key]=1 if key=='debug.nvaccelfb' and value==3 else value
p.write_text(json.dumps(s))
'''

class PanelControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='intel-panel-control-')
        self.root = Path(self.temp.name)
        for name in ('sysctl', 'ioreg'):
            path = self.root/name
            path.write_text('#!'+sys.executable+'\n'+STUB)
            path.chmod(0o755)
        source = (ROOT/'tools/intel_panel_control.sh').read_text()
        source = source.replace('SYSCTL=/usr/sbin/sysctl', 'SYSCTL='+str(self.root/'sysctl'))
        source = source.replace('IOREG=/usr/sbin/ioreg', 'IOREG='+str(self.root/'ioreg'))
        source = source.replace('"$(id -u)"', '"0"').replace('/bin/sleep ', '/usr/bin/true ')
        self.script = self.root/'control.sh'; self.script.write_text(source)
        self.state = self.root/'state.json'
        keys = ['nvaccel_igpu','nvaccelfb','nmintelfb_agdc','nvaccel_iop','nvaccel_iop_direct',
                'nvaccel_iop_memcpy','nvaccel_iop_blank_hold','nmintelfb_ram','nmintelfb_flips',
                'nmintelfb_flip_late','nvaccel_iop_ok','nvaccel_iop_ram_frames','nvaccel_iop_quarantined']
        self.initial = {'knobs':{'debug.'+key:0 for key in keys},'writes':[]}

    def tearDown(self): self.temp.cleanup()
    def run_control(self, action, **changes):
        data = dict(self.initial, **changes); self.state.write_text(json.dumps(data))
        result = subprocess.run(['/bin/bash',str(self.script),action], text=True, capture_output=True,
                                env=dict(os.environ,PANEL_STATE=str(self.state)))
        return result, json.loads(self.state.read_text())
    def test_status_is_read_only(self):
        r,s=self.run_control('status'); self.assertEqual(r.returncode,0,r.stderr); self.assertEqual(s['writes'],[])
    def test_no_panel_never_changes_knobs(self):
        r,s=self.run_control('fast',panel=False); self.assertNotEqual(r.returncode,0); self.assertEqual(s['writes'],[])
    def test_enable_reregisters_a_late_panel_and_keeps_fast_off(self):
        self.initial['knobs']['debug.nvaccelfb']=1
        r,s=self.run_control('enable'); self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(s['writes'],[['debug.nvaccel_igpu',1],['debug.nvaccelfb',3],['debug.nmintelfb_agdc',1],['debug.nvaccel_iop',1]])
        self.assertEqual(s['knobs']['debug.nmintelfb_ram'],0)
        self.assertEqual(s['knobs']['debug.nvaccel_iop_direct'],0)
    def test_fast_needs_matched_frames(self):
        r,s=self.run_control('fast',matched=False); self.assertNotEqual(r.returncode,0)
        self.assertNotIn(['debug.nmintelfb_ram',2],s['writes'])
        self.assertEqual(s['knobs']['debug.nvaccel_iop_direct'],0)
    def test_fast_maps_ram_before_enabling_dma(self):
        r,s=self.run_control('fast',busy=2); self.assertEqual(r.returncode,0,r.stderr)
        writes=s['writes']; self.assertLess(writes.index(['debug.nvaccel_iop_direct',0]),writes.index(['debug.nmintelfb_ram',2]))
        self.assertLess(max(i for i,v in enumerate(writes) if v==['debug.nmintelfb_ram',2]),writes.index(['debug.nvaccel_iop_direct',2]))
        self.assertEqual(s['knobs']['debug.nmintelfb_ram'],2)
    def test_refused_ram_does_not_enable_dma(self):
        r,s=self.run_control('fast',refuse=True); self.assertNotEqual(r.returncode,0)
        self.assertEqual(s['knobs']['debug.nmintelfb_ram'],0)
        self.assertNotIn(['debug.nvaccel_iop_direct',2],s['writes'])
    def test_ordinary_stops_dma_before_restoring_firmware_pages(self):
        self.initial['knobs'].update({'debug.nvaccel_iop_direct':2,'debug.nmintelfb_ram':2})
        r,s=self.run_control('ordinary'); self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(s['writes'][0],['debug.nvaccel_iop_direct',0])
        self.assertEqual(s['writes'][1],['debug.nmintelfb_ram',0])
    def test_unknown_command_is_read_only(self):
        r,s=self.run_control('unsupported'); self.assertEqual(r.returncode,2); self.assertEqual(s['writes'],[])

if __name__ == '__main__': unittest.main(verbosity=2)
