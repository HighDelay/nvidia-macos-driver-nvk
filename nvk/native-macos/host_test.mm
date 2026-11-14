#import <IOSurface/IOSurface.h>
#include <atomic>
static std::atomic<int> surface_locks{},surface_unlocks{};
static IOReturn tracked_surface_lock(IOSurfaceRef surface,IOSurfaceLockOptions options,uint32_t *seed) {
   auto result=IOSurfaceLock(surface,options,seed);if(result==kIOReturnSuccess)surface_locks++;return result;
}
static IOReturn tracked_surface_unlock(IOSurfaceRef surface,IOSurfaceLockOptions options,uint32_t *seed) {
   auto result=IOSurfaceUnlock(surface,options,seed);if(result==kIOReturnSuccess)surface_unlocks++;return result;
}
#define IOSurfaceLock tracked_surface_lock
#define IOSurfaceUnlock tracked_surface_unlock
#include "bridge.mm"
#undef IOSurfaceLock
#undef IOSurfaceUnlock
#include <cassert>
#include <future>

struct HostBlock {
   std::mutex mutex;
   std::condition_variable changed;
   bool blocking{},entered{};
   void enable() { std::lock_guard<std::mutex> lock(mutex);blocking=true;entered=false; }
   void wait() {
      std::unique_lock<std::mutex> lock(mutex);
      if(blocking) { entered=true;changed.notify_all();changed.wait(lock,[&]{return !blocking;}); }
   }
   void await() {
      std::unique_lock<std::mutex> lock(mutex);
      assert(changed.wait_for(lock,std::chrono::seconds(5),[&]{return entered;}));
   }
   void release() { std::lock_guard<std::mutex> lock(mutex);blocking=false;changed.notify_all(); }
};
static HostBlock drawable_block,fence_block;

#ifdef NVKBRIDGE_WINE
// Objective-C presentation objects are stand-ins; the IOSurface is host memory.
@interface HostDevice : NSObject
@property(readonly) uint64_t registryID;
@end
@implementation HostDevice
- (uint64_t)registryID { return 17; }
@end
@interface HostTexture : NSObject
@property(strong) id device;
@property(assign) IOSurfaceRef iosurface;
@end
@implementation HostTexture
- (NSUInteger)width { return 4; }
- (NSUInteger)height { return 4; }
- (NSUInteger)iosurfacePlane { return 0; }
- (MTLPixelFormat)pixelFormat { return MTLPixelFormatBGRA8Unorm; }
@end
static std::atomic<int> drawable_presented{},dirty_called{},native_released{};
static std::atomic<int> native_created{},pool_resets{},fence_resets{};
static std::atomic<VkResult> host_wait_result{VK_TIMEOUT},host_reset_result{VK_SUCCESS};
static VkResult fd_import_result=VK_SUCCESS,host_import_result=VK_SUCCESS,host_bind_result=VK_SUCCESS;
static int host_imports{},host_barriers{};
static uint32_t host_memory_bits=1,host_pointer_bits=2;
static VkDeviceSize host_requirement_bytes=256;
@interface HostDrawable : NSObject
@property(strong) HostTexture *texture;
@end
@implementation HostDrawable
- (void)present { drawable_presented++; }
@end
@interface HostLayer : NSObject
@property(strong) HostDrawable *drawable;
@end
@implementation HostLayer
- (id)nextDrawable { drawable_block.wait();return self.drawable; }
@end
static VkResult host_buffer(VkDevice,const VkBufferCreateInfo *,const VkAllocationCallbacks *,VkBuffer *out) { native_created++;*out=(VkBuffer)(uintptr_t)71;return VK_SUCCESS; }
static void host_requirements(VkDevice,VkBuffer,VkMemoryRequirements *out) { *out={host_requirement_bytes,64,host_memory_bits}; }
static VkResult host_allocate(VkDevice,const VkMemoryAllocateInfo *info,const VkAllocationCallbacks *,VkDeviceMemory *out) {
   auto import=(const VkBaseInStructure *)info->pNext;
   if(import->sType==VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR) {
      if(fd_import_result!=VK_SUCCESS)return fd_import_result;
      close(((const VkImportMemoryFdInfoKHR *)import)->fd);
   } else {
      assert(import->sType==VK_STRUCTURE_TYPE_IMPORT_MEMORY_HOST_POINTER_INFO_EXT);
      auto host=(const VkImportMemoryHostPointerInfoEXT *)import;
      assert(host->handleType==VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT && host->pHostPointer);
      assert(info->memoryTypeIndex==1 && surface_locks==surface_unlocks+1);host_imports++;
      if(host_import_result!=VK_SUCCESS)return host_import_result;
   }
   native_created++;*out=(VkDeviceMemory)(uintptr_t)72;return VK_SUCCESS;
}
static VkResult host_pointer_properties(VkDevice,VkExternalMemoryHandleTypeFlagBits type,const void *pointer,VkMemoryHostPointerPropertiesEXT *out) {
   assert(type==VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT && pointer);out->memoryTypeBits=host_pointer_bits;return VK_SUCCESS;
}
static VkResult host_bind(VkDevice,VkBuffer,VkDeviceMemory,VkDeviceSize) { return host_bind_result; }
static VkResult host_pool(VkDevice,const VkCommandPoolCreateInfo *,const VkAllocationCallbacks *,VkCommandPool *out) { native_created++;*out=(VkCommandPool)(uintptr_t)73;return VK_SUCCESS; }
static VkResult host_commands(VkDevice,const VkCommandBufferAllocateInfo *,VkCommandBuffer *out) { *out=(VkCommandBuffer)(uintptr_t)74;return VK_SUCCESS; }
static VkResult host_begin(VkCommandBuffer,const VkCommandBufferBeginInfo *) { return VK_SUCCESS; }
static VkResult host_end(VkCommandBuffer) { return VK_SUCCESS; }
static void host_barrier(VkCommandBuffer,VkPipelineStageFlags src,VkPipelineStageFlags dst,VkDependencyFlags,uint32_t count,const VkMemoryBarrier *barriers,uint32_t,const VkBufferMemoryBarrier *,uint32_t,const VkImageMemoryBarrier *) {
   if(count) {
      assert(count==1 && src==VK_PIPELINE_STAGE_TRANSFER_BIT && dst==VK_PIPELINE_STAGE_HOST_BIT);
      assert(barriers->srcAccessMask==VK_ACCESS_TRANSFER_WRITE_BIT && barriers->dstAccessMask==VK_ACCESS_HOST_READ_BIT);
      assert(surface_locks==surface_unlocks+1);host_barriers++;
   }
}
static void host_copy(VkCommandBuffer,VkImage,VkImageLayout,VkBuffer,uint32_t,const VkBufferImageCopy *) {}
static VkResult host_fence(VkDevice,const VkFenceCreateInfo *,const VkAllocationCallbacks *,VkFence *out) { native_created++;*out=(VkFence)(uintptr_t)75;return VK_SUCCESS; }
static VkResult host_timeout(VkDevice,uint32_t,const VkFence *,VkBool32,uint64_t) { fence_block.wait();return host_wait_result; }
static VkResult host_reset_pool(VkDevice,VkCommandPool,VkCommandPoolResetFlags) { pool_resets++;return host_reset_result; }
static VkResult host_reset_fences(VkDevice,uint32_t n,const VkFence *fences) { assert(n==1&&fences&&*fences);fence_resets++;return VK_SUCCESS; }
static void host_destroy_image(VkDevice,VkImage,const VkAllocationCallbacks *) {}
static void host_release_pool(VkDevice,VkCommandPool,const VkAllocationCallbacks *) { native_released++; }
static void host_release_fence(VkDevice,VkFence,const VkAllocationCallbacks *) { native_released++; }
static void host_release_buffer(VkDevice,VkBuffer,const VkAllocationCallbacks *) { native_released++; }
static void host_release_memory(VkDevice,VkDeviceMemory,const VkAllocationCallbacks *) { native_released++; }
static PFN_vkVoidFunction host_proc(const char *name) {
#define HOST(vk,fn) if (!strcmp(name,"vk" #vk)) return (PFN_vkVoidFunction)fn
   HOST(CreateBuffer,host_buffer);HOST(GetBufferMemoryRequirements,host_requirements);HOST(AllocateMemory,host_allocate);
   HOST(BindBufferMemory,host_bind);HOST(CreateCommandPool,host_pool);HOST(AllocateCommandBuffers,host_commands);
   HOST(BeginCommandBuffer,host_begin);HOST(EndCommandBuffer,host_end);HOST(CmdPipelineBarrier,host_barrier);
   HOST(CmdCopyImageToBuffer,host_copy);HOST(CreateFence,host_fence);HOST(WaitForFences,host_timeout);
   HOST(ResetCommandPool,host_reset_pool);HOST(ResetFences,host_reset_fences);HOST(DestroyImage,host_destroy_image);
   HOST(DestroyCommandPool,host_release_pool);HOST(DestroyFence,host_release_fence);
   HOST(DestroyBuffer,host_release_buffer);HOST(FreeMemory,host_release_memory);
   HOST(GetMemoryHostPointerPropertiesEXT,host_pointer_properties);
#undef HOST
   return nullptr;
}
static void host_stage(const Share *) {}
static uint32_t host_dirty(VkDevice,uint32_t,uint32_t) { dirty_called++;return 0; }
#endif

