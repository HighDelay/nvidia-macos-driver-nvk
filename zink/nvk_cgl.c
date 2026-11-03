/* SPDX-License-Identifier: MIT */
#include "nvk_cgl.h"
#include "nvk_zink.h"
#include "native_drawable.h"
#define GL_DO_NOT_WARN_IF_MULTI_GL_VERSION_HEADERS_INCLUDED 1
#include <OpenGL/gl3.h>
#include <OpenGL/gl.h>
#include <dlfcn.h>
#include <execinfo.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define EXPORT __attribute__((visibility("default")))
#define PREFIX_BYTES 0x1e80
#define MAX_ENTRIES 1024
struct native_cleanup {
   void *share;
   GLuint textures[3];
   GLsync fences[2];
   IOSurfaceRef surfaces[2];
   struct native_cleanup *next;
};
struct nvk_cgl_bridge {
   CGLContextObj context;
   void *engine;
   struct nvk_zink_context *zink;
   unsigned width, height;
   void *original[PREFIX_BYTES / sizeof(void *)];
   nvk_zink_proc procs[MAX_ENTRIES];
   GLuint present_texture, present_fbo;
   unsigned char *present_pixels;
   size_t present_capacity;
   unsigned present_width, present_height;
   struct {
      IOSurfaceRef surface;
      GLuint texture;
      GLsync fence;
   } gpu_present[2];
   unsigned gpu_width, gpu_height, gpu_slot;
   bool present_configured, gpu_disabled, native_present, native_required, apple_uncertain;
   void *driver;
   void **engine_back;
   void *apple_back[(PREFIX_BYTES - 8) / sizeof(void *)];
   bool automatic;
   bool legacy;
   bool resize_pending;
   unsigned pending_width, pending_height;
   GLint native_x, native_y;
   struct native_cleanup *cleanup_reserve;
   struct nvk_cgl_bridge *next;
};
struct dispatch_entry { const char *name; size_t offset; void *adapter; };
static pthread_mutex_t bridges_lock = PTHREAD_MUTEX_INITIALIZER;
static struct nvk_cgl_bridge *bridges;
static struct nvk_cgl_bridge *quarantined_bridges;
static struct native_cleanup *native_cleanups;
static struct native_cleanup *native_quarantine;
static _Atomic unsigned generation;
static _Thread_local unsigned thread_generation;
static _Thread_local struct nvk_cgl_bridge *thread_bridge;
static _Thread_local const char *last_error;
static int materialize(struct nvk_cgl_bridge *b);
static void release_gpu_present(struct nvk_cgl_bridge *b);
static void drain_native_cleanups(struct nvk_cgl_bridge *b);
static int present_drawable(CGLContextObj context);
void nvk_cgl_install_lwjgl_appkit(void);
int nvk_cgl_obs_compatibility_enabled(void);

__attribute__((noinline)) static int fail(const char *error) { last_error = error; return 0; }
EXPORT const char *nvk_cgl_last_error(void) { return last_error ? last_error : "none"; }

static struct nvk_cgl_bridge *find_bridge(CGLContextObj context)
{
   struct nvk_cgl_bridge *b;
   for (b = bridges; b && b->context != context; b = b->next) {}
   return b;
}

void nvk_cgl_lwjgl_drawable_size(CGLContextObj context, unsigned width, unsigned height)
{
   if (!context || !width || !height) return;
   pthread_mutex_lock(&bridges_lock);
   struct nvk_cgl_bridge *b = find_bridge(context);
   if (b && b->automatic &&
       (width != b->width || height != b->height || b->resize_pending)) {
      b->pending_width = width; b->pending_height = height;
      b->native_x = 0; b->native_y = 0;
      b->resize_pending = true;
   }
   pthread_mutex_unlock(&bridges_lock);
}

static void *original_noncurrent(void *engine, size_t offset)
{
   void *proc = NULL;
   CGLContextObj current = CGLGetCurrentContext();
   if (current && *(void **)current == engine) return NULL;
   pthread_mutex_lock(&bridges_lock);
   for (struct nvk_cgl_bridge *b = bridges; b; b = b->next)
      if (b->automatic && b->engine == engine && b->context != current) {
         proc = b->apple_back[(offset - 8) / 8]; break;
      }
   pthread_mutex_unlock(&bridges_lock);
   return proc == (void *)1 ? NULL : proc;
}

/* CGL and NSOpenGLContext call the public viewport dispatch during native
 * surface updates, sometimes before the context becomes current. Restrict
 * drawable sizing to those observed callers, preserving application viewports. */
static int native_viewport(void *engine, void *caller, GLint x, GLint y,
                           GLsizei width, GLsizei height)
{
   Dl_info info;
   if (!dladdr(caller, &info) || !info.dli_sname) return 0;
   if (strcmp(info.dli_sname, "CGLSetSurface") &&
       strcmp(info.dli_sname, "-[NSOpenGLContext(NSOpenGLContextInternal) _updateOpenGLViewport]")) return 0;
   pthread_mutex_lock(&bridges_lock);
   struct nvk_cgl_bridge *b;
   for (b = bridges; b && b->engine != engine; b = b->next) {}
   if (b && b->automatic && width > 0 && height > 0) {
      /* AppKit can make the same native context current on main while the
       * application's Mesa context is rendering on another thread. Publish
       * geometry only; reallocating its attachments here races that render. */
      b->pending_width = width; b->pending_height = height;
      b->native_x = x; b->native_y = y;
      b->resize_pending = true;
   }
   void (*apple)(void *, GLint, GLint, GLsizei, GLsizei) =
      b && b->automatic ? b->apple_back[(0xa80 - 8) / 8] : NULL;
   pthread_mutex_unlock(&bridges_lock);
   if (!apple || apple == (void *)1) return 0;
   apple(engine, x, y, width, height);
   return 1;
}

