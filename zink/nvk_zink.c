/* SPDX-License-Identifier: MIT */
#include "nvk_zink.h"
#include "nvk_zink_private.h"
#include "nvk_present.h"
#include "zink/zink_screen.h"
#include <stdio.h>
#include "pipe/p_screen.h"
#include "pipe/p_context.h"
#include "util/u_inlines.h"
#include "util/u_atomic.h"
#include "state_tracker/st_context.h"
#include "glapi/glapi/glapi.h"
#include "zink/zink_public.h"
#include "state_tracker/st_atom.h"
#include "state_tracker/st_cb_bitmap.h"
#include "state_tracker/st_util.h"
#include "main/state.h"
#include "main/context.h"
#include <limits.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>

#define NVK_EXPORT __attribute__((visibility("default")))
static uint32_t next_drawable_id;
static pthread_mutex_t screen_lock = PTHREAD_MUTEX_INITIALIZER;
static struct nvk_zink_screen *process_screen;

static bool finish(struct nvk_zink_context *ctx)
{
   if (p_atomic_read(&ctx->screen->uncertain)) return false;
   struct pipe_screen *screen = ctx->screen->fscreen.screen;
   struct pipe_fence_handle *fence = NULL;
   st_context_flush(ctx->st, 0, &fence, NULL, NULL);
   if (!fence || !screen->fence_finish(screen, ctx->st->pipe, fence, 5000000000ull)) {
      /* No buffer, surface or screen can be freed while queued GPU work may
       * still refer to it. Quarantine this share group until process exit. */
      p_atomic_set(&ctx->screen->uncertain, 1);
      fprintf(stderr, "NVK Zink: completion uncertain; share group quarantined\n");
      return false;
   }
   screen->fence_reference(screen, &fence, NULL);
   return true;
}

/* Submit rendering before rotating drawable attachments. Presentation has its
 * own fence; waiting for the same frame again here serializes the CPU. */
static void submit(struct nvk_zink_context *ctx)
{
   st_context_flush(ctx->st, 0, NULL, NULL, NULL);
}

static bool flush_front(struct st_context *st, struct pipe_frontend_drawable *drawable,
                         enum st_attachment_type attachment)
{
   struct nvk_zink_context *ctx = (void *)drawable;
   /* Mesa calls this while finishing its flush. Defer the native handoff
    * until the public GL entrypoint returns: presenting here would reenter
    * Mesa's state validation and flush path. Back-buffer/FBO flushes do not
    * request a window update. */
   if (attachment == ST_ATTACHMENT_FRONT_LEFT && !ctx->front_flush_active)
      ctx->front_flush_pending = true;
   return true;
}

NVK_EXPORT int nvk_zink_begin_front_flush(struct nvk_zink_context *ctx)
{
   if (!ctx || st_api_get_current() != ctx->st || !ctx->front_flush_pending ||
       ctx->front_flush_active || p_atomic_read(&ctx->screen->uncertain) ||
       _mesa_inside_begin_end(ctx->st->ctx)) return 0;
   ctx->front_flush_pending = false;
   ctx->front_flush_active = true;
   return 1;
}

NVK_EXPORT void nvk_zink_end_front_flush(struct nvk_zink_context *ctx, int presented)
{
   if (!ctx || !ctx->front_flush_active) return;
   ctx->front_flush_active = false;
   /* A failed handoff must remain retryable even though Mesa has already
    * cleared the front renderbuffer's defined flag. */
   if (!presented) ctx->front_flush_pending = true;
}

static int get_param(struct pipe_frontend_screen *fscreen,
                     enum st_manager_param param)
{
   return 0;
}

