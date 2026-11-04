/* SPDX-License-Identifier: MIT */
#ifndef NVK_ZINK_H
#define NVK_ZINK_H
#include <stddef.h>
#include <stdint.h>
#ifdef __APPLE__
#include <IOSurface/IOSurface.h>
#endif
#ifdef __cplusplus
extern "C" {
#endif
struct nvk_zink_context;
typedef void (*nvk_zink_proc)(void);
/* Contexts belong to one thread at a time. Release before moving threads.
 * The pinned native NVK frontend is selected privately; VK_DRIVER_FILES is
 * neither consulted nor changed. This is not an Apple CGLContextObj. */
struct nvk_zink_context *nvk_zink_create(unsigned width, unsigned height);
struct nvk_zink_context *nvk_zink_create_shared(unsigned width, unsigned height,
                                                struct nvk_zink_context *share);
struct nvk_zink_context *nvk_zink_create_drawable(unsigned width, unsigned height,
                                                  struct nvk_zink_context *share,
                                                  int double_buffer);
/* legacy != 0 requests desktop OpenGL 2.1 compatibility. has_alpha mirrors
 * the native pixel format; RGB drawables must expose alpha=1 to consumers. */
struct nvk_zink_context *nvk_zink_create_drawable_profile(unsigned width, unsigned height,
                                                          struct nvk_zink_context *share,
                                                          int double_buffer, int legacy,
                                                          int has_alpha);
int nvk_zink_swap(struct nvk_zink_context *ctx);
/* Consume Mesa's dirty-front notification after glFlush/glFinish return.
 * While active, presentation copies FRONT without rotating double buffers.
 * End with presented=0 to retry a failed native handoff on the next flush.
 * These calls require ctx current and must be paired when begin returns 1. */
int nvk_zink_begin_front_flush(struct nvk_zink_context *ctx);
void nvk_zink_end_front_flush(struct nvk_zink_context *ctx, int presented);
int nvk_zink_make_current(struct nvk_zink_context *ctx);
struct nvk_zink_context *nvk_zink_get_current(void);
void nvk_zink_destroy(struct nvk_zink_context *ctx);
int nvk_zink_resize(struct nvk_zink_context *ctx, unsigned width, unsigned height);
nvk_zink_proc nvk_zink_get_proc_address(const char *name);
/* RGBA8, top row first. Requires ctx current on the calling thread. */
int nvk_zink_readback(struct nvk_zink_context *ctx, void *pixels,
                       size_t size, size_t stride);
/* RGBA8, bottom row first, for native GL upload. Stride must be a multiple of 4. */
int nvk_zink_readback_bottom_up(struct nvk_zink_context *ctx, void *pixels,
                                 size_t size, size_t stride);
#ifdef __APPLE__
/* CGL-compatible status code (zero on success). The caller binds a named
 * rectangle texture and keeps ctx current. The surface remains live across
 * external writers; bind notifications refresh it, and flush publishes GPU
 * writes before handing ownership to external consumers. */
int nvk_zink_bind_iosurface(struct nvk_zink_context *, unsigned target,
                             unsigned internal_format, int width, int height,
                             unsigned format, unsigned type, IOSurfaceRef, unsigned plane);
int nvk_zink_flush_iosurfaces(struct nvk_zink_context *);
int nvk_zink_refresh_iosurfaces(struct nvk_zink_context *, unsigned target, int framebuffer);
/* Optional diagnostic observer. The queue is externally locked during the
 * callback. 1 = verified, 0 = safe diagnostic failure, -1 = uncertain GPU work.
 * Gameplay leaves this unset; diagnostic readback is separate pixel traffic. */
struct nvk_zink_present_event {
   void *get_device_proc_addr, *device, *queue;
   uint64_t buffer;
   uint32_t row, width, height, queue_family, host_coherent_types;
   uint32_t host_backing; /* GPU copy into IOSurface pages after a VRAM import refusal. */
};
typedef int (*nvk_zink_present_observer)(const struct nvk_zink_present_event *);
void nvk_zink_set_present_observer(nvk_zink_present_observer);
/* Direct native drawable path. Returns -1 when completion is uncertain: retain
 * the drawable, stop using this share group, and do not publish its contents. */
int nvk_zink_device_info(struct nvk_zink_context *, uint32_t *vendor, uint32_t *device);
int nvk_zink_present_native(struct nvk_zink_context *, IOSurfaceRef, int opaque);
/* Two reusable GPU presentation slots. Import maps IOSurface pages through
 * VK_EXT_external_memory_host; begin blits/copies on the GPU and submits a
 * fence. Wait must complete before another API reads the surface. The caller
 * must also finish the previous consumer before begin writes it again, and
 * hold an IOSurface write lock from begin through wait. Requires ctx current.
 * Resize/destroy release both imports after their work completes. Opaque
 * presentation forces only the copied image's alpha to 1; GL state and the
 * application's framebuffer alpha remain untouched. */
int nvk_zink_present_import(struct nvk_zink_context *ctx, unsigned slot,
                              IOSurfaceRef surface);
int nvk_zink_present_begin(struct nvk_zink_context *ctx, unsigned slot,
                             int bottom_up, int opaque);
int nvk_zink_present_wait(struct nvk_zink_context *ctx, unsigned slot);
void nvk_zink_present_release(struct nvk_zink_context *ctx);
/* Copy into a caller-owned, same-size, nonplanar RGBA/BGRA IOSurface. The
 * caller remains responsible for submitting the surface to its compositor. */
int nvk_zink_present_iosurface(struct nvk_zink_context *ctx, IOSurfaceRef surface);
#endif
#ifdef __cplusplus
}
#endif
#endif
