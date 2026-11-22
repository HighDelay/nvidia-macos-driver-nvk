#!/usr/bin/env python3
"""The D3cold rules (kexts/NVRM/nv_rtd3_policy.h): off unless asked for, only when the GPU is idle with its firmware
suspended, no display lit and no client; never touched while its resource is off. Compiled and run on a table.

usage: test_nv_rtd3_policy.py [repo root]"""
import pathlib, shutil, subprocess, sys, tempfile
root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parents[1])
fails = 0
def check(n, ok, d=''):
    global fails
    print(('ok   ' if ok else 'FAIL ') + n + (f' ({d})' if d else '')); fails += 0 if ok else 1
hdr = root / 'kexts/NVRM/nv_rtd3_policy.h'
check('the policy header exists', hdr.exists())
if hdr.exists() and shutil.which('cc'):
    src = r'''#include <stdio.h>
#include "nv_rtd3_policy.h"
int main(void) {
 struct nv_rtd3 s = {1, NV_RTD3_ON, 1};
 printf("%d\n", nv_rtd3_may_power_off(&s, 1, 0, 0, 60));
 printf("%d\n", nv_rtd3_may_power_off(&s, 1, 0, 0, 59));
 printf("%d\n", nv_rtd3_may_power_off(&s, 0, 0, 0, 600));
 printf("%d\n", nv_rtd3_may_power_off(&s, 1, 1, 0, 600));
 printf("%d\n", nv_rtd3_may_power_off(&s, 1, 0, 1, 600));
 struct nv_rtd3 off = {0, NV_RTD3_ON, 1}; printf("%d\n", nv_rtd3_may_power_off(&off, 1, 0, 0, 600));
 struct nv_rtd3 nores = {1, NV_RTD3_ON, 0}; printf("%d\n", nv_rtd3_may_power_off(&nores, 1, 0, 0, 600));
 printf("%d\n", nv_rtd3_gpu_reachable(&s));
 s.state = NV_RTD3_OFF; printf("%d %d\n", nv_rtd3_gpu_reachable(&s), nv_rtd3_may_power_off(&s, 1, 0, 0, 600));
 nv_rtd3_on_done(&s, 0); printf("%d\n", nv_rtd3_gpu_reachable(&s));
 nv_rtd3_on_done(&s, 1); printf("%d\n", nv_rtd3_gpu_reachable(&s));
 return 0; }
'''
    with tempfile.TemporaryDirectory() as d:
        pathlib.Path(d, 't.c').write_text(src)
        r = subprocess.run(['cc', '-I', str(hdr.parent), '-o', d + '/t', d + '/t.c'], capture_output=True, text=True)
        check('the header compiles', r.returncode == 0, r.stderr[-200:])
        if r.returncode == 0:
            o = subprocess.run([d + '/t'], capture_output=True, text=True).stdout.split('\n')
            check('idle 60 s, firmware suspended, no head, no client: may power off', o[0] == '1')
            check('59 s of idle is not enough', o[1] == '0')
            check('with the firmware not suspended it never powers off', o[2] == '0')
            check('a lit display head blocks it', o[3] == '0')
            check('an open client blocks it', o[4] == '0')
            check('without the nvrtd3 switch it never powers off', o[5] == '0')
            check('without a power resource path it never powers off', o[6] == '0')
            check('an on GPU is reachable', o[7] == '1')
            check('after _OFF the GPU is unreachable and cannot be powered off again', o[8] == '0 0')
            check('a failed _ON keeps the GPU marked unreachable', o[9] == '0')
            check('after _ON the GPU is reachable again', o[10] == '1')

