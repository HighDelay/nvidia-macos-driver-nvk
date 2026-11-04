#!/usr/bin/env python3
"""Execute bounded regression kernels through a matching built plugin/translator.

No files are installed and no application is restarted. The original unit-test
LLVM fixtures supply the pointer/resource shapes. The phi fixture's loop inputs
are made false, and texture fixtures gain observable pixel outputs. Unsafe,
metadata-free fallback kernels are deliberately excluded from GPU dispatch.
"""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--plugin', type=Path, required=True)
parser.add_argument('--translator', type=Path, required=True)
parser.add_argument('--tools', type=Path, required=True, help='directory with spirv-val/dis')
parser.add_argument('--headers', type=Path, default=Path.home() / 'Vulkan-Headers/include',
                    help='Vulkan headers matching the selected plugin')
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('--cases', nargs='+')
parser.add_argument('--prepare-only', action='store_true')
args = parser.parse_args()
args.out.mkdir(parents=True, exist_ok=True)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

(args.out / 'INPUTS.json').write_text(json.dumps({
    'plugin': str(args.plugin.resolve()),
    'plugin_sha256': digest(args.plugin),
    'translator': str(args.translator.resolve()),
    'translator_sha256': digest(args.translator),
    'api_header_sha256': digest(ROOT / 'plugin/nvmtl_vk.h'),
    'readback_source_sha256': digest(ROOT / 'tools/translator_readback.cpp'),
    'validator': subprocess.check_output([str(args.tools / 'spirv-val'), '--version'], text=True),
    'runtime_caps': 3,
    'installed_files_changed': False,
}, indent=2) + '\n')

def fixture(kind, name):
    source = (ROOT / f'translator/translator/src/native/tests/{kind}.rs').read_text()
    match = re.search(r'fn ' + re.escape(name) + r'\(\)\s*\{.*?let ll = r#"(.*?)"#;', source, re.S)
    assert match, name
    return match[1]

def replace(text, old, new):
    assert text.count(old) == 1, old
    return text.replace(old, new, 1)

def add_argument(ll, declaration, metadata):
    header = re.search(r'define void @k\(([^\n]*)\)', ll)
    assert header
    count = len(header[1].split(','))
    # These fixtures have scalar pointer parameters, not vector commas.
    ll = replace(ll, header[0], 'define void @k(' + header[1] + ', ' + declaration + ')')
    node = 1 + max(map(int, re.findall(r'^!(\d+) =', ll, re.M)))
    line = re.search(r'^!2 = !\{([^}]*)\}', ll, re.M)
    assert line
    ll = replace(ll, line[0], '!2 = !{' + line[1] + ', !' + str(node) + '}')
    return ll + f'\n!{node} = !{{i32 {count}, {metadata}}}\n'

BUFFER_OUT = '!"air.buffer", !"air.location_index", i32 0, i32 1, !"air.write", !"air.address_space", i32 1, !"air.arg_type_size", i32 16, !"air.arg_type_align_size", i32 16, !"air.arg_type_name", !"float4*", !"air.arg_name", !"out"'
SAMPLE_DECL = 'declare { <4 x float>, i8 } @air.sample_texture_2d.v4f32(ptr addrspace(1), ptr addrspace(2), <2 x float>, i1, <2 x i32>, i1, float, float, i32)\n'

def sample2d(result, texture, sampler):
    return f'  %{result} = call {{ <4 x float>, i8 }} @air.sample_texture_2d.v4f32(ptr addrspace(1) %{texture}, ptr addrspace(2) %{sampler}, <2 x float> <float 0.5, float 0.5>, i1 false, <2 x i32> zeroinitializer, i1 false, float 0.0, float 0.0, i32 0)\n'