static struct nvk_cgl_bridge *bridge_current(void *engine)
{
   unsigned current_generation = atomic_load(&generation);
   CGLContextObj context = CGLGetCurrentContext();
   if (thread_generation != current_generation || !thread_bridge ||
       thread_bridge->context != context || thread_bridge->engine != engine) {
      pthread_mutex_lock(&bridges_lock);
      thread_bridge = find_bridge(context);
      thread_generation = atomic_load(&generation);
      pthread_mutex_unlock(&bridges_lock);
   }
   if (!thread_bridge || thread_bridge->engine != engine ||
       (!thread_bridge->zink && !materialize(thread_bridge)) ||
       !nvk_zink_make_current(thread_bridge->zink)) {
      fail("No attached Zink context current on this thread"); return NULL;
   }
   unsigned width = thread_bridge->width, height = thread_bridge->height;
   GLint native_x = 0, native_y = 0;
   pthread_mutex_lock(&bridges_lock);
   bool pending = thread_bridge->resize_pending;
   if (pending) {
      width = thread_bridge->pending_width; height = thread_bridge->pending_height;
      native_x = thread_bridge->native_x; native_y = thread_bridge->native_y;
      thread_bridge->resize_pending = false;
   }
   pthread_mutex_unlock(&bridges_lock);
   /* GLFW can resize a Cocoa window through a path that does not pass the
    * CGLSetSurface/NSOpenGLContext viewport callers above.  The native CGL
    * surface still exposes its backing dimensions through this public CGL
    * parameter, so check it at the frame boundary as a fallback. */
   if (thread_bridge->automatic && !pending) {
      GLint surface[2] = {0, 0};
      if (CGLGetParameter(thread_bridge->context, kCGLCPSurfaceBackingSize, surface) == kCGLNoError &&
          surface[0] > 0 && surface[1] > 0 &&
          ((unsigned)surface[0] != thread_bridge->width ||
           (unsigned)surface[1] != thread_bridge->height)) {
         width = (unsigned)surface[0]; height = (unsigned)surface[1];
         pending = true;
      }
   }
   /* NSOpenGLContext can update a surface while another CGL context is current.
    * Mesa requires this drawable current before reallocating its buffers. */
   if (pending) {
      if (!nvk_zink_resize(thread_bridge->zink, width, height)) {
         pthread_mutex_lock(&bridges_lock);
         if (!thread_bridge->resize_pending) {
            thread_bridge->pending_width = width; thread_bridge->pending_height = height;
            thread_bridge->native_x = native_x; thread_bridge->native_y = native_y;
            thread_bridge->resize_pending = true;
         }
         pthread_mutex_unlock(&bridges_lock);
         fail("Cannot resize native Zink drawable"); return NULL;
      }
      pthread_mutex_lock(&bridges_lock);
      thread_bridge->width = width; thread_bridge->height = height;
      pthread_mutex_unlock(&bridges_lock);
      /* Resizing attachments must not overwrite the application viewport. */
   }
   return thread_bridge;
}

static nvk_zink_proc mesa_entrypoint(const char *name)
{
   nvk_zink_proc proc = nvk_zink_get_proc_address(name);
   if (proc) return proc;
   /* Apple exposes its VAO aliases at separate slots. Mesa exposes the same
    * object semantics through the core names in compatibility contexts. */
   static const struct { const char *apple, *mesa; } aliases[] = {
      {"glBindVertexArrayAPPLE", "glBindVertexArray"},
      {"glDeleteVertexArraysAPPLE", "glDeleteVertexArrays"},
      {"glGenVertexArraysAPPLE", "glGenVertexArrays"},
      {"glIsVertexArrayAPPLE", "glIsVertexArray"},
      {"glFlushRenderAPPLE", "glFlush"},
      {"glFinishRenderAPPLE", "glFinish"},
   };
   for (size_t i = 0; i < sizeof(aliases) / sizeof(aliases[0]); ++i)
      if (!strcmp(name, aliases[i].apple)) return nvk_zink_get_proc_address(aliases[i].mesa);
   return NULL;
}

static void missing_entrypoint(const char *name)
{
   fail(name);
   /* Leave unsupported extensions in Mesa's error stream. Calling an Apple
    * handler here would split object and fixed-function state between drivers. */
   void (*enable)(GLenum) = (void *)nvk_zink_get_proc_address("glEnable");
   enable(0);
}

static void flush_front_buffer(struct nvk_cgl_bridge *b)
{
   if (!nvk_zink_begin_front_flush(b->zink)) return;
   int presented = present_drawable(b->context) == 1;
   nvk_zink_end_front_flush(b->zink, presented);
}

#include "cgl_dispatch.inc"
_Static_assert(sizeof(entries) / sizeof(entries[0]) <= MAX_ENTRIES, "adapter inventory too large");

