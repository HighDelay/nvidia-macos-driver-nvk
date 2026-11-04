/* SPDX-License-Identifier: MIT */
#include "nvk_zink_private.h"
#include "pipe/p_context.h"
#include "util/u_atomic.h"
#include "util/u_inlines.h"
#include "util/u_threaded_context.h"
#include "util/format/u_format.h"
#include "zink/zink_context.h"
#include "zink/zink_resource.h"
#include "state_tracker/st_format.h"
#include "state_tracker/st_texture.h"
#include "state_tracker/st_sampler_view.h"
#include "main/context.h"
#include "main/errors.h"
#include "main/framebuffer.h"
#include "main/hash.h"
#include "main/teximage.h"
#include "main/texobj.h"
#include <limits.h>
#include <pthread.h>
#include <stdlib.h>

#define NVK_EXPORT __attribute__((visibility("default")))
/* Keep this Gallium frontend independent of Apple's OpenGL headers. */
enum { IO_BAD_CONTEXT=10004, IO_BAD_STATE=10007, IO_BAD_VALUE=10008,
       IO_BAD_ENUM=10010, IO_BAD_ADDRESS=10014, IO_BAD_ALLOC=10016 };
struct surface_binding {
   struct surface_binding *next;
   struct gl_texture_object *texture;
   struct pipe_resource *image, *buffer;
   struct pipe_fence_handle *fence;
   IOSurfaceRef surface;
   void *base;
   unsigned plane, offset, row, layer;
   uint32_t seed;
   uint64_t published;
   bool locked, readonly;
};
struct nvk_iosurface_group {
   pthread_mutex_t mutex;
   int refs, count;
   struct surface_binding *bindings;
};

