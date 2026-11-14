#!/usr/bin/env python3
"""Verify limited-BAR drawable recovery with real GPU copies and forced import refusal.

Builds test-only frontends from the production bridge. Only the original fd
import is refused; host import, GPU work, fences, pixels and SDL resize/restore
use the real renderer. Does not install or alter the input runtime.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = r'''
#include "BRIDGE_SOURCE"
#include <cassert>
static PFN_vkGetInstanceProcAddr real_gipa;
static PFN_vkGetDeviceProcAddr real_gdpa;
static VkResult VKAPI_CALL refuse_fd(VkDevice device,const VkMemoryAllocateInfo *info,
                                    const VkAllocationCallbacks *allocator,VkDeviceMemory *out) {
   for(auto p=(const VkBaseInStructure *)info->pNext;p;p=p->pNext)
      if(p->sType==VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR)
         return VK_ERROR_INVALID_EXTERNAL_HANDLE;
   return ((PFN_vkAllocateMemory)real_gdpa(device,"vkAllocateMemory"))(device,info,allocator,out);
}
static PFN_vkVoidFunction VKAPI_CALL test_gdpa(VkDevice device,const char *name) {
   if(!strcmp(name,"vkAllocateMemory"))return (PFN_vkVoidFunction)refuse_fd;
   return real_gdpa(device,name);
}
static PFN_vkVoidFunction VKAPI_CALL test_gipa(VkInstance instance,const char *name) {
   auto fn=real_gipa(instance,name);
   if(!strcmp(name,"vkGetDeviceProcAddr")) {
      real_gdpa=(PFN_vkGetDeviceProcAddr)fn;return (PFN_vkVoidFunction)test_gdpa;
   }
   return fn;
}
extern "C" void nvkbridgeTestHostPresent() {
   assert(initialize());real_gipa=backend_gipa;backend_gipa=test_gipa;
}
'''


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--sdl', type=Path, required=True)
    parser.add_argument('--headers', type=Path, default=Path.home() / 'Vulkan-Headers/include')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    renderer = (args.runtime / 'lib/libnvk_renderer.dylib').resolve(strict=True)
    sdl = args.sdl.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    results = {'source_files': {name: sha(ROOT / name) for name in (
        'nvk/native-macos/bridge.mm', 'nvk/native-macos/smoke.mm',
        'nvk/native-macos/present_worker.h', 'nvk/native-macos/present_hud.h',
        'plugin/nvmtl_fd_token.h', 'plugin/nvmtl_rebind_import.h',
        'tools/test_native_host_present.py')},
        'renderer_sha256': sha(renderer), 'sdl_sha256': sha(sdl), 'cases': []}

    def run(command, name, env=None):
        with (output / (name + '.log')).open('w') as log:
            process = subprocess.run(list(map(str, command)), cwd=ROOT, env=env,
                                     stdout=log, stderr=subprocess.STDOUT)
        if process.returncode:
            raise RuntimeError(f'{name} failed ({process.returncode}):\n' +
                               (output / (name + '.log')).read_text()[-6000:])

    compile_options = ['xcrun', 'clang++', '-arch', 'x86_64', '-std=c++17',
                       '-mmacosx-version-min=15.5', '-fobjc-arc', '-O2',
                       '-Wall', '-Wextra', '-Wno-missing-field-initializers',
                       '-I' + str(args.headers)]
    smoke = output / 'smoke'
    run([*compile_options, ROOT / 'nvk/native-macos/smoke.mm',
         '-framework', 'Foundation', '-o', smoke], 'build-smoke')
    fixture = output / 'fixture.mm'
    fixture.write_text(FIXTURE.replace('BRIDGE_SOURCE', str(ROOT / 'nvk/native-macos/bridge.mm')))
    for kind in ('native', 'wine'):
        directory = output / kind
        directory.mkdir()
        shutil.copy2(renderer, directory / 'libnvk_renderer.dylib')
        entry = directory / 'test_frontend.dylib'
        run([*compile_options, *(['-DNVKBRIDGE_WINE'] if kind == 'wine' else []),
             '-dynamiclib', fixture, '-framework', 'Foundation', '-framework', 'QuartzCore',
             '-framework', 'Metal', '-framework', 'IOSurface', '-framework', 'IOKit',
             '-Wl,-install_name,@loader_path/test_frontend.dylib', '-o', entry], 'build-' + kind)
        run(['codesign', '--force', '--sign', '-', '--timestamp=none', entry], 'sign-' + kind)
        run(['codesign', '--verify', '--strict', entry], 'verify-' + kind)
        cases = [('default', {})] if kind == 'native' else [
            ('async', {}), ('sync', {'WINE_NVK_ASYNC_PRESENT': '0'}),
            ('no-reuse', {'WINE_NVK_PRESENT_REUSE': '0'})]
        for label, options in cases:
            name = kind + '-' + label
            environment = {key: value for key, value in os.environ.items()
                           if key not in ('WINE_NVK_ASYNC_PRESENT', 'WINE_NVK_PRESENT_REUSE',
                                          'WINE_NVK_HUD', 'NVKBRIDGE_VERIFY_HUD')}
            environment.update(options, NVKBRIDGE_TEST_HOST_PRESENT='1', NVKBRIDGE_VERIFY_PIXELS='1')
            run([smoke, entry, sdl, '3', '90'], name, environment)
            log = (output / (name + '.log')).read_text()
            assert 'PASS native SDL presentation/resize/restore' in log
            stats = re.search(r'STATS copies=90 bytes=\d+ import_failures=(\d+) cpu_readback=0 cpu_upload=0', log)
            assert stats and 0 < int(stats[1]) <= 90
            results['cases'].append({'name': name, 'host_frames': 90, 'forced_refusals': int(stats[1]),
                                     'verified_gpu_pixels': True, 'resize_restore': True,
                                     'entry_sha256': sha(entry), 'log_sha256': sha(output / (name + '.log'))})
            print('PASS', name, '90 host frames with verified pixels and resize/restore;', stats[1], 'recovered imports', flush=True)
    (output / 'RESULTS.json').write_text(json.dumps(results, indent=2) + '\n')


if __name__ == '__main__':
    main()