static bool validate(struct st_context *st, struct pipe_frontend_drawable *drawable,
                     const enum st_attachment_type *requested, unsigned count,
                     struct pipe_resource **out, struct pipe_resource **resolve)
{
   struct nvk_zink_context *ctx = (void *)drawable;
   if (resolve) *resolve = NULL;
   for (unsigned i = 0; i < count; ++i) {
      enum st_attachment_type a = requested[i];
      if (a != ST_ATTACHMENT_FRONT_LEFT && a != ST_ATTACHMENT_DEPTH_STENCIL &&
          !(ctx->double_buffer && a == ST_ATTACHMENT_BACK_LEFT))
         return false;
      if (!ctx->attachments[a]) {
         struct pipe_resource t = {0};
         t.target = PIPE_TEXTURE_2D;
         t.format = a == ST_ATTACHMENT_DEPTH_STENCIL ? ctx->visual.depth_stencil_format
                                                    : ctx->visual.color_format;
         t.width0 = ctx->width;
         t.height0 = ctx->height;
         t.depth0 = t.array_size = 1;
         t.bind = a == ST_ATTACHMENT_DEPTH_STENCIL ? PIPE_BIND_DEPTH_STENCIL
                                                 : PIPE_BIND_RENDER_TARGET | PIPE_BIND_SAMPLER_VIEW;
         ctx->attachments[a] = ctx->screen->fscreen.screen->resource_create(ctx->screen->fscreen.screen, &t);
         if (!ctx->attachments[a]) return false;
      }
      pipe_resource_reference(&out[i], ctx->attachments[a]);
   }
   return true;
}

NVK_EXPORT struct nvk_zink_context *
nvk_zink_create(unsigned width, unsigned height)
{
   return nvk_zink_create_shared(width, height, NULL);
}

NVK_EXPORT struct nvk_zink_context *
nvk_zink_create_shared(unsigned width, unsigned height, struct nvk_zink_context *share)
{
   return nvk_zink_create_drawable(width, height, share, 0);
}

NVK_EXPORT struct nvk_zink_context *
nvk_zink_create_drawable(unsigned width, unsigned height, struct nvk_zink_context *share,
                         int double_buffer)
{
   return nvk_zink_create_drawable_profile(width, height, share, double_buffer, 0, 1);
}

NVK_EXPORT struct nvk_zink_context *
nvk_zink_create_drawable_profile(unsigned width, unsigned height, struct nvk_zink_context *share,
                                   int double_buffer, int legacy, int has_alpha)
{
   if (!width || !height || width > INT_MAX || height > INT_MAX) return NULL;
   struct nvk_zink_context *ctx = calloc(1, sizeof(*ctx));
   if (!ctx) return NULL;
   if (share) {
      ctx->screen = share->screen;
      p_atomic_inc(&ctx->screen->refs);
   } else {
      /* A screen/device belongs to the GLD process, rather than one CGL
       * context. Share GPU resources and compiler caches, but pass NULL as
       * Mesa's share context unless CGL explicitly shares GL objects.
       * The original NVK binary has an unsafe Darwin disk-cache teardown
       * barrier; leave its one private instance to process cleanup. */
      pthread_mutex_lock(&screen_lock);
      if (!process_screen) {
         struct nvk_zink_screen *screen = calloc(1,sizeof(*screen));
         if (screen) {
            screen->fscreen.screen = zink_create_screen(NULL,NULL);
            if (!screen->fscreen.screen) { free(screen); screen = NULL; }
            else screen->fscreen.get_param = get_param;
         }
         process_screen = screen;
      }
      ctx->screen = process_screen;
      if (ctx->screen) p_atomic_inc(&ctx->screen->refs);
      pthread_mutex_unlock(&screen_lock);
      if (!ctx->screen) { free(ctx); return NULL; }
   }
   if (p_atomic_read(&ctx->screen->uncertain)) { p_atomic_dec(&ctx->screen->refs); free(ctx); return NULL; }
   ctx->width = width;
   ctx->height = height;
   ctx->double_buffer = double_buffer != 0;
   ctx->visual.color_format = has_alpha ? PIPE_FORMAT_R8G8B8A8_UNORM : PIPE_FORMAT_R8G8B8X8_UNORM;
   ctx->visual.depth_stencil_format = PIPE_FORMAT_Z24_UNORM_S8_UINT;
   ctx->visual.buffer_mask = ST_ATTACHMENT_FRONT_LEFT_MASK | ST_ATTACHMENT_DEPTH_STENCIL_MASK;
   if (ctx->double_buffer) ctx->visual.buffer_mask |= ST_ATTACHMENT_BACK_LEFT_MASK;
   ctx->drawable.fscreen = &ctx->screen->fscreen;
   ctx->drawable.visual = &ctx->visual;
   ctx->drawable.validate = validate;
   ctx->drawable.flush_front = flush_front;
   ctx->drawable.ID = p_atomic_inc_return(&next_drawable_id);
   p_atomic_set(&ctx->drawable.stamp, 1);
   struct st_context_attribs attrs = {0};
   attrs.profile = legacy ? API_OPENGL_COMPAT : API_OPENGL_CORE;
   attrs.major = legacy ? 2 : 4;
   attrs.minor = 1;
   attrs.visual = ctx->visual;
   enum st_context_error error;
   pthread_mutex_lock(&screen_lock);
   ctx->st = st_api_create_context(&ctx->screen->fscreen, &attrs, &error, share ? share->st : NULL);
   pthread_mutex_unlock(&screen_lock);
   if (!ctx->st) { nvk_zink_destroy(ctx); return NULL; }
   ctx->st->frontend_context = ctx;
   if (!nvk_iosurface_group_init(ctx, share)) { nvk_zink_destroy(ctx); return NULL; }
   return ctx;
}