int nvk_iosurface_group_init(struct nvk_zink_context *ctx, struct nvk_zink_context *share)
{
   if (share) { ctx->iosurfaces=share->iosurfaces; p_atomic_inc(&ctx->iosurfaces->refs); return 1; }
   struct nvk_iosurface_group *g=calloc(1,sizeof(*g));
   if (!g) return 0;
   if (pthread_mutex_init(&g->mutex,NULL)) { free(g); return 0; }
   g->refs=1; ctx->iosurfaces=g; return 1;
}
static bool usable(struct nvk_zink_context *ctx)
{
   return ctx && ctx->iosurfaces && !p_atomic_read(&ctx->screen->uncertain);
}
static struct zink_context *backend(struct nvk_zink_context *ctx)
{
   return zink_context(threaded_context_unwrap_sync(ctx->st->pipe));
}
static uint64_t epoch(struct surface_binding *b)
{
   return p_atomic_read(&zink_resource(b->image)->nvk_iosurface_writes);
}
static bool dirty(struct surface_binding *b) { return epoch(b)!=b->published; }
static bool active(struct surface_binding *b)
{
   return b->texture->surface_based && b->texture->pt==b->image;
}
static void release_binding(struct surface_binding *b)
{
   /* Every GPU operation referencing these pages has completed. */
   p_atomic_set(&zink_resource(b->image)->nvk_iosurface_tracked,0);
   _mesa_reference_texobj(&b->texture,NULL);
   pipe_resource_reference(&b->image,NULL); pipe_resource_reference(&b->buffer,NULL);
   CFRelease(b->surface); free(b);
}
static bool obsolete(struct nvk_zink_context *ctx, struct surface_binding *b)
{
   if (!active(b)) return true;
   return p_atomic_read(&b->texture->RefCount)==1 &&
      _mesa_lookup_texture(ctx->st->ctx,b->texture->Name)!=b->texture;
}
static void raw_flush(struct nvk_zink_context *ctx)
{
   if (st_api_get_current()==ctx->st) {
      void (*flush)(void)=(void *)nvk_zink_get_proc_address("glFlush"); flush();
   } else st_context_flush(ctx->st,0,NULL,NULL,NULL);
   threaded_context_unwrap_sync(ctx->st->pipe);
}
static bool import_pages(struct nvk_zink_context *ctx, struct surface_binding *b)
{
   void *base=IOSurfaceGetBaseAddress(b->surface);
   if (!base) return false;
   if (b->buffer && b->base==base) return true;
   size_t size=IOSurfaceGetAllocSize(b->surface);
   if (!size || size>INT_MAX) return false;
   struct pipe_resource t={0}; t.target=PIPE_BUFFER; t.format=PIPE_FORMAT_R8_UNORM;
   t.width0=(unsigned)size; t.height0=t.depth0=t.array_size=1; t.usage=PIPE_USAGE_STAGING;
   struct pipe_screen *s=ctx->screen->fscreen.screen;
   struct pipe_resource *buffer=s->resource_from_user_memory ? s->resource_from_user_memory(s,&t,base) : NULL;
   if (!buffer) return false;
   pipe_resource_reference(&b->buffer,NULL); b->buffer=buffer; b->base=base; return true;
}
static bool copy_surface(struct nvk_zink_context *ctx, struct surface_binding *b, bool output)
{
   if (b->locked || b->fence) return false;
   uint32_t seed=0;
   b->readonly=!output;
   if (IOSurfaceLock(b->surface,output?0:kIOSurfaceLockReadOnly,&seed)!=kIOReturnSuccess) return false;
   b->locked=true;
   if (!import_pages(ctx,b)) goto safe_failure;
   struct pipe_context *pipe=ctx->st->pipe;
   if (!pipe->image_copy_buffer) goto safe_failure;
   uint64_t before=epoch(b);
   struct pipe_box box={.width=b->image->width0,.height=b->image->height0,.depth=1};
   pipe->memory_barrier(pipe,PIPE_BARRIER_MAPPED_BUFFER);
   pipe->image_copy_buffer(pipe,output?b->buffer:b->image,output?b->image:b->buffer,
                           b->offset,b->row,b->layer,0,&box);
   pipe->memory_barrier(pipe,PIPE_BARRIER_MAPPED_BUFFER);
   st_context_flush(ctx->st,0,&b->fence,NULL,NULL);
   struct pipe_screen *screen=ctx->screen->fscreen.screen;
   if (!b->fence || !screen->fence_finish(screen,pipe,b->fence,5000000000ull)) {
      /* Keep the fence, imported pages, image, surface and lock alive. No
       * further context can use this screen after uncertain completion. */
      p_atomic_set(&ctx->screen->uncertain,1);
      _mesa_error(ctx->st->ctx,GL_OUT_OF_MEMORY,"IOSurface GPU completion uncertain");
      return false;
   }
   screen->fence_reference(screen,&b->fence,NULL);
   threaded_context_unwrap_sync(pipe);
   if (IOSurfaceUnlock(b->surface,output?0:kIOSurfaceLockReadOnly,&seed)!=kIOReturnSuccess) {
      p_atomic_set(&ctx->screen->uncertain,1); return false;
   }
   b->locked=false; b->seed=seed; b->published=output?before:epoch(b);
   return true;
safe_failure:
   IOSurfaceUnlock(b->surface,output?0:kIOSurfaceLockReadOnly,NULL); b->locked=false;
   return false;
}

