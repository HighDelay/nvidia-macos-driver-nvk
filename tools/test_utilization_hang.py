#!/usr/bin/env python3
"""Run the production sampler admission/cutoff against a controlled firmware clock."""
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT/'kexts/NVRM/NVRM.cpp').read_text()
start = source.index('        IOLockLock(fUtilizationLock);', source.index('IOReturn NVRM::callPlatformFunction('))
end = source.index('\n    }', start)
body = source[start:end]
limit = re.search(r'static const uint64_t kUtilHangNs = [^;]+;', source)[0]
fixture = r'''
#include <cstdint>
#include <cstdio>
enum { kIOReturnSuccess, kIOReturnIOError, kIOReturnNotReady, kGpuPmRunning };
using IOReturn = int;
struct Sample {};
static uint64_t clockNs, durationNs;
static bool answer = true;
static unsigned queries, locks, exits, errors;
static void IOLockLock(void *) { ++locks; }
static void IOLockUnlock(void *) { --locks; }
static int OSAddAtomic(int, int *p) { return *p; }
static uint64_t mach_absolute_time() { return clockNs; }
static void absolutetime_to_nanoseconds(uint64_t ns, uint64_t *out) { *out = ns; }
static void nv_xnu_pm_exit(int counted) { if (locks || counted != 1) ++errors; ++exits; }
static bool nvrm_query_utilization(unsigned, void **, Sample *) {
    if (locks != 1) ++errors;
    ++queries; clockNs += durationNs; return answer;
}
#define LOG(...) ((void)0)
@@LIMIT@@
struct Device {
    bool fUtilizationStopping = false, inactive = false, fAdapter = true;
    unsigned fPassDone = 2; int fGpuPm = kGpuPmRunning;
    void *fUtilizationLock = this, *fUtilizationState = nullptr;
    struct { unsigned gpu_id = 1; } fNv;
    bool isInactive() { return inactive; }
    IOReturn query(Sample *sample) {
        int counted = 1;
@@BODY@@
    }
};
static void check(bool ok, const char *message) {
    if (!ok) { ++errors; std::fprintf(stderr, "FAIL: %s\n", message); }
}
int main() {
    Sample sample;
    Device fast; durationNs = 1000000;
    check(fast.query(&sample) == kIOReturnSuccess && fast.query(&sample) == kIOReturnSuccess,
          "ordinary sampling keeps working");
    Device failed; answer = false;
    unsigned before = queries;
    check(failed.query(&sample) == kIOReturnIOError && failed.query(&sample) == kIOReturnIOError && queries == before+2,
          "fast firmware failures keep retrying");
    for (bool result : {false, true}) {
        Device hung; answer = result; durationNs = kUtilHangNs+1; before = queries;
        check(hung.query(&sample) == (result ? kIOReturnSuccess : kIOReturnIOError), "first hung read keeps its actual result");
        check(hung.query(&sample) == kIOReturnNotReady && queries == before+1,
              "a slow success or failure cannot repeat the firmware read");
    }
    Device boundary; answer = true; durationNs = kUtilHangNs; before = queries;
    check(boundary.query(&sample) == kIOReturnSuccess && boundary.query(&sample) == kIOReturnSuccess && queries == before+2,
          "the exact cutoff does not trip the strictly-longer policy");
    Device unavailable; unavailable.fAdapter = false; durationNs = kUtilHangNs*2; before = queries;
    check(unavailable.query(&sample) == kIOReturnNotReady && queries == before && !unavailable.fUtilizationStopping,
          "an unavailable adapter does not read firmware or manufacture a hang");
    check(locks == 0 && exits == 11, "every path releases the state lock and PM admission");
    std::printf("%u failures\n", errors);
    return errors ? 1 : 0;
}
'''.replace('@@LIMIT@@', limit)

with tempfile.TemporaryDirectory(prefix='utilization-cutoff-') as temporary:
    temporary = Path(temporary)
    for name, snippet in [('production', body), ('negative-control', body.replace('fUtilizationStopping = true;', '/* missing cutoff */'))]:
        cpp = temporary/(name+'.cpp'); binary = temporary/name
        cpp.write_text('#include <initializer_list>\n'+fixture.replace('@@BODY@@', snippet))
        subprocess.run(['xcrun','clang++','-std=c++17','-Wall','-Wextra','-Werror',str(cpp),'-o',str(binary)], check=True)
        result = subprocess.run([str(binary)], capture_output=True, text=True)
        if name == 'production':
            assert result.returncode == 0, result.stdout+result.stderr
        else:
            assert result.returncode != 0 and 'cannot repeat the firmware read' in result.stderr, result.stdout+result.stderr
        print('PASS: '+name)