__attribute__((constructor)) static void initialize_appkit_compatibility(void)
{
   nvk_cgl_install_lwjgl_appkit();
}

static bool native_surface_copy_context(void)
{
   /* AppKit's NSIOAccelSurface uses a private legacy context to copy the
    * Apple drawable into an IOSurface for CAImageQueue. Its CGL texture
    * imports and GL objects must remain in the native driver together. */
   void *frames[32];
   int count = backtrace(frames, 32);
   const char prefix[] = "-[NSIOAccelSurface ";
   for (int i = 0; i < count; ++i) {
      Dl_info info;
      if (dladdr(frames[i], &info) && info.dli_sname &&
          !strncmp(info.dli_sname, prefix, sizeof(prefix) - 1)) return true;
   }
   return false;
}

static void use_original(struct nvk_cgl_bridge *b)
{
   if (b->automatic) {
      b->original[0] = b->engine;
      memcpy(b->original + 1, b->apple_back, PREFIX_BYTES - 8);
   }
   memcpy(b->context, b->original, PREFIX_BYTES);
}

/* CGLFlushDrawable calls a distinct dispatch slot, not public glFlush. */
static void swap_drawable(void *engine)
{
   CGLContextObj current = CGLGetCurrentContext();
   if (current && *(void **)current == engine) {
      /* Keep ordinary game swaps on the cached current-context path. */
      struct nvk_cgl_bridge *b = bridge_current(engine);
      if (b) nvk_cgl_present(b->context);
      return;
   }
   /* CGLFlushDrawable accepts a context that is not current. For example,
    * AnyDesk releases its render context before flushing the Cocoa window. */
   pthread_mutex_lock(&bridges_lock);
   struct nvk_cgl_bridge *b = bridges;
   while (b && b->engine != engine) b = b->next;
   CGLContextObj context = b ? b->context : NULL;
   pthread_mutex_unlock(&bridges_lock);
   if (!context) { fail("Swap requires an attached Zink context"); return; }
   /* The surrounding CGL flush owns/locks this carrier. Do not acquire a
    * CGL reference while holding bridges_lock (attachment locks CGL first). */
   CGLRetainContext(context);
   nvk_cgl_present(context);
   CGLReleaseContext(context);
}

static void use_zink(struct nvk_cgl_bridge *b)
{
   for (size_t i = 0; i < sizeof(entries) / sizeof(entries[0]); ++i)
      *(void **)((char *)b->context + entries[i].offset) = entries[i].adapter;
   *(void **)((char *)b->context + 0x1278) = (void *)swap_drawable;
}

/* GLD context creation runs before gliInitDispatchTable. The 24H32 and 25G241
 * evidence has the same dispatch layout. GLI
 * +b2c8 points at CGL+8 and +b2d0 at the saved public dispatch table. Give
 * GLEngine a private table to populate, and leave adapters in the original
 * saved table for CGL's final restore. This is distinct from the HAL table.
 * The GLD proxy owns this registration and calls destroy during teardown. */
EXPORT int nvk_cgl_prepare_gld(void *engine, void *driver)
{
   if (!engine || !driver) return 0;
   /* Keep an entire application's GL objects in Apple's driver when testing
    * clients that require CGL/IOSurface interop. Never switch a live context. */
   const char *disabled = getenv("NVKZINK_DISABLE");
   if ((disabled && !strcmp(disabled, "1")) || nvk_cgl_obs_compatibility_enabled()) return 0;
   if (native_surface_copy_context()) return 0;
   unsigned char profile = *(unsigned char *)((char *)engine + 0xc6a6);
   if (profile > 1) return 0;
   CGLContextObj context = (CGLContextObj)((char *)*(void **)((char *)engine + 0xb2c8) - 8);
   void **back = *(void ***)((char *)engine + 0xb2d0);
   if (!context || !back) return 0;
   struct nvk_cgl_bridge *b = calloc(1, sizeof(*b));
   if (!b) return 0;
   b->context = context; b->engine = engine; b->driver = driver;
   b->legacy = profile == 0;
   b->automatic = true; b->engine_back = back;
   memcpy(b->original, context, PREFIX_BYTES);
   /* Every nonzero private slot directs gliInitDispatchTable to write here. */
   for (size_t i = 0; i < sizeof(b->apple_back) / sizeof(b->apple_back[0]); ++i)
      b->apple_back[i] = (void *)1;
   for (size_t i = 0; i < sizeof(entries) / sizeof(entries[0]); ++i) {
      b->procs[i] = mesa_entrypoint(entries[i].name);
   }
   for (size_t i = 0; i < sizeof(entries) / sizeof(entries[0]); ++i) {
      back[(entries[i].offset - 8) / 8] = entries[i].adapter;
   }
   back[(0x1278 - 8) / 8] = (void *)swap_drawable;
   *(void ***)((char *)engine + 0xb2d0) = b->apple_back;
   pthread_mutex_lock(&bridges_lock);
   b->next = bridges; bridges = b;
   atomic_fetch_add(&generation, 1);
   pthread_mutex_unlock(&bridges_lock);
   return 1;
}

