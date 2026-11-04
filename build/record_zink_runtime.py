#!/usr/bin/env python3
"""Record component hashes at build time, before package relocation."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = {
    'zink': (['libnvk_zink.dylib','nvk_zink.h','zink_probe','zink_sync_probe','cgl_renderer_probe','cgl-api-requirements.json'],
             ['zink/nvk_zink.c','zink/nvk_zink.h','zink/nvk_zink_private.h','zink/nvk_iosurface.c','zink/nvk_present.c','zink/nvk_present.h',
              'zink/mesa-zink-nvk.patch','zink/meson.build','build/build_zink.sh','build/prepare_zink.py','build/opengl_api.py',
              'tools/zink_probe.c','tools/zink_sync_probe.c','tools/cgl_renderer_probe.c']),
    'cgl': (['libnvk_cgl.dylib','nvk_cgl.h','cgl-os-build.txt','cgl-dispatch-map.json',
             'cgl_zink_probe','cgl_zink_legacy_probe','cgl_iosurface_probe','cgl_iosurface_interop_probe','obs_zink_policy_probe','cgl_zink_window_probe','cgl_zink_front_probe','zink_native_present_probe'],
            ['zink/nvk_cgl.c','zink/nvk_cgl.h','zink/appkit.m','zink/native_drawable.m','zink/native_drawable.h',
             'build/build_cgl_zink.sh','build/generate_cgl_dispatch.py','build/opengl_api.py','tools/cgl_zink_probe.c',
             'tools/cgl_zink_legacy_probe.c','tools/cgl_iosurface_probe.c','tools/cgl_iosurface_interop_probe.c','tools/obs_zink_policy_probe.m','tools/cgl_zink_window_probe.m','tools/cgl_zink_front_probe.m','tools/zink_native_present_probe.mm']),
    'gld': (['NVKZinkGLDriver.bundle/Contents/Info.plist',
             'NVKZinkGLDriver.bundle/Contents/MacOS/NVKZinkGLDriver','gld_bundle_probe'],
            ['zink/gld_bridge.c','zink/nvk_cgl_gld.h','build/build_gld_bridge.sh',
             'build/generate_gld_forward.py','tools/gld_bundle_probe.c']),
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record(output, component, mesa=None):
    path = output / 'ZINK-BUILD.json'
    data = json.loads(path.read_text()) if path.exists() else {'schema_version':1,'components':{}}
    files, sources = COMPONENTS[component]
    data['components'][component] = {'files':{name:digest(output/name) for name in files},
        'source_files':{name:digest(ROOT/name) for name in sources},
        'minimum_macos':'15.5','architecture':'x86_64'}
    if mesa:
        data['components'][component]['mesa_source'] = json.loads(mesa.read_text())
    path.write_text(json.dumps(data,indent=2)+'\n')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('output',type=Path)
    ap.add_argument('component',choices=COMPONENTS)
    ap.add_argument('--mesa',type=Path)
    args = ap.parse_args()
    if args.component == 'zink' and not args.mesa:
        ap.error('--mesa is required for the pinned native Zink build')
    record(args.output,args.component,args.mesa)
