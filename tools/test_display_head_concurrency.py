#!/usr/bin/env python3
"""Run the production head monitor and flip wait with concurrent OS/KMS doubles.

The lock double follows Apple's IORecursiveLockSleep* contract: drop all
recursions for the wait and restore them on return. No kernel or display changes.
Two negative controls retain the old global wait or remove head reservation.
"""
from pathlib import Path
import argparse
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FB = (ROOT / 'kexts/NVRMFB/fb/nvrm-fb.cpp').read_text()
HEADER = (ROOT / 'kexts/NVRMFB/fb/nvrm-head-gate.h').read_text()


def method(name):
    start = FB.index(name)
    body = FB.index('{', start)
    depth, end = 1, body + 1
    while depth:
        depth += (FB[end] == '{') - (FB[end] == '}')
        end += 1
    return FB[start:end]


waiter = method('bool NVRMNVDAFramebuffer::waitFlipDone(').replace('NVRMNVDAFramebuffer::', '')
wake = method('void NVRMNVDAFramebuffer::flipWake(').replace('void NVRMNVDAFramebuffer::', 'static void ')
for name in ('reflip', 'setHeadActive', 'applyMode', 'switchMode', 'flipToMemory', 'headOff', 'setHeadPower', 'nvrmSetGamma'):
    body = method('NVRMNVDAFramebuffer::' + name + '(')
    assert 'NVRMHeadGate primary(startLock(), fPrimaryGate,' in body, name
    assert 'primary.valid()' in body, name
for name in ('cursorApply', 'setCursorImage'):
    assert 'NVRMHeadGate::waitForCursor(startLock(), fPrimaryGate);' in method('NVRMNVDAFramebuffer::' + name + '(')
stop = method('NVRMNVDAFramebuffer::stop(')
assert stop.index('fStopping = 1;') < stop.index('NVRMHeadGate primary(') < stop.index('unregisterVblankIntrCallback')
assert stop.index('NVRMHeadGate primary(') < stop.index('while (OSAddAtomic(0, &fCursorOutstanding))')
flip = method('NVRMNVDAFramebuffer::flipToMemory(')
assert flip.index('NVRMHeadGate primary(') < flip.index('gAllocations.size(')
assert flip.index('dupMemory(') < flip.index('submitConfig(') < flip.index('fFrontSurf = surf;')

