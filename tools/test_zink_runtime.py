#!/usr/bin/env python3
"""Run bounded Zink GPU diagnostics without installing a GLD or modifying the OS."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--runtime',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--front-buffer',action='store_true',
                    help='Also capture the probe windows; requires existing screen-recording permission')
    args = ap.parse_args()
    runtime = args.runtime.resolve(strict=True); out = args.output.resolve()
    out.mkdir(parents=True,exist_ok=False)
    shim = out/'gld-test-lookup.dylib'
    subprocess.run(['clang','-arch','x86_64','-mmacosx-version-min=15.5','-dynamiclib',
        '-Wall','-Wextra','-Werror','-Wl,-undefined,dynamic_lookup','-Wl,-rpath,'+str(runtime),'-framework','CoreFoundation',
        str(Path(__file__).with_name('zink_gld_test_lookup.c')),'-o',str(shim)],check=True)
    cases = [
        ('mesa-synchronization','zink_sync_probe',[],False,None),
        ('standalone','zink_probe',[],False,'gpu'),
        ('private-icd-selection','zink_probe',[],False,'invalid-icd'),
        ('core','cgl_zink_probe',[],False,'gpu'),
        ('core32','cgl_zink_probe',['--core32'],False,'gpu'),
        ('legacy','cgl_zink_legacy_probe',[],False,'gpu'),
        ('gld-core','cgl_zink_probe',['--system'],True,'gpu'),
        ('gld-core32','cgl_zink_probe',['--system','--core32'],True,'gpu'),
        ('gld-legacy','cgl_zink_legacy_probe',['--system'],True,'gpu'),
        ('gld-apple-iosurface','cgl_iosurface_probe',['--require-apple'],True,'disabled'),
        ('gld-native-iosurface','cgl_iosurface_probe',[],True,None),
        ('gld-iosurface-interop','cgl_iosurface_interop_probe',[],True,None),
        ('gld-iosurface-interop-core32','cgl_iosurface_interop_probe',['--core32'],True,None),
        ('obs-default','obs_zink_policy_probe',['enabled'],True,'obs-default'),
        ('obs-preference-on','obs_zink_policy_probe',['enabled'],True,'obs-preference-on'),
        ('obs-preference-off','obs_zink_policy_probe',['disabled'],True,'obs-preference-off'),
        ('obs-environment-off','obs_zink_policy_probe',['disabled'],True,'obs-environment-off'),
        ('obs-environment-on','obs_zink_policy_probe',['enabled'],True,'obs-environment-on'),
        ('obs-invalid-environment','obs_zink_policy_probe',['enabled'],True,'obs-invalid-environment'),
        ('gld-window','cgl_zink_window_probe',['--system','--appkit-present'],True,'gpu'),
        ('gld-rgbx-worker','cgl_zink_window_probe',['--system','--legacy','--no-alpha','--appkit-present','--render-thread'],True,'gpu'),
        ('native-default','zink_native_present_probe',['default'],False,None),
        ('native-transparent','zink_native_present_probe',['transparent'],False,'native'),
        ('native-worker','zink_native_present_probe',['worker'],False,'native'),
        ('native-reject','zink_native_present_probe',['reject'],False,'native'),
        ('native-uncertain','zink_native_present_probe',['uncertain'],False,'native'),
        ('native-gld-auto','zink_native_present_probe',['automatic','default'],True,None),
        ('native-large-resize','zink_native_present_probe',['large-resize','default'],False,None),
        ('native-gld-large-resize','zink_native_present_probe',['automatic','large-resize','default'],True,None),
        ('native-no-vram','zink_native_present_probe',['automatic','large-resize','default'],True,None),
        ('native-odd-resize','zink_native_present_probe',['odd-resize','default'],False,None),
        ('native-gld-odd-resize','zink_native_present_probe',['automatic','odd-resize','default'],True,None),
        ('native-no-vram-odd-resize','zink_native_present_probe',['automatic','odd-resize','default'],True,None),
        ('native-odd-transparent','zink_native_present_probe',['automatic','odd-resize','transparent','default'],True,None),
        ('native-no-vram-reject','zink_native_present_probe',['reject'],False,None),
        ('native-no-vram-uncertain','zink_native_present_probe',['uncertain'],False,None),
        ('native-gld-worker','zink_native_present_probe',['automatic','worker','transparent'],True,'native'),
        ('native-multiple-windows','zink_native_present_probe',['automatic','windows'],True,'native'),
        ('native-swap-interval','zink_native_present_probe',['swap-interval'],False,'native'),
        ('native-gld-swap-interval','zink_native_present_probe',['automatic','worker','windows','swap-interval'],True,None),
        ('native-obs-policy-isolation','zink_native_present_probe',['automatic','default'],True,None),
        ('native-optout-zero','zink_native_present_probe',['automatic','default'],True,None),
        ('native-updated-os-build','zink_native_present_probe',['automatic','default'],True,None),
    ]
    if args.front_buffer:
        for mode in (None, 'gpu'):
            for operation in ('flush','finish','flush-apple','finish-apple','double','worker',
                              'noncurrent','other','apple-other','late-view'):
                cases.append(('front-'+(mode or 'auto')+'-'+operation,
                              'cgl_zink_front_probe',[operation],True,mode))
        cases.append(('front-auto-early-swap','cgl_zink_front_probe',['early-swap'],True,None))
    results = []
    for name,binary,options,automatic,mode in cases:
        env = dict(os.environ,NVKZINK_REQUIRE_GPU='1')
        env.pop('DYLD_INSERT_LIBRARIES',None);env.pop('NVKZINK_PRESENT',None)
        env.pop('ZINK_TEST_OS_BUILD',None)
        env.pop('ZINK_TEST_NO_FAMILY_VRAM',None)
        if name.startswith('native-no-vram'): env['ZINK_TEST_NO_FAMILY_VRAM']='1'
        env.pop('ZINK_TEST_GLD_BUNDLE',None);env.pop('ZINK_TEST_APPLE_GLD',None)
        env.pop('NVKZINK_DISABLE',None)
        env.pop('NVKZINK_OBS_COMPAT',None)
        env.pop('ZINK_TEST_OBS_PREFERENCE',None)
        if name == 'native-updated-os-build': env['ZINK_TEST_OS_BUILD']='25G242'
        if name == 'native-obs-policy-isolation': env['NVKZINK_OBS_COMPAT']='1'
        if name == 'native-optout-zero': env['NVKZINK_DISABLE']='0'
        if mode and mode.startswith('obs-'):
            env['ZINK_TEST_OBS_PREFERENCE']='absent'
            if mode=='obs-preference-on': env['ZINK_TEST_OBS_PREFERENCE']='on'
            if mode=='obs-preference-off': env['ZINK_TEST_OBS_PREFERENCE']='off'
            if mode=='obs-environment-off': env.update(ZINK_TEST_OBS_PREFERENCE='on',NVKZINK_OBS_COMPAT='0')
            if mode=='obs-environment-on': env.update(ZINK_TEST_OBS_PREFERENCE='off',NVKZINK_OBS_COMPAT='1')
            if mode=='obs-invalid-environment': env['NVKZINK_OBS_COMPAT']='invalid'
        elif mode=='disabled': env['NVKZINK_DISABLE']='1'
        elif mode and mode!='invalid-icd': env['NVKZINK_PRESENT']=mode
        if automatic:
            env.update(ZINK_TEST_GLD_BUNDLE=str((runtime/'NVKZinkGLDriver.bundle').resolve(strict=True)),DYLD_INSERT_LIBRARIES=str(shim))
        else:
            env.update(ZINK_TEST_APPLE_GLD='1',DYLD_INSERT_LIBRARIES=str(shim))
        library = runtime/('libnvk_zink.dylib' if binary=='zink_probe' else 'libnvk_cgl.dylib')
        icd = '/nonexistent/zink-test-icd.json' if mode=='invalid-icd' else str(runtime/'nvk_icd.json')
        argv = [str(runtime/binary),str(library),icd]+options
        if binary == 'zink_sync_probe': argv = [str(runtime/binary)]
        if binary == 'cgl_zink_front_probe': argv = [str(runtime/binary)]+options
        started = time.monotonic()
        with (out/(name+'.log')).open('w') as log:
            process = subprocess.Popen(argv,env=env,stdout=log,stderr=subprocess.STDOUT)
            try: code = process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                subprocess.run(['sample',str(process.pid),'1','1','-file',str(out/(name+'-sample.txt'))],
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5)
                try: code = process.wait(timeout=50)
                except subprocess.TimeoutExpired: process.kill();process.wait();code = 124
        results.append({'case':name,'returncode':code,'seconds':round(time.monotonic()-started,3)})
        print(name,code,flush=True)
    (out/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    raise SystemExit(any(case['returncode'] for case in results))


if __name__ == '__main__':
    main()
