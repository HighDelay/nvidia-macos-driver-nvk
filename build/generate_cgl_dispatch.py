#!/usr/bin/env python3
"""Recover local libGL dispatch slots; emit adapters, never Apple instructions."""
import argparse
import json
from pathlib import Path
import re
import subprocess
from opengl_api import supported


PROTOTYPE = (r'(?:GLAPI|extern)\s+([^;{}]+?)\s+(?:APIENTRY\s+)?(gl\w+)\s*'
             r'\(([^();{}]*?)\)\s*(?:OPENGL_DEPRECATED(?:\([^)]*\))?\s*)?;')
SDK_HEADERS = ('gl3.h', 'gl3ext.h', 'gl.h', 'glext.h')


def collect_prototypes(headers):
    prototypes = {}
    for header in SDK_HEADERS:
        text = re.sub(r'/\*.*?\*/|//[^\n]*', ' ', (headers / header).read_text(), flags=re.S)
        # A macro such as '#define GLAPI extern' must not become a return type.
        # Skip continued directives too, while retaining actual declarations.
        lines, continued = [], False
        for line in text.splitlines():
            if continued or line.lstrip().startswith('#'):
                continued = line.rstrip().endswith('\\')
            else:
                lines.append(line)
        for ret, name, params in re.findall(PROTOTYPE, '\n'.join(lines)):
            prototypes.setdefault(name, (ret.strip(), name, params.strip()))
    return prototypes


def parameters(params):
    """Give the SDK's occasional unnamed parameters local adapter names."""
    if params.strip() == 'void':
        return 'void', []
    declarations, names = [], []
    for i, param in enumerate(params.split(',')):
        param = param.strip()
        match = re.search(r'(\w+)\s*(?:\[.*\])?$', param)
        named = match and not match.group(1).startswith('GL') and match.group(1) not in {'void', 'const'}
        name = match.group(1) if named else f'arg{i}'
        declarations.append(param if named else param + ' ' + name)
        names.append(name)
    return ', '.join(declarations), names