NVK_EXPORT struct nvk_zink_context *nvk_zink_get_current(void)
{
   struct st_context *st = st_api_get_current();
   return st ? st->frontend_context : NULL;
}

NVK_EXPORT int nvk_zink_make_current(struct nvk_zink_context *ctx)
{
   if (ctx && p_atomic_read(&ctx->screen->uncertain)) return 0;
   /* Each native GL entrypoint verifies the Mesa context. Rebinding the same
    * frontend on every call needlessly validates/refcounts its framebuffers.
    * Drawable stamp changes are still handled by Mesa's draw validation.
    */
   if (ctx && st_api_get_current() == ctx->st) return 1;
   return ctx ? st_api_make_current(ctx->st, &ctx->drawable, &ctx->drawable)
              : st_api_make_current(NULL, NULL, NULL);
}

NVK_EXPORT void nvk_zink_destroy(struct nvk_zink_context *ctx)
{
   if (!ctx || p_atomic_read(&ctx->screen->uncertain)) return;
   if (ctx->st) {
      if (!nvk_iosurface_group_release(ctx)) return;
      nvk_zink_present_release(ctx);
      if (p_atomic_read(&ctx->screen->uncertain)) return;
      if (st_api_get_current() == ctx->st) nvk_zink_make_current(NULL);
      pipe_resource_reference(&ctx->iosurface_staging, NULL);
      st_destroy_context(ctx->st);
   }
   st_api_destroy_drawable(&ctx->drawable);
   for (unsigned i = 0; i < ST_ATTACHMENT_COUNT; ++i)
      pipe_resource_reference(&ctx->attachments[i], NULL);
   p_atomic_dec(&ctx->screen->refs);
   free(ctx);
}

NVK_EXPORT int nvk_zink_resize(struct nvk_zink_context *ctx, unsigned width, unsigned height)
{
   if (!ctx || !width || !height || width > INT_MAX || height > INT_MAX ||
       st_api_get_current() != ctx->st) return 0;
   if (ctx->width == width && ctx->height == height) return 1;
   nvk_zink_present_release(ctx);
   if (p_atomic_read(&ctx->screen->uncertain)) return 0;
   for (unsigned i = 0; i < ST_ATTACHMENT_COUNT; ++i)
      pipe_resource_reference(&ctx->attachments[i], NULL);
   ctx->width = width;
   ctx->height = height;
   p_atomic_inc(&ctx->drawable.stamp);
   st_context_invalidate_state(ctx->st, ST_INVALIDATE_FB_STATE);
   return 1;
}

NVK_EXPORT int nvk_zink_swap(struct nvk_zink_context *ctx)
{
   if (!ctx || st_api_get_current() != ctx->st) return 0;
   submit(ctx);
   if (ctx->double_buffer && !ctx->front_flush_active) {
      struct pipe_resource *front = ctx->attachments[ST_ATTACHMENT_FRONT_LEFT];
      ctx->attachments[ST_ATTACHMENT_FRONT_LEFT] = ctx->attachments[ST_ATTACHMENT_BACK_LEFT];
      ctx->attachments[ST_ATTACHMENT_BACK_LEFT] = front;
      p_atomic_inc(&ctx->drawable.stamp);
      /* The cached make-current path deliberately skips rebinding. A stamp
       * alone does not invalidate Mesa's framebuffer state: the next clear
       * could otherwise write the old front image rather than the new back. */
      st_context_invalidate_state(ctx->st, ST_INVALIDATE_FB_STATE);
   }
   ctx->front_flush_pending = false;
   return 1;
}