static int materialize(struct nvk_cgl_bridge *b)
{
   GLint double_buffer = 0, alpha_bits = 0;
   if (!b->automatic || b->context != CGLGetCurrentContext()) return 0;
   CGLDescribePixelFormat(CGLGetPixelFormat(b->context), 0, kCGLPFADoubleBuffer, &double_buffer);
   CGLDescribePixelFormat(CGLGetPixelFormat(b->context), 0, kCGLPFAAlphaSize, &alpha_bits);
   GLint viewport[4] = {0};
   use_original(b);
   glGetIntegerv(GL_VIEWPORT, viewport);
   use_zink(b);
   b->width = viewport[2] > 0 ? (unsigned)viewport[2] : 1;
   b->height = viewport[3] > 0 ? (unsigned)viewport[3] : 1;
   pthread_mutex_lock(&bridges_lock);
   struct nvk_zink_context *share = NULL;
   for (struct nvk_cgl_bridge *other = bridges; other; other = other->next)
      if (other != b && other->zink &&
          *(void **)((char *)other->engine + 0x5b00) ==
          *(void **)((char *)b->engine + 0x5b00)) { share = other->zink; break; }
   b->zink = nvk_zink_create_drawable_profile(b->width, b->height, share, double_buffer, b->legacy,
                                            alpha_bits > 0);
   pthread_mutex_unlock(&bridges_lock);
   if (!b->zink) return fail("Cannot create automatic Zink context");
   return 1;
}

EXPORT void nvk_cgl_destroy_gld(void *driver)
{
   pthread_mutex_lock(&bridges_lock);
   struct nvk_cgl_bridge **link = &bridges;
   while (*link && (*link)->driver != driver) link = &(*link)->next;
   struct nvk_cgl_bridge *b = *link;
   if (b) { *link = b->next; atomic_fetch_add(&generation, 1); }
   pthread_mutex_unlock(&bridges_lock);
   if (!b) return;
   nvk_native_remove(b->context);
   use_original(b);
   *(void ***)((char *)b->engine + 0xb2d0) = b->engine_back;
   release_gpu_present(b);
   use_original(b);
   if (b->apple_uncertain) {
      pthread_mutex_lock(&bridges_lock);
      b->next = quarantined_bridges; quarantined_bridges = b;
      pthread_mutex_unlock(&bridges_lock);
      return;
   }
   if (b->context == CGLGetCurrentContext()) {
      if (b->present_texture) glDeleteTextures(1, &b->present_texture);
      if (b->present_fbo) glDeleteFramebuffers(1, &b->present_fbo);
   }
   nvk_zink_destroy(b->zink);
   free(b->present_pixels);
   free(b->cleanup_reserve);
   free(b);
}

EXPORT int nvk_cgl_attach(CGLContextObj context, unsigned width, unsigned height)
{
   last_error = NULL;
   if (!context || context != CGLGetCurrentContext() || !width || !height)
      return fail("Attach requires a current CGL context and nonzero dimensions");
   GLint profile = 0;
   if (CGLDescribePixelFormat(CGLGetPixelFormat(context), 0, kCGLPFAOpenGLProfile, &profile) ||
       (profile != kCGLOGLPVersion_GL4_Core && profile != kCGLOGLPVersion_GL3_Core && profile != kCGLOGLPVersion_Legacy))
      return fail("Only legacy and GL 3.2/4.1 core CGL contexts are supported");
   if (CGLLockContext(context)) return fail("Cannot lock CGL context");
   pthread_mutex_lock(&bridges_lock);
   bool exists = find_bridge(context) != NULL;
   struct nvk_zink_context *share = NULL;
   for (struct nvk_cgl_bridge *other = bridges; other; other = other->next)
      if (other->context != context &&
          *(void **)((char *)other->engine + 0x5b00) ==
          *(void **)((char *)*(void **)context + 0x5b00)) share = other->zink;
   pthread_mutex_unlock(&bridges_lock);
   if (exists) { CGLUnlockContext(context); return fail("Context is already attached"); }
   struct nvk_cgl_bridge *b = calloc(1, sizeof(*b));
   if (!b) { CGLUnlockContext(context); return fail("Out of memory"); }
   b->context = context; b->engine = *(void **)context;
   b->legacy = profile == kCGLOGLPVersion_Legacy;
   b->width = width; b->height = height;
   b->resize_pending = false;
   memcpy(b->original, context, PREFIX_BYTES);
   for (size_t i = 0; i < sizeof(entries) / sizeof(entries[0]); ++i) {
      b->procs[i] = mesa_entrypoint(entries[i].name);
   }
   GLint double_buffer = 0, alpha_bits = 0;
   CGLDescribePixelFormat(CGLGetPixelFormat(context), 0, kCGLPFADoubleBuffer, &double_buffer);
   CGLDescribePixelFormat(CGLGetPixelFormat(context), 0, kCGLPFAAlphaSize, &alpha_bits);
   pthread_mutex_lock(&bridges_lock);
   share = NULL;
   for (struct nvk_cgl_bridge *other = bridges; other; other = other->next)
      if (other->zink && *(void **)((char *)other->engine + 0x5b00) ==
          *(void **)((char *)b->engine + 0x5b00)) { share = other->zink; break; }
   b->zink = nvk_zink_create_drawable_profile(width, height, share, double_buffer, b->legacy,
                                             alpha_bits > 0);
   pthread_mutex_unlock(&bridges_lock);
   if (!b->zink || !nvk_zink_make_current(b->zink)) {
      nvk_zink_destroy(b->zink); free(b); CGLUnlockContext(context);
      return fail("Cannot create Zink/NVK context");
   }
   CGLRetainContext(context);
   pthread_mutex_lock(&bridges_lock);
   b->next = bridges; bridges = b;
   atomic_fetch_add(&generation, 1);
   pthread_mutex_unlock(&bridges_lock);
   use_zink(b);
   CGLUnlockContext(context);
   return 1;
}

