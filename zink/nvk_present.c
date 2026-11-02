/* SPDX-License-Identifier: MIT */
#include "nvk_present.h"
#include "nvk_zink.h"
#include "zink/zink_screen.h"
#include "zink/zink_resource.h"
#include "util/u_inlines.h"
#include "util/u_dl.h"
#include <dlfcn.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <stdatomic.h>

/* The version-3 IOSurface import and dirty/tag publication are the same
 * protocol used by nvk/native-macos/bridge.mm. Never import another device's
 * VkImage: the Gallium scratch image belongs to this Zink screen. */
struct surface_import { uint32_t version, surface, plane, pad; uint64_t bytes; };
_Static_assert(sizeof(struct surface_import) == 24, "NVK import ABI");
typedef void (*stage_import_fn)(const struct surface_import *);
typedef uint32_t (*surface_dirty_fn)(VkDevice, uint32_t, uint32_t);

struct copy_frame {
   struct copy_frame *next;
   void *renderer;
   IOSurfaceRef surface;
   struct pipe_resource *image;
   VkBuffer buffer;
   VkDeviceMemory memory;
   VkCommandPool pool;
   VkFence fence;
   bool host_locked;
};
static pthread_mutex_t quarantine_lock = PTHREAD_MUTEX_INITIALIZER;
static struct copy_frame *quarantine;
static _Atomic(nvk_zink_present_observer) present_observer;

__attribute__((visibility("default"))) void
nvk_zink_set_present_observer(nvk_zink_present_observer observer)
{
   atomic_store(&present_observer, observer);
}

static void release_frame(struct zink_screen *screen, struct copy_frame *frame)
{
   if (frame->pool) screen->vk.DestroyCommandPool(screen->dev, frame->pool, NULL);
   if (frame->fence) screen->vk.DestroyFence(screen->dev, frame->fence, NULL);
   if (frame->buffer) screen->vk.DestroyBuffer(screen->dev, frame->buffer, NULL);
   if (frame->memory) screen->vk.FreeMemory(screen->dev, frame->memory, NULL);
   if (frame->host_locked) IOSurfaceUnlock(frame->surface, 0, NULL);
   pipe_resource_reference(&frame->image, NULL);
   if (frame->surface) CFRelease(frame->surface);
   if (frame->renderer) dlclose(frame->renderer);
   free(frame);
}

static int publish_tag(IOSurfaceRef surface)
{
   struct { uint32_t magic, seed, generation; } tag = {0x564d5634u, IOSurfaceGetSeed(surface), 0};
   CFTypeRef old = IOSurfaceCopyValue(surface, CFSTR("NVMTLVramAt:p0"));
   if (old && CFGetTypeID(old) == CFDataGetTypeID() && CFDataGetLength(old) == sizeof(tag)) {
      uint32_t words[3]; memcpy(words, CFDataGetBytePtr(old), sizeof(words));
      if (words[0] == tag.magic) tag.generation = words[2] + 1;
   }
   if (old) CFRelease(old);
   CFDataRef value = CFDataCreate(NULL, (const UInt8 *)&tag, sizeof(tag));
   if (!value) return 0;
   IOSurfaceSetValue(surface, CFSTR("NVMTLVramAt:p0"), value);
   CFRelease(value);
   return 1;
}