static std::atomic<int> submits{}, waits{}, destroyed_semaphores{};
static std::atomic<VkResult> injected{VK_SUCCESS};
static VkResult fake_submit(VkQueue,uint32_t count,const VkSubmitInfo *infos,VkFence) {
   assert(count==1);
   if (infos->waitSemaphoreCount) assert(infos->pWaitSemaphores && infos->pWaitDstStageMask);
   submits++; return injected;
}
static VkResult fake_idle(VkQueue) { waits++;return VK_SUCCESS; }
static VkResult fake_semaphore(VkDevice,const VkSemaphoreCreateInfo *,const VkAllocationCallbacks *,VkSemaphore *out) {
   *out=(VkSemaphore)(uintptr_t)0x44;return VK_SUCCESS;
}
static void fake_destroy_semaphore(VkDevice,VkSemaphore,const VkAllocationCallbacks *) { destroyed_semaphores++; }
static VkResult fake_device_idle(VkDevice) { return VK_SUCCESS; }
static void fake_destroy_device(VkDevice,const VkAllocationCallbacks *) {}
static PFN_vkVoidFunction fake_gdpa(VkDevice,const char *name) {
#ifdef NVKBRIDGE_WINE
   if (auto fn=host_proc(name)) return fn;
#endif
   if (!strcmp(name,"vkQueueSubmit")) return (PFN_vkVoidFunction)fake_submit;
   if (!strcmp(name,"vkQueueWaitIdle")) return (PFN_vkVoidFunction)fake_idle;
   if (!strcmp(name,"vkCreateSemaphore")) return (PFN_vkVoidFunction)fake_semaphore;
   if (!strcmp(name,"vkDestroySemaphore")) return (PFN_vkVoidFunction)fake_destroy_semaphore;
   if (!strcmp(name,"vkDeviceWaitIdle")) return (PFN_vkVoidFunction)fake_device_idle;
   if (!strcmp(name,"vkDestroyDevice")) return (PFN_vkVoidFunction)fake_destroy_device;
   return nullptr;
}
static void fake_properties(VkPhysicalDevice,VkPhysicalDeviceProperties *out) { *out={};strcpy(out->deviceName,"host fixture"); }
static void fake_memory_properties(VkPhysicalDevice,VkPhysicalDeviceMemoryProperties *out) { *out={}; }
static bool advertise_host_memory=false;
static int host_property_queries{};
static VkDeviceSize host_import_alignment=4096;
static void fake_properties2(VkPhysicalDevice physical,VkPhysicalDeviceProperties2 *out) {
   fake_properties(physical,&out->properties);host_property_queries++;
   assert(out->pNext && ((VkBaseOutStructure *)out->pNext)->sType==VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_MEMORY_HOST_PROPERTIES_EXT);
   ((VkPhysicalDeviceExternalMemoryHostPropertiesEXT *)out->pNext)->minImportedHostPointerAlignment=host_import_alignment;
}
static VkResult fake_create_device(VkPhysicalDevice,const VkDeviceCreateInfo *,const VkAllocationCallbacks *,VkDevice *out) {
   *out=(VkDevice)(uintptr_t)0x100;return VK_SUCCESS;
}
static void fake_families(VkPhysicalDevice,uint32_t *count,VkQueueFamilyProperties *out) {
   if (out && *count) { *out={};out->queueCount=1;out->queueFlags=VK_QUEUE_GRAPHICS_BIT|VK_QUEUE_COMPUTE_BIT|VK_QUEUE_TRANSFER_BIT; }
   *count=1;
}
static VkResult fake_device_extensions(VkPhysicalDevice,const char *,uint32_t *count,VkExtensionProperties *out) {
   std::vector<VkExtensionProperties> available{extension("VK_EXT_hdr_metadata",1),
      extension("VK_KHR_external_memory_fd",1),extension("VK_KHR_external_semaphore_fd",1),
      extension("VK_KHR_external_fence_fd",1),extension("VK_EXT_transform_feedback",1),
      extension("VK_KHR_swapchain",1)};
   if(advertise_host_memory)available.push_back(extension(VK_EXT_EXTERNAL_MEMORY_HOST_EXTENSION_NAME,1));
   return enumerate(available,count,out);
}
static PFN_vkVoidFunction fake_gipa(VkInstance,const char *name) {
   if (!strcmp(name,"vkGetPhysicalDeviceProperties")) return (PFN_vkVoidFunction)fake_properties;
   if (!strcmp(name,"vkGetPhysicalDeviceProperties2")) return (PFN_vkVoidFunction)fake_properties2;
   if (!strcmp(name,"vkGetPhysicalDeviceMemoryProperties")) return (PFN_vkVoidFunction)fake_memory_properties;
   if (!strcmp(name,"vkCreateDevice")) return (PFN_vkVoidFunction)fake_create_device;
   if (!strcmp(name,"vkGetDeviceProcAddr")) return (PFN_vkVoidFunction)fake_gdpa;
   if (!strcmp(name,"vkGetPhysicalDeviceQueueFamilyProperties")) return (PFN_vkVoidFunction)fake_families;
   if (!strcmp(name,"vkEnumerateDeviceExtensionProperties")) return (PFN_vkVoidFunction)fake_device_extensions;
   return (PFN_vkVoidFunction)fake_idle;
}
static VkResult throw_oom() { throw std::bad_alloc(); }
static VkResult throw_error() { throw std::runtime_error("injected"); }

