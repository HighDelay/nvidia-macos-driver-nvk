#include "bridge.mm"
#include <cassert>

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
static int drawable_presented,dirty_called,native_released;
static int native_created,pool_resets,fence_resets;
static VkResult host_wait_result=VK_TIMEOUT,host_reset_result=VK_SUCCESS;
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
- (id)nextDrawable { return self.drawable; }
@end
static VkResult host_buffer(VkDevice,const VkBufferCreateInfo *,const VkAllocationCallbacks *,VkBuffer *out) { native_created++;*out=(VkBuffer)(uintptr_t)71;return VK_SUCCESS; }
static void host_requirements(VkDevice,VkBuffer,VkMemoryRequirements *out) { *out={256,64,1}; }
static VkResult host_allocate(VkDevice,const VkMemoryAllocateInfo *info,const VkAllocationCallbacks *,VkDeviceMemory *out) {
   native_created++;close(((const VkImportMemoryFdInfoKHR *)info->pNext)->fd);*out=(VkDeviceMemory)(uintptr_t)72;return VK_SUCCESS;
}
static VkResult host_bind(VkDevice,VkBuffer,VkDeviceMemory,VkDeviceSize) { return VK_SUCCESS; }
static VkResult host_pool(VkDevice,const VkCommandPoolCreateInfo *,const VkAllocationCallbacks *,VkCommandPool *out) { native_created++;*out=(VkCommandPool)(uintptr_t)73;return VK_SUCCESS; }
static VkResult host_commands(VkDevice,const VkCommandBufferAllocateInfo *,VkCommandBuffer *out) { *out=(VkCommandBuffer)(uintptr_t)74;return VK_SUCCESS; }
static VkResult host_begin(VkCommandBuffer,const VkCommandBufferBeginInfo *) { return VK_SUCCESS; }
static VkResult host_end(VkCommandBuffer) { return VK_SUCCESS; }
static void host_barrier(VkCommandBuffer,VkPipelineStageFlags,VkPipelineStageFlags,VkDependencyFlags,uint32_t,const VkMemoryBarrier *,uint32_t,const VkBufferMemoryBarrier *,uint32_t,const VkImageMemoryBarrier *) {}
static void host_copy(VkCommandBuffer,VkImage,VkImageLayout,VkBuffer,uint32_t,const VkBufferImageCopy *) {}
static VkResult host_fence(VkDevice,const VkFenceCreateInfo *,const VkAllocationCallbacks *,VkFence *out) { native_created++;*out=(VkFence)(uintptr_t)75;return VK_SUCCESS; }
static VkResult host_timeout(VkDevice,uint32_t,const VkFence *,VkBool32,uint64_t) { return host_wait_result; }
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
#undef HOST
   return nullptr;
}
static void host_stage(const Share *) {}
static uint32_t host_dirty(VkDevice,uint32_t,uint32_t) { dirty_called++;return 0; }
#endif

static int submits, waits, destroyed_semaphores;
static VkResult injected=VK_SUCCESS;
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
static PFN_vkVoidFunction fake_gdpa(VkDevice,const char *name) {
#ifdef NVKBRIDGE_WINE
   if (auto fn=host_proc(name)) return fn;
#endif
   if (!strcmp(name,"vkQueueSubmit")) return (PFN_vkVoidFunction)fake_submit;
   if (!strcmp(name,"vkQueueWaitIdle")) return (PFN_vkVoidFunction)fake_idle;
   if (!strcmp(name,"vkCreateSemaphore")) return (PFN_vkVoidFunction)fake_semaphore;
   if (!strcmp(name,"vkDestroySemaphore")) return (PFN_vkVoidFunction)fake_destroy_semaphore;
   return nullptr;
}
static void fake_families(VkPhysicalDevice,uint32_t *count,VkQueueFamilyProperties *out) {
   if (out && *count) { *out={};out->queueCount=1;out->queueFlags=VK_QUEUE_GRAPHICS_BIT|VK_QUEUE_COMPUTE_BIT|VK_QUEUE_TRANSFER_BIT; }
   *count=1;
}
static VkResult fake_device_extensions(VkPhysicalDevice,const char *,uint32_t *count,VkExtensionProperties *out) {
   return enumerate(std::vector<VkExtensionProperties>{extension("VK_EXT_hdr_metadata",1),
      extension("VK_KHR_external_memory_fd",1),extension("VK_KHR_external_semaphore_fd",1),
      extension("VK_KHR_external_fence_fd",1),extension("VK_EXT_transform_feedback",1),
      extension("VK_KHR_swapchain",1)},count,out);
}
static PFN_vkVoidFunction fake_gipa(VkInstance,const char *name) {
   if (!strcmp(name,"vkGetPhysicalDeviceQueueFamilyProperties")) return (PFN_vkVoidFunction)fake_families;
   if (!strcmp(name,"vkEnumerateDeviceExtensionProperties")) return (PFN_vkVoidFunction)fake_device_extensions;
   return (PFN_vkVoidFunction)fake_idle;
}
static VkResult throw_oom() { throw std::bad_alloc(); }
static VkResult throw_error() { throw std::runtime_error("injected"); }

int main() { @autoreleasepool {
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
   d->uncertain=false;quarantine.clear();assert(native_released==before_release+4);CFRelease(cpu);
   puts("PASS uncertain GPU completion retains native resources, device, drawable and image reservations without publication");
#endif
   puts("HOST CHECKS PASSED; bridge submission functions were mocked");
} }
