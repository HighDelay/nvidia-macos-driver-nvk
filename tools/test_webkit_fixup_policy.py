#!/usr/bin/env python3
"""Check the helper's real identity, resource and permission boundaries."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = r'''
#include <cassert>
#include <cstdio>
#include "policy.hpp"
using namespace NVWebKitPolicy;
int main() {
    assert(client("com.apple.WebKit.GPU",true,true));
    for (const char *name : {"Safari","com.apple.Safari","com.apple.WebKit.WebContent","com.apple.WebKit.GPU.fake","com.google.Chrome", "", (const char *)nullptr})
        assert(!client(name,true,true));
    assert(!client("com.apple.WebKit.GPU",false,true));
    assert(!client("com.apple.WebKit.GPU",true,false));
    const char *binary="/Library/GPUBundles/NVMTLDriver.bundle/Contents/MacOS/NVMTLDriver";
    assert(resource(binary)==Resource::Runtime);
    assert(resource("/System/Volumes/Data/Library/GPUBundles/NVMTLDriver.bundle/Contents/MacOS/NVMTLDriver")==Resource::Runtime);
    for(const char *bad : {"/Library/GPUBundles/evil.dylib", "/Library/GPUBundles/NVMTLDriver.bundle/Contents/MacOS/NVMTLDriver.evil", "/tmp/NVMTLDriver", "/Library/GPUBundles/nvmtl/../evil.dylib", "/System/Volumes/Data/dev/null", "/System/Volumes/DataX/Library/GPUBundles/NVMTLDriver.bundle/Contents/MacOS/NVMTLDriver", "", (const char *)nullptr})
        assert(resource(bad)==Resource::None);
    assert(resource("/dev/null")==Resource::NullDevice);
    assert(map(1,5,2,resource(binary)) && map(1,4,2,resource(binary)));
    for(int result : {0,2,13,22}) assert(!map(result,5,2,resource(binary)));
    for(int prot=0;prot<16;prot++) if(prot!=4&&prot!=5) assert(!map(1,prot,2,resource(binary)));
    assert(!map(1,5,1,resource(binary)) && !map(1,5,3,resource(binary)));
    assert(!map(1,5,2|0x800,resource(binary)) && !map(1,5,2|0x1000,resource(binary)));
    assert(!map(1,5,2,Resource::NullDevice) && !map(1,5,2,Resource::None));
    assert(read(1,1,Resource::NullDevice));
    for(int mode=0;mode<1024;mode++) if(mode!=1) assert(!read(1,mode,Resource::NullDevice));
    assert(!read(0,1,Resource::NullDevice) && !read(13,1,Resource::NullDevice));
    assert(!read(1,1,Resource::Runtime) && !read(1,1,Resource::None));
    puts("PASS: WebKit signing role, exact paths/firmlink, read-only null and executable/private mapping boundaries");
}
'''
with tempfile.TemporaryDirectory(prefix='nvwk-policy-') as directory:
    folder = Path(directory)
    (folder / 'test.cpp').write_text('#include <initializer_list>\n' + source)
    subprocess.run(['xcrun', 'clang++', '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                    '-I'+str(ROOT/'kexts/NVWebKitFixup'), str(folder/'test.cpp'), '-o', str(folder/'test')], check=True)
    subprocess.run([str(folder/'test')], check=True)
