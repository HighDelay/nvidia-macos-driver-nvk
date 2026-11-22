# NVRMFB display hot-plug lifecycle

NVRMFB keeps the NVKMS display handle and framebuffer instance across cable changes. A disconnect confirmed by `getDynamicDisplayInfo` marks that instance offline, clears its cached EDID length and display-audio properties, and schedules the registered `kIOFBConnectInterruptType` handler after leaving the display gate. Scanout storage and the WindowServer surface remain allocated.

A reconnect restores the handle's current EDID and physical size, relights the existing head with `reflip()`, republishes current display-audio properties, and schedules the same per-framebuffer handler. The callback is owned by the framebuffer instance; `scheduleCall()` retains it for queued work and `stop()` cancels and drains that call.

`hasDDCConnect`, `getDDCBlock`, `kConnectionEnable`, and `kConnectionCheckEnable` reflect the offline state, so cached EDID cannot make a disconnected connector look live. If NVKMS omits a known handle from `getDisplays`, NVRMFB queries that handle directly: a successful offline result triggers the transition, while query failure or a connected result preserves the current state.

The scanner retains the NVIDIA parent service so it can match idle nubs even
after every initial framebuffer detaches. Set `nvfbheads` to the number of
outputs you expect to use, up to four. If NVKMS reports connector handles but
none is connected at boot, the driver retries at most six times, three seconds
apart. Set `nvfbbootretry=0` to skip that startup retry.

Ongoing detection is event-driven by default. `nvfbhotplugpoll=1` through
`nvfbhotplugpoll=60` requests an additional scan every 1 to 60 seconds. Both
paths depend on NVKMS reporting the connector and its current state. The
scanner leaves a GPU in runtime D3cold asleep until a consumer wakes it.

Run `python tools/test_nvrmfb_hotplug_lifecycle.py` with `clang++` available. The check compiles extracted production methods and inspects the production transition path; hardware hot-plug behavior still requires runtime validation.