kernels = {}
kernels['control'] = '''target triple = "spirv-unknown-vulkan1.2"
define void @k(ptr addrspace(1) %out) {
entry:
  store i32 305419896, ptr addrspace(1) %out, align 4
  ret void
}
!air.kernel = !{!0}
!0 = !{ptr @k, !1, !2}
!1 = !{}
!2 = !{!3}
!3 = !{i32 0, !"air.buffer", !"air.location_index", i32 0, i32 1, !"air.write", !"air.address_space", i32 1, !"air.arg_type_size", i32 4, !"air.arg_type_align_size", i32 4, !"air.arg_type_name", !"uint*", !"air.arg_name", !"out"}
'''
for mode, name in {
    'phi': 'native_device_buffer_array_keeps_direct_buffer_pointer_phi_network_modeled',
    'table': 'native_device_buffer_array_through_a_generic_state_pointer_stores_through_the_table',
    'helper': 'native_helper_alloca_keeps_parameter_pointer_chain_real',
    'i64': 'native_mtl_force_not_checked_i64_load_uses_bda_inttoptr_address',
    'alloca': 'native_device_pointer_round_trips_through_direct_pointer_alloca',
    'callback': 'native_generic_callback_table_cursor_keeps_its_store',
}.items():
    kernels[mode] = fixture('buffers', name)
kernels['phi'] = replace(kernels['phi'], 'call void @copy_nested(ptr addrspace(1) %src, ptr addrspace(1) %dst, i1 %inner_again, i1 %outer_again)', 'call void @copy_nested(ptr addrspace(1) %src, ptr addrspace(1) %dst, i1 false, i1 false)')
kernels['pack565'] = fixture('intrinsics', 'native_air_pack_unorm_rgb565_f16_lowers_to_exact_fields')
kernels['pack10'] = fixture('intrinsics', 'native_air_pack_unorm_rgb10a2_rounds_the_exact_product')
ll = kernels['pack565']
ll = replace(ll, 'define void @main(ptr addrspace(1) %out)', 'define void @main(ptr addrspace(1) %out, ptr addrspace(1) %in)')
ll = replace(ll, 'entry:\n  %packed', 'entry:\n  %input = load <3 x half>, ptr addrspace(1) %in, align 2\n  %packed')
ll = replace(ll, '<3 x half> <half 0xH3C00, half 0xH3800, half 0xH0000>', '<3 x half> %input')
ll = replace(ll, '!2 = !{!3}', '!2 = !{!3, !4}')
kernels['pack565dyn'] = ll + '\n!4 = !{i32 1, !"air.buffer", !"air.location_index", i32 1, i32 1, !"air.read", !"air.address_space", i32 1, !"air.arg_type_size", i32 6, !"air.arg_type_align_size", i32 2, !"air.arg_type_name", !"half3*", !"air.arg_name", !"in"}\n'

for arm in [0, 1]:
    ll = fixture('buffers', 'native_forward_device_gep_phi_keeps_integer_addresses_without_private_pointer_storage')
    ll = replace(ll, 'define float @k(ptr addrspace(2) %table, i1 %cond, i64 %idx)', 'define void @k(ptr addrspace(2) %table, i1 %cond, i64 %idx, ptr addrspace(1) %out)')
    ll = replace(ll, 'br i1 %cond', 'br i1 ' + ('true' if arm else 'false'))
    ll = replace(ll, '  ret float %v', '  store float %v, ptr addrspace(1) %out, align 4\n  ret void')
    ll = ll.replace('ptr addrspace(1) %b0, i64 %idx', 'ptr addrspace(1) %b0, i64 0').replace('ptr addrspace(1) %b1, i64 %idx', 'ptr addrspace(1) %b1, i64 0')
    kernels['forward' + str(arm)] = ll + '''
!air.kernel = !{!0}
!0 = !{ptr @k, !1, !2}
!1 = !{}
!2 = !{!3, !4}
!3 = !{i32 0, !"air.buffer", !"air.location_index", i32 0, i32 1, !"air.read", !"air.address_space", i32 2, !"air.arg_type_size", i32 16, !"air.arg_type_align_size", i32 8, !"air.arg_type_name", !"Table*", !"air.arg_name", !"table"}
!4 = !{i32 3, !"air.buffer", !"air.location_index", i32 3, i32 1, !"air.write", !"air.address_space", i32 1, !"air.arg_type_size", i32 4, !"air.arg_type_align_size", i32 4, !"air.arg_type_name", !"float*", !"air.arg_name", !"out"}
'''

