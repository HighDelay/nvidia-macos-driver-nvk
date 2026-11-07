#!/usr/bin/env python3
"""Check decoder registration policy and IOKit cleanup without opening a GPU."""
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "plugin/NVMTLDevice.m").read_text()
START = SOURCE.index("static bool nvmtl_is_decode_service(")
END = SOURCE.index("static void nvmtl_nvdec_register(", START)

HARNESS = r'''
#include <dispatch/dispatch.h>
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
typedef uint32_t io_service_t, io_connect_t;
typedef int IOReturn;
#define IO_OBJECT_NULL 0
#define kIOMainPortDefault 0
#define kIOReturnSuccess 0
#define kIOReturnNotFound 1
static int scenario, queries, opens, closes, releases, matches, logs;
static int program_argc = 2;
static char *argument_storage[6] = {"VideoApp", "--type=gpu-process", NULL, NULL, NULL, NULL};
static char **program_argv = argument_storage;
static int *_NSGetArgc(void) { return &program_argc; }
static char ***_NSGetArgv(void) { return &program_argv; }
// exec lays the argument strings out back to back; Gecko's CheckArg then shifts the pointer array
static char packed_arguments[256];
static void gecko_arguments(const char *type, const char *flag, int removed) {
    const char *args[5] = {"plugin-container", flag, "1", "4", type};
    char *p = packed_arguments;
    for (int i = 0; i < 5; i++) { strcpy(p, args[i]); argument_storage[i] = p; p += strlen(p) + 1; }
    program_argc = 5;
    for (int r = 0; r < removed; r++) {
        for (int i = 1; i < 5; i++) argument_storage[i] = argument_storage[i + 1];
    }
}
static bool nvmtl_is_sandboxed(void) { queries++; return scenario != 0; }
static void nvlog(const char *fmt, ...) { logs++; }
#undef mach_task_self
static unsigned mach_task_self(void) { return 99; }
static void *IOServiceMatching(const char *name) {
    assert(!strcmp(name, "NVAccel")); return (void *)1;
}
static io_service_t IOServiceGetMatchingService(unsigned port, void *match) {
    assert(!port && match == (void *)1); matches++;
    return scenario == 2 ? 0 : 17;
}
static IOReturn IOServiceOpen(io_service_t service, unsigned task, uint32_t type,
                             io_connect_t *out) {
    assert(service == 17 && task == 99);
    assert(type == 0x4E560000u + (unsigned)opens++);
    assert(*out == 0);
    if ((scenario == 3 && type == 0x4E560000u) ||
        (scenario == 4 && type == 0x4E560001u)) return 2;
    if (scenario != 6) *out = type == 0x4E560000u ? 101 : 102;
    return 0;
}
static void IOServiceClose(io_connect_t port) {
    assert(port == 101 || port == 102); closes++;
}
static void IOObjectRelease(io_service_t service) { assert(service == 17); releases++; }
PRODUCTION
int main(int argc, char **argv) {
    scenario = atoi(argv[1]);
    const char *name = scenario == 1 ? "VTDecoderXPCService.arm64" : "Discord Helper";
    if (scenario == 7) name = "VTDecoderXPCServic";
    if (scenario == 8) name = NULL;
    if (scenario == 0 || scenario == 1 || scenario == 9 || scenario == 10) program_argc = 1;
    if (scenario == 9) name = "QuickTime Player";
    if (scenario == 10) name = "Safari";
    if (scenario == 11) argument_storage[1] = "--type=renderer";
    if (scenario == 12) argument_storage[1] = "--type=gpu-process-extra";
    if (scenario == 13) argument_storage[1] = "--other=--type=gpu-process";
    if (scenario == 14) { program_argc = 0; program_argv = NULL; }
    if (scenario == 15) argument_storage[1] = NULL;
    if (scenario == 16) { program_argc = 3; argument_storage[1] = "--"; argument_storage[2] = "--type=gpu-process"; }
    if (scenario == 17) { program_argc = 1; argument_storage[0] = "--type=gpu-process"; }
    if (scenario == 18) name = "Custom Browser Helper";
    if (scenario == 19) { program_argc = 3; argument_storage[1] = "--other=1"; argument_storage[2] = "--type=gpu-process"; }
    if (scenario == 20) program_argc = -1;
    if (scenario == 21) { program_argc = 3; argument_storage[2] = "--type=renderer"; }
    if (scenario == 22) { program_argc = 3; argument_storage[1] = "--type=renderer"; argument_storage[2] = "--type=gpu-process"; }
    if (scenario >= 23 && scenario <= 36) {
        name = "plugin-container";
        const char *type = scenario == 24 || scenario == 29 ? "gpu" : scenario == 25 ? "tab" :
                           scenario == 26 ? "utility" : "rdd";
        gecko_arguments(type, scenario == 27 ? "-appDir" : "-parentPid",
                        scenario == 28 ? 2 : scenario == 29 ? 4 : scenario == 30 ? 2 : 0);
        if (scenario == 30) { strcpy(argument_storage[0] + strlen(argument_storage[0]) + 1, "-parentpid"); }
        if (scenario >= 31 && scenario <= 34) {
            const char *parent = scenario == 31 ? "0" : scenario == 32 ? "-1" : scenario == 33 ? "x" : "2147483648";
            char *parent_slot=argument_storage[2];memmove(parent_slot+strlen(parent)+1,argument_storage[3],strlen(argument_storage[3])+strlen(argument_storage[4])+2);strcpy(parent_slot,parent);
        }
        if (scenario == 35) { program_argc=4097; }
        if (scenario == 36) { argument_storage[0]=(char *)(uintptr_t)1; }
    }
    if (scenario < 23 && program_argv && program_argc > 0) {
        // the other scenarios get exec's layout too, so the Gecko walk reads exactly these strings
        char *p = packed_arguments;
        for (int i = 0; i < program_argc && i < 6; i++) {
            const char *a = argument_storage[i] ? argument_storage[i] : "";
            strcpy(p, a); if (argument_storage[i]) argument_storage[i] = p; p += strlen(a) + 1;
        }
    }
    bool expected = scenario == 0 || scenario == 1 || scenario == 5 || scenario == 7 ||
                    scenario == 8 || scenario == 18 || scenario == 19 || scenario == 22 ||
                    scenario == 23 || scenario == 24 || scenario == 28 || scenario == 29;
    assert(nvmtl_can_register_decoder(name) == expected);
    assert(nvmtl_can_register_decoder(name) == expected); // both codec registrations share one check
    unsigned scope=!name?0u:nvmtl_is_decode_service(name)?1u:(nvmtl_gecko_decoder_process_type()==1?(expected?1u:0u):(2u|(expected?1u:0u)));
    assert(nvmtl_nvencvt_scope(name,expected)==scope);
    assert(nvmtl_nvencvt_scope("WindowServer",true)==0);
    assert(nvmtl_nvencvt_scope(NULL,true)==0);
    assert(nvmtl_nvencvt_scope("VTEncoderXPCService",true)==2);
    assert(nvmtl_nvencvt_scope("VTEncoderXPCService.x86_64",false)==2);
    assert(nvmtl_nvencvt_scope("VTDecoderXPCService",false)==1);
    setenv("NVMTL_NO_NVDEC","1",1);
    assert(nvmtl_nvencvt_scope(name,expected)==(scope&~1u));
    unsetenv("NVMTL_NO_NVDEC");setenv("NVMTL_NO_NVENC","1",1);
    assert(nvmtl_nvencvt_scope(name,expected)==(scope&~2u));
    setenv("NVMTL_NO_NVDEC","1",1);assert(!nvmtl_nvencvt_scope(name,expected));
    unsetenv("NVMTL_NO_NVENC");unsetenv("NVMTL_NO_NVDEC");
    assert(!nvmtl_video_pci_has_codecs(0x1f97)&&!nvmtl_video_pci_has_codecs(0x1f98)&&!nvmtl_video_pci_has_codecs(0x1f9c));
    assert(nvmtl_video_pci_has_codecs(0x1f99)&&nvmtl_video_pci_has_codecs(0x1f9f));
    assert(nvmtl_video_pci_has_codecs(0x1f06)&&nvmtl_video_pci_has_codecs(0x2484)&&nvmtl_video_pci_has_codecs(0x2684)&&nvmtl_video_pci_has_codecs(0x2b85));
    if (scenario <= 1 || (scenario >= 9 && scenario <= 17) || (scenario >= 20 && scenario <= 21) ||
        (scenario >= 25 && scenario <= 27) || (scenario >= 30 && scenario <= 36)) {
        assert(!opens && !closes && !matches && !releases && !logs);
        assert(queries == (scenario == 1 ? 0 : 2));
    } else {
        assert(matches == 1 && logs == 1);
        if (scenario == 2) assert(!opens && !closes && !releases);
        else if (scenario == 3) assert(opens == 1 && !closes && releases == 1);
        else if (scenario == 4) assert(opens == 2 && closes == 1 && releases == 1);
        else if (scenario == 6) assert(opens == 2 && !closes && releases == 1);
        else assert(opens == 2 && closes == 2 && releases == 1);
    }
    puts("PASS");
}
'''


