/* SPDX-License-Identifier: MIT */
#ifndef NVK_CGL_H
#define NVK_CGL_H
#include <OpenGL/OpenGL.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Native CGL dispatch bridge using Apple's private object layout. OS build
 * numbers are not gated; ABI changes require updating the adapter. Attach
 * before the application creates GL objects. CGL contexts
 * retain their identity and drawable; public GL core calls then use Zink.
 * The caller must serialize access with CGLLockContext and detach before
 * destroying the context. Serialize attachment/detachment in a share group.
 * No injection or Apple binary patching is involved. */
int nvk_cgl_attach(CGLContextObj context, unsigned width, unsigned height);
int nvk_cgl_detach(CGLContextObj context);
int nvk_cgl_resize(CGLContextObj context, unsigned width, unsigned height);
/* Automatic GLD selection needs no application calls to this API.
 * NVKZINK_PRESENT=auto (default) prefers native CAMetalLayer VRAM copies,
 * using the Minecraft import/dirty protocol. CGL clients without an AppKit
 * drawable retain GPU IOSurface/Apple presentation. =native requires the
 * direct path, =gpu selects Apple presentation, =cpu is explicit readback.
 * GPU failures never silently select CPU transfer or reuse uncertain work. */
/* Like CGLFlushDrawable, presentation may target a non-current context.
 * It is locked and bound temporarily; the caller's CGL/Mesa bindings return
 * unchanged. The application must still serialize use of each context. */
int nvk_cgl_present(CGLContextObj context);
const char *nvk_cgl_last_error(void);
#ifdef __cplusplus
}
#endif
#endif