int main() { @autoreleasepool {
   unsetenv("WINE_NVK_ASYNC_PRESENT");
#ifdef NVKBRIDGE_WINE
   assert(async_present_requested());
#else
   assert(!async_present_requested());
#endif
   setenv("WINE_NVK_ASYNC_PRESENT","0",1);assert(!async_present_requested());
   setenv("WINE_NVK_ASYNC_PRESENT","1",1);
#ifdef NVKBRIDGE_WINE
   assert(async_present_requested());
#else
   assert(!async_present_requested());
#endif
   unsetenv("WINE_NVK_ASYNC_PRESENT");
   puts("PASS asynchronous default and synchronous opt-out remain Wine-only");
   {
      std::atomic<unsigned> failures{},completed{};
      nvkbridge::PresentWorker worker([&](std::exception_ptr){++failures;});
      assert(worker.post([]{throw std::runtime_error("injected job failure");}));
      assert(worker.post([&]{++completed;}));worker.drain();worker.stop();worker.stop();
      assert(failures==1 && completed==1 && !worker.post([]{}));
      puts("PASS worker exception containment, drain, repeated stop and post-after-stop refusal");
   }
#ifdef NVKBRIDGE_WINE
   {
      nvkbridge_hud::Timing timing;double fps=0,ms=0;
      assert(!timing.frame(0,fps,ms));
      for (unsigned frame=1;frame<=60;frame++) {
         if (timing.frame(frame/60.0,fps,ms)) {
            assert(std::abs(fps-60)<0.001 && std::abs(ms-1000.0/60)<0.001);
         }
      }
      assert(!timing.frame(0.5,fps,ms)); // Clock reset cannot report negative FPS.
      assert(!timing.frame(NAN,fps,ms));
      assert(timing.frame(10.5,fps,ms) && std::abs(fps-0.1)<0.0001);
      nvkbridge_hud::State overlay;
      assert(!overlay.bitmap(100,100));
      auto initial=overlay.bitmap(640,360);assert(initial && initial->pixels.size()==224*44);
      assert(initial->pixels[0]==0xff101010u);
      assert(std::count(initial->pixels.begin(),initial->pixels.end(),0xffffffffu)>100);
      assert(overlay.bitmap(1920,1080)==initial);
      overlay.record(0);overlay.record(.5);
      auto next=overlay.bitmap(640,360);assert(next!=initial && next->version>initial->version);
      assert(next->pixels!=initial->pixels);
      setenv("WINE_NVK_HUD","0",1);assert(!nvkbridge_hud::enabled());
      setenv("WINE_NVK_HUD","fps,frametimes",1);assert(nvkbridge_hud::enabled());
      unsetenv("WINE_NVK_HUD");
      puts("PASS HUD pacing, finite/reset handling, cached bitmap, clipping and Wine child opt-in");
   }
#endif
   std::call_once(backend_once,[]{});backend_gipa=fake_gipa;backend_gdpa=fake_gdpa;
   uint32_t count=0;int out=99;
   assert(enumerate(std::vector<int>{1,2},&count,&out)==VK_INCOMPLETE && count==0 && out==99);
   count=1;assert(enumerate(std::vector<int>{1,2},&count,&out)==VK_INCOMPLETE && count==1 && out==1);
   assert(enumerate(std::vector<int>{1,2},&count,(int *)nullptr)==VK_SUCCESS && count==2);
   puts("PASS bounded enumeration and zero-capacity writes");

   auto i=std::make_shared<Instance>();i->raw=(VkInstance)(uintptr_t)1;
   VkPhysicalDevice p=(VkPhysicalDevice)(uintptr_t)2;
   auto s=std::make_shared<Surface>();
   VkSurfaceKHR surface=(VkSurfaceKHR)(uintptr_t)3;
   physicals[p]=i;surfaces[surface]=s;
   {
      auto topology=std::make_shared<Device>();topology->physical=p;topology->instance=i;
      auto &m=topology->memory;
      constexpr VkDeviceSize gib=1ull<<30;
      m.memoryHeapCount=2;m.memoryHeaps[0]={8*gib,VK_MEMORY_HEAP_DEVICE_LOCAL_BIT};m.memoryHeaps[1]={12*gib,0};
      m.memoryTypeCount=3;m.memoryTypes[0]={VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT,0};
      m.memoryTypes[1]={VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT|VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,0};
      m.memoryTypes[2]={VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT|VK_MEMORY_PROPERTY_HOST_CACHED_BIT,1};
      std::vector<const char *> extensions{VK_KHR_EXTERNAL_MEMORY_FD_EXTENSION_NAME};
      advertise_host_memory=true;const int before=host_property_queries;
      assert(!limited_bar_memory(m));enable_host_present(topology,extensions);
      assert(!topology->host_present_alignment && host_property_queries==before && extensions.size()==1);
      // The old limited-BAR layout has a small visible heap; the new one has
      // no visible VRAM type. Both permit the same presentation recovery.
      m.memoryHeapCount=3;m.memoryHeaps[2]={256ull<<20,VK_MEMORY_HEAP_DEVICE_LOCAL_BIT};m.memoryTypes[1].heapIndex=2;
      assert(limited_bar_memory(m));enable_host_present(topology,extensions);
      assert(topology->host_present_alignment==4096 && extensions.size()==2);
      enable_host_present(topology,extensions);assert(extensions.size()==2);
      m.memoryTypes[1]=m.memoryTypes[2];m.memoryTypeCount=2;m.memoryHeapCount=2;
      assert(limited_bar_memory(m));
      topology->host_present_alignment=0;extensions.resize(1);advertise_host_memory=false;
      enable_host_present(topology,extensions);assert(!topology->host_present_alignment && extensions.size()==1);
      advertise_host_memory=true;
      for(auto bad:{0ull,3ull}) {
         host_import_alignment=bad;enable_host_present(topology,extensions);
         assert(!topology->host_present_alignment && extensions.size()==1);
      }
      host_import_alignment=4096;enable_host_present(topology,extensions);assert(topology->host_present_alignment==4096);
      m.memoryHeaps[0].size=0;assert(!limited_bar_memory(m));
      advertise_host_memory=false;
      puts("PASS limited-BAR host recovery capability checks; full BAR leaves extensions and queries unchanged");
   }
   {
      float priority=1;
      VkDeviceQueueCreateInfo queueInfo{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};
      queueInfo.queueCount=1;queueInfo.pQueuePriorities=&priority;
      VkDeviceCreateInfo deviceInfo{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};
      deviceInfo.queueCreateInfoCount=1;deviceInfo.pQueueCreateInfos=&queueInfo;
      auto check_options=[&](bool reuse,bool profile) {
         VkDevice created{};assert(create_device(p,&deviceInfo,nullptr,&created)==VK_SUCCESS);
         auto state=find(devices,created);assert(state);
#ifdef NVKBRIDGE_WINE
         assert(state->reuse_present_resources==reuse && state->profile_present==profile);
#else
         (void)reuse;(void)profile;
         assert(!state->reuse_present_resources && !state->profile_present);
#endif
         destroy_device(created,nullptr);assert(!find(devices,created));
      };
      unsetenv("WINE_NVK_PRESENT_REUSE");unsetenv("WINE_NVK_PRESENT_PROFILE");
      check_options(true,false);
      setenv("WINE_NVK_PRESENT_REUSE","0",1);setenv("WINE_NVK_PRESENT_PROFILE","1",1);
      check_options(false,true);
      setenv("WINE_NVK_PRESENT_REUSE","1",1);setenv("WINE_NVK_PRESENT_PROFILE","0",1);
      check_options(true,false);
      unsetenv("WINE_NVK_PRESENT_REUSE");unsetenv("WINE_NVK_PRESENT_PROFILE");
      puts("PASS device creation defaults, reuse opt-out and independent profiling stay Wine-only");
   }
   VkPhysicalDeviceSurfaceInfo2KHR info{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SURFACE_INFO_2_KHR};info.surface=surface;
   VkSurfaceFormat2KHR format{VK_STRUCTURE_TYPE_SURFACE_FORMAT_2_KHR};format.surfaceFormat.format=VK_FORMAT_UNDEFINED;
   count=0;assert(surface_formats2(p,&info,&count,&format)==VK_INCOMPLETE && !count && format.surfaceFormat.format==VK_FORMAT_UNDEFINED);
   count=1;assert(surface_formats2(p,&info,&count,&format)==VK_INCOMPLETE && count==1 && format.surfaceFormat.format==VK_FORMAT_B8G8R8A8_SRGB);
   puts("PASS surface-formats2 preserves caller bounds");

   auto d=std::make_shared<Device>();d->raw=(VkDevice)(uintptr_t)4;d->copy_queue=(VkQueue)(uintptr_t)5;d->swapchain=true;
   auto c=std::make_shared<Chain>();c->dev=d;c->surface=s;c->images.resize(2);c->handle=(VkSwapchainKHR)(uintptr_t)6;
   devices[d->raw]=d;chains[c->handle]=c;
   uint32_t index=99;VkSemaphore semaphore=(VkSemaphore)(uintptr_t)7;
   assert(acquire_image(d->raw,c->handle,0,semaphore,VK_NULL_HANDLE,&index)==VK_SUCCESS && index==0 && submits==1);
   assert(acquire_image(d->raw,c->handle,0,semaphore,VK_NULL_HANDLE,&index)==VK_SUCCESS && index==1 && submits==2);
   assert(acquire_image(d->raw,c->handle,0,semaphore,VK_NULL_HANDLE,&index)==VK_NOT_READY && submits==2);
   assert(acquire_image(d->raw,c->handle,1000,semaphore,VK_NULL_HANDLE,&index)==VK_TIMEOUT && submits==2);
   puts("PASS acquire reservations, immediate exhaustion and bounded timeout");
   c->images[0].acquired=false;injected=VK_ERROR_OUT_OF_DEVICE_MEMORY;
   assert(acquire_image(d->raw,c->handle,0,semaphore,VK_NULL_HANDLE,&index)==injected && !c->images[0].acquired);
   injected=VK_SUCCESS;c->retired=true;
   assert(acquire_image(d->raw,c->handle,UINT64_MAX,semaphore,VK_NULL_HANDLE,&index)==VK_ERROR_OUT_OF_DATE_KHR);
   assert(acquire_image(d->raw,c->handle,0,VK_NULL_HANDLE,VK_NULL_HANDLE,&index)==VK_ERROR_INITIALIZATION_FAILED);
   puts("PASS failed submission rolls back acquisition; retired chains unblock");

   auto q=std::make_shared<Queue>();q->raw=(VkQueue)(uintptr_t)8;q->dev=d;queues[q->raw]=q;
   VkSwapchainKHR absent=(VkSwapchainKHR)(uintptr_t)99;uint32_t zero=0;
   VkPresentInfoKHR present{VK_STRUCTURE_TYPE_PRESENT_INFO_KHR};present.swapchainCount=1;present.pSwapchains=&absent;present.pImageIndices=&zero;
   submits=waits=destroyed_semaphores=0;
   assert(queue_present(q->raw,&present)==VK_ERROR_OUT_OF_DATE_KHR && submits==2 && waits==1 && destroyed_semaphores==1);
   injected=VK_ERROR_OUT_OF_DEVICE_MEMORY;submits=waits=destroyed_semaphores=0;
   assert(queue_present(q->raw,&present)==injected && submits==1 && waits==0 && destroyed_semaphores==1);
   injected=VK_SUCCESS;
   puts("PASS rejected presents consume queued handoff once; failed submissions unwind");
   assert(vkGetInstanceProcAddr(VK_NULL_HANDLE,"vkCreateSwapchainKHR")==nullptr);
   assert(vkGetDeviceProcAddr(d->raw,"vkCreateInstance")==nullptr);
   d->swapchain=false;assert(vkGetDeviceProcAddr(d->raw,"vkCreateSwapchainKHR")==nullptr);
   assert(ApiGuard<throw_oom>::call()==VK_ERROR_OUT_OF_HOST_MEMORY);
   assert(ApiGuard<throw_error>::call()==VK_ERROR_UNKNOWN);
   puts("PASS command scope, extension gating and C ABI exception containment");
   assert(lookup("vkCreateMetalSurfaceEXT")==reinterpret_cast<PFN_vkVoidFunction>(&vkCreateMetalSurfaceEXT));
   VkMetalSurfaceCreateInfoEXT metalInfo{VK_STRUCTURE_TYPE_METAL_SURFACE_CREATE_INFO_EXT};
   VkSurfaceKHR untouched=(VkSurfaceKHR)(uintptr_t)0x1234;
   assert(vkCreateMetalSurfaceEXT(VK_NULL_HANDLE,&metalInfo,nullptr,&untouched)==VK_ERROR_INITIALIZATION_FAILED);
   assert(untouched==(VkSurfaceKHR)(uintptr_t)0x1234);
   puts("PASS direct Metal surface export shares guarded dispatch and rejects invalid input");
#ifdef NVKBRIDGE_WINE
   count=0;assert(device_extensions(p,nullptr,&count,nullptr)==VK_SUCCESS && count==2);
   std::vector<VkExtensionProperties> exposed(count);assert(device_extensions(p,nullptr,&count,exposed.data())==VK_SUCCESS);
   assert(!strcmp(exposed[0].extensionName,"VK_EXT_transform_feedback") && !strcmp(exposed[1].extensionName,"VK_KHR_swapchain"));
   assert(vkGetInstanceProcAddr(i->raw,"vkSetHdrMetadataEXT")==nullptr);
   assert(vkGetInstanceProcAddr(i->raw,"vkGetMemoryFdKHR")==nullptr);
   assert(vkGetInstanceProcAddr(i->raw,"vkWaitForPresentKHR")==nullptr);
   VkExternalBufferProperties external{VK_STRUCTURE_TYPE_EXTERNAL_BUFFER_PROPERTIES};
   external.externalMemoryProperties.externalMemoryFeatures=~0u;
   auto externalQuery=(PFN_vkGetPhysicalDeviceExternalBufferProperties)lookup("vkGetPhysicalDeviceExternalBufferProperties");
   externalQuery(p,nullptr,&external);assert(external.externalMemoryProperties.externalMemoryFeatures==0);
   VkPhysicalDeviceExternalImageFormatInfo externalInfo{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_IMAGE_FORMAT_INFO};externalInfo.handleType=VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT;
   VkPhysicalDeviceImageFormatInfo2 imageInfo{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2};imageInfo.pNext=&externalInfo;
   VkImageFormatProperties2 imageProperties{VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2};
   assert(image_format2(p,&imageInfo,&imageProperties)==VK_ERROR_FORMAT_NOT_SUPPORTED);
   puts("PASS Wine capabilities refuse unimplemented WSI and external sharing without forwarding handles");

   NSDictionary *props=@{(__bridge NSString *)kIOSurfaceWidth:@4,(__bridge NSString *)kIOSurfaceHeight:@4,
      (__bridge NSString *)kIOSurfaceBytesPerElement:@4,(__bridge NSString *)kIOSurfaceBytesPerRow:@64};
   IOSurfaceRef cpu=IOSurfaceCreate((__bridge CFDictionaryRef)props);assert(cpu && IOSurfaceGetAllocSize(cpu)>=256);
   auto metal=[HostDevice new];auto texture=[HostTexture new];texture.device=metal;texture.iosurface=cpu;
   auto drawable=[HostDrawable new];drawable.texture=texture;auto layer=[HostLayer new];layer.drawable=drawable;
   s->layer=(CAMetalLayer *)layer;d->metal=(id<MTLDevice>)metal;
   d->memory.memoryTypeCount=1;d->memory.memoryTypes[0].propertyFlags=VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT;
   d->swapchain=true;c->retired=false;c->format=VK_FORMAT_B8G8R8A8_UNORM;c->extent={4,4};c->images[0].raw=(VkImage)(uintptr_t)76;c->images[0].acquired=true;
   stage_import=host_stage;surface_dirty=host_dirty;
   present.pSwapchains=&c->handle;present.pImageIndices=&zero;
   // Completed resources can be reused without keeping a drawable or chain.
   d->reuse_present_resources=true;d->profile_present=true;host_wait_result=VK_SUCCESS;
   const int first_created=native_created;
   assert(queue_present(q->raw,&present)==VK_SUCCESS);
   assert(native_created==first_created+4 && c->present_cache_bytes==IOSurfaceGetAllocSize(cpu));
   auto parked=*std::find_if(c->present_cache.begin(),c->present_cache.end(),[](auto &entry){return bool(entry);});
   assert(parked && !parked->drawable && !parked->chain);
   std::weak_ptr<Frame> lifetime=parked;parked.reset();
   c->images[0].acquired=true;
   assert(queue_present(q->raw,&present)==VK_SUCCESS);
   assert(native_created==first_created+4 && pool_resets==1 && fence_resets==1);
   assert(present_stats[10]==1 && !lifetime.expired());
   // Distinct retained surfaces must never borrow another surface's import.
   IOSurfaceRef other_surfaces[3]{};
   for(auto &other:other_surfaces) {
      other=IOSurfaceCreate((__bridge CFDictionaryRef)props);assert(other&&other!=cpu);
      texture.iosurface=other;c->images[0].acquired=true;
      assert(queue_present(q->raw,&present)==VK_SUCCESS);
      assert(c->present_cache_bytes<=3*IOSurfaceGetAllocSize(cpu));
   }
   assert(lifetime.expired() && present_stats[11]==1);
   assert(std::count_if(c->present_cache.begin(),c->present_cache.end(),[](auto &entry){return bool(entry);})==3);
   release_images(c);assert(!c->present_cache_bytes);
   for(auto &entry:c->present_cache)assert(!entry);
   for(auto other:other_surfaces)CFRelease(other);
   puts("PASS completed-resource reuse, surface isolation, three-entry bound and swapchain cleanup without ownership cycles");
   // A reset failure drops the idle entry, drains the handoff and permits retry.
   texture.iosurface=cpu;c->images[0].raw=(VkImage)(uintptr_t)76;c->images[0].acquired=true;
   assert(queue_present(q->raw,&present)==VK_SUCCESS);
   host_reset_result=VK_ERROR_OUT_OF_HOST_MEMORY;c->images[0].acquired=true;
   assert(queue_present(q->raw,&present)==VK_ERROR_OUT_OF_HOST_MEMORY);
   assert(!d->uncertain&&!c->images[0].acquired&&!c->present_cache_bytes);
   host_reset_result=VK_SUCCESS;c->images[0].acquired=true;
   assert(queue_present(q->raw,&present)==VK_SUCCESS);
   puts("PASS cached-resource reset failure releases idle resources and preserves handoff/retry rules");
   {
      release_images(c);c->images[0].raw=(VkImage)(uintptr_t)76;
      d->memory.memoryTypeCount=2;
      d->memory.memoryTypes[1].propertyFlags=VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
      host_memory_bits=3;fd_import_result=VK_ERROR_INVALID_EXTERNAL_HANDLE;
      bool consumed=false;
      int imports_before=host_imports,locks_before=surface_locks;
      // Full BAR, unsupported host import and unrelated allocation errors
      // keep the original failure; only the precise limited-BAR refusal retries.
      assert(copy_frame(c,0,VK_NULL_HANDLE,consumed)==VK_ERROR_INVALID_EXTERNAL_HANDLE);
      assert(host_imports==imports_before && surface_locks==locks_before);
      d->host_present_alignment=64;fd_import_result=VK_ERROR_OUT_OF_DEVICE_MEMORY;
      assert(copy_frame(c,0,VK_NULL_HANDLE,consumed)==VK_ERROR_OUT_OF_DEVICE_MEMORY);
      assert(host_imports==imports_before && surface_locks==locks_before);
      fd_import_result=VK_ERROR_INVALID_EXTERNAL_HANDLE;
      const int dirty_before=dirty_called,presented_before=drawable_presented,barriers_before=host_barriers;
      const uint32_t seed_before=IOSurfaceGetSeed(cpu);
      assert(copy_frame(c,0,VK_NULL_HANDLE,consumed)==VK_SUCCESS);
      assert(host_imports==imports_before+1 && host_barriers==barriers_before+1);
      assert(surface_locks==locks_before+1 && surface_unlocks==surface_locks);
      assert(dirty_called==dirty_before && drawable_presented==presented_before+1 && IOSurfaceGetSeed(cpu)!=seed_before);
      auto cached=*std::find_if(c->present_cache.begin(),c->present_cache.end(),[](auto &entry){return bool(entry);});
      assert(cached->host_pointer && !cached->host_locked && !cached->drawable);cached.reset();
      assert(copy_frame(c,0,VK_NULL_HANDLE,consumed)==VK_SUCCESS);
      assert(host_imports==imports_before+1 && host_barriers==barriers_before+2);
      assert(surface_locks==locks_before+2 && surface_unlocks==surface_locks && dirty_called==dirty_before);
      release_images(c);c->images[0].raw=(VkImage)(uintptr_t)76;d->reuse_present_resources=false;
      for(auto failure:{VK_ERROR_OUT_OF_HOST_MEMORY,VK_ERROR_OUT_OF_DEVICE_MEMORY}) {
         host_import_result=failure;const int before=native_released;
         assert(copy_frame(c,0,VK_NULL_HANDLE,consumed)==failure);
         assert(surface_locks==surface_unlocks && native_released==before+2);
      }
      host_import_result=VK_SUCCESS;host_bind_result=VK_ERROR_OUT_OF_DEVICE_MEMORY;
      const int before_bind=native_released;
      assert(copy_frame(c,0,VK_NULL_HANDLE,consumed)==VK_ERROR_OUT_OF_DEVICE_MEMORY);
      assert(surface_locks==surface_unlocks && native_released==before_bind+3);
      host_bind_result=VK_SUCCESS;host_pointer_bits=0;
      assert(copy_frame(c,0,VK_NULL_HANDLE,consumed)==VK_ERROR_INVALID_EXTERNAL_HANDLE);
      assert(surface_locks==surface_unlocks);host_pointer_bits=2;
      host_requirement_bytes=UINT64_MAX;
      assert(copy_frame(c,0,VK_NULL_HANDLE,consumed)==VK_ERROR_OUT_OF_DEVICE_MEMORY);
      assert(surface_locks==surface_unlocks);host_requirement_bytes=256;
      const int before_timeout=native_released,before_unlock=surface_unlocks;
      host_wait_result=VK_TIMEOUT;
      assert(copy_frame(c,0,VK_NULL_HANDLE,consumed)==VK_ERROR_DEVICE_LOST);
      assert(d->uncertain && quarantine.size()==1 && native_released==before_timeout+1);
      assert(surface_locks==surface_unlocks+1 && surface_unlocks==before_unlock);
      assert(quarantine.back()->host_locked && quarantine.back()->host_pointer);
      assert(dirty_called==dirty_before && drawable_presented==presented_before+2);
      // Only the fixture's later proof of completion permits releasing pages.
      d->uncertain=false;quarantine.clear();assert(surface_locks==surface_unlocks);
      d->host_present_alignment=0;d->reuse_present_resources=true;host_memory_bits=1;
      fd_import_result=VK_SUCCESS;host_wait_result=VK_SUCCESS;
      puts("PASS limited-BAR host GPU-copy recovery, publication, cache re-lock, failed-import cleanup and timeout page quarantine");
   }
   // The actual bridge worker must release submit during both kinds of wait.
   d->presenter=std::make_unique<nvkbridge::PresentWorker>([device=d.get()](std::exception_ptr){fail_presenter(device);});
   drawable_block.enable();c->images[0].acquired=true;c->images[1].acquired=true;
   assert(queue_present(q->raw,&present)==VK_SUCCESS);drawable_block.await();
   assert(d->submit.try_lock());d->submit.unlock();
   assert(c->images[0].acquired && c->images[0].present_pending);
   assert(acquire_image(d->raw,c->handle,0,semaphore,VK_NULL_HANDLE,&index)==VK_NOT_READY);
   VkSubmitInfo empty{VK_STRUCTURE_TYPE_SUBMIT_INFO};
   assert(queue_submit(q->raw,1,&empty,VK_NULL_HANDLE)==VK_SUCCESS);
   auto idle=std::async(std::launch::async,[&]{return device_idle(d->raw);});
   assert(idle.wait_for(std::chrono::milliseconds(50))==std::future_status::timeout);
   drawable_block.release();assert(idle.get()==VK_SUCCESS);
   assert(!c->images[0].acquired && !c->images[0].present_pending);c->images[1].acquired=false;
   puts("PASS drawable wait is off submission thread; idle drains before releasing source image");
   fence_block.enable();c->images[0].acquired=true;
   const int published_before=drawable_presented;
   assert(queue_present(q->raw,&present)==VK_SUCCESS);fence_block.await();
   assert(d->submit.try_lock());d->submit.unlock();assert(drawable_presented==published_before);
   assert(queue_submit(q->raw,1,&empty,VK_NULL_HANDLE)==VK_SUCCESS);
   // Caller arrays/results go out of scope while the second job is queued.
   { VkSwapchainKHR local_chain=c->handle;uint32_t local_index=1;VkResult local_result=VK_ERROR_UNKNOWN;
     c->images[1].acquired=true;VkPresentInfoKHR local=present;
     local.pSwapchains=&local_chain;local.pImageIndices=&local_index;local.pResults=&local_result;
     assert(queue_present(q->raw,&local)==VK_SUCCESS && local_result==VK_SUCCESS);
     local_chain=absent;local_index=99;local_result=VK_ERROR_UNKNOWN;
   }
   VkPresentInfoKHR rejected=present;rejected.pSwapchains=&absent;
   auto blocked_post=std::async(std::launch::async,[&]{return queue_present(q->raw,&rejected);});
   assert(blocked_post.wait_for(std::chrono::milliseconds(50))==std::future_status::timeout);
   fence_block.release();assert(blocked_post.get()==VK_ERROR_OUT_OF_DATE_KHR);
   assert(device_idle(d->raw)==VK_SUCCESS);
   assert(drawable_presented==published_before+2 && !c->images[0].acquired && !c->images[1].acquired);
   puts("PASS fence wait permits submissions; bounded backpressure, copied caller arrays and rejected handoff drain");
   c->extent.width=8;c->images[0].acquired=true;
   assert(queue_present(q->raw,&present)==VK_SUCCESS);drain_presenter(d);
   assert(c->present_error==VK_ERROR_OUT_OF_DATE_KHR && !c->images[0].acquired);
   assert(acquire_image(d->raw,c->handle,UINT64_MAX,semaphore,VK_NULL_HANDLE,&index)==VK_ERROR_OUT_OF_DATE_KHR);
   c->present_error=VK_SUCCESS;c->extent.width=4;
   fence_block.enable();c->images[0].acquired=true;
   assert(queue_present(q->raw,&present)==VK_SUCCESS);fence_block.await();
   const int before_destroy=native_released;
   auto destruction=std::async(std::launch::async,[&]{destroy_swapchain(d->raw,c->handle,nullptr);});
   assert(destruction.wait_for(std::chrono::milliseconds(50))==std::future_status::timeout);
   assert(native_released==before_destroy);
   fence_block.release();destruction.get();assert(!find(chains,c->handle));
   chains[c->handle]=c;c->retired=false;c->images[0].raw=(VkImage)(uintptr_t)76;
   d->presenter->stop();d->presenter.reset();
   puts("PASS late resize latching and swapchain destruction drain pending work before freeing resources");
   // Timeout on the cached entry must retain every GPU-owned object unchanged.
   host_wait_result=VK_TIMEOUT;c->images[0].acquired=true;dirty_called=drawable_presented=0;
   const int before_release=native_released,before_semaphores=destroyed_semaphores;
   assert(queue_present(q->raw,&present)==VK_ERROR_DEVICE_LOST);
   assert(d->uncertain && c->images[0].acquired && quarantine.size()==1);
   assert(native_released==before_release && destroyed_semaphores==before_semaphores);
   assert(!dirty_called && !drawable_presented);
   destroy_swapchain(d->raw,c->handle,nullptr);destroy_device(d->raw,nullptr);
   assert(find(chains,c->handle)==c && find(devices,d->raw)==d && native_released==before_release);
   // The mock now supplies proof of completion solely for fixture cleanup.
   d->uncertain=false;quarantine.clear();assert(native_released==before_release+4);
   puts("PASS uncertain GPU completion retains native resources, device, drawable and image reservations without publication");
   c->retired=false;c->present_error=VK_SUCCESS;c->images[0].acquired=true;c->images[1].acquired=true;
   d->presenter=std::make_unique<nvkbridge::PresentWorker>([device=d.get()](std::exception_ptr){fail_presenter(device);});
   fence_block.enable();dirty_called=drawable_presented=0;
   const int async_releases=native_released,async_semaphores=destroyed_semaphores;
   assert(queue_present(q->raw,&present)==VK_SUCCESS);fence_block.await();
   auto acquisition=std::async(std::launch::async,[&]{uint32_t image;return acquire_image(d->raw,c->handle,UINT64_MAX,semaphore,VK_NULL_HANDLE,&image);});
   assert(acquisition.wait_for(std::chrono::milliseconds(50))==std::future_status::timeout);
   fence_block.release();drain_presenter(d);
   assert(acquisition.get()==VK_ERROR_DEVICE_LOST && d->uncertain && quarantine.size()==1);
   assert(!dirty_called && !drawable_presented && native_released==async_releases && destroyed_semaphores==async_semaphores);
   assert(c->images[0].acquired && c->images[0].present_pending);
   destroy_device(d->raw,nullptr);assert(find(devices,d->raw)==d && !d->presenter->post([]{}));
   d->presenter.reset();d->uncertain=false;quarantine.clear();CFRelease(cpu);
   puts("PASS asynchronous timeout wakes acquisition, quarantines work and joins worker before retained-device teardown");
#endif
   puts("HOST CHECKS PASSED; bridge submission functions were mocked");
} }
