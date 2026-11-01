#!/usr/bin/env python3
"""Check explicit no-rate-map capability through the built translator's public ABI."""
import argparse
import ctypes
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('library', type=Path)
parser.add_argument('--spirv-val', default='spirv-val')
args = parser.parse_args()
source = (ROOT / 'translator/translator/src/native/tests/interface.rs').read_text()
match = re.search(r'fn native_kernel_raster_rate_map_requires_explicit_no_maps_capability\(\).*?let ll = r#"(.*?)"#;', source, re.S)
assert match, 'upstream rate-map fixture missing'
library = ctypes.CDLL(str(args.library.resolve()))
library.nvmtl_translate_set_caps.argtypes = [ctypes.c_uint32]
library.nvmtl_translate_set_caps.restype = None
signature = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p),
             ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
library.nvmtl_translate.argtypes = signature
library.nvmtl_translate.restype = ctypes.c_int
library.nvmtl_translate_samplers.argtypes = signature[:2] + [ctypes.c_char_p] + signature[2:]
library.nvmtl_translate_samplers.restype = ctypes.c_int
library.nvmtl_translate_free.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
library.nvmtl_translate_free.restype = None

with tempfile.TemporaryDirectory(prefix='nv-rate-caps-') as directory:
    for direction in ('screen_to_physical', 'physical_to_screen'):
        ll = match[1].replace('screen_to_physical', direction).encode()
        for specialized in (False, True):
            for caps in (0, 3, 7, 4, 3):
                # Return to 3 after success: clearing the capability must take effect.
                library.nvmtl_translate_set_caps(caps)
                output, size = ctypes.c_void_p(), ctypes.c_size_t()
                error = ctypes.create_string_buffer(8192)
                params = [ll, b'kernel'] + ([b'{"samplers":{},"constants":{}}'] if specialized else [])
                params += [ctypes.byref(output), ctypes.byref(size), error, len(error)]
                function = library.nvmtl_translate_samplers if specialized else library.nvmtl_translate
                rc = function(*params)
                try:
                    if caps & 4:
                        assert rc == 0 and output.value and size.value, error.value
                        path = Path(directory) / 'module.spv'
                        path.write_bytes(ctypes.string_at(output, size.value))
                        subprocess.run([args.spirv_val, '--target-env', 'vulkan1.2',
                                        '--scalar-block-layout', str(path)], check=True)
                    else:
                        assert rc != 0 and b'rasterization-rate-map parameter data decoding' in error.value, error.value
                        assert not output.value and not size.value
                finally:
                    if output.value:
                        library.nvmtl_translate_free(output, size.value)
    library.nvmtl_translate_set_caps(0)
print('PASS: both map directions, normal/specialized C ABI, explicit capability, refusal after clearing and SPIR-V validation')
