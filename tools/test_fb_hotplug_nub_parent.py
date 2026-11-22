#!/usr/bin/env python3
"""Check that an empty boot enumeration can match idle NVIDIA display nubs later."""
from pathlib import Path

source = (Path(__file__).resolve().parents[1] / 'kexts/NVRMFB/fb/nvrm-fb.cpp').read_text()
start = source[source.index('bool NVRMNVDAFramebuffer::start('):source.index('void NVRMNVDAFramebuffer::scheduleCall(')]
scan = source[source.index('void NVRMNVDAFramebuffer::hotplugFire('):source.index('static char gTrace')]
initial = source[source.index('bool NVRMNVDAFramebuffer::kapiInit('):source.index('bool consoleSilenced')]
assert start.index('gNubParent = provider->getProvider()') < start.index('bool kapiOk = kapiInit();')
assert start.index('gNubParent->retain();') < start.index('bool kapiOk = kapiInit();')
assert 'IOService *gpu = gNubParent;' in scan and 'gpu->getChildIterator(gIOServicePlane)' in scan
assert 'gNDpy = 0;' in initial and 'if (n == 0)' not in initial
assert initial.index('if (gNDpyBoot < 0) gNDpyBoot = gNDpy;') < initial.index('if (gNDpy <= 0')
assert 'nub->getClient()' in scan and 'nub->getProperty("nm-intel-panel")' in scan
assert 'nub->registerService(kIOServiceAsynchronous)' in scan
print("PASS: an empty boot records zero displays and the retained NVIDIA parent can rematch idle nubs")