NVK_EXPORT int nvk_zink_present_wait(struct nvk_zink_context *ctx, unsigned slot)
{
   if (!ctx || slot >= 2) return 0;
   struct pipe_screen *screen = ctx->screen->fscreen.screen;
   if (ctx->present[slot].fence &&
       !screen->fence_finish(screen, ctx->st->pipe, ctx->present[slot].fence, 5000000000ull)) {
      p_atomic_set(&ctx->screen->uncertain, 1);
      return 0;
   }
   screen->fence_reference(screen, &ctx->present[slot].fence, NULL);
   return 1;
}

NVK_EXPORT void nvk_zink_present_release(struct nvk_zink_context *ctx)
{
   if (!ctx) return;
   /* Drain even when this Mesa context is not current (GLD teardown). The
    * imports retain the surface while NVK can still access its pinned pages. */
   if (!finish(ctx)) return;
   pipe_resource_reference(&ctx->native_image, NULL);
   for (unsigned i = 0; i < 2; ++i) {
      if (!nvk_zink_present_wait(ctx, i)) return;
      pipe_resource_reference(&ctx->present[i].buffer, NULL);
      pipe_resource_reference(&ctx->present[i].image, NULL);
      if (ctx->present[i].surface) CFRelease(ctx->present[i].surface);
      ctx->present[i].surface = NULL;
   }
}

NVK_EXPORT int nvk_zink_present_import(struct nvk_zink_context *ctx, unsigned slot,
                                       IOSurfaceRef surface)
{
   if (!ctx || slot >= 2 || !surface || st_api_get_current() != ctx->st ||
       IOSurfaceGetWidth(surface) != ctx->width ||
       IOSurfaceGetHeight(surface) != ctx->height ||
       IOSurfaceGetPlaneCount(surface) || IOSurfaceGetBytesPerElement(surface) != 4)
      return 0;
   if (ctx->present[slot].surface == surface) return 1;
   /* An import is immutable until release; do not replace a consumer's pages. */
   if (ctx->present[slot].surface) return 0;
   struct pipe_screen *screen = ctx->screen->fscreen.screen;
   if (!screen->resource_from_user_memory || !ctx->st->pipe->image_copy_buffer)
      return 0;
   uint32_t format = IOSurfaceGetPixelFormat(surface);
   if (format != 0x42475241 && format != 0x52474241) return 0;
   size_t stride = IOSurfaceGetBytesPerRow(surface), size = IOSurfaceGetAllocSize(surface);
   if (stride < (size_t)ctx->width * 4 || stride % 4 ||
       stride > UINT_MAX / ctx->height || size < stride * ctx->height || size > INT_MAX)
      return 0;
   if (IOSurfaceLock(surface, 0, NULL) != kIOReturnSuccess) return 0;
   struct pipe_resource templ = {0};
   templ.target = PIPE_BUFFER;
   templ.format = PIPE_FORMAT_R8_UNORM;
   templ.width0 = (unsigned)size;
   templ.height0 = templ.depth0 = templ.array_size = 1;
   templ.usage = PIPE_USAGE_STAGING;
   void *base = IOSurfaceGetBaseAddress(surface);
   struct pipe_resource *buffer = base ? screen->resource_from_user_memory(screen, &templ, base) : NULL;
   int unlocked = IOSurfaceUnlock(surface, 0, NULL) == kIOReturnSuccess;
   if (!buffer || !unlocked) { pipe_resource_reference(&buffer, NULL); return 0; }
   templ.target = PIPE_TEXTURE_2D;
   templ.format = format == 0x42475241 ? PIPE_FORMAT_B8G8R8A8_UNORM : PIPE_FORMAT_R8G8B8A8_UNORM;
   templ.width0 = ctx->width;
   templ.height0 = ctx->height;
   templ.bind = PIPE_BIND_RENDER_TARGET | PIPE_BIND_SAMPLER_VIEW;
   templ.usage = PIPE_USAGE_DEFAULT;
   struct pipe_resource *image = screen->resource_create(screen, &templ);
   if (!image) { pipe_resource_reference(&buffer, NULL); return 0; }
   ctx->present[slot].buffer = buffer;
   ctx->present[slot].image = image;
   ctx->present[slot].surface = (IOSurfaceRef)CFRetain(surface);
   return 1;
}