EXPORT int nvk_cgl_resize(CGLContextObj context, unsigned width, unsigned height)
{
   if (!context || context != CGLGetCurrentContext()) return fail("Resize requires the context current");
   struct nvk_cgl_bridge *b = bridge_current(*(void **)context);
   if (!b || !nvk_zink_resize(b->zink, width, height)) return fail("Cannot resize Zink drawable");
   pthread_mutex_lock(&bridges_lock);
   b->width = width; b->height = height;
   pthread_mutex_unlock(&bridges_lock);
   return 1;
}

static int reserve_native_cleanup(struct nvk_cgl_bridge *b)
{
   if (!b->cleanup_reserve) b->cleanup_reserve = calloc(1, sizeof(*b->cleanup_reserve));
   return b->cleanup_reserve != NULL;
}

static void free_native_cleanup(struct native_cleanup *cleanup)
{
   for (unsigned i = 0; i < 2; ++i)
      if (cleanup->surfaces[i]) CFRelease(cleanup->surfaces[i]);
   free(cleanup);
}

static void defer_native_cleanup(struct nvk_cgl_bridge *b)
{
   void *share = *(void **)((char *)b->engine + 0x5b00);
   struct native_cleanup *cleanup = b->cleanup_reserve;
   if (cleanup) {
      cleanup->share = share;
      cleanup->textures[0] = b->present_texture;
      b->present_texture = 0;
      for (unsigned i = 0; i < 2; ++i) {
         cleanup->textures[i + 1] = b->gpu_present[i].texture;
         cleanup->fences[i] = b->gpu_present[i].fence;
         cleanup->surfaces[i] = b->gpu_present[i].surface;
         memset(&b->gpu_present[i], 0, sizeof(b->gpu_present[i]));
      }
      b->cleanup_reserve = NULL;
   }
   pthread_mutex_lock(&bridges_lock);
   bool peer = false;
   for (struct nvk_cgl_bridge *other = bridges; other; other = other->next)
      if (other != b && *(void **)((char *)other->engine + 0x5b00) == share) {
         peer = true; break;
      }
   if (peer && cleanup) {
      cleanup->next = native_cleanups; native_cleanups = cleanup;
      cleanup = NULL;
   }
   struct native_cleanup *discard = NULL;
   if (!peer) {
      /* Final native share-group destruction owns these remaining GL objects. */
      struct native_cleanup **link = &native_cleanups;
      while (*link) {
         struct native_cleanup *item = *link;
         if (item->share == share) {
            *link = item->next; item->next = discard; discard = item;
         } else link = &item->next;
      }
   }
   pthread_mutex_unlock(&bridges_lock);
   if (cleanup) free_native_cleanup(cleanup);
   while (discard) {
      struct native_cleanup *next = discard->next;
      free_native_cleanup(discard); discard = next;
   }
}

static void drain_native_cleanups(struct nvk_cgl_bridge *b)
{
   if (b->context != CGLGetCurrentContext()) return;
   void *share = *(void **)((char *)b->engine + 0x5b00);
   struct native_cleanup *pending = NULL;
   pthread_mutex_lock(&bridges_lock);
   struct native_cleanup **link = &native_cleanups;
   while (*link) {
      struct native_cleanup *item = *link;
      if (item->share == share) {
         *link = item->next; item->next = pending; pending = item;
      } else link = &item->next;
   }
   pthread_mutex_unlock(&bridges_lock);
   if (!pending) return;
   use_original(b);
   while (pending) {
      struct native_cleanup *next = pending->next;
      for (unsigned i = 0; i < 2; ++i) {
         if (pending->fences[i]) {
            GLenum result;
            result = glClientWaitSync(pending->fences[i], GL_SYNC_FLUSH_COMMANDS_BIT, 5000000000ULL);
            if (result != GL_ALREADY_SIGNALED && result != GL_CONDITION_SATISFIED) {
               b->apple_uncertain = true;
               pthread_mutex_lock(&bridges_lock);
               struct native_cleanup **tail = &native_quarantine;
               while (*tail) tail = &(*tail)->next;
               *tail = pending;
               pthread_mutex_unlock(&bridges_lock);
               use_zink(b);
               return;
            }
            glDeleteSync(pending->fences[i]);
         }
      }
      glDeleteTextures(3, pending->textures);
      free_native_cleanup(pending); pending = next;
   }
   use_zink(b);
}

