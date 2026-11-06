/* SPDX-License-Identifier: MIT
 * Probe-only interposition: prevent VideoToolbox's fallback device enumeration
 * from loading the installed plugin alongside an isolated codec candidate.
 * Never use this shim with a browser/player or install it with the driver.
 */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

static id<MTLDevice> no_device(void) { return nil; }
static NSArray<id<MTLDevice>> *no_devices(void) { return @[]; }
static NSArray<id<MTLDevice>> *no_observed_devices(id<NSObject> *observer, MTLDeviceNotificationHandler handler) {
    (void)handler;
    if (observer) *observer = nil;
    return @[];
}
#define INTERPOSE(replacement, original) \
    __attribute__((used)) static struct { const void *new_function, *old_function; } \
    pair_##original __attribute__((section("__DATA,__interpose"))) = { (const void *)&replacement, (const void *)&original }
INTERPOSE(no_device, MTLCreateSystemDefaultDevice);
INTERPOSE(no_devices, MTLCopyAllDevices);
INTERPOSE(no_observed_devices, MTLCopyAllDevicesWithObserver);
