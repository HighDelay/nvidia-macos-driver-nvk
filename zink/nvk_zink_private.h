/* SPDX-License-Identifier: MIT */
#ifndef NVK_ZINK_PRIVATE_H
#define NVK_ZINK_PRIVATE_H
#include "nvk_zink.h"
#include "state_tracker/st_context.h"
#include "pipe/p_screen.h"

struct nvk_iosurface_group;
struct nvk_zink_screen {
   struct pipe_frontend_screen fscreen;
   int refs;
   int uncertain;
};
struct nvk_zink_context {
   struct pipe_frontend_drawable drawable;
   struct nvk_zink_screen *screen;
   struct st_visual visual;
   struct st_context *st;
   struct pipe_resource *attachments[ST_ATTACHMENT_COUNT];
   unsigned width, height;
   bool double_buffer;
   bool front_flush_pending, front_flush_active;
   struct pipe_resource *native_image;
   struct {
      IOSurfaceRef surface;
      struct pipe_resource *buffer, *image;
      struct pipe_fence_handle *fence;
   } present[2];
   struct nvk_iosurface_group *iosurfaces;
   struct pipe_resource *iosurface_staging;
};
int nvk_iosurface_group_init(struct nvk_zink_context *, struct nvk_zink_context *share);
int nvk_iosurface_group_release(struct nvk_zink_context *);
#endif