fixture = r'''
#include <atomic>
#include <cassert>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <mutex>
#include <thread>
using Clock = std::chrono::steady_clock;
using SInt32 = int32_t;
using NvBool = bool;
using event_t = void *;
enum { THREAD_UNINT = 0, kMicrosecondScale = 1000, NVKMS_KAPI_LAYER_PRIMARY_IDX = 0, NV_TRUE = 1 };
static void *IOThreadSelf() { static thread_local char identity; return &identity; }
struct IORecursiveLock {
    std::mutex mutex;
    std::condition_variable condition;
    std::atomic<void *> owner{nullptr};
    unsigned depth = 0;
};
static IORecursiveLock management;
static IORecursiveLock *startLock() { return &management; }
static bool IORecursiveLockHaveLock(IORecursiveLock *lock) { return lock->owner.load() == IOThreadSelf(); }
static void IORecursiveLockLock(IORecursiveLock *lock) {
    if (!IORecursiveLockHaveLock(lock)) {
        lock->mutex.lock(); assert(lock->owner.load() == nullptr && !lock->depth);
        lock->owner = IOThreadSelf();
    }
    ++lock->depth;
}
static void IORecursiveLockUnlock(IORecursiveLock *lock) {
    assert(IORecursiveLockHaveLock(lock) && lock->depth);
    if (!--lock->depth) { lock->owner = nullptr; lock->mutex.unlock(); }
}
static uint64_t mach_absolute_time() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count();
}
static void clock_interval_to_deadline(uint32_t value, uint64_t scale, uint64_t *deadline) {
    *deadline = mach_absolute_time() + uint64_t(value) * scale;
}
static void IORecursiveLockSleepDeadline(IORecursiveLock *lock, void *, uint64_t deadline, unsigned) {
    assert(IORecursiveLockHaveLock(lock));
    const unsigned depth = lock->depth;
    lock->depth = 0; lock->owner = nullptr;
    std::unique_lock<std::mutex> hold(lock->mutex, std::adopt_lock);
    lock->condition.wait_until(hold, Clock::time_point(std::chrono::nanoseconds(deadline)));
    assert(!lock->depth && lock->owner.load() == nullptr);
    lock->owner = IOThreadSelf(); lock->depth = depth;
    hold.release();
}
static void IORecursiveLockSleep(IORecursiveLock *lock, void *event, unsigned kind) {
    IORecursiveLockSleepDeadline(lock, event, mach_absolute_time() + 1000000000ull, kind);
}
static void IORecursiveLockWakeup(IORecursiveLock *lock, void *, bool) { lock->condition.notify_all(); }
static void KeepManagementLockDeadline(IORecursiveLock *lock, void *, uint64_t deadline, unsigned) {
    assert(IORecursiveLockHaveLock(lock));
    std::this_thread::sleep_until(Clock::time_point(std::chrono::nanoseconds(deadline)));
}
static bool OSCompareAndSwapPtr(void *old, void *replacement, void *volatile *target) {
    if (*target != old) return false; *target = replacement; return true;
}
struct Management {
    Management() { IORecursiveLockLock(startLock()); }
    ~Management() { IORecursiveLockUnlock(startLock()); }
};
HEADER
static IORecursiveLock *sFlipWaitLock;
static volatile SInt32 sFlipWakeEv[8];
struct Kms {
    std::atomic<bool> pending[2]{};
    bool getFlipPendingStatus(void *, unsigned head, unsigned, bool *out) {
        *out = pending[head]; return true;
    }
};
struct FB {
    Kms *fKms; void *fDev = this; unsigned fHead;
    NVRMHeadGateState fPrimaryGate;
    FB(Kms &kms, unsigned head) : fKms(&kms), fHead(head) {}
    WAKE
    WAITER
};
template<typename F> static void await(F predicate) {
    const auto end = Clock::now() + std::chrono::seconds(2);
    while (!predicate()) { assert(Clock::now() < end && "concurrency rendezvous timed out"); std::this_thread::yield(); }
}
static void finish(Kms &kms, unsigned head) {
    kms.pending[head] = false; FB::flipWake(head);
}
int main() {
    // A secondary flip and a cursor can proceed while the first head waits.
    // A same-head mode change cannot reuse that head until completion.
    {
        Kms kms; FB heads[] = {{kms, 0}, {kms, 1}};
        std::atomic<unsigned> submitted{0};
        std::atomic<bool> cursor{false}, modeAttempted{false}, modeEntered{false};
        auto frame = [&](unsigned h) {
            Management outer, recursive;
            NVRMHeadGate primary(startLock(), heads[h].fPrimaryGate, false);
            assert(primary.valid()); kms.pending[h] = true;
            submitted.fetch_or(1u << h);
            assert(heads[h].waitFlipDone(1000000) && "a flip was blocked from reaching its completion rendezvous");
            assert(management.depth == 2 && heads[h].fPrimaryGate.owned());
        };
        std::thread first(frame, 0); await([&] { return submitted.load() == 1; });
        std::thread mode([&] {
            Management hold; modeAttempted = true;
            NVRMHeadGate primary(startLock(), heads[0].fPrimaryGate, true);
            assert(!kms.pending[0]); modeEntered = true;
        });
        std::thread second(frame, 1);
        std::thread mouse([&] {
            Management hold;
            NVRMHeadGate::waitForCursor(startLock(), heads[0].fPrimaryGate);
            cursor = true;
        });
        await([&] { return submitted.load() == 3 && cursor.load() && modeAttempted.load(); });
        assert(!modeEntered.load() && "mode transition entered a head whose flip is not complete");
        finish(kms, 0); finish(kms, 1);
        first.join(); second.join(); mouse.join(); mode.join();
        assert(modeEntered && !heads[0].fPrimaryGate.owner && !heads[1].fPrimaryGate.owner);
        puts("PASS: two heads submit concurrently; cursor progresses; same-head mode waits; recursive lock restored");
    }
    // Cursor/geometry changes wait for a real mode transition, but do not hold
    // management while waiting. Nested transition calls remain reentrant.
    {
        Kms kms; FB head(kms, 0); int geometry = 100;
        std::atomic<bool> began{false}, mouseAttempted{false}, mouseDone{false}, allocationDone{false};
        std::thread transition([&] {
            Management first, second;
            NVRMHeadGate primary(startLock(), head.fPrimaryGate, true);
            NVRMHeadGate nested(startLock(), head.fPrimaryGate, true);
            assert(head.fPrimaryGate.depth == 2 && head.fPrimaryGate.transitions == 2);
            NVRMHeadGate::waitForCursor(startLock(), head.fPrimaryGate); // same-thread reentry
            kms.pending[0] = true; began = true;
            assert(head.waitFlipDone(1000000)); geometry = 200;
        });
        await([&] { return began.load(); });
        std::thread mouse([&] {
            Management hold; mouseAttempted = true;
            NVRMHeadGate::waitForCursor(startLock(), head.fPrimaryGate);
            assert(geometry == 200); mouseDone = true;
        });
        await([&] { return mouseAttempted.load(); });
        std::thread allocation([&] { Management hold; allocationDone = true; });
        await([&] { return allocationDone.load(); });
        assert(!mouseDone && "cursor used geometry from an unfinished mode transition");
        finish(kms, 0);
        transition.join(); mouse.join(); allocation.join();
        assert(!head.fPrimaryGate.owner && !head.fPrimaryGate.depth && !head.fPrimaryGate.transitions);
        puts("PASS: cursor sees completed geometry; allocation proceeds; nested head reservation drains correctly");
    }
    // Stop first blocks new work and then drains the accepted head reservation.
    // Resources stay available to the accepted flip until completion is proven.
    {
        Kms kms; FB head(kms, 0);
        std::atomic<bool> began{false}, stopping{false}, drained{false}, resourcesLive{true};
        std::thread frame([&] {
            Management hold;
            NVRMHeadGate primary(startLock(), head.fPrimaryGate, false);
            kms.pending[0] = true; began = true;
            assert(head.waitFlipDone(1000000)); assert(resourcesLive);
        });
        await([&] { return began.load(); });
        std::thread stop([&] {
            Management hold; stopping = true;
            NVRMHeadGate primary(startLock(), head.fPrimaryGate, true);
            assert(!kms.pending[0]); resourcesLive = false; drained = true;
        });
        await([&] { return stopping.load(); });
        assert(!drained && resourcesLive);
        finish(kms, 0); frame.join(); stop.join();
        assert(drained && !head.fPrimaryGate.owner);
        puts("PASS: stop drains the accepted flip before resource teardown");
    }
    // A deadline still fails; unlocking must never stand in for completion.
    {
        Kms kms; FB head(kms, 0); Management hold;
        NVRMHeadGate primary(startLock(), head.fPrimaryGate, false);
        kms.pending[0] = true;
        assert(!head.waitFlipDone(2000)); assert(kms.pending[0]);
        assert(head.fPrimaryGate.owned() && management.depth == 1);
        puts("PASS: timeout retains head ownership and does not report an unfinished flip complete");
    }
}
'''