ll = fixture('textures', 'native_kernel_texture_sampler_metadata_bindings')
ll = add_argument(ll, 'ptr addrspace(1) %out', BUFFER_OUT)
ll = ll.replace('!"air.read"', '!"air.sample"').replace('texture2d<float, read>', 'texture2d<float, sample>')
body = sample2d('s', 'tex', 'sampler') + '  %c = extractvalue { <4 x float>, i8 } %s, 0\n  store <4 x float> %c, ptr addrspace(1) %out, align 16\n'
kernels['texture5'] = replace(ll, 'entry:\n  ret void', 'entry:\n' + body + '  ret void') + SAMPLE_DECL

ll = fixture('textures', 'native_texture_and_sampler_binding_bands_cover_high_and_last_abi_indices')
ll = add_argument(ll, 'ptr addrspace(1) %out', BUFFER_OUT)
ll = ll.replace('!"air.read"', '!"air.sample"').replace('texture2d<float, read>', 'texture2d<float, sample>')
body = sample2d('s0', 'tex40', 'sampler8') + sample2d('s1', 'tex127', 'sampler15')
body += '  %c0 = extractvalue { <4 x float>, i8 } %s0, 0\n  %c1 = extractvalue { <4 x float>, i8 } %s1, 0\n  %c = fadd <4 x float> %c0, %c1\n  store <4 x float> %c, ptr addrspace(1) %out, align 16\n'
kernels['texture127'] = replace(ll, 'entry:\n  ret void', 'entry:\n' + body + '  ret void') + SAMPLE_DECL

ll = fixture('textures', 'native_kernel_unused_texture1d_uses_metadata_dimension')
ll = add_argument(ll, 'ptr addrspace(2) %sampler', '!"air.sampler", !"air.location_index", i32 2, i32 1, !"air.arg_type_name", !"sampler", !"air.arg_name", !"sampler"')
ll = ll.replace(', !"air.arg_unused"', '')
body = '  %s = call { <4 x float>, i8 } @air.sample_texture_1d.v4f32(ptr addrspace(1) %lut, ptr addrspace(2) %sampler, float 0.5, i1 false, i32 0, i1 false, float 0.0, float 0.0, i32 0)\n  %c = extractvalue { <4 x float>, i8 } %s, 0\n  store <4 x float> %c, ptr addrspace(1) %out, align 16'
kernels['texture1d'] = replace(ll, '  store i32 1, ptr addrspace(1) %out, align 4', body) + '\ndeclare { <4 x float>, i8 } @air.sample_texture_1d.v4f32(ptr addrspace(1), ptr addrspace(2), float, i1, i32, i1, float, float, i32)\n'

ll = fixture('textures', 'native_sampled_and_storage_views_of_one_metal_texture_use_distinct_bands')
body = '  %s = call { <4 x float>, i8 } @air.read_texture_2d.v4f32(ptr addrspace(1) %source, <2 x i32> zeroinitializer, i32 0, i32 0)\n  %c = extractvalue { <4 x float>, i8 } %s, 0\n  call void @air.write_texture_2d.v4f32(ptr addrspace(1) %dest, <2 x i32> zeroinitializer, <4 x float> %c, i32 0, i32 0)\n'
kernels['storage40'] = replace(ll, 'entry:\n  ret void', 'entry:\n' + body + '  ret void') + '\ndeclare { <4 x float>, i8 } @air.read_texture_2d.v4f32(ptr addrspace(1), <2 x i32>, i32, i32)\ndeclare void @air.write_texture_2d.v4f32(ptr addrspace(1), <2 x i32>, <4 x float>, i32, i32)\n'