static int blit_default(struct nvk_zink_context *ctx, struct pipe_resource *image,
                         int bottom_up, int opaque)
{
   /* Validate the default drawable, including a newly resized or rotated
    * attachment, even if a shaderpack leaves its own read FBO bound. */
   void (*get)(GLenum, GLint *) = (void *)_mesa_glapi_get_proc_address("glGetIntegerv");
   void (*bindfb)(GLenum, GLuint) = (void *)_mesa_glapi_get_proc_address("glBindFramebuffer");
   void (*readbuf)(GLenum) = (void *)_mesa_glapi_get_proc_address("glReadBuffer");
   void (*flush)(void) = (void *)_mesa_glapi_get_proc_address("glFlush");
   flush();
   GLint saved_fb, saved_read;
   get(GL_READ_FRAMEBUFFER_BINDING, &saved_fb);
   bindfb(GL_READ_FRAMEBUFFER, 0);
   get(GL_READ_BUFFER, &saved_read);
   bool back = ctx->double_buffer && !ctx->front_flush_active;
   readbuf(back ? GL_BACK : GL_FRONT);
   _mesa_update_state(ctx->st->ctx);
   ST_PIPELINE_UPDATE_FB_STATE_MASK(mask);
   st_validate_state(ctx->st, mask);
   st_flush_bitmap_cache(ctx->st);
   readbuf(saved_read);
   bindfb(GL_READ_FRAMEBUFFER, saved_fb);
   struct pipe_resource *src = ctx->attachments[back ?
      ST_ATTACHMENT_BACK_LEFT : ST_ATTACHMENT_FRONT_LEFT];
   if (!src) return 0;
   struct pipe_context *pipe = ctx->st->pipe;
   struct pipe_blit_info blit = {0};
   blit.src.resource = src;
   blit.src.format = src->format;
   blit.src.box.width = ctx->width;
   blit.src.box.height = bottom_up ? -(int)ctx->height : (int)ctx->height;
   blit.src.box.y = bottom_up ? ctx->height : 0;
   blit.src.box.depth = 1;
   blit.dst.resource = image;
   blit.dst.format = image->format;
   blit.dst.box.width = ctx->width;
   blit.dst.box.height = ctx->height;
   blit.dst.box.depth = 1;
   blit.mask = PIPE_MASK_RGBA;
   blit.filter = PIPE_TEX_FILTER_NEAREST;
   if (opaque || ctx->visual.color_format == PIPE_FORMAT_R8G8B8X8_UNORM) {
      /* The RGBX sampler view supplies alpha=1 in this same GPU blit.
       * A separate clear followed by an RGB-only blit allowed Zink to
       * reorder the scratch clear after the blit, producing black frames. */
      blit.src.format = PIPE_FORMAT_R8G8B8X8_UNORM;
   }
   /* Swizzle RGBA -> BGRA and flip rows on the GPU. The transfer then writes
    * directly into imported IOSurface pages; neither image is CPU mapped. */
   pipe->blit(pipe, &blit);
   return 1;
}

NVK_EXPORT int nvk_zink_present_begin(struct nvk_zink_context *ctx, unsigned slot,
                                      int bottom_up, int opaque)
{
   if (!ctx || slot >= 2 || st_api_get_current() != ctx->st ||
       !ctx->present[slot].surface || ctx->present[slot].fence) return 0;
   if (!blit_default(ctx, ctx->present[slot].image, bottom_up, opaque)) return 0;
   struct pipe_context *pipe = ctx->st->pipe;
   struct pipe_box box = {.width = ctx->width, .height = ctx->height, .depth = 1};
   unsigned stride = (unsigned)IOSurfaceGetBytesPerRow(ctx->present[slot].surface);
   pipe->image_copy_buffer(pipe, ctx->present[slot].buffer, ctx->present[slot].image, 0,
                           stride, stride * ctx->height, 0, &box);
   pipe->memory_barrier(pipe, PIPE_BARRIER_MAPPED_BUFFER);
   st_context_flush(ctx->st, 0, &ctx->present[slot].fence, NULL, NULL);
   if (ctx->present[slot].fence) return 1;
   /* Submission failure must not allow the caller to unlock imported pages
    * while the commands queued above may still write them. */
   finish(ctx);
   return 0;
}