static void release_gpu_present(struct nvk_cgl_bridge *b)
{
   if (b->apple_uncertain) return;
   bool current = b->context == CGLGetCurrentContext();
   if (current) {
      drain_native_cleanups(b);
      if (b->apple_uncertain) return;
      use_original(b);
      for (unsigned i = 0; i < 2; ++i) if (b->gpu_present[i].fence) {
         GLenum result = glClientWaitSync(b->gpu_present[i].fence, GL_SYNC_FLUSH_COMMANDS_BIT, 5000000000ULL);
         if (result != GL_ALREADY_SIGNALED && result != GL_CONDITION_SATISFIED) {
            b->apple_uncertain = true;
            use_zink(b);
            return;
         }
      }
      for (unsigned i = 0; i < 2; ++i) {
         if (b->gpu_present[i].fence) glDeleteSync(b->gpu_present[i].fence);
         if (b->gpu_present[i].texture) glDeleteTextures(1, &b->gpu_present[i].texture);
      }
   } else defer_native_cleanup(b);
   if (b->zink) nvk_zink_present_release(b->zink);
   for (unsigned i = 0; i < 2; ++i) {
      if (b->gpu_present[i].surface) CFRelease(b->gpu_present[i].surface);
      memset(&b->gpu_present[i], 0, sizeof(b->gpu_present[i]));
   }
   b->gpu_width = b->gpu_height = b->gpu_slot = 0;
   if (current) use_zink(b);
}