# Two differently colored images and nearest/linear samplers distinguish array
# elements on the GPU, including the last sampler binding and a runtime index.
for mode in ['texture-array0', 'texture-array1', 'texture-array-dynamic', 'sampler-array0', 'sampler-array1']:
    textures = mode.startswith('texture-')
    declarations = ('ptr byval([2 x ptr addrspace(1)]) %textures, ptr addrspace(2) %sampler' if textures
                    else 'ptr addrspace(1) %tex, ptr byval([2 x ptr addrspace(2)]) %samplers')
    index = '%index' if mode.endswith('dynamic') else mode[-1]
    body = ('  %index = load i64, ptr addrspace(1) %input, align 8\n' if mode.endswith('dynamic') else '')
    body += (f'  %slot = getelementptr inbounds [2 x ptr addrspace(1)], ptr %textures, i32 0, i64 {index}\n'
             '  %tex = load ptr addrspace(1), ptr %slot, align 8\n' if textures else
             f'  %slot = getelementptr inbounds [2 x ptr addrspace(2)], ptr %samplers, i32 0, i32 {index}\n'
             '  %sampler = load ptr addrspace(2), ptr %slot, align 8\n')
    body += sample2d('sample', 'tex', 'sampler')
    body += '  %color = extractvalue { <4 x float>, i8 } %sample, 0\n  store <4 x float> %color, ptr addrspace(1) %out, align 16\n  ret void\n'
    texture_type = 'array<texture2d<float, sample>, 2>' if textures else 'texture2d<float, sample>'
    sampler_type = 'sampler' if textures else 'array<sampler, 2>'
    kernels[mode] = f'''define void @k({declarations}, ptr addrspace(1) %out, ptr addrspace(1) %input) {{
entry:
{body}}}
{SAMPLE_DECL}
!air.kernel = !{{!0}}
!0 = !{{ptr @k, !1, !2}}
!1 = !{{}}
!2 = !{{!3, !4, !5, !6}}
!3 = !{{i32 0, !"air.texture", !"air.location_index", i32 40, i32 {2 if textures else 1}, !"air.sample", !"air.arg_type_name", !"{texture_type}", !"air.arg_name", !"tex"}}
!4 = !{{i32 1, !"air.sampler", !"air.location_index", i32 14, i32 {1 if textures else 2}, !"air.arg_type_name", !"{sampler_type}", !"air.arg_name", !"sampler"}}
!5 = !{{i32 2, {BUFFER_OUT}}}
!6 = !{{i32 3, !"air.buffer", !"air.location_index", i32 1, i32 1, !"air.read", !"air.address_space", i32 1, !"air.arg_type_size", i32 8, !"air.arg_type_align_size", i32 8, !"air.arg_type_name", !"ulong*", !"air.arg_name", !"input"}}
'''

# Struct-wrapped uniform slots must preserve the LLVM layout and lane bits.
wrapped = (ROOT/'translator/translator/tests/fixtures/buffers/wrapped_uniform_load.ll').read_text()
kernels['wrapped-nested'] = wrapped
kernels['wrapped-single'] = wrapped.replace('type { { [4 x i32] } }', 'type { [4 x i32] }').replace(
    'i64 0, i32 0, i32 0, i64 1', 'i64 0, i32 0, i64 1')
kernels['wrapped-trailing'] = wrapped.replace('type { { [4 x i32] } }', 'type { { [4 x i32] }, <4 x i32> }').replace(
    '!"air.arg_type_size", i32 16, !"air.arg_type_align_size", i32 4',
    '!"air.arg_type_size", i32 32, !"air.arg_type_align_size", i32 4')
kernels['wrapped-array'] = wrapped.replace('[4 x i32]', '[2 x [4 x i32]]').replace(
    'i64 0, i32 0, i32 0, i64 1', 'i64 0, i32 0, i32 0, i64 0, i64 1').replace(
    '!"air.arg_type_size", i32 16, !"air.arg_type_align_size", i32 4',
    '!"air.arg_type_size", i32 32, !"air.arg_type_align_size", i32 4')