NVK_EXPORT int nvk_zink_present_iosurface(struct nvk_zink_context *ctx, IOSurfaceRef surface)
{
   if (!ctx || !surface || IOSurfaceGetPlaneCount(surface) ||
       IOSurfaceGetWidth(surface) != ctx->width || IOSurfaceGetHeight(surface) != ctx->height ||
       IOSurfaceGetBytesPerElement(surface) != 4) return 0;
   uint32_t format = IOSurfaceGetPixelFormat(surface);
   if (format != 0x52474241 /* RGBA */ && format != 0x42475241 /* BGRA */) return 0;
   size_t row = (size_t)ctx->width * 4;
   if (row > SIZE_MAX / ctx->height) return 0;
   size_t size = row * ctx->height, stride = IOSurfaceGetBytesPerRow(surface);
   if (stride < row || stride > SIZE_MAX / ctx->height ||
       IOSurfaceGetAllocSize(surface) < stride * ctx->height) return 0;
   const char *mode = getenv("NVKZINK_PRESENT");
   bool cpu = mode && !strcmp(mode, "cpu");
   if (!cpu && nvk_zink_present_import(ctx, 0, surface)) {
      if (IOSurfaceLock(surface, 0, NULL) != kIOReturnSuccess) return 0;
      int ok = nvk_zink_present_begin(ctx, 0, 0, 0);
      /* Always drain before unlocking, including submission failure. */
      if (!nvk_zink_present_wait(ctx, 0)) { finish(ctx); ok = 0; }
      ok &= IOSurfaceUnlock(surface, 0, NULL) == kIOReturnSuccess;
      nvk_zink_present_release(ctx);
      return ok;
   }
   if (!cpu) return 0;
   unsigned char *pixels = malloc(size);
   if (!pixels) return 0;
   if (!nvk_zink_readback(ctx, pixels, size, row)) { free(pixels); return 0; }
   if (IOSurfaceLock(surface, 0, NULL) != kIOReturnSuccess) { free(pixels); return 0; }
   unsigned char *base = IOSurfaceGetBaseAddress(surface);
   if (base) {
      for (unsigned y = 0; y < ctx->height; ++y) {
         unsigned char *dst = base + y * stride, *src = pixels + y * row;
         if (format == 0x52474241) memcpy(dst, src, row);
         else for (unsigned x = 0; x < ctx->width; ++x) {
            dst[x * 4] = src[x * 4 + 2]; dst[x * 4 + 1] = src[x * 4 + 1];
            dst[x * 4 + 2] = src[x * 4]; dst[x * 4 + 3] = src[x * 4 + 3];
         }
      }
   }
   int ok = IOSurfaceUnlock(surface, 0, NULL) == kIOReturnSuccess && base;
   free(pixels);
   return ok;
}

NVK_EXPORT int nvk_zink_device_info(struct nvk_zink_context *ctx, uint32_t *vendor, uint32_t *device)
{
   if (!ctx || !vendor || !device || p_atomic_read(&ctx->screen->uncertain)) return 0;
   struct zink_screen *screen = zink_screen(ctx->screen->fscreen.screen);
   if (screen->info.driver_props.driverID != VK_DRIVER_ID_MESA_NVK) return 0;
   *vendor = screen->info.props.vendorID; *device = screen->info.props.deviceID;
   return 1;
}

NVK_EXPORT int nvk_zink_present_native(struct nvk_zink_context *ctx, IOSurfaceRef surface, int opaque)
{
   if (!ctx || !surface || st_api_get_current() != ctx->st || p_atomic_read(&ctx->screen->uncertain) ||
       IOSurfaceGetWidth(surface) != ctx->width || IOSurfaceGetHeight(surface) != ctx->height) return 0;
   struct pipe_resource templ = {.target = PIPE_TEXTURE_2D, .format = PIPE_FORMAT_B8G8R8A8_UNORM,
      .width0 = ctx->width, .height0 = ctx->height, .depth0 = 1, .array_size = 1,
      .bind = PIPE_BIND_RENDER_TARGET | PIPE_BIND_SAMPLER_VIEW};
   if (!ctx->native_image)
      ctx->native_image = ctx->screen->fscreen.screen->resource_create(ctx->screen->fscreen.screen, &templ);
   struct pipe_resource *image = ctx->native_image;
   if (!image) return 0;
   int result = 0;
   /* Mesa already stores this default drawable with its top row first.
    * Apple GL presentation needs a flip; CAMetalLayer presentation does not. */
   if (blit_default(ctx, image, 0, opaque) && finish(ctx))
      result = nvk_zink_copy_vram(ctx->screen->fscreen.screen, image, surface);
   if (result < 0) p_atomic_set(&ctx->screen->uncertain, 1);
   return p_atomic_read(&ctx->screen->uncertain) ? -1 : result;
}

