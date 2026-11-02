#!/usr/bin/env python3
"""The fork enables the ANGLE Metal rule by default for all apps, including Electron.

Lift the production constructor into a fixture with stubs to check the default and
NVMTL_ANGLE_METAL overrides without a GPU or a browser. Keep this historical filename
so existing test commands continue to cover the fork's broader policy.
"""
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
source = (ROOT / "plugin/NVMTLDevice.m").read_text()

start = source.index("__attribute__((constructor)) static void nvmtl_angle_allow_nvidia(void)")
end = source.index("static bool nvmtl_process_is_allowed(void)", start)
region = source[start:end].replace("__attribute__((constructor)) ", "")

fixture = r"""
#include <cstdio>
#include <cstdlib>
#include <cstring>

#define LOG_NOTICE 5
static const char *g_name = "";
static const char *g_opt = nullptr;
static char g_angle[1024] = {0};
static int g_logs = 0;

const char *getprogname(void) { return g_name; }
char *getenv(const char *k) {
    if (!strcmp(k, "NVMTL_ANGLE_METAL")) return (char *)g_opt;
    if (!strcmp(k, "ANGLE_FEATURE_OVERRIDES_DISABLED")) return g_angle[0] ? g_angle : nullptr;
    return nullptr;
}
int setenv(const char *k, const char *v, int) {
    if (!strcmp(k, "ANGLE_FEATURE_OVERRIDES_DISABLED")) {
        strncpy(g_angle, v, sizeof g_angle - 1);
        g_angle[sizeof g_angle - 1] = 0;
    }
    return 0;
}
static void syslog(int, const char *, ...) { g_logs++; }
// the images dyld reports as loaded; empty = none of ANGLE's
static const char *g_images[4] = {nullptr};
uint32_t _dyld_image_count(void) { uint32_t n = 0; while (n < 4 && g_images[n]) n++; return n; }
const char *_dyld_get_image_name(uint32_t i) { return i < 4 ? g_images[i] : nullptr; }

""" + region + r"""

static void run(const char *name, const char *opt, const char *pre, const char *want, int logs = 1) {
    g_name = name;
    g_opt = opt;
    if (pre) {
        strncpy(g_angle, pre, sizeof g_angle - 1);
        g_angle[sizeof g_angle - 1] = 0;
    } else {
        g_angle[0] = 0;
    }
    g_logs = 0;
    nvmtl_angle_allow_nvidia();
    if (strcmp(g_angle, want) != 0 || g_logs != logs) {
        fprintf(stderr, "%s opt=%s pre=%s -> '%s', want '%s'; logs=%d, want %d\n",
                name ? name : "<null>", opt ? opt : "-", pre ? pre : "-", g_angle, want, g_logs, logs);
        exit(1);
    }
}

int main() {
    // Chrome and Chromium, including their helper processes, are on by default.
    run("Google Chrome", nullptr, nullptr, "disableMetalOnNvidia");
    run("Google Chrome Helper (Aperitif)", nullptr, nullptr, "disableMetalOnNvidia");
    run("Chromium", nullptr, nullptr, "disableMetalOnNvidia");
    run("Chromium Helper (GPU)", nullptr, nullptr, "disableMetalOnNvidia");
    // The fork keeps all apps on by default, including Edge and Electron helpers.
    run("Microsoft Edge", nullptr, nullptr, "disableMetalOnNvidia");
    run("Microsoft Edge Helper (GPU)", nullptr, nullptr, "disableMetalOnNvidia");
    run("Electron", nullptr, nullptr, "disableMetalOnNvidia");
    run("Discord Helper (GPU)", nullptr, nullptr, "disableMetalOnNvidia");
    run("Safari", nullptr, nullptr, "disableMetalOnNvidia");
    run("Slack Helper (Renderer)", nullptr, nullptr, "disableMetalOnNvidia");
    run("QQ Helper", nullptr, nullptr, "disableMetalOnNvidia");
    run("policy", nullptr, nullptr, "disableMetalOnNvidia");
    run(nullptr, nullptr, nullptr, "disableMetalOnNvidia");
    // The environment variable still overrides both ways.
    run("Safari", "1", nullptr, "disableMetalOnNvidia");
    run("Google Chrome", "0", nullptr, "", 0);
    run("Discord Helper (GPU)", "0", nullptr, "", 0);
    run("policy", "0", "otherRule", "otherRule", 0);
    // An existing ANGLE override is kept, without duplicating the rule.
    run("Google Chrome", nullptr, "foo", "foo:disableMetalOnNvidia");
    run("Google Chrome", nullptr, "disableMetalOnNvidia", "disableMetalOnNvidia", 0);
    run("Discord Helper (GPU)", nullptr, "foo:disableMetalOnNvidia", "foo:disableMetalOnNvidia", 0);
    printf("ok\n");
    return 0;
}
"""

with tempfile.TemporaryDirectory() as tmp:
    exe = pathlib.Path(tmp) / "test_angle"
    subprocess.run(
        ["xcrun", "clang++", "-std=c++17", "-O0", "-x", "c++", "-o", str(exe), "-"],
        input=fixture.encode(), check=True,
    )
    result = subprocess.run([str(exe)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok", result.stdout

print("ok: ANGLE enabled for all apps, Electron/Edge defaults, opt-out, existing overrides and activation logging")