kernels['wrapped-float'] = wrapped.replace('[4 x i32]', '[4 x float]').replace('packed_uint4', 'packed_float4')
bits = kernels['wrapped-float'].replace('<4 x float>', '<4 x i32>')
bits = replace(bits, '  %pos = fadd fast <4 x i32> %copyload, %rect\n', '')
kernels['wrapped-bitcopy'] = replace(bits, 'store <4 x i32> %pos', 'store <4 x i32> %copyload')

translator = ctypes.CDLL(str(args.translator.resolve()))
translator.nvmtl_translate.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
translator.nvmtl_translate.restype = ctypes.c_int
translator.nvmtl_translate_free.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
# Match NVMTLLibraryLoad: writable textures use the runtime format, rather
# than the standalone translator's conservative single-channel R32f fallback.
translator.nvmtl_translate_set_caps.argtypes = [ctypes.c_uint32]
translator.nvmtl_translate_set_caps(3)
binary = args.out / 'translator_readback'
if not args.prepare_only:
    subprocess.run(['xcrun', 'clang++', '-std=c++17', '-O1', '-I' + str(args.headers),
                    str(ROOT / 'tools/translator_readback.cpp'), '-o', str(binary)], check=True)
results = []
for mode in args.cases or kernels:
    ll = kernels[mode]
    (args.out / (mode + '.ll')).write_text(ll)
    output = ctypes.c_void_p(); size = ctypes.c_size_t(); error = ctypes.create_string_buffer(65536)
    rc = translator.nvmtl_translate(ll.encode(), b'kernel', ctypes.byref(output), ctypes.byref(size), error, len(error))
    assert rc == 0, (mode, error.value)
    data = ctypes.string_at(output.value, size.value)
    translator.nvmtl_translate_free(output, size)
    spv = args.out / (mode + '.spv'); spv.write_bytes(data)
    subprocess.run([str(args.tools / 'spirv-val'), '--target-env', 'vulkan1.2', str(spv)], check=True)
    asm = subprocess.check_output([str(args.tools / 'spirv-dis'), str(spv)], text=True)
    (args.out / (mode + '.spvasm')).write_text(asm)
    if mode in {'phi', 'table', 'helper', 'alloca', 'callback', 'i64'}:
        # Refuse a known bad serialization before dispatching it. The emitter
        # can leave a zero placeholder in a dead logical-pointer slot after
        # forwarding every read; permit that only when the local has no uses
        # other than stores (no load, alias or escape).
        lines = [line.split() for line in asm.splitlines()]
        address_types = {v[0] for v in lines if len(v) == 5 and v[1:4] == ['=', 'OpTypeInt', '64']}
        zeros = {v[0] for v in lines if len(v) >= 4 and v[1] == '=' and v[3] in address_types
                 and (v[2] == 'OpConstantNull' or (v[2] == 'OpConstant' and v[4:] == ['0']))}
        locals_ = {v[0] for v in lines if len(v) >= 5 and v[1:3] == ['=', 'OpVariable'] and v[4] == 'Function'}
        dead_locals = {pointer for pointer in locals_ if all(
            pointer not in (v[3:] if len(v) > 1 and v[1] == '=' else v[1:])
            or (v[0] == 'OpStore' and v[1] == pointer and pointer not in v[2:])
            for v in lines)}
        assert not any(len(v) >= 3 and v[0] == 'OpStore' and v[2] in zeros and v[1] not in dead_locals
                       for v in lines), (mode, 'device pointer serialized as zero')
    row = {'case': mode, 'spirv_sha256': hashlib.sha256(data).hexdigest(), 'spirv_valid': True}
    if not args.prepare_only:
        result = subprocess.run([str(binary), str(args.plugin.resolve()), str(spv), mode], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30)
        (args.out / (mode + '.log')).write_text(result.stdout)
        row.update(exit_code=result.returncode, output=result.stdout)
        results.append(row)
        (args.out / 'RESULTS.json').write_text(json.dumps(results, indent=2) + '\n')
        print(result.stdout, end='', flush=True)
        assert result.returncode == 0, mode
    else:
        results.append(row)
        print('Prepared and validated', mode, flush=True)
(args.out / 'RESULTS.json').write_text(json.dumps(results, indent=2) + '\n')
