#!/usr/bin/env python3
"""Run the native Vulkan/VMA staging and GPU readback probe in private processes."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

repo = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--runtime', type=Path, required=True, help='Native runtime with lib/libnvk_minecraft.dylib')
parser.add_argument('--baseline-runtime', type=Path, help='Optional runtime expected to reproduce a failed map')
parser.add_argument('--lwjgl', type=Path, required=True, help='LWJGL Maven directory, containing lwjgl and lwjgl-vma')
parser.add_argument('--lwjgl-version', default='3.4.3')
parser.add_argument('--java', type=Path, default=Path(shutil.which('java') or 'java'))
parser.add_argument('--javac', type=Path, default=Path(shutil.which('javac') or 'javac'))
parser.add_argument('--blocks', type=int, default=24, help='Number of persistently mapped 16 MiB staging buffers')
parser.add_argument('--expect-system', action='store_true', help='Require staging allocations to use system RAM')
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
if not 1 <= args.blocks <= 64:
    parser.error('--blocks must be 1..64')
args.output = args.output.resolve()
args.output.mkdir(parents=True, exist_ok=False)
names = ['lwjgl', 'lwjgl-vulkan', 'lwjgl-vma', 'lwjgl-natives-macos',
         'lwjgl-vma-natives-macos', 'lwjgl-vulkan-natives-macos']
jars = [args.lwjgl / name / args.lwjgl_version / f'{name}-{args.lwjgl_version}.jar' for name in names]
for path in jars:
    if not path.is_file():
        parser.error(f'Missing dependency: {path}')
classpath = ':'.join(str(path.resolve()) for path in jars)
source = repo / 'tools/NativeMappedMemoryProbe.java'
compile_command = [str(args.javac.resolve()), '-cp', classpath, '-d', str(args.output), str(source)]
with (args.output / 'compile.log').open('w') as log:
    subprocess.run(compile_command, stdout=log, stderr=subprocess.STDOUT, check=True)
commands = [compile_command]
results = []
for name, runtime in [('baseline', args.baseline_runtime), ('candidate', args.runtime)]:
    if runtime is None:
        continue
    runtime = runtime.resolve()
    library = runtime / 'lib/libnvk_minecraft.dylib'
    renderer = runtime / 'lib/libnvk_renderer.dylib'
    command = [str(args.java.resolve()), '--enable-native-access=ALL-UNNAMED',
               '-XX:ErrorFile=' + str(args.output / (name + '-hs_err_pid%p.log')),
               '-Dorg.lwjgl.vulkan.libname=' + str(library), '-cp', str(args.output) + ':' + classpath,
               'NativeMappedMemoryProbe', str(args.blocks)]
    commands.append(command)
    logfile = args.output / (name + '.log')
    with logfile.open('w') as log:
        process = subprocess.run(command, cwd=args.output, stdout=log, stderr=subprocess.STDOUT, timeout=90)
    text = logfile.read_text()
    allocations = [int(value, 16) for value in re.findall(r'^allocation type=\d+ flags=0x([0-9a-f]+)', text, re.M)]
    if name == 'baseline':
        success = process.returncode == 1 and 'vmaMapMemory: VkResult=-2' in text and '[!] nvRes: 0x51' in text
    else:
        success = process.returncode == 0 and f'PASS: {args.blocks * 16} MiB persistently mapped;' in text
        success = success and len(allocations) == args.blocks + 2
        if args.expect_system:
            success = success and all(flags == 0xe for flags in allocations[:args.blocks])
        success = success and (allocations[-2:] == [0x1, 0xe])
    row = {'case': name, 'result': 'PASS' if success else 'FAIL', 'exit': process.returncode,
           'expected': 'map failure reproduced' if name == 'baseline' else 'mapped staging and GPU readback',
           'staging_mib': args.blocks * 16, 'allocation_flags': allocations,
           'frontend_sha256': hashlib.sha256(library.read_bytes()).hexdigest(),
           'renderer_sha256': hashlib.sha256(renderer.read_bytes()).hexdigest(),
           'log': logfile.name, 'log_sha256': hashlib.sha256(logfile.read_bytes()).hexdigest()}
    results.append(row)
    print(f'{name}: {row["result"]}; exit={process.returncode}; {row["staging_mib"]} MiB staging', flush=True)
    if not success:
        print(text[-5000:], flush=True)
(args.output / 'COMMANDS.json').write_text(json.dumps(commands, indent=2) + '\n')
(args.output / 'RESULTS.json').write_text(json.dumps({
    'source': 'tools/NativeMappedMemoryProbe.java',
    'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
    'lwjgl_version': args.lwjgl_version,
    'dependencies': {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in jars},
    'checks': results,
}, indent=2) + '\n')
raise SystemExit(0 if all(row['result'] == 'PASS' for row in results) else 1)