if hdr.exists() and shutil.which('cc'):
    seq = r"""#include <stdio.h>
#include <string.h>
#include "nv_rtd3_policy.h"
static char log[256]; static int heads, clients, off_ok = 1, on_ok = 1, standby_ok = 1, resume_ok = 1;
static void add(const char *w) { strcat(log, w); strcat(log, " "); }
static int h(void *c) { (void)c; return heads; } static int cl(void *c) { (void)c; return clients; }
static int su(void *c) { (void)c; add("suspend"); return standby_ok; }
static int re(void *c) { (void)c; add("resume"); return resume_ok; }
static int off(void *c) { (void)c; add("acpi_off"); return off_ok; } static int on(void *c) { (void)c; add("acpi_on"); return on_ok; }
int main(void) {
 struct nv_rtd3_ops o = { 0, h, cl, su, re, off, on };
 struct nv_rtd3 s = {1, NV_RTD3_ON, 1}; unsigned idle = 0; int r = 0;
 for (int i = 0; i < 59; i++) r |= nv_rtd3_tick(&s, &o, &idle, 1);
 printf("A %d [%s]\n", r, log);
 r = nv_rtd3_tick(&s, &o, &idle, 1); printf("B %d [%s] state %d\n", r, log, s.state);
 log[0] = 0; r = nv_rtd3_tick(&s, &o, &idle, 1); printf("C %d [%s]\n", r, log);
 clients = 1; r = nv_rtd3_tick(&s, &o, &idle, 1); printf("D %d [%s] state %d\n", r, log, s.state);
 clients = 0; log[0] = 0; for (int i = 0; i < 60; i++) nv_rtd3_tick(&s, &o, &idle, 1); printf("E [%s] state %d\n", log, s.state);
 log[0] = 0; on_ok = 0; heads = 1; r = nv_rtd3_tick(&s, &o, &idle, 1); printf("F %d [%s] state %d\n", r, log, s.state);
 on_ok = 1; log[0] = 0; r = nv_rtd3_tick(&s, &o, &idle, 1); printf("G %d [%s] state %d\n", r, log, s.state);
 heads = 0; off_ok = 0; log[0] = 0; for (int i = 0; i < 60; i++) r = nv_rtd3_tick(&s, &o, &idle, 1); printf("H %d [%s] state %d\n", r, log, s.state);
 struct nv_rtd3 d = {0, NV_RTD3_ON, 1}; unsigned i2 = 0; log[0] = 0; for (int i = 0; i < 600; i++) nv_rtd3_tick(&d, &o, &i2, 1); printf("I [%s]\n", log);
 standby_ok = 0; log[0] = 0; idle = 59; r = nv_rtd3_tick(&s, &o, &idle, 1);
 printf("J %d [%s] state %d\n", r, log, s.state);
 standby_ok = 1; on_ok = 0; log[0] = 0; idle = 59; r = nv_rtd3_tick(&s, &o, &idle, 1);
 printf("K %d [%s] state %d\n", r, log, s.state);
 on_ok = 1; resume_ok = 0; log[0] = 0; r = nv_rtd3_wake(&s, &o, &idle);
 printf("L %d [%s] state %d\n", r, log, s.state);
 return 0; }
"""
    with tempfile.TemporaryDirectory() as d:
        pathlib.Path(d, 's.c').write_text(seq)
        r = subprocess.run(['cc', '-I', str(hdr.parent), '-o', d + '/s', d + '/s.c'], capture_output=True, text=True)
        check('the call-sequence harness compiles', r.returncode == 0, r.stderr[-200:])
        if r.returncode == 0:
            o = {l[0]: l[2:] for l in subprocess.run([d + '/s'], capture_output=True, text=True).stdout.strip().split('\n')}
            check('59 idle seconds do nothing', o['A'] == '0 []')
            check('the 60th suspends the driver, then switches the resource off, in that order', o['B'] == '1 [suspend acpi_off ] state 1')
            check('while off and idle nothing is called', o['C'] == '0 []')
            check('a client while off switches the resource on first and resumes after it', o['D'] == '2 [acpi_on resume ] state 0')
            check('after waking, another 60 idle seconds powers off again', o['E'] == '[suspend acpi_off ] state 1')
            check('a failed _ON leaves the GPU off and does not resume it', o['F'] == '-2 [acpi_on ] state 1')
            check('the next try that works resumes', o['G'] == '2 [acpi_on resume ] state 0')
            check('a failed _OFF requires _ON before attempting recovery', o['H'] == '-1 [suspend acpi_off acpi_on resume ] state 0')
            check('with the switch off nothing is ever called', o['I'] == '[]')
            check('failed RM standby never invokes _OFF', o['J'] == '-3 [suspend ] state 0')
            check('failed recovery keeps the GPU unreachable', o['K'] == '-2 [suspend acpi_off acpi_on ] state 1')
            check('RM resume failure is distinct from a failed ACPI _ON', o['L'] == '-4 [acpi_on resume ] state 0')
