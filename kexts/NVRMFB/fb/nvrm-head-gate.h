/*
 * Copyright (c) 2026 NullMoth Systems.
 * SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
 */
#pragma once

// Protected by the display-management recursive lock. A head keeps its
// transaction reservation while IORecursiveLockSleep* drops every recursion of
// that lock. Other heads, cursor-only requests and allocation management can
// then run without changing the reserved head's primary surface or mode.
struct NVRMHeadGateState {
    void *owner = nullptr;
    unsigned depth = 0;
    unsigned transitions = 0;

    bool owned() const { return owner == IOThreadSelf(); }
};

class NVRMHeadGate {
    IORecursiveLock *lock;
    NVRMHeadGateState &state;
    bool transition;
public:
    NVRMHeadGate(IORecursiveLock *management, NVRMHeadGateState &head, bool changingMode)
        : lock(management), state(head), transition(changingMode)
    {
        if (!lock || !IORecursiveLockHaveLock(lock)) { lock = nullptr; return; }
        while (state.owner && !state.owned())
            IORecursiveLockSleep(lock, &state, THREAD_UNINT);
        state.owner = IOThreadSelf();
        ++state.depth;
        if (transition) ++state.transitions;
    }
    ~NVRMHeadGate()
    {
        if (!lock) return;
        if (transition) --state.transitions;
        if (!--state.depth) state.owner = nullptr;
        IORecursiveLockWakeup(lock, &state, false);
    }
    NVRMHeadGate(const NVRMHeadGate &) = delete;
    NVRMHeadGate &operator=(const NVRMHeadGate &) = delete;
    bool valid() const { return lock != nullptr; }

    // A primary flip leaves the cursor channel independent. A mode transition
    // must finish publishing its new geometry first. A reentrant request from
    // that transition's owner already holds its state protection.
    static void waitForCursor(IORecursiveLock *lock, NVRMHeadGateState &state)
    {
        while (state.transitions && !state.owned())
            IORecursiveLockSleep(lock, &state, THREAD_UNINT);
    }
};
