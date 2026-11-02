#!/usr/bin/env python3
"""Exercise the production Metal desktop-owner probe against bounded registry trees."""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/rotation'))
from test_support import method

source = (ROOT / 'kexts/NVRM/accel/nvrm-accel.cpp').read_text()
probe = method(source, 'static bool gpuHasMetalAccelerator(')
fixture = r'''
#include <cassert>
#include <cstring>
#include <vector>
struct OSObject { virtual ~OSObject() = default; };
static unsigned iterators;
struct OSIterator : OSObject {
    std::vector<OSObject *> entries;
    unsigned index = 0;
    explicit OSIterator(const std::vector<OSObject *> &e) : entries(e) { ++iterators; }
    OSObject *getNextObject() { return index < entries.size() ? entries[index++] : nullptr; }
    void release() { --iterators; delete this; }
};
struct IORegistryEntry : OSObject {
    std::vector<OSObject *> children;
    bool metal = false, noIterator = false;
    OSIterator *getChildIterator(const char *) { return noIterator ? nullptr : new OSIterator(children); }
    OSObject *getProperty(const char *key) { return metal && !strcmp(key, "MetalPluginName") ? this : nullptr; }
};
#define OSDynamicCast(type, object) dynamic_cast<type *>(object)
static const char *gIOServicePlane = "IOService";
@@PROBE@@
int main() {
    assert(!gpuHasMetalAccelerator(nullptr));
    IORegistryEntry pci, framebuffer, controller, accelerator, unrelated;
    assert(!gpuHasMetalAccelerator(&pci));
    pci.noIterator = true;
    assert(!gpuHasMetalAccelerator(&pci));
    pci.noIterator = false;
    pci.children = { &framebuffer };
    assert(!gpuHasMetalAccelerator(&pci)); // firmware-only framebuffer
    accelerator.metal = true;
    pci.children = { &framebuffer, &accelerator };
    assert(gpuHasMetalAccelerator(&pci)); // accelerator directly on the GPU
    assert(iterators == 0); // early success still releases the iterator
    pci.children = { &framebuffer, &controller };
    controller.children = { &unrelated, &accelerator };
    assert(gpuHasMetalAccelerator(&pci)); // accelerator below its controller
    assert(iterators == 0); // including both iterators on nested early success
    accelerator.metal = false;
    assert(!gpuHasMetalAccelerator(&pci)); // a framebuffer alone is insufficient
    accelerator.metal = true;
    controller.children = { &unrelated };
    unrelated.children = { &accelerator };
    assert(!gpuHasMetalAccelerator(&pci)); // unrelated deeper services do not qualify
    controller.noIterator = true;
    assert(!gpuHasMetalAccelerator(&pci));
    assert(iterators == 0);
}
'''

with tempfile.TemporaryDirectory(prefix='nv-headless-owner-') as directory:
    root = Path(directory)
    for name, body in [('current', probe), ('negative', probe.replace('found = true', 'found = false'))]:
        path = root / (name + '.cpp')
        path.write_text(fixture.replace('@@PROBE@@', body))
        binary = root / name
        subprocess.run(['xcrun', 'clang++', '-std=c++17', '-O1',
                        '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                        str(path), '-o', str(binary)], check=True)
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
        if name == 'current':
            assert result.returncode == 0, result.stdout + result.stderr
        else:
            assert result.returncode != 0, 'negative control must reject missing Metal ownership'
print('PASS: firmware-only, direct/nested Metal, bounded traversal and balanced iterator release; negative control detected')