NVK_EXPORT int nvk_zink_flush_iosurfaces(struct nvk_zink_context *ctx)
{
   if (!ctx || !ctx->iosurfaces || !p_atomic_read(&ctx->iosurfaces->count)) return 1;
   if (!usable(ctx)) return 0;
   if (st_api_get_current()==ctx->st && _mesa_inside_begin_end(ctx->st->ctx)) return 1;
   raw_flush(ctx); struct zink_context *writer=backend(ctx);
   struct nvk_iosurface_group *g=ctx->iosurfaces; int ok=1;
   pthread_mutex_lock(&g->mutex);
   struct surface_binding **link=&g->bindings;
   while (*link) {
      struct surface_binding *b=*link;
      if (dirty(b) && p_atomic_read(&zink_resource(b->image)->nvk_iosurface_writer)==writer) {
         if (!copy_surface(ctx,b,true)) { ok=0; break; }
      }
      if (!dirty(b) && obsolete(ctx,b)) {
         *link=b->next; release_binding(b); p_atomic_dec(&g->count);
      } else link=&b->next;
   }
   pthread_mutex_unlock(&g->mutex);
   if (!ok && !p_atomic_read(&ctx->screen->uncertain))
      _mesa_error(ctx->st->ctx,GL_OUT_OF_MEMORY,"Cannot publish IOSurface GPU writes");
   return ok;
}
static bool attached_to(struct gl_framebuffer *fb, struct gl_texture_object *texture)
{
   if (!fb || !fb->Name) return false;
   for (unsigned i=0;i<BUFFER_COUNT;++i)
      if (fb->Attachment[i].Texture==texture) return true;
   return false;
}
static bool selected(struct nvk_zink_context *ctx, struct surface_binding *b, unsigned target, bool framebuffer)
{
   if (!active(b)) return false;
   if (!framebuffer) return target==GL_TEXTURE_RECTANGLE &&
      _mesa_get_current_tex_object(ctx->st->ctx,target)==b->texture;
   if (target==GL_FRAMEBUFFER || target==GL_DRAW_FRAMEBUFFER)
      if (attached_to(ctx->st->ctx->DrawBuffer,b->texture)) return true;
   if (target==GL_FRAMEBUFFER || target==GL_READ_FRAMEBUFFER)
      if (attached_to(ctx->st->ctx->ReadBuffer,b->texture)) return true;
   return false;
}
NVK_EXPORT int nvk_zink_refresh_iosurfaces(struct nvk_zink_context *ctx, unsigned target, int framebuffer)
{
   if (!ctx || !ctx->iosurfaces || !p_atomic_read(&ctx->iosurfaces->count)) return 1;
   if (!usable(ctx) || st_api_get_current()!=ctx->st) return 0;
   if (_mesa_inside_begin_end(ctx->st->ctx)) return 1;
   struct nvk_iosurface_group *g=ctx->iosurfaces; bool stale=false;
   pthread_mutex_lock(&g->mutex);
   for (struct surface_binding *b=g->bindings;b;b=b->next)
      if (selected(ctx,b,target,framebuffer) && IOSurfaceGetSeed(b->surface)!=b->seed) { stale=true; break; }
   pthread_mutex_unlock(&g->mutex);
   if (!stale) return 1;
   raw_flush(ctx);
   int ok=1; pthread_mutex_lock(&g->mutex);
   for (struct surface_binding *b=g->bindings;b;b=b->next) {
      if (!selected(ctx,b,target,framebuffer) || IOSurfaceGetSeed(b->surface)==b->seed) continue;
      if (dirty(b)) {
         _mesa_error(ctx->st->ctx,GL_INVALID_OPERATION,"IOSurface changed with unflushed GL writes");
         ok=0; break;
      }
      if (!copy_surface(ctx,b,false)) { ok=0; break; }
   }
   pthread_mutex_unlock(&g->mutex); return ok;
}

