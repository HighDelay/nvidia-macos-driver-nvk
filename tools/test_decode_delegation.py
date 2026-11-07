#!/usr/bin/env python3
"""Exercise decoder policy inside a real sandbox, with simulated accelerator access.

Native sandboxed clients delegate. Chromium GPU helpers and Gecko RDD/GPU
children register locally only when both RM bridge clients are available,
independent of bundle or helper name.
No GPU connections or decoder sessions are opened by this test.
"""
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import tempfile

root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parents[1])
src = (root / 'plugin/NVMTLDevice.m').read_text()
fails = 0


def check(name, ok, detail=''):
    global fails
    print(('ok   ' if ok else 'FAIL ') + name + (f' ({detail})' if detail else ''))
    fails += 0 if ok else 1


def function(signature):
    i = src.index(signature)
    line = src[i:src.index('\n', i) + 1]
    if line.rstrip().endswith('}'):   # one-line definition
        return line
    return src[i:src.index('\n}\n', i) + 3]


if platform.system() != 'Darwin' or not shutil.which('clang') or not pathlib.Path('/usr/bin/sandbox-exec').exists():
    print('skip: needs macOS with clang and sandbox-exec')
    sys.exit(0)

body = ''.join(function(s) for s in ('static bool nvmtl_is_sandboxed(void)',
                                   'static bool nvmtl_is_decode_service(const char *me)',
                                   'static bool nvmtl_is_chromium_gpu_process(void)',
                                   'static int nvmtl_gecko_decoder_process_type(void)',
                                   'static bool nvmtl_is_gecko_decoder_process(void)',
                                   'static bool nvmtl_can_register_decoder(const char *me)'))
bridge = r'''
typedef uint32_t io_service_t, io_connect_t;
typedef int IOReturn;
#define IO_OBJECT_NULL 0
#define kIOMainPortDefault 0
#define kIOReturnSuccess 0
#define kIOReturnNotFound 1
#define nvlog(...) ((void)0)
static unsigned mach_task_self(void) { return 99; }
static void *IOServiceMatching(const char *name) { return (void *)1; }
static io_service_t IOServiceGetMatchingService(unsigned port, void *match) { return 17; }
static IOReturn IOServiceOpen(io_service_t service, unsigned task, uint32_t type, io_connect_t *out)
{
    if (getenv("NVMTL_TEST_BRIDGE_DENIED")) return 2;
    *out = type == 0x4E560000u ? 101 : 102;
    return 0;
}
static void IOServiceClose(io_connect_t port) {}
static void IOObjectRelease(io_service_t service) {}
'''
harness = ('#include <CoreFoundation/CoreFoundation.h>\n#include <dispatch/dispatch.h>\n#include <dlfcn.h>\n'
           '#include <stdbool.h>\n#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n#include <unistd.h>\n'
           '#include <stdint.h>\n#include <crt_externs.h>\n#include <mach/mach.h>\n#include <mach/mach_vm.h>\n'
           '#undef mach_task_self\n'
           + bridge + body + 'int main(void) { printf("%d\\n", !nvmtl_can_register_decoder(getprogname())); return 0; }\n')
plist = ('<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict><key>CFBundleIdentifier</key>'
         '<string>{}</string></dict></plist>')

with tempfile.TemporaryDirectory() as d:
    d = pathlib.Path(d)
    (d / 'h.c').write_text(harness)
    progs = {}
    for name, ident in (('apple', 'com.apple.Preview'), ('discord', 'com.hnc.Discord'),
                        ('custom-helper', 'org.example.CustomBrowser')):
        (d / f'{name}.plist').write_text(plist.format(ident))
        r = subprocess.run(['clang', '-fblocks', '-o', str(d / name), str(d / 'h.c'), '-framework', 'CoreFoundation',
                            '-Wl,-sectcreate,__TEXT,__info_plist,' + str(d / f'{name}.plist')],
                           capture_output=True, text=True, timeout=120)
        check(f'the {name} probe builds from the plugin source', r.returncode == 0, r.stderr.strip()[-300:])
        progs[name] = d / name

    def run(prog, sandboxed, args=(), bridge_denied=False):
        cmd = ['/usr/bin/sandbox-exec', '-p', '(version 1)(allow default)', str(prog)] if sandboxed else [str(prog)]
        env = os.environ.copy()
        env.pop('NVMTL_TEST_BRIDGE_DENIED', None)
        if bridge_denied:
            env['NVMTL_TEST_BRIDGE_DENIED'] = '1'
        r = subprocess.run([*cmd, *args], env=env, capture_output=True, text=True, timeout=30)
        if r.returncode:
            return f'exit {r.returncode}: {r.stderr.strip()[-300:]}'
        return r.stdout.strip()

    if all(p.exists() for p in progs.values()):
        check('a sandboxed Apple app delegates decode to VTDecoderXPCService', run(progs['apple'], True) == '1')
        check('a sandboxed Discord GPU process with bridge access registers locally',
              run(progs['discord'], True, ('--type=gpu-process',)) == '0')
        check('a sandboxed Discord GPU process denied bridge access delegates',
              run(progs['discord'], True, ('--type=gpu-process',), bridge_denied=True) == '1')
        check('a sandboxed Chromium renderer delegates',
              run(progs['discord'], True, ('--type=renderer',)) == '1')
        check('a native sandboxed non-Apple app delegates even when the bridge is available',
              run(progs['custom-helper'], True) == '1')
        check('a custom Chromium GPU helper registers without a bundle-name allowlist',
              run(progs['custom-helper'], True, ('--type=gpu-process',)) == '0')
        check('a process-type argument after -- does not qualify in a real sandbox',
              run(progs['discord'], True, ('--', '--type=gpu-process')) == '1')
        check('a sandboxed Gecko RDD child with bridge access registers locally',
              run(progs['custom-helper'], True, ('-parentPid', '1', 'rdd')) == '0')
        check('a sandboxed Gecko RDD child denied bridge access delegates',
              run(progs['custom-helper'], True, ('-parentPid', '1', 'rdd'), bridge_denied=True) == '1')
        check('a sandboxed Gecko content child delegates',
              run(progs['custom-helper'], True, ('-parentPid', '1', 'tab')) == '1')
        check('control: an unsandboxed Apple app registers in-process', run(progs['apple'], False) == '0')
        check('control: an unsandboxed Discord registers in-process', run(progs['discord'], False) == '0')
sys.exit(1 if fails else 0)