static IOSurfaceRef create_present_surface(unsigned width, unsigned height)
{
   size_t stride = IOSurfaceAlignProperty(kIOSurfaceBytesPerRow, (size_t)width * 4);
   if (stride > SIZE_MAX / height) return NULL;
   size_t size = IOSurfaceAlignProperty(kIOSurfaceAllocSize, stride * height);
   if (size > INT32_MAX) return NULL;
   CFMutableDictionaryRef properties = CFDictionaryCreateMutable(NULL, 0,
      &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
   if (!properties) return NULL;
   const CFStringRef keys[] = {kIOSurfaceWidth, kIOSurfaceHeight, kIOSurfaceBytesPerElement,
                              kIOSurfaceBytesPerRow, kIOSurfaceAllocSize, kIOSurfacePixelFormat};
   int64_t values[] = {width, height, 4, (int64_t)stride, (int64_t)size, 0x42475241};
   for (unsigned i = 0; i < 6; ++i) {
      CFNumberRef n = CFNumberCreate(NULL, kCFNumberSInt64Type, &values[i]);
      if (!n) { CFRelease(properties); return NULL; }
      CFDictionarySetValue(properties, keys[i], n);
      CFRelease(n);
   }
   IOSurfaceRef surface = IOSurfaceCreate(properties);
   CFRelease(properties);
   return surface;
}

static int prepare_gpu_present(struct nvk_cgl_bridge *b)
{
   if (b->gpu_width == b->width && b->gpu_height == b->height) return 1;
   if (!reserve_native_cleanup(b)) return 0;
   release_gpu_present(b);
   if (b->apple_uncertain || !nvk_zink_make_current(b->zink)) return 0;
   for (unsigned i = 0; i < 2; ++i) {
      b->gpu_present[i].surface = create_present_surface(b->width, b->height);
      if (!b->gpu_present[i].surface ||
          !nvk_zink_present_import(b->zink, i, b->gpu_present[i].surface)) goto failure;
      use_original(b);
      glGenTextures(1, &b->gpu_present[i].texture);
      glBindTexture(GL_TEXTURE_RECTANGLE, b->gpu_present[i].texture);
      glTexParameteri(GL_TEXTURE_RECTANGLE, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
      glTexParameteri(GL_TEXTURE_RECTANGLE, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
      CGLError error = CGLTexImageIOSurface2D(b->context, GL_TEXTURE_RECTANGLE, GL_RGBA,
         b->width, b->height, GL_BGRA, GL_UNSIGNED_INT_8_8_8_8_REV, b->gpu_present[i].surface, 0);
      GLenum gl_error = glGetError();
      use_zink(b);
      if (error != kCGLNoError || gl_error != GL_NO_ERROR) goto failure;
   }
   b->gpu_width = b->width;
   b->gpu_height = b->height;
   return 1;
failure:
   release_gpu_present(b);
   return 0;
}

static bool opaque_drawable(CGLContextObj context)
{
   GLint opacity = 1;
   CGLGetParameter(context, kCGLCPSurfaceOpacity, &opacity);
   return opacity != 0;
}

static int gpu_present(struct nvk_cgl_bridge *b)
{
   unsigned slot = b->gpu_slot;
   use_original(b);
   if (b->gpu_present[slot].fence) {
      GLenum result;
      result = glClientWaitSync(b->gpu_present[slot].fence,
                                GL_SYNC_FLUSH_COMMANDS_BIT, 5000000000ULL);
      glDeleteSync(b->gpu_present[slot].fence);
      b->gpu_present[slot].fence = NULL;
      if (result != GL_ALREADY_SIGNALED && result != GL_CONDITION_SATISFIED) { b->apple_uncertain = true; use_zink(b); return fail("Apple IOSurface fence wait failed"); }
   }
   use_zink(b);
   IOSurfaceRef surface = b->gpu_present[slot].surface;
   if (IOSurfaceLock(surface, 0, NULL) != kIOReturnSuccess) return fail("Cannot lock GPU presentation surface");
   int ok = nvk_zink_present_begin(b->zink, slot, 1, opaque_drawable(b->context));
   if (!nvk_zink_present_wait(b->zink, slot)) {
      nvk_zink_present_release(b->zink);
      ok = 0;
   }
   ok &= IOSurfaceUnlock(surface, 0, NULL) == kIOReturnSuccess;
   if (!ok) return fail("Zink IOSurface GPU copy failed");
   use_original(b);
   if (!b->present_fbo) glGenFramebuffers(1, &b->present_fbo);
   glBindFramebuffer(GL_READ_FRAMEBUFFER, b->present_fbo);
   glFramebufferTexture2D(GL_READ_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                          GL_TEXTURE_RECTANGLE, b->gpu_present[slot].texture, 0);
   glReadBuffer(GL_COLOR_ATTACHMENT0);
   glBindFramebuffer(GL_DRAW_FRAMEBUFFER, 0);
   GLint double_buffer = 0;
   glGetIntegerv(GL_DOUBLEBUFFER, &double_buffer);
   glDrawBuffer(double_buffer ? GL_BACK : GL_FRONT);
   glBlitFramebuffer(0, 0, b->width, b->height, 0, 0, b->width, b->height,
                      GL_COLOR_BUFFER_BIT, GL_NEAREST);
   /* Native presentation must select the window framebuffer on both sides,
    * rather than leaving our offscreen IOSurface selected for reads. */
   glBindFramebuffer(GL_READ_FRAMEBUFFER, 0);
   GLenum error = glGetError();
   CGLError swap_error = CGLFlushDrawable(b->context);
   b->gpu_present[slot].fence = glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE, 0);
   glFlush();
   bool fenced = b->gpu_present[slot].fence != NULL;
   use_zink(b);
   if (error != GL_NO_ERROR || swap_error != kCGLNoError || !fenced) {
      b->apple_uncertain = true;
      return fail("Apple IOSurface blit or swap failed; resources quarantined");
   }
   b->gpu_slot ^= 1;
   return nvk_zink_swap(b->zink);
}

/* 2 is a deferred native acquire, rather than a completed publication. */
static int present_drawable(CGLContextObj context)
{
   last_error = NULL;
   if (!context || context != CGLGetCurrentContext()) return fail("Present requires the context current");
   struct nvk_cgl_bridge *b = bridge_current(*(void **)context);
   if (!b) return 0;
   drain_native_cleanups(b);
   if (b->apple_uncertain) return fail("Apple presentation completion uncertain; resources quarantined");
   if (!b->present_configured) {
      const char *mode = getenv("NVKZINK_PRESENT");
      if (mode && strcmp(mode, "auto") && strcmp(mode, "gpu") && strcmp(mode, "native") && strcmp(mode, "cpu"))
         return fail("NVKZINK_PRESENT must be auto, native, gpu or cpu");
      b->gpu_disabled = mode && !strcmp(mode, "cpu");
      b->native_required = mode && !strcmp(mode, "native");
      b->native_present = !mode || !strcmp(mode, "auto") || b->native_required;
      b->present_configured = true;
   }
   if (b->native_present) {
      uint32_t vendor = 0, device = 0;
      if (!nvk_zink_device_info(b->zink, &vendor, &device)) return fail("Native presentation requires an NVK device");
      void *frame = NULL; IOSurfaceRef surface = NULL;
      int acquired = nvk_native_acquire(context, vendor, device, b->width, b->height, opaque_drawable(context), &frame, &surface);
      if (acquired == 2) return 2; /* Main-thread setup/resize or minimized window. */
      if (acquired != 1) {
         if (acquired == NVK_NATIVE_NO_VIEW) {
            GLint has_drawable = 0;
            if (CGLGetParameter(context, kCGLCPHasDrawable, &has_drawable) == kCGLNoError &&
                !has_drawable)
               return 2; /* No output yet: do not blit into an unattached Apple FBO. */
         }
         if (b->native_required) return fail("Native presentation needs an AppKit view and matching NVIDIA drawable");
         /* CGL clients without an NSView retain the GPU IOSurface path.
          * Never fall back after an uncertain submission or to CPU pixels. */
         if (acquired != NVK_NATIVE_NO_VIEW) {
            nvk_native_remove(context);
            b->native_present = false;
         }
         /* Early Cocoa swaps may precede setView/update. Keep auto eligible
          * for native presentation once that view is attached. */
      } else {
      int copied = nvk_zink_present_native(b->zink, surface, opaque_drawable(context));
      if (copied < 0) {
         /* The retained frame pins the drawable while uncertain GPU work can
          * still reference its pages. The Zink share group is quarantined. */
         return fail("Native NVK completion uncertain; drawable quarantined");
      }
      int ok = copied && nvk_native_publish(frame);
      nvk_native_release(frame);
      if (!ok) return fail("Native NVK drawable handoff failed");
      return nvk_zink_swap(b->zink);
      }
   }
   if (!b->gpu_disabled) {
      if (!prepare_gpu_present(b)) return fail("GPU presentation needs NVK host import and Apple IOSurface textures");
      return gpu_present(b);
   }
   size_t row = (size_t)b->width * 4;
   if (row > SIZE_MAX / b->height) return fail("Drawable size overflow");
   size_t size = row * b->height;
   if (size > b->present_capacity) {
      unsigned char *pixels = realloc(b->present_pixels, size);
      if (!pixels) return fail("Out of memory during presentation");
      b->present_pixels = pixels; b->present_capacity = size;
   }
   int ok = nvk_zink_readback_bottom_up(b->zink, b->present_pixels, size, row);
   if (!ok) return fail("Zink readback failed");
   if (opaque_drawable(context))
      for (size_t i = 3; i < size; i += 4) b->present_pixels[i] = 255;
   /* Apple GL state is private to the presentation side. The application's
    * Mesa GL state is untouched, including its FBOs, pixel stores and buffers. */
   use_original(b);
   if (!reserve_native_cleanup(b)) { use_zink(b); return fail("Cannot reserve native presentation cleanup"); }
   if (!b->present_texture) glGenTextures(1, &b->present_texture);
   if (!b->present_fbo) glGenFramebuffers(1, &b->present_fbo);
   glBindTexture(GL_TEXTURE_2D, b->present_texture);
   glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
   if (b->present_width != b->width || b->present_height != b->height) {
      glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, b->width, b->height, 0,
                   GL_RGBA, GL_UNSIGNED_BYTE, b->present_pixels);
      b->present_width = b->width; b->present_height = b->height;
   } else {
      glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, b->width, b->height,
                      GL_RGBA, GL_UNSIGNED_BYTE, b->present_pixels);
   }
   glBindFramebuffer(GL_READ_FRAMEBUFFER, b->present_fbo);
   glFramebufferTexture2D(GL_READ_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                          GL_TEXTURE_2D, b->present_texture, 0);
   glReadBuffer(GL_COLOR_ATTACHMENT0);
   glBindFramebuffer(GL_DRAW_FRAMEBUFFER, 0);
   GLint double_buffer = 0;
   glGetIntegerv(GL_DOUBLEBUFFER, &double_buffer);
   glDrawBuffer(double_buffer ? GL_BACK : GL_FRONT);
   glBlitFramebuffer(0, 0, b->width, b->height, 0, 0, b->width, b->height,
                      GL_COLOR_BUFFER_BIT, GL_NEAREST);
   glBindFramebuffer(GL_READ_FRAMEBUFFER, 0);
   GLenum error = glGetError();
   CGLError swap_error = CGLFlushDrawable(context);
   use_zink(b);
   if (error != GL_NO_ERROR || swap_error != kCGLNoError) return fail("Apple drawable upload or swap failed");
   return nvk_zink_swap(b->zink);
}