NVK_EXPORT nvk_zink_proc nvk_zink_get_proc_address(const char *name)
{
   return name && strncmp(name, "gl", 2) == 0 ? _mesa_glapi_get_proc_address(name) : NULL;
}

static int readback(struct nvk_zink_context *ctx, void *pixels,
                     size_t size, size_t stride, bool top_first)
{
   if (!ctx || !pixels || st_api_get_current() != ctx->st ||
       stride < (size_t)ctx->width * 4 || stride > SIZE_MAX / ctx->height ||
       size < stride * ctx->height ||
       (!top_first && (stride % 4 || stride / 4 > INT_MAX))) return 0;
   /* Mesa's readback path validates drawable state and synchronizes Zink's
    * transfer. Raw texture_map on this frontend returned stale data on NVK.
    * Preserve the application's pack state and read framebuffer binding. */
   void (*get)(GLenum, GLint *) = (void *)_mesa_glapi_get_proc_address("glGetIntegerv");
   void (*bindfb)(GLenum, GLuint) = (void *)_mesa_glapi_get_proc_address("glBindFramebuffer");
   void (*bindbuf)(GLenum, GLuint) = (void *)_mesa_glapi_get_proc_address("glBindBuffer");
   void (*store)(GLenum, GLint) = (void *)_mesa_glapi_get_proc_address("glPixelStorei");
   void (*readbuf)(GLenum) = (void *)_mesa_glapi_get_proc_address("glReadBuffer");
   void (*read)(GLint, GLint, GLsizei, GLsizei, GLenum, GLenum, void *) =
      (void *)_mesa_glapi_get_proc_address("glReadPixels");
   size_t row = (size_t)ctx->width * 4;
   if (row > SIZE_MAX / ctx->height) return 0;
   unsigned char *map = top_first ? malloc(row * ctx->height) : pixels;
   if (!map) return 0;
   const GLenum pack_names[] = {GL_PACK_ALIGNMENT, GL_PACK_ROW_LENGTH, GL_PACK_SKIP_ROWS,
                                GL_PACK_SKIP_PIXELS, GL_PACK_IMAGE_HEIGHT, GL_PACK_SKIP_IMAGES};
   GLint saved_pack[6], saved_fb, saved_pbo, saved_read;
   get(GL_READ_FRAMEBUFFER_BINDING, &saved_fb);
   get(GL_PIXEL_PACK_BUFFER_BINDING, &saved_pbo);
   for (unsigned i = 0; i < 6; ++i) get(pack_names[i], &saved_pack[i]);
   bindfb(GL_READ_FRAMEBUFFER, 0);
   get(GL_READ_BUFFER, &saved_read);
   readbuf(ctx->double_buffer && !ctx->front_flush_active ? GL_BACK : GL_FRONT);
   bindbuf(GL_PIXEL_PACK_BUFFER, 0);
   for (unsigned i = 0; i < 6; ++i) store(pack_names[i], i ? 0 : 1);
   if (!top_first) store(GL_PACK_ROW_LENGTH, stride / 4);
   read(0, 0, ctx->width, ctx->height, GL_RGBA, GL_UNSIGNED_BYTE, map);
   for (unsigned i = 0; i < 6; ++i) store(pack_names[i], saved_pack[i]);
   bindbuf(GL_PIXEL_PACK_BUFFER, saved_pbo);
   readbuf(saved_read);
   bindfb(GL_READ_FRAMEBUFFER, saved_fb);
   if (top_first) {
      for (unsigned y = 0; y < ctx->height; ++y)
         memcpy((unsigned char *)pixels + y * stride,
                map + (ctx->height - 1 - y) * row, row);
      free(map);
   }
   return 1;
}

NVK_EXPORT int nvk_zink_readback(struct nvk_zink_context *ctx, void *pixels,
                                 size_t size, size_t stride)
{
   return readback(ctx, pixels, size, stride, true);
}

NVK_EXPORT int nvk_zink_readback_bottom_up(struct nvk_zink_context *ctx, void *pixels,
                                           size_t size, size_t stride)
{
   return readback(ctx, pixels, size, stride, false);
}
