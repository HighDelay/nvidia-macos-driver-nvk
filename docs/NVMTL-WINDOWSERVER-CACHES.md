# NVMTL WindowServer recording caches

WindowServer repeatedly records the same compositor state while wrapping and drawing
IOSurfaces. The NVMTL path now keeps a small cache in each command recording for the
normalized viewport, scissor, primitive topology and restart flag, cull/front-face
pair, and blend constants. Repeated values are skipped; changed values still emit
the same Vulkan dynamic-state commands. Pass defaults and the direct tessellation
patch path update the cache explicitly, and a new recording starts with an empty
cache. No state is shared between recordings or in-flight GPU work.

The four common IOSurface VRAM tag keys (`p0` through `p3`) use retained constant
CFStrings. Unusual plane numbers retain the formatted-key fallback and all callers
keep their existing `CFRelease` ownership. This removes a formatted CFString
allocation from the hot surface publication/import path used by WindowServer.

`tools/test_windowserver_caches.py` compiles the production state helpers with
AddressSanitizer and UndefinedBehaviorSanitizer, checks repeated/changed state,
restart toggles, NaN depth normalization, fresh recordings, and the metadata-key
fallback. `tools/bench_windowserver_state.m` is an isolated compositor-like GPU
probe; it loads only the named plugin and never installs or stages it.

On macOS 26.7.2 (25G332), x86_64, RTX 2060/TU106, the five-process alternating
benchmark at 1,200 repeated quads per frame reduced calling-thread recording CPU
from 5.561 ms to 5.139 ms (−7.60%). A deliberately varying-state workload measured
5.393 ms to 5.546 ms (+2.84%), so the cache is beneficial for repeated compositor
state and carries a small comparison overhead when every value changes. These are
recording microbenchmarks, not WindowServer-wide CPU or application-FPS claims.
Live WindowServer profiling was unavailable without elevated sampling privileges;
the package has not been installed or reboot-tested.