def run(directory, label, header, wait, sanitizer):
    cpp = directory / (label + '.cpp')
    exe = directory / label
    gate = directory / (label + '-gate.h')
    gate.write_text(header)
    cpp.write_text(fixture.replace('HEADER', '#include "' + gate.name + '"').replace('WAKE', wake).replace('WAITER', wait))
    subprocess.run(['clang++', '-std=c++17', '-O1', '-g', '-pthread', '-Wall', '-Wextra',
                    '-Wno-unused-function', '-Werror', '-fsanitize=' + sanitizer,
                    '-fno-sanitize-recover=all', str(cpp), '-o', str(exe)], check=True)
    return subprocess.run([str(exe)], text=True, capture_output=True, timeout=8)


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--sanitizer', choices=['address,undefined', 'thread'], default='address,undefined')
parser.add_argument('--positive-only', action='store_true')
args = parser.parse_args()
with tempfile.TemporaryDirectory(prefix='nvfb-heads-') as name:
    directory = Path(name)
    result = run(directory, 'production', HEADER, waiter, args.sanitizer)
    print(result.stdout, end='')
    assert result.returncode == 0, result.stderr
    if not args.positive_only:
        old_wait = waiter.replace('IORecursiveLockSleepDeadline(lock,', 'KeepManagementLockDeadline(lock,')
        assert old_wait != waiter
        no_reservation = HEADER.replace('while (state.owner && !state.owned())',
                                        'while (false && state.owner && !state.owned())')
        assert no_reservation != HEADER
        for label, header, wait in [('global-lock-held', HEADER, old_wait),
                                    ('head-reservation-removed', no_reservation, waiter)]:
            result = run(directory, label, header, wait, args.sanitizer)
            assert result.returncode != 0, 'negative control passed: ' + label
            print('PASS negative control rejected:', label)