int nvk_zink_copy_vram(struct pipe_screen *pscreen, struct pipe_resource *image,
                       IOSurfaceRef surface)
{
   struct zink_screen *screen = zink_screen(pscreen);
   if (!image || !surface || screen->info.driver_props.driverID != VK_DRIVER_ID_MESA_NVK ||
       screen->info.props.vendorID != 0x10de || !screen->info.have_KHR_external_memory_fd ||
       IOSurfaceGetPlaneCount(surface) > 1 || IOSurfaceGetPixelFormat(surface) != 0x42475241 ||
       IOSurfaceGetWidth(surface) != image->width0 || IOSurfaceGetHeight(surface) != image->height0 ||
       IOSurfaceGetBytesPerElement(surface) != 4 || image->format != PIPE_FORMAT_B8G8R8A8_UNORM)
      return 0;
   size_t row = IOSurfaceGetBytesPerRow(surface), size = IOSurfaceGetAllocSize(surface);
   if (!row || row % 4 || row < (uint64_t)image->width0 * 4 || row / 4 > UINT32_MAX ||
       image->height0 > SIZE_MAX / row || row * image->height0 > size)
      return 0;

   /* Resolve private hooks only from the ICD implementing this exact device's
    * command entrypoint. A loader/layer wrapper or unrelated NVK image is
    * rejected rather than passing foreign handles to a private ABI. */
   if (!util_dl_get_proc_address(screen->loader_lib, "nvkbridgeSetPresentObserver")) return 0;
   PFN_vkVoidFunction copy = screen->vk_GetDeviceProcAddr(screen->dev, "vkCmdCopyImageToBuffer");
   Dl_info origin = {0};
   if (!copy || !dladdr((void *)copy, &origin) || !origin.dli_fname) return 0;
   struct copy_frame *frame = calloc(1, sizeof(*frame));
   if (!frame) return 0;
   frame->renderer = dlopen(origin.dli_fname, RTLD_NOW | RTLD_LOCAL | RTLD_FIRST | RTLD_NOLOAD);
   if (!frame->renderer) { free(frame); return 0; }
   PFN_vkGetInstanceProcAddr gipa = (void *)dlsym(frame->renderer, "vk_icdGetInstanceProcAddr");
   PFN_vkGetDeviceProcAddr gdpa = gipa ? (void *)gipa(screen->instance, "vkGetDeviceProcAddr") : NULL;
   stage_import_fn stage = (void *)dlsym(frame->renderer, "nvmtl_nvk_stage_import");
   surface_dirty_fn dirty = (void *)dlsym(frame->renderer, "nvmtl_nvk_surface_dirty");
   if (!gdpa || gdpa(screen->dev, "vkCmdCopyImageToBuffer") != copy || !stage || !dirty)
      goto failed;
   frame->surface = (IOSurfaceRef)CFRetain(surface);
   pipe_resource_reference(&frame->image, image);
   VkExternalMemoryBufferCreateInfo external = { .sType = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_BUFFER_CREATE_INFO,
      .handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT };
   VkBufferCreateInfo buffer = { .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, .pNext = &external,
      .size = row * image->height0, .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT | VK_BUFFER_USAGE_TRANSFER_SRC_BIT,
      .sharingMode = VK_SHARING_MODE_EXCLUSIVE };
   if (screen->vk.CreateBuffer(screen->dev, &buffer, NULL, &frame->buffer) != VK_SUCCESS) goto failed;
   VkMemoryRequirements requirements;
   screen->vk.GetBufferMemoryRequirements(screen->dev, frame->buffer, &requirements);
   if (requirements.size > size) goto failed;
   uint32_t type;
   for (type = 0; type < screen->info.mem_props.memoryTypeCount; ++type)
      if ((requirements.memoryTypeBits & (1u << type)) &&
          (screen->info.mem_props.memoryTypes[type].propertyFlags & VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT)) break;
   if (type == screen->info.mem_props.memoryTypeCount) goto failed;
   int fd = open("/dev/null", O_RDONLY | O_CLOEXEC);
   if (fd < 0) goto failed;
   VkImportMemoryFdInfoKHR imported = { .sType = VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR,
      .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT, .fd = fd };
   VkMemoryAllocateInfo allocation = { .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
      .pNext = &imported, .allocationSize = requirements.size, .memoryTypeIndex = type };
   const struct surface_import record = {3, IOSurfaceGetID(surface), 0, 0, requirements.size};
   stage(&record);
   VkResult result = screen->vk.AllocateMemory(screen->dev, &allocation, NULL, &frame->memory);
   stage(NULL); /* Clear thread-local staged imports on every allocation result. */
   if (result != VK_SUCCESS) {
      close(fd);
      /* A small BAR1 aperture can be full while ordinary GPU memory remains
       * available. Copy directly into the IOSurface's imported system pages
       * in that case. No CPU reads or copies of game pixels are involved.
       * IOSurface locking/seed publication replaces the family-VRAM tag. */
      if (result != VK_ERROR_INVALID_EXTERNAL_HANDLE || !screen->info.have_EXT_external_memory_host)
         goto failed;
      size_t align = screen->info.ext_host_mem_props.minImportedHostPointerAlignment;
      if (!align || (align & (align - 1)) || requirements.size > SIZE_MAX - (align - 1)) goto failed;
      if (IOSurfaceLock(surface, 0, NULL) != kIOReturnSuccess) goto failed;
      frame->host_locked = true;
      void *base = IOSurfaceGetBaseAddress(surface);
      if (!base || (uintptr_t)base % align) goto failed;
      screen->vk.DestroyBuffer(screen->dev, frame->buffer, NULL); frame->buffer = VK_NULL_HANDLE;
      external.handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT;
      if (screen->vk.CreateBuffer(screen->dev, &buffer, NULL, &frame->buffer) != VK_SUCCESS) goto failed;
      screen->vk.GetBufferMemoryRequirements(screen->dev, frame->buffer, &requirements);
      if (requirements.size > SIZE_MAX - (align - 1)) goto failed;
      size_t bytes = (requirements.size + align - 1) & ~(align - 1);
      if (bytes > size) goto failed;
      VkMemoryHostPointerPropertiesEXT properties = { .sType = VK_STRUCTURE_TYPE_MEMORY_HOST_POINTER_PROPERTIES_EXT };
      if (screen->vk.GetMemoryHostPointerPropertiesEXT(screen->dev, external.handleTypes, base, &properties) != VK_SUCCESS)
         goto failed;
      for (type = 0; type < screen->info.mem_props.memoryTypeCount; ++type)
         if ((requirements.memoryTypeBits & properties.memoryTypeBits & (1u << type)) &&
             (screen->info.mem_props.memoryTypes[type].propertyFlags &
              (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) ==
             (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) break;
      if (type == screen->info.mem_props.memoryTypeCount) goto failed;
      VkImportMemoryHostPointerInfoEXT host = { .sType = VK_STRUCTURE_TYPE_IMPORT_MEMORY_HOST_POINTER_INFO_EXT,
         .handleType = external.handleTypes, .pHostPointer = base };
      allocation.pNext = &host; allocation.allocationSize = bytes; allocation.memoryTypeIndex = type;
      if (screen->vk.AllocateMemory(screen->dev, &allocation, NULL, &frame->memory) != VK_SUCCESS) goto failed;
   }
   if (screen->vk.BindBufferMemory(screen->dev, frame->buffer, frame->memory, 0) != VK_SUCCESS) goto failed;
   VkCommandPoolCreateInfo pool = { .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
      .queueFamilyIndex = screen->gfx_queue };
   if (screen->vk.CreateCommandPool(screen->dev, &pool, NULL, &frame->pool) != VK_SUCCESS) goto failed;
   VkCommandBuffer command;
   VkCommandBufferAllocateInfo cb = { .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
      .commandPool = frame->pool, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1 };
   if (screen->vk.AllocateCommandBuffers(screen->dev, &cb, &command) != VK_SUCCESS) goto failed;
   VkCommandBufferBeginInfo begin = { .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
      .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT };
   if (screen->vk.BeginCommandBuffer(command, &begin) != VK_SUCCESS) goto failed;
   struct zink_resource *resource = zink_resource(image);
   const VkImageLayout prior = resource->layout;
   if (prior == VK_IMAGE_LAYOUT_UNDEFINED || !resource->obj || resource->obj->is_buffer) goto failed;
   VkImageMemoryBarrier barrier = { .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
      .srcAccessMask = VK_ACCESS_MEMORY_WRITE_BIT, .dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT,
      .oldLayout = prior, .newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
      .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
      .image = resource->obj->image, .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1} };
   screen->vk.CmdPipelineBarrier(command, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT,
                                 0, 0, NULL, 0, NULL, 1, &barrier);
   VkBufferImageCopy region = { .bufferRowLength = row / 4, .bufferImageHeight = image->height0,
      .imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1},
      .imageExtent = {image->width0, image->height0, 1} };
   screen->vk.CmdCopyImageToBuffer(command, resource->obj->image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                                  frame->buffer, 1, &region);
   if (frame->host_locked) {
      VkMemoryBarrier host = { .sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER,
         .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT, .dstAccessMask = VK_ACCESS_HOST_READ_BIT };
      screen->vk.CmdPipelineBarrier(command, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT,
                                    0, 1, &host, 0, NULL, 0, NULL);
   }
   /* Restore the tracked layout. The application never observes our scratch
    * image, and subsequent Gallium operations retain their normal state. */
   barrier.srcAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
   barrier.dstAccessMask = VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT;
   barrier.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL; barrier.newLayout = prior;
   screen->vk.CmdPipelineBarrier(command, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,
                                 0, 0, NULL, 0, NULL, 1, &barrier);
   if (screen->vk.EndCommandBuffer(command) != VK_SUCCESS) goto failed;
   VkFenceCreateInfo fence = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
   if (screen->vk.CreateFence(screen->dev, &fence, NULL, &frame->fence) != VK_SUCCESS) goto failed;
   VkSubmitInfo submit = { .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
      .commandBufferCount = 1, .pCommandBuffers = &command };
   /* Respect Zink's own queue lock, including other shared GL contexts and its
    * submission worker. The caller has already completed the scratch blit. */
   simple_mtx_lock(screen->queue_lock);
   result = screen->vk.QueueSubmit(screen->queue, 1, &submit, frame->fence);
   simple_mtx_unlock(screen->queue_lock);
   if (result == VK_SUCCESS)
      result = screen->vk.WaitForFences(screen->dev, 1, &frame->fence, VK_TRUE, 5000000000ull);
   nvk_zink_present_observer observer = atomic_load(&present_observer);
   int observed = 1;
   if (result == VK_SUCCESS && observer) {
      uint32_t host_types = 0;
      for (uint32_t i = 0; i < screen->info.mem_props.memoryTypeCount; ++i)
         if ((screen->info.mem_props.memoryTypes[i].propertyFlags &
              (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) ==
             (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) host_types |= 1u << i;
      const struct nvk_zink_present_event event = {
         (void *)screen->vk_GetDeviceProcAddr, (void *)screen->dev, (void *)screen->queue,
         (uint64_t)frame->buffer, (uint32_t)row, image->width0, image->height0, screen->gfx_queue, host_types,
         frame->host_locked};
      simple_mtx_lock(screen->queue_lock);
      observed = observer(&event);
      simple_mtx_unlock(screen->queue_lock);
   }
   if (result != VK_SUCCESS || observed < 0) {
      pthread_mutex_lock(&quarantine_lock);
      frame->next = quarantine; quarantine = frame;
      pthread_mutex_unlock(&quarantine_lock);
      return -1; /* No destruction or drawable publication after uncertain work. */
   }
   int published;
   if (frame->host_locked) {
      published = IOSurfaceUnlock(surface, 0, NULL) == kIOReturnSuccess;
      frame->host_locked = false;
      published = observed && published;
   } else {
      published = observed && dirty(screen->dev, IOSurfaceGetID(surface), 0) == 0 && publish_tag(surface);
   }
   release_frame(screen, frame);
   return published;
failed:
   release_frame(screen, frame);
   return 0;
}