static enum pipe_format surface_format(unsigned internal, unsigned format, unsigned type)
{
   if (format==GL_BGRA && type==GL_UNSIGNED_INT_8_8_8_8_REV) {
      if (internal==GL_RGBA || internal==GL_RGBA8) return PIPE_FORMAT_B8G8R8A8_UNORM;
      if (internal==GL_RGB || internal==GL_RGB8) return PIPE_FORMAT_B8G8R8X8_UNORM;
      if (internal==GL_SRGB_ALPHA || internal==GL_SRGB8_ALPHA8) return PIPE_FORMAT_B8G8R8A8_SRGB;
      if (internal==GL_SRGB || internal==GL_SRGB8) return PIPE_FORMAT_B8G8R8X8_SRGB;
   }
   if (format==GL_RGBA) {
      if (type==GL_UNSIGNED_BYTE && (internal==GL_RGBA || internal==GL_RGBA8)) return PIPE_FORMAT_R8G8B8A8_UNORM;
      if (type==GL_HALF_FLOAT && (internal==GL_RGBA || internal==GL_RGBA16F)) return PIPE_FORMAT_R16G16B16A16_FLOAT;
      if (type==GL_FLOAT && (internal==GL_RGBA || internal==GL_RGBA32F)) return PIPE_FORMAT_R32G32B32A32_FLOAT;
      if (type==GL_UNSIGNED_SHORT && (internal==GL_RGBA || internal==GL_RGBA16)) return PIPE_FORMAT_R16G16B16A16_UNORM;
   }
   if (format==GL_BGRA && type==GL_UNSIGNED_INT_2_10_10_10_REV && (internal==GL_RGBA || internal==GL_RGB10_A2)) return PIPE_FORMAT_B10G10R10A2_UNORM;
   if (format==GL_BGRA && type==GL_UNSIGNED_SHORT_1_5_5_5_REV && (internal==GL_RGBA || internal==GL_RGB5_A1)) return PIPE_FORMAT_B5G5R5A1_UNORM;
   if (format==GL_BGRA && type==GL_UNSIGNED_SHORT_4_4_4_4_REV && (internal==GL_RGBA || internal==GL_RGBA4)) return PIPE_FORMAT_B4G4R4A4_UNORM;
   if (format==GL_RED) {
      if (type==GL_UNSIGNED_BYTE && (internal==GL_RED || internal==GL_R8)) return PIPE_FORMAT_R8_UNORM;
      if (type==GL_UNSIGNED_SHORT && (internal==GL_RED || internal==GL_R16)) return PIPE_FORMAT_R16_UNORM;
      if (type==GL_HALF_FLOAT && (internal==GL_RED || internal==GL_R16F)) return PIPE_FORMAT_R16_FLOAT;
      if (type==GL_FLOAT && (internal==GL_RED || internal==GL_R32F)) return PIPE_FORMAT_R32_FLOAT;
   }
   if (format==GL_RG) {
      if (type==GL_UNSIGNED_BYTE && (internal==GL_RG || internal==GL_RG8)) return PIPE_FORMAT_R8G8_UNORM;
      if (type==GL_UNSIGNED_SHORT && (internal==GL_RG || internal==GL_RG16)) return PIPE_FORMAT_R16G16_UNORM;
      if (type==GL_HALF_FLOAT && (internal==GL_RG || internal==GL_RG16F)) return PIPE_FORMAT_R16G16_FLOAT;
      if (type==GL_FLOAT && (internal==GL_RG || internal==GL_RG32F)) return PIPE_FORMAT_R32G32_FLOAT;
   }
   if (format==GL_ALPHA && type==GL_UNSIGNED_BYTE && (internal==GL_ALPHA || internal==GL_ALPHA8)) return PIPE_FORMAT_A8_UNORM;
   if (format==GL_LUMINANCE && type==GL_UNSIGNED_BYTE) {
      if (internal==GL_LUMINANCE || internal==GL_LUMINANCE8) return PIPE_FORMAT_L8_UNORM;
      if (internal==GL_INTENSITY || internal==GL_INTENSITY8) return PIPE_FORMAT_I8_UNORM;
   }
   if (format==GL_LUMINANCE_ALPHA && type==GL_UNSIGNED_BYTE && (internal==GL_LUMINANCE_ALPHA || internal==GL_LUMINANCE8_ALPHA8)) return PIPE_FORMAT_L8A8_UNORM;
   return PIPE_FORMAT_NONE;
}
NVK_EXPORT int nvk_zink_bind_iosurface(struct nvk_zink_context *ctx, unsigned target,
   unsigned internal, int width, int height, unsigned format, unsigned type, IOSurfaceRef surface, unsigned plane)
{
   if (!usable(ctx) || st_api_get_current()!=ctx->st) return IO_BAD_CONTEXT;
   if (!surface) return IO_BAD_ADDRESS;
   if (_mesa_inside_begin_end(ctx->st->ctx)) return IO_BAD_STATE;
   if (target!=GL_TEXTURE_RECTANGLE) return IO_BAD_ENUM;
   if (width<=0 || height<=0 || width>ctx->st->ctx->Const.MaxTextureRectSize || height>ctx->st->ctx->Const.MaxTextureRectSize) return IO_BAD_VALUE;
   struct gl_texture_object *texture=_mesa_get_current_tex_object(ctx->st->ctx,target);
   if (!texture || !texture->Name || texture->Immutable) return IO_BAD_STATE;
   enum pipe_format fmt=surface_format(internal,format,type);
   if (fmt==PIPE_FORMAT_NONE) return IO_BAD_ENUM;
   unsigned element=util_format_get_blocksize(fmt);
   size_t count=IOSurfaceGetPlaneCount(surface);
   if ((count && plane>=count) || (!count && plane)) return IO_BAD_VALUE;
   size_t pw=count?IOSurfaceGetWidthOfPlane(surface,plane):IOSurfaceGetWidth(surface);
   size_t ph=count?IOSurfaceGetHeightOfPlane(surface,plane):IOSurfaceGetHeight(surface);
   size_t bpe=count?IOSurfaceGetBytesPerElementOfPlane(surface,plane):IOSurfaceGetBytesPerElement(surface);
   size_t row=count?IOSurfaceGetBytesPerRowOfPlane(surface,plane):IOSurfaceGetBytesPerRow(surface);
   size_t size=IOSurfaceGetAllocSize(surface);
   if ((size_t)width>pw || (size_t)height>ph || bpe!=element || !row || row%element || row<(size_t)width*element || row>UINT_MAX/(unsigned)height || size>INT_MAX) return IO_BAD_VALUE;
   struct pipe_screen *screen=ctx->screen->fscreen.screen;
   if (!screen->resource_from_user_memory || !ctx->st->pipe->image_copy_buffer ||
       !screen->is_format_supported(screen,fmt,PIPE_TEXTURE_RECT,0,0,PIPE_BIND_SAMPLER_VIEW|PIPE_BIND_RENDER_TARGET)) return IO_BAD_ENUM;
   if (!nvk_zink_flush_iosurfaces(ctx)) return IO_BAD_STATE;
   struct surface_binding *b=calloc(1,sizeof(*b)); if (!b) return IO_BAD_ALLOC;
   b->surface=(IOSurfaceRef)CFRetain(surface);b->plane=plane;b->row=(unsigned)row;b->layer=(unsigned)row*height;
   struct pipe_resource t={0};t.target=PIPE_TEXTURE_RECT;t.format=fmt;t.width0=width;t.height0=height;
   t.depth0=t.array_size=1;t.bind=PIPE_BIND_RENDER_TARGET|PIPE_BIND_SAMPLER_VIEW;t.usage=PIPE_USAGE_DEFAULT;
   b->image=screen->resource_create(screen,&t);
   if (!b->image) { CFRelease(b->surface);free(b);return IO_BAD_ALLOC; }
   _mesa_reference_texobj(&b->texture,texture);
   p_atomic_set(&zink_resource(b->image)->nvk_iosurface_tracked,1);
   int error=IO_BAD_ALLOC;
   if (IOSurfaceLock(surface,kIOSurfaceLockReadOnly,NULL)!=kIOReturnSuccess) goto failed;
   void *base=IOSurfaceGetBaseAddress(surface),*part=count?IOSurfaceGetBaseAddressOfPlane(surface,plane):base;
   bool layout=base && part && (uintptr_t)part>=(uintptr_t)base;
   uint64_t offset=layout?(uintptr_t)part-(uintptr_t)base:UINT64_MAX;
   layout=layout && offset<=UINT_MAX && offset<=size && b->layer<=size-offset;
   int unlocked=IOSurfaceUnlock(surface,kIOSurfaceLockReadOnly,NULL)==kIOReturnSuccess;
   if (!layout || !unlocked) { error=IO_BAD_VALUE;goto failed; }
   b->offset=(unsigned)offset;
   if (!copy_surface(ctx,b,false)) goto failed;
   _mesa_lock_texture(ctx->st->ctx,texture);
   _mesa_clear_texture_object(ctx->st->ctx,texture,NULL);
   struct gl_texture_image *image=_mesa_get_tex_image(ctx->st->ctx,texture,target,0);
   if (!image) { _mesa_unlock_texture(ctx->st->ctx,texture);goto failed; }
   _mesa_init_teximage_fields(ctx->st->ctx,image,width,height,1,0,internal,st_pipe_format_to_mesa_format(fmt));
   texture->surface_based=true;texture->surface_format=fmt;texture->lastLevel=0;
   pipe_resource_reference(&texture->pt,b->image);pipe_resource_reference(&image->pt,b->image);
   st_texture_release_all_sampler_views(ctx->st,texture);
   texture->needs_validation=true;texture->needs_yuv_plane_views=false;
   _mesa_update_texture_object_swizzle(ctx->st->ctx,texture);_mesa_dirty_texobj(ctx->st->ctx,texture);
   _mesa_update_fbo_texture(ctx->st->ctx,texture,0,0);
   ctx->st->ctx->Shared->HasExternallySharedImages=true;
   _mesa_unlock_texture(ctx->st->ctx,texture);
   st_context_invalidate_state(ctx->st,ST_INVALIDATE_FB_STATE);
   ctx->st->ctx->NewState|=_NEW_BUFFERS;
   pthread_mutex_lock(&ctx->iosurfaces->mutex);
   b->next=ctx->iosurfaces->bindings;ctx->iosurfaces->bindings=b;p_atomic_inc(&ctx->iosurfaces->count);
   pthread_mutex_unlock(&ctx->iosurfaces->mutex);return 0;
failed:
   if (p_atomic_read(&ctx->screen->uncertain)) {
      pthread_mutex_lock(&ctx->iosurfaces->mutex);
      b->next=ctx->iosurfaces->bindings;ctx->iosurfaces->bindings=b;p_atomic_inc(&ctx->iosurfaces->count);
      pthread_mutex_unlock(&ctx->iosurfaces->mutex);
   } else release_binding(b);
   return error;
}
int nvk_iosurface_group_release(struct nvk_zink_context *ctx)
{
   struct nvk_iosurface_group *g=ctx->iosurfaces;if (!g) return 1;
   /* GLD can destroy a context after CGL has unbound it. Drain Mesa's vertex
    * state as well as its pipe queue without disturbing another context's
    * current binding. No Apple GL calls participate in this operation. */
   struct nvk_zink_context *previous=nvk_zink_get_current();
   bool borrowed=previous!=ctx && p_atomic_read(&g->count);
   if (borrowed && !nvk_zink_make_current(ctx)) return 0;
   int flushed=nvk_zink_flush_iosurfaces(ctx);
   if (!flushed) {
      if (borrowed) nvk_zink_make_current(previous);
      return 0;
   }
   if (p_atomic_dec_zero(&g->refs)) {
      while (g->bindings) {
         struct surface_binding *b=g->bindings;g->bindings=b->next;release_binding(b);
      }
      pthread_mutex_destroy(&g->mutex);free(g);
   }
   ctx->iosurfaces=NULL;
   return !borrowed || nvk_zink_make_current(previous);
}