def main():
    cases = (
        "unsandboxed client needs no bridge probe",
        "suffixed decode service bypasses the client restriction",
        "absent accelerator leaves decoding to the service",
        "denied control connection never opens the GPU connection",
        "denied GPU connection closes the successful control connection",
        "sandboxed GPU client registers after both bridge opens succeed",
        "success without connection handles is refused",
        "partial service name does not bypass the access check",
        "null process name still requires bridge access",
        "QuickTime keeps service decoding even when the bridge would be accessible",
        "native sandboxed apps keep service decoding",
        "Chromium renderer receives no GPU-process exception",
        "GPU process type must match the full value",
        "GPU type text inside another argument does not qualify",
        "missing process arguments do not qualify",
        "null argument does not qualify",
        "a positional GPU type after -- does not qualify",
        "an executable name is not a process-type argument",
        "custom Chromium helper names still qualify",
        "other arguments may precede the GPU process type",
        "invalid argument count does not qualify",
        "last process-type switch wins when it selects a renderer",
        "last process-type switch wins when it selects the GPU process",
        "sandboxed Gecko RDD process registers after both bridge opens succeed",
        "sandboxed Gecko GPU process registers after both bridge opens succeed",
        "Gecko content process receives no decoder exception",
        "Gecko utility process receives no decoder exception",
        "a trailing rdd argument without -parentPid does not qualify",
        "Gecko RDD still qualifies after CheckArg removed -parentPid from argv",
        "Gecko GPU process qualifies after CheckArg removed every flag and the type",
        "the original strings decide: a different flag in the removed slot does not qualify",
        "zero Gecko parent pid is rejected",
        "negative Gecko parent pid is rejected",
        "nonnumeric Gecko parent pid is rejected",
        "overflowing Gecko parent pid is rejected",
        "unreasonable Gecko argument count is rejected",
        "an unreadable argv string pointer is rejected without dereferencing it",
    )
    with tempfile.TemporaryDirectory(prefix="nvmtl-video-registration-") as tmp:
        src, exe = Path(tmp) / "test.m", Path(tmp) / "test"
        media_start = SOURCE.index("static bool nvmtl_video_pci_has_codecs(")
        media_end = SOURCE.index("\n}", media_start) + 2
        src.write_text(HARNESS.replace("PRODUCTION", SOURCE[media_start:media_end] + '\n' + SOURCE[START:END]))
        subprocess.run(["xcrun", "clang", "-fobjc-arc", "-O1", "-g", "-fsanitize=address,undefined", str(src),
                        "-framework", "Foundation", "-o", str(exe)], check=True)
        for index, case in enumerate(cases):
            subprocess.run([str(exe), str(index)], check=True, stdout=subprocess.DEVNULL)
            print("PASS", case)
    for codec in ("nvmtl_nvdec_register", "nvmtl_nvdec_hevc_register"):
        start = SOURCE.index("static void " + codec + "(void)\n{")
        body = SOURCE[start:SOURCE.index("\n}\n", start) + 3]
        assert "if (!nvmtl_can_register_decoder(me))" in body
        assert body.index('"WindowServer"') < body.index("nvmtl_can_register_decoder(me)")
        assert "NVMTL_NO_NVDEC" in body
    print("PASS both decoders retain disable/WindowServer guards and use the access check")


if __name__ == "__main__":
    main()