EXPORT int nvk_cgl_present(CGLContextObj context)
{
   if (!context) return fail("Present requires an attached Zink context");
   CGLContextObj previous = CGLGetCurrentContext();
   if (context == previous) return present_drawable(context) != 0;
   if (CGLLockContext(context)) return fail("Cannot lock presentation context");
   pthread_mutex_lock(&bridges_lock);
   bool attached = find_bridge(context) != NULL;
   pthread_mutex_unlock(&bridges_lock);
   if (!attached) { CGLUnlockContext(context); return fail("Present requires an attached Zink context"); }
   struct nvk_zink_context *previous_zink = nvk_zink_get_current();
   if (previous) CGLRetainContext(previous);
   if (CGLSetCurrentContext(context)) {
      if (previous) CGLReleaseContext(previous);
      CGLUnlockContext(context);
      return fail("Cannot bind presentation context");
   }
   int presented = present_drawable(context) != 0;
   const char *presentation_error = last_error;
   CGLError restored = CGLSetCurrentContext(previous);
   int mesa_restored = nvk_zink_make_current(previous_zink);
   if (!mesa_restored) nvk_zink_make_current(NULL);
   if (previous) CGLReleaseContext(previous);
   CGLUnlockContext(context);
   if (restored || !mesa_restored) return fail("Cannot restore context after presentation");
   last_error = presentation_error;
   return presented;
}

EXPORT int nvk_cgl_detach(CGLContextObj context)
{
   if (!context || context != CGLGetCurrentContext()) return fail("Detach requires the context current");
   if (CGLLockContext(context)) return fail("Cannot lock CGL context");
   pthread_mutex_lock(&bridges_lock);
   struct nvk_cgl_bridge **link = &bridges;
   while (*link && (*link)->context != context) link = &(*link)->next;
   struct nvk_cgl_bridge *b = *link;
   if (b) { *link = b->next; atomic_fetch_add(&generation, 1); }
   pthread_mutex_unlock(&bridges_lock);
   if (!b) { CGLUnlockContext(context); return fail("Context is not attached"); }
   nvk_native_remove(b->context);
   release_gpu_present(b);
   use_original(b);
   bool automatic = b->automatic;
   if (automatic) *(void ***)((char *)b->engine + 0xb2d0) = b->engine_back;
   if (b->apple_uncertain) {
      pthread_mutex_lock(&bridges_lock);
      b->next = quarantined_bridges; quarantined_bridges = b;
      pthread_mutex_unlock(&bridges_lock);
      CGLUnlockContext(context);
      if (!automatic) CGLReleaseContext(context);
      return 1;
   }
   if (b->present_texture) glDeleteTextures(1, &b->present_texture);
   if (b->present_fbo) glDeleteFramebuffers(1, &b->present_fbo);
   nvk_zink_destroy(b->zink);
   free(b->present_pixels);
   free(b->cleanup_reserve);
   free(b);
   CGLUnlockContext(context);
   if (!automatic) CGLReleaseContext(context);
   return 1;
}
