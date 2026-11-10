// SPDX-License-Identifier: BSD-3-Clause
// HighDelay: optional NVMTL loading compatibility for Apple's WebKit GPU service.
// The scoped Sandbox hook approach was informed by b00t0x/WhateverXe.
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wsign-conversion"
#pragma clang diagnostic ignored "-Wshadow"
#include <Headers/plugin_start.hpp>
#include <Headers/kern_api.hpp>
#pragma clang diagnostic pop
#include <IOKit/IORegistryEntry.h>
#include <sys/proc.h>
#include <sys/file.h>
#include <sys/vnode.h>
#include <sys/vnode_if.h>
#include <sys/stat.h>
#include <sys/sysctl.h>
#include <mach/kmod.h>
#include "policy.hpp"
#include "compatibility.hpp"

namespace {
const char *sandboxPaths[] = {"/System/Library/Extensions/Sandbox.kext/Contents/MacOS/Sandbox"};
KernelPatcher::KextInfo sandbox {"com.apple.security.sandbox", sandboxPaths, 1, {true}, {}, KernelPatcher::KextInfo::Unloaded};
proc_t (*currentProcess)();
const char *(*processIdentity)(proc_t);
int (*platformBinary)(proc_t);
int (*validSignature)(proc_t);
vnode_t (*fileVnode)(void *);
int (*vnodePath)(vnode_t, char *, int *);
using Open = int (*)(void *, vnode_t, void *, int);
using Map = int (*)(void *, void *, void *, int, int, uint64_t, int *);
Open originalOpen;
Map originalMap;
bool ready;
bool registered;
const char *status = "not initialized";
uint64_t readGrants, mapGrants, identityRefusals, trustRefusals, protectionRefusals;

// No logging, object allocation, file opens or PID caches in the MAC callbacks.
// Attribute lookup concerns only a matched, already-referenced vnode after the
// current process passes its kernel signing checks. Errors retain the denial.
bool trusted(vnode_t vnode, NVWebKitPolicy::Resource kind) {
    auto process = currentProcess();
    if (!process || !NVWebKitPolicy::client(processIdentity(process),
            platformBinary(process) != 0, validSignature(process) != 0)) {
        __atomic_fetch_add(&identityRefusals, 1, __ATOMIC_RELAXED);
        return false;
    }
    struct vnode_attr attributes;
    VATTR_INIT(&attributes);
    VATTR_WANTED(&attributes, va_uid);
    VATTR_WANTED(&attributes, va_mode);
    if (kind == NVWebKitPolicy::Resource::Runtime) VATTR_WANTED(&attributes, va_flags);
    else VATTR_WANTED(&attributes, va_rdev);
    if (vnode_getattr(vnode, &attributes, vfs_context_current()) ||
        !VATTR_IS_SUPPORTED(&attributes, va_uid) || attributes.va_uid != 0 ||
        !VATTR_IS_SUPPORTED(&attributes, va_mode)) goto refused;
    if (kind == NVWebKitPolicy::Resource::Runtime) {
        if (vnode_vtype(vnode) != VREG || (attributes.va_mode & (S_IWGRP | S_IWOTH)) ||
            !VATTR_IS_SUPPORTED(&attributes, va_flags) || !(attributes.va_flags & SF_RESTRICTED)) goto refused;
    } else if (vnode_vtype(vnode) != VCHR || !VATTR_IS_SUPPORTED(&attributes, va_rdev) ||
               major((uint32_t)attributes.va_rdev) != 3 || minor((uint32_t)attributes.va_rdev) != 2) goto refused;
    return true;
refused:
    __atomic_fetch_add(&trustRefusals, 1, __ATOMIC_RELAXED);
    return false;
}

NVWebKitPolicy::Resource resource(vnode_t vnode) {
    if (!vnode) return NVWebKitPolicy::Resource::None;
    char path[1024];
    int length = sizeof(path);
    if (vnodePath(vnode, path, &length) || length <= 0 || length > (int)sizeof(path) ||
        !memchr(path, 0, (size_t)length)) return NVWebKitPolicy::Resource::None;
    return NVWebKitPolicy::resource(path);
}

int checkOpen(void *credentials, vnode_t vnode, void *label, int mode) {
    const int result = originalOpen(credentials, vnode, label, mode);
    if (!__atomic_load_n(&ready, __ATOMIC_ACQUIRE) || result != EPERM || mode != FREAD) return result;
    const auto kind = resource(vnode);
    if (!NVWebKitPolicy::read(result, mode, kind) || !trusted(vnode, kind)) return result;
    __atomic_fetch_add(&readGrants, 1, __ATOMIC_RELAXED);
    return 0;
}

int checkMap(void *credentials, void *file, void *label, int prot, int flags, uint64_t offset, int *maxprot) {
    const int result = originalMap(credentials, file, label, prot, flags, offset, maxprot);
    if (!__atomic_load_n(&ready, __ATOMIC_ACQUIRE) || result != EPERM) return result;
    auto vnode = fileVnode(file);
    const auto kind = resource(vnode);
    if (!NVWebKitPolicy::map(result, prot, flags, kind)) return result;
    // Never raise rights removed by this or an earlier MAC policy.
    if (!maxprot || ((*maxprot & prot) != prot)) {
        __atomic_fetch_add(&protectionRefusals, 1, __ATOMIC_RELAXED);
        return result;
    }
    if (!trusted(vnode, kind)) return result;
    __atomic_fetch_add(&mapGrants, 1, __ATOMIC_RELAXED);
    return 0;
}

template<typename T> T symbol(KernelPatcher &patcher, const char *name) {
    auto address = patcher.solveSymbol(KernelPatcher::KernelID, name);
    patcher.clearError();
    return reinterpret_cast<T>(address);
}

void kernelReady(void *, KernelPatcher &patcher) {
    if (!registered) return;
    // Apple retains these MAC callback signatures across Darwin 24 and 25.
    // Symbols and relocatable hook prefixes are checked separately at runtime.
    static_assert(KernelVersion::Sequoia == NVWebKitCompatibility::Sequoia);
    static_assert(KernelVersion::Tahoe == NVWebKitCompatibility::Tahoe);
    if (!NVWebKitCompatibility::kernel(getKernelVersion())) {
        status = "inactive: requires Sequoia or Tahoe";
        return;
    }
    currentProcess = symbol<decltype(currentProcess)>(patcher, "_current_proc");
    processIdentity = symbol<decltype(processIdentity)>(patcher, "_csproc_get_identity");
    platformBinary = symbol<decltype(platformBinary)>(patcher, "_csproc_get_platform_binary");
    validSignature = symbol<decltype(validSignature)>(patcher, "_cs_valid");
    fileVnode = symbol<decltype(fileVnode)>(patcher, "_fg_get_vnode");
    vnodePath = symbol<decltype(vnodePath)>(patcher, "_vn_getpath");
    status = currentProcess && processIdentity && platformBinary && validSignature && fileVnode && vnodePath ?
        "kernel symbols ready" : "inactive: missing kernel symbols";
}

void sandboxReady(void *, KernelPatcher &patcher, size_t index, mach_vm_address_t base, size_t size) {
    if (index != sandbox.loadIndex || strcmp(status, "kernel symbols ready")) return;
    const auto open = patcher.solveSymbol(index, "_hook_vnode_check_open", base, size);
    patcher.clearError();
    const auto map = patcher.solveSymbol(index, "_hook_file_check_mmap", base, size);
    patcher.clearError();
    using namespace NVWebKitCompatibility;
    if (!inImage(open, base, size) || !inImage(map, base, size)) {
        status = "inactive: missing or out-of-range Sandbox hooks";
        return;
    }
    const size_t openLength = prefix(reinterpret_cast<const uint8_t *>(open), Lookahead, Hook::Open);
    const size_t mapLength = prefix(reinterpret_cast<const uint8_t *>(map), Lookahead, Hook::Map);
    if (!openLength || !mapLength ||
        Disassembler::quickInstructionSize(open, LongJump) != openLength ||
        Disassembler::quickInstructionSize(map, LongJump) != mapLength) {
        status = "inactive: unreviewed Sandbox hooks";
        return;
    }
    KernelPatcher::RouteRequest routes[] = {
        {"_hook_vnode_check_open", checkOpen, originalOpen},
        {"_hook_file_check_mmap", checkMap, originalMap}
    };
    routes[0].from = open;
    routes[1].from = map;
    bool installed = patcher.routeMultipleLong(index, routes, arrsize(routes), base, size);
    patcher.clearError();
    __atomic_store_n(&ready, installed && originalOpen && originalMap, __ATOMIC_RELEASE);
    status = ready ? "ready: scoped WebKit GPU compatibility" : "inactive: hook installation failed";
    IORegistryEntry::getRegistryRoot()->setProperty("NVWebKitFixupReady", ready);
    SYSLOG("nvwk", "%s", status);
}

int diagnostics SYSCTL_HANDLER_ARGS {
    (void)oidp; (void)arg1; (void)arg2;
    char text[512];
    snprintf(text, sizeof(text), "%s; read=%llu map=%llu identity_refused=%llu trust_refused=%llu protection_refused=%llu",
        status, (unsigned long long)__atomic_load_n(&readGrants, __ATOMIC_RELAXED),
        (unsigned long long)__atomic_load_n(&mapGrants, __ATOMIC_RELAXED),
        (unsigned long long)__atomic_load_n(&identityRefusals, __ATOMIC_RELAXED),
        (unsigned long long)__atomic_load_n(&trustRefusals, __ATOMIC_RELAXED),
        (unsigned long long)__atomic_load_n(&protectionRefusals, __ATOMIC_RELAXED));
    return SYSCTL_OUT(req, text, strlen(text) + 1);
}
SYSCTL_PROC(_debug, OID_AUTO, nvwkfixup, CTLTYPE_STRING | CTLFLAG_RD | CTLFLAG_KERN,
    nullptr, 0, diagnostics, "A", "Scoped NVMTL WebKit GPU loading status");

void start() {
    sysctl_register_oid(&sysctl__debug_nvwkfixup);
    status = "waiting for kernel and Sandbox";
    if (lilu.onPatcherLoad(kernelReady) != LiluAPI::Error::NoError ||
        lilu.onKextLoad(&sandbox, 1, sandboxReady) != LiluAPI::Error::NoError) {
        status = "inactive: callback registration failed";
        return;
    }
    registered = true;
}
const char *disable[] = {"-nvwkoff"};
const char *debug[] = {"-nvwkdbg"};
}

PluginConfiguration ADDPR(config) {
    xStringify(PRODUCT_NAME), parseModuleVersion(xStringify(MODULE_VERSION)), LiluAPI::AllowNormal,
    disable, arrsize(disable), debug, arrsize(debug), nullptr, 0,
    KernelVersion::Sequoia, KernelVersion::Tahoe, start
};
extern "C" kern_return_t NVWebKitFixup_kern_start(kmod_info_t *, void *);
extern "C" kern_return_t NVWebKitFixup_kern_stop(kmod_info_t *, void *);
extern "C" { KMOD_EXPLICIT_DECL(com.highdelay.NVWebKitFixup, xStringify(MODULE_VERSION), NVWebKitFixup_kern_start, NVWebKitFixup_kern_stop) }