def signature(ret, params):
    params, names = parameters(params)
    types = [] if params == 'void' else [re.sub(r'\b' + name + r'\b', '', param)
                                           for param, name in zip(params.split(','), names)]
    value = ret + ':' + ','.join(types)
    # Apple's shader handles are pointer-sized. Scalar arguments occupy the
    # same integer ABI register; core GLuint adapters deliberately use low32.
    for old, new in {'GLhandleARB': 'GLuint', 'GLcharARB': 'GLchar',
                     'GLintptrARB': 'GLintptr', 'GLsizeiptrARB': 'GLsizeiptr',
                     'GLclampf': 'GLfloat', 'GLclampd': 'GLdouble', 'GLvoid': 'void',
                     'GLint64EXT': 'GLint64', 'GLuint64EXT': 'GLuint64'}.items():
        value = re.sub(r'\b' + old + r'\b', new, value)
    return re.sub(r'\s+', '', re.sub(r'\bconst\b', '', value))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('probe', type=Path)
    ap.add_argument('output', type=Path)
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    sdk = subprocess.check_output(['xcrun', '--show-sdk-path'], text=True).strip()
    headers = Path(sdk) / 'System/Library/Frameworks/OpenGL.framework/Headers'
    prototypes = collect_prototypes(headers)
    required = {'glMapBufferRange', 'glFlushMappedBufferRange', 'glVertexAttribDivisor',
                'glBegin', 'glEnd', 'glNewList', 'glCallList', 'glMatrixMode',
                'glActiveTextureARB', 'glClientActiveTextureARB', 'glBindBufferARB',
                'glBindFramebufferEXT', 'glGetAttachedObjectsARB'}
    if len(prototypes) < 1000 or not required.issubset(prototypes):
        raise SystemExit('Incomplete SDK core/legacy/extension prototype inventory')
    commands = args.output / 'dispatch.lldb'
    # SBCommandReturnObject lets an absent obsolete extension declaration be
    # recorded without stopping before later public EXT/APPLE stubs.
    program = ('import lldb\nfor name in ' + repr(list(prototypes)) + ':\n'
               ' result=lldb.SBCommandReturnObject()\n'
               " lldb.debugger.GetCommandInterpreter().HandleCommand('disassemble -n '+name,result)\n"
               " print('(lldb) disassemble -n '+name)\n"
               ' print(result.GetOutput() or result.GetError())')
    commands.write_text('target create ' + str(args.probe.resolve()) + '\nscript exec(' + repr(program) + ')\nquit\n')
    result = subprocess.run(['lldb', '--no-lldbinit', '-b', '-s', str(commands)],
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (args.output / 'dispatch-local.asm').write_text(result.stdout)
    if result.returncode:
        raise SystemExit(result.stderr)
    blocks = re.split(r'\(lldb\) disassemble -n (gl\w+)\n', result.stdout)
    bodies = dict(zip(blocks[1::2], blocks[2::2]))
    slots, excluded = {}, {}
    for name in prototypes:
        body = bodies.get(name, '')
        if 'Unable to find symbol' in body:
            excluded[name] = 'SDK declaration has no local public symbol'
            continue
        if '%gs:0xf0' not in body:
            instructions = re.findall(r'<\+\d+>:\s+(\w+)', body)
            if prototypes[name][0] == 'void' and instructions == ['pushq', 'movq', 'popq', 'retq']:
                excluded[name] = 'Local public symbol is an empty compatibility stub'
                continue
            raise SystemExit(f'{name}: unknown current-context convention')
        offsets = set(int(x, 16) for x in re.findall(r'(0x[0-9a-f]+)\(%r(?:ax|bx|cx|dx|si|di|8|9|1[0-5])\)', body))
        offsets.discard(0)
        if len(offsets) != 1:
            raise SystemExit(f'{name}: ambiguous dispatch offsets {offsets}')
        slot = offsets.pop()
        if not 0 < slot < 0x1e80 or slot % 8:
            raise SystemExit(f'{name}: slot outside observed CGL dispatch prefix')
        slots[name] = slot
    if not required.issubset(slots):
        raise SystemExit('Required core/legacy APIs have no recovered local dispatch')
    shared = {}
    for name, slot in slots.items():
        shared.setdefault(slot, []).append(name)
    for slot, names in shared.items():
        if len({signature(prototypes[n][0], prototypes[n][2]) for n in names}) != 1:
            raise SystemExit(f'Incompatible shared GL dispatch slot {slot:#x}: {names}')
    contract = json.loads((args.probe.parent/'cgl-api-requirements.json').read_text())
    capabilities = {}
    policy = ['/* SPDX-License-Identifier: Apache-2.0',
              ' * Copyright 2013-2020 The Khronos Group Inc.',
              ' * Derived API metadata intersected with recovered public dispatch. */']
    for profile in ('core', 'compatibility'):
        version, extensions = supported(contract, slots, profile)
        capabilities[profile] = {'version': version, 'extensions': extensions}
        policy += [f'static const unsigned api_{profile}_version = {version};',
                   f'static const char *const api_{profile}_extensions[] = {{']
        policy += ['  '+json.dumps(name)+',' for name in extensions]
        policy += ['};']
    (args.output/'cgl_api.inc').write_text('\n'.join(policy)+'\n')
    wrappers = ['/* Generated from public Khronos prototypes and local slot observations. */']
    rows = []
    for slot, aliases in shared.items():
        ret, name, params = prototypes[aliases[0]]
        params, names = parameters(params)
        with_ctx = 'void *engine' + (', ' + params if params != 'void' else '')
        wrappers += [f'static {ret} nvk_{name}({with_ctx}) {{']
        if name == 'glViewport':
            wrappers += ['  if (native_viewport(engine, __builtin_return_address(0), x, y, width, height)) return;']
        wrappers += [f'  {ret} (*nvk_apple)({with_ctx}) = original_noncurrent(engine, {slot});',
                     f'  if (nvk_apple) {{ {"" if ret == "void" else "return "}nvk_apple(engine{", " if names else ""}{", ".join(names)});' +
                     (' return; }' if ret == 'void' else ' }'),
                     '  struct nvk_cgl_bridge *nvk_bridge = bridge_current(engine);',
                     ('  if (!nvk_bridge) return;' if ret == 'void' else '  if (!nvk_bridge) return 0;')]
        if name in {'glDeleteProgram', 'glDeleteShader'}:
            wrappers += [
                '  GLboolean (*is_program)(GLuint) = (void *)nvk_zink_get_proc_address("glIsProgram");',
                '  void (*delete_object)(GLuint) = (void *)nvk_zink_get_proc_address(',
                '     is_program(' + names[0] + ') ? "glDeleteProgram" : "glDeleteShader");',
                '  delete_object(' + names[0] + ');']
        elif name == 'glSwapAPPLE':
            wrappers += ['  nvk_cgl_present(nvk_bridge->context);']
        elif name == 'glGetString':
            wrappers += [f'  return capability_string(nvk_bridge, {names[0]});']
        elif name == 'glGetStringi':
            wrappers += [f'  return capability_string_indexed(nvk_bridge, {names[0]}, {names[1]});']
        else:
            # Mesa and Apple use pointer-sized GLhandleARB on macOS, including
            # output arrays. Shared core slots above use GLuint declarations;
            # unique ARB slots must retain their native pointer-sized signature.
            wrappers += [f'  {ret} (*nvk_func)({params}) = (void *)nvk_bridge->procs[{len(rows)}];',
                         f'  if (!nvk_func) {{ missing_entrypoint("{name}");' +
                         (' return; }' if ret == 'void' else ' return 0; }')]
            if name in {'glGetIntegerv','glGetInteger64v','glGetFloatv','glGetDoublev','glGetBooleanv'}:
                value = '!!nvk_value' if name == 'glGetBooleanv' else 'nvk_value'
                wrappers += ['  GLint nvk_value;',
                             f'  if (capability_parameter(nvk_bridge, {names[0]}, &nvk_value)) {{ *{names[1]} = {value}; return; }}']
            if set(aliases) & {'glFlush', 'glFinish', 'glFlushRenderAPPLE', 'glFinishRenderAPPLE', 'glFenceSync'}:
                wrappers += ['  if (!prepare_iosurface_flush(nvk_bridge))' +
                             (' return;' if ret == 'void' else ' return 0;')]
            wrappers += [f'  {"" if ret == "void" else "return "}nvk_func({", ".join(names)});']
            if set(aliases) & {'glBindTexture', 'glBindTextureEXT'}:
                wrappers += [f'  refresh_iosurface_bindings(nvk_bridge, {names[0]}, 0);']
            if set(aliases) & {'glBindFramebuffer', 'glBindFramebufferEXT'}:
                wrappers += [f'  refresh_iosurface_bindings(nvk_bridge, {names[0]}, 1);']
            if set(aliases) & {'glFlush', 'glFinish', 'glFlushRenderAPPLE', 'glFinishRenderAPPLE'}:
                wrappers += ['  flush_front_buffer(nvk_bridge);']
        wrappers += ['}']
        rows.append(f'  {{"{name}", {slot}, (void *)nvk_{name}}},')
    wrappers += ['static const struct dispatch_entry entries[] = {', *rows, '};']
    (args.output / 'cgl_dispatch.inc').write_text('\n'.join(wrappers) + '\n')
    (args.output / 'cgl-dispatch-map.json').write_text(json.dumps({
        'macos': subprocess.check_output(['sw_vers'], text=True),
        'source': 'local LLDB static disassembly of libGL public stubs',
        'headers': list(SDK_HEADERS),
        'context_prefix_bytes': 0x1e80, 'slots': slots,
        'aliases': {hex(slot): names for slot, names in shared.items() if len(names) > 1},
        'capabilities': capabilities,
        'registry_sha256': contract['registry_sha256'],
        'excluded': excluded,
        'limitations': ['Offsets are local OS observations, not a stable Apple ABI.',
                        'Absent symbols and empty compatibility stubs are explicitly excluded.',
                        'A dispatch mapping does not establish Mesa extension support.',
                        'No Apple binary or instructions belong in distributable output.']}, indent=2) + '\n')
    print(f'Generated {len(rows)} GL adapters for {len(slots)} core/legacy/extension symbols; excluded {len(excluded)} SDK declarations in {args.output}')


if __name__ == '__main__':
    main()