# Compile the production admission and system-power entry points with a real
# mutex. A client arriving during standby must observe the completed transition.
src = (hdr.parent / 'NVRM.cpp').read_text()

def method(marker):
    start = src.index(marker)
    return src[start:src.index('\n}\n', start) + 3]

gate_fixture = r'''
#include <atomic>
#include <cassert>
#include <cstdint>
#include <condition_variable>
#include <mutex>
#include <thread>
#include <vector>
#include "nv_rtd3_policy.h"
using IOReturn = int;
using SInt32 = int;
struct IOService {};
using IOLock = std::mutex;
using thread_call_param_t = void *;
static void IOLockLock(IOLock *lock) { lock->lock(); }
static void IOLockUnlock(IOLock *lock) { lock->unlock(); }
static bool IOLockTryLock(IOLock *lock) { return lock->try_lock(); }
static void IOLockWakeup(IOLock *, void *, bool) {}
static int OSAddAtomic(int increment, int *target) { int previous = *target; *target += increment; return previous; }
static void nv_xnu_power_refresh() {}
enum { kIOReturnSuccess, kIOReturnBadArgument, kIOReturnNotReady, kIOPMAckImplied };
enum { kNvrmRuntimeRelease, kNvrmRuntimeWake, kNvrmRuntimeTry, kSecondScale };
static void clock_interval_to_deadline(unsigned, unsigned, uint64_t *deadline) { *deadline = 0; }
static bool thread_call_enter_delayed(void *, uint64_t) { return false; }
#define LOG(...) ((void)0)
enum Event { Standby, Off, On, Resume };

class NVRM {
public:
    enum { kGpuPmRunning, kGpuPmSuspended, kGpuPmLost };
    IOLock transition;
    IOLock *fRtd3Lock = &transition;
    nv_rtd3 fRtd3 = {1, NV_RTD3_ON, 1};
    int fShutdownHandled = 0, fGpuPm = kGpuPmRunning;
    unsigned fRtd3Users = 0, fRtd3Idle = 59, fRtd3Polls = 1;
    bool fAdapter = true, fRtd3Sleeping = false, fRtd3Stopping = false;
    void *fRtd3Call = nullptr;
    std::vector<Event> events;
    bool onOkay = true;

    std::mutex barrier;
    std::condition_variable ready;
    bool pauseStandby = false, inStandby = false, finishStandby = false;
    bool gpuSuspend(bool runtime = false) {
        events.push_back(Standby);
        if (runtime && pauseStandby) {
            std::unique_lock guard(barrier);
            inStandby = true;
            ready.notify_all();
            assert(ready.wait_for(guard, std::chrono::seconds(5), [&] { return finishStandby; }));
        }
        fGpuPm = kGpuPmSuspended;
        return true;
    }
    bool gpuResume() {
        assert(nv_rtd3_gpu_reachable(&fRtd3));
        events.push_back(Resume);
        fGpuPm = kGpuPmRunning;
        return true;
    }
    nv_rtd3_ops rtd3Ops() {
        return {this, [](void *) { return 0; },
            [](void *p) { return int(static_cast<NVRM *>(p)->fRtd3Users); },
            [](void *p) { return int(static_cast<NVRM *>(p)->gpuSuspend(true)); },
            [](void *p) { return int(static_cast<NVRM *>(p)->gpuResume()); },
            [](void *p) { static_cast<NVRM *>(p)->events.push_back(Off); return 1; },
            [](void *p) {
                auto *rm = static_cast<NVRM *>(p);
                rm->events.push_back(On);
                return int(rm->onOkay);
            }};
    }
    void setProperty(const char *, const char *) {}
    IOReturn rtd3Demand(uintptr_t);
    IOReturn setPowerState(unsigned long, IOService *);
    static void rtd3Poll(thread_call_param_t, thread_call_param_t);
};
''' + method('IOReturn NVRM::rtd3Demand(') + method('IOReturn NVRM::setPowerState(') + method('void NVRM::rtd3Poll(') + r'''
int main() {
    NVRM rm;
    rm.pauseStandby = true;
    std::thread monitor(NVRM::rtd3Poll, &rm, nullptr);
    {
        std::unique_lock guard(rm.barrier);
        assert(rm.ready.wait_for(guard, std::chrono::seconds(5), [&] { return rm.inStandby; }));
    }
    assert(rm.rtd3Demand(kNvrmRuntimeTry) == kIOReturnNotReady);
    std::atomic<bool> admitting = false, admitted = false;
    std::thread client([&] {
        admitting = true;
        assert(rm.rtd3Demand(kNvrmRuntimeWake) == kIOReturnSuccess);
        admitted = true;
    });
    while (!admitting) std::this_thread::yield();
    assert(!admitted);
    {
        std::lock_guard guard(rm.barrier);
        rm.finishStandby = true;
        rm.ready.notify_all();
    }
    monitor.join();
    client.join();
    assert((rm.events == std::vector<Event>{Standby, Off, On, Resume}));
    assert(rm.fRtd3Users == 1 && rm.fGpuPm == NVRM::kGpuPmRunning);
    rm.fRtd3Idle = 59;
    NVRM::rtd3Poll(&rm, nullptr);
    assert(rm.events.size() == 4 && rm.fRtd3Idle == 0);
    assert(rm.rtd3Demand(kNvrmRuntimeRelease) == kIOReturnSuccess);

    rm.events.clear();
    rm.pauseStandby = false;
    rm.fRtd3Idle = 59;
    NVRM::rtd3Poll(&rm, nullptr);
    rm.setPowerState(0, nullptr);
    assert(rm.rtd3Demand(kNvrmRuntimeWake) == kIOReturnNotReady);
    assert((rm.events == std::vector<Event>{Standby, Off}));
    rm.onOkay = false;
    rm.setPowerState(1, nullptr);
    assert((rm.events == std::vector<Event>{Standby, Off, On}));
    assert(!nv_rtd3_gpu_reachable(&rm.fRtd3) && rm.fGpuPm == NVRM::kGpuPmSuspended);
    rm.onOkay = true;
    assert(rm.rtd3Demand(kNvrmRuntimeWake) == kIOReturnSuccess);
    assert((rm.events == std::vector<Event>{Standby, Off, On, On, Resume}));
}
'''
with tempfile.TemporaryDirectory() as d:
    temporary = pathlib.Path(d)
    fixture = temporary / 'rtd3.cpp'
    fixture.write_text(gate_fixture)
    binary = temporary / 'rtd3'
    subprocess.run(['clang++', '-std=c++17', '-pthread', '-Wall', '-Werror',
                    '-I', str(hdr.parent), str(fixture), '-o', str(binary)], check=True)
    subprocess.run([binary], check=True, timeout=15)
print('PASS: concurrent admission, idle hold and system wake from runtime D3cold')
print(f'{fails} failed' if fails else 'all passed')
sys.exit(1 if fails else 0)
