#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>
#include <dlfcn.h>
#include <mach-o/dyld.h>
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>
#include <deque>
#include <mutex>
#include <unistd.h>

static void check(VkResult r,const char *name) { if (r!=VK_SUCCESS) throw std::runtime_error(std::string(name)+" -> "+std::to_string(r)); }
static void require(bool value,const char *name) { if (!value) throw std::runtime_error(name); }
#define I(name) auto name=(PFN_vk##name)gipa(instance,"vk" #name);require(name!=nullptr,"vk" #name)
#define D(name) auto name=(PFN_vk##name)gdpa(device,"vk" #name);require(name!=nullptr,"vk" #name)
template<class T> T sym(void *lib,const char *name) { auto f=(T)dlsym(lib,name);require(f!=nullptr,name);return f; }
static VkPhysicalDeviceMemoryProperties memory_properties;
static std::mutex expected_mutex;
static std::deque<unsigned> expected_frames;
static uint64_t verified_frames{},verified_bytes{};
using PresentObserver=void (*)(PFN_vkGetDeviceProcAddr,VkDevice,VkQueue,VkBuffer,uint32_t,uint32_t,uint32_t,uint32_t);
static void verify_pixels(PFN_vkGetDeviceProcAddr gdpa,VkDevice device,VkQueue queue,VkBuffer source,uint32_t row,uint32_t width,uint32_t height,uint32_t family) {
   unsigned current_frame;
   { std::lock_guard<std::mutex> lock(expected_mutex);
     require(!expected_frames.empty(),"unexpected presentation callback");
     current_frame=expected_frames.front();expected_frames.pop_front(); }
   D(CreateBuffer);D(DestroyBuffer);D(GetBufferMemoryRequirements);D(AllocateMemory);D(FreeMemory);D(BindBufferMemory);
   D(CreateCommandPool);D(DestroyCommandPool);D(AllocateCommandBuffers);D(BeginCommandBuffer);D(EndCommandBuffer);
   D(CmdCopyBuffer);D(QueueSubmit);D(CreateFence);D(DestroyFence);D(WaitForFences);D(MapMemory);D(UnmapMemory);D(CmdPipelineBarrier);
   VkBufferCreateInfo b{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};b.size=(uint64_t)row*height;b.usage=VK_BUFFER_USAGE_TRANSFER_DST_BIT;
   VkBuffer target{};check(CreateBuffer(device,&b,nullptr,&target),"verification buffer");
   VkMemoryRequirements req{};GetBufferMemoryRequirements(device,target,&req);uint32_t mt=UINT32_MAX;
   for (uint32_t i=0;i<memory_properties.memoryTypeCount;i++) if ((req.memoryTypeBits&(1u<<i)) &&
       (memory_properties.memoryTypes[i].propertyFlags&(VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT))==
       (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) {
      if (mt==UINT32_MAX) mt=i;
      if (memory_properties.memoryTypes[i].propertyFlags&VK_MEMORY_PROPERTY_HOST_CACHED_BIT) { mt=i;break; }
   }
   require(mt!=UINT32_MAX,"verification host memory");VkMemoryAllocateInfo ma{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};ma.allocationSize=req.size;ma.memoryTypeIndex=mt;
   VkDeviceMemory memory{};check(AllocateMemory(device,&ma,nullptr,&memory),"verification allocate");check(BindBufferMemory(device,target,memory,0),"verification bind");
   VkCommandPoolCreateInfo pc{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};pc.queueFamilyIndex=family;
   VkCommandPool pool{};check(CreateCommandPool(device,&pc,nullptr,&pool),"verification pool");
   VkCommandBufferAllocateInfo ca{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};ca.commandPool=pool;ca.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY;ca.commandBufferCount=1;
   VkCommandBuffer commands{};check(AllocateCommandBuffers(device,&ca,&commands),"verification command buffer");
   VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};check(BeginCommandBuffer(commands,&begin),"verification begin");
   VkMemoryBarrier barrier{VK_STRUCTURE_TYPE_MEMORY_BARRIER};barrier.srcAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT;barrier.dstAccessMask=VK_ACCESS_TRANSFER_READ_BIT;
   CmdPipelineBarrier(commands,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,0,1,&barrier,0,nullptr,0,nullptr);
   VkBufferCopy region{0,0,(uint64_t)row*height};CmdCopyBuffer(commands,source,target,1,&region);check(EndCommandBuffer(commands),"verification end");
   VkFenceCreateInfo fi{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};VkFence fence{};check(CreateFence(device,&fi,nullptr,&fence),"verification fence");
   VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO};submit.commandBufferCount=1;submit.pCommandBuffers=&commands;check(QueueSubmit(queue,1,&submit,fence),"verification submit");
   check(WaitForFences(device,1,&fence,VK_TRUE,5000000000ull),"verification completion");void *data=nullptr;check(MapMemory(device,memory,0,VK_WHOLE_SIZE,0,&data),"verification map");
   std::vector<unsigned char> snapshot((size_t)row*height);memcpy(snapshot.data(),data,snapshot.size());
   int red=(int)lround((current_frame%60)/59.0*255.0),blue=255-red;
   bool verify_hud=getenv("NVKBRIDGE_VERIFY_HUD")!=nullptr;uint32_t hud_white=0,hud_background=0;
   for (uint32_t y=0;y<height;y++) for (uint32_t x=0;x<width;x++) {
      auto p=snapshot.data()+(size_t)y*row+x*4;
      if(verify_hud && x>=8 && x<232 && y>=8 && y<52) {
         bool white=p[0]==255&&p[1]==255&&p[2]==255&&p[3]==255;
         bool background=p[0]==16&&p[1]==16&&p[2]==16&&p[3]==255;
         require(white||background,"invalid GPU HUD pixel");hud_white+=white;hud_background+=background;continue;
      }
      if (abs((int)p[0]-blue)>1 || abs((int)p[1]-51)>1 || abs((int)p[2]-red)>1 || p[3]!=255) {
         fprintf(stderr,"PIXEL MISMATCH frame=%u x=%u y=%u BGRA=%u,%u,%u,%u expected=%d,51,%d,255\n",current_frame,x,y,p[0],p[1],p[2],p[3],blue,red);
         throw std::runtime_error("drawable pixel verification failed");
      }
   }
   if(verify_hud)require(hud_white>100&&hud_background>100,"GPU HUD text/background is missing");
   verified_frames++;verified_bytes+=(uint64_t)row*height;UnmapMemory(device,memory);
   DestroyCommandPool(device,pool,nullptr);DestroyFence(device,fence,nullptr);DestroyBuffer(device,target,nullptr);FreeMemory(device,memory,nullptr);
}

int main(int argc,char **argv) { @autoreleasepool { try {
   require(argc>=2,"Usage: smoke entry.dylib [SDL3.dylib [images=3 [frames=90]]]");
   bool windowed=argc>=3;
   void *entry=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL|RTLD_FIRST);require(entry!=nullptr,dlerror()?:"entry load");
   auto gipa=sym<PFN_vkGetInstanceProcAddr>(entry,"vkGetInstanceProcAddr");
   auto create=(PFN_vkCreateInstance)gipa(nullptr,"vkCreateInstance");require(create!=nullptr,"global create");
   auto enumerate=(PFN_vkEnumerateInstanceExtensionProperties)gipa(nullptr,"vkEnumerateInstanceExtensionProperties");
   uint32_t n=0;check(enumerate(nullptr,&n,nullptr),"instance extensions");
   std::vector<VkExtensionProperties> extensions(n);check(enumerate(nullptr,&n,extensions.data()),"instance extensions");
   for (auto &e:extensions) printf("INSTANCE_EXTENSION %s\n",e.extensionName);
   auto layers=(PFN_vkEnumerateInstanceLayerProperties)gipa(nullptr,"vkEnumerateInstanceLayerProperties");
   check(layers(&n,nullptr),"layer enumeration");require(n==0,"zero application layers");
   auto version=(PFN_vkEnumerateInstanceVersion)gipa(nullptr,"vkEnumerateInstanceVersion");
   uint32_t api=0;check(version(&api),"instance version");require(api>=VK_API_VERSION_1_2,"Vulkan 1.2");
   printf("API %u.%u.%u\n",VK_API_VERSION_MAJOR(api),VK_API_VERSION_MINOR(api),VK_API_VERSION_PATCH(api));

   void *sdl=nullptr,*window=nullptr;
   bool (*SDL_Init)(uint32_t)=nullptr;
   void *(*SDL_CreateWindow)(const char *,int,int,uint64_t)=nullptr;
   bool (*SDL_PollEvent)(void *)=nullptr;
   void (*SDL_DestroyWindow)(void *)=nullptr;
   void (*SDL_Quit)()=nullptr;
   bool (*SDL_CreateSurface)(void *,VkInstance,const VkAllocationCallbacks *,VkSurfaceKHR *)=nullptr;
   bool (*SDL_SetWindowSize)(void *,int,int)=nullptr;
   bool (*SDL_MinimizeWindow)(void *)=nullptr;
   bool (*SDL_RestoreWindow)(void *)=nullptr;
   const char *const *(*SDL_Extensions)(uint32_t *)=nullptr;
   const char *(*SDL_GetError)()=nullptr;
   if (windowed) {
      sdl=dlopen(argv[2],RTLD_NOW|RTLD_LOCAL);require(sdl!=nullptr,dlerror()?:"SDL load");
      SDL_Init=sym<decltype(SDL_Init)>(sdl,"SDL_Init");
      SDL_GetError=sym<decltype(SDL_GetError)>(sdl,"SDL_GetError");
      require(SDL_Init(0x20),SDL_GetError());
      auto load=sym<bool(*)(const char *)>(sdl,"SDL_Vulkan_LoadLibrary");require(load(argv[1]),SDL_GetError());
      auto provider=sym<void *(*)()>(sdl,"SDL_Vulkan_GetVkGetInstanceProcAddr");
      require(provider()==(void *)gipa,"SDL and application providers differ");
      puts("PASS SDL/provider equality");
      SDL_CreateWindow=sym<decltype(SDL_CreateWindow)>(sdl,"SDL_CreateWindow");
      SDL_PollEvent=sym<decltype(SDL_PollEvent)>(sdl,"SDL_PollEvent");
      SDL_DestroyWindow=sym<decltype(SDL_DestroyWindow)>(sdl,"SDL_DestroyWindow");
      SDL_Quit=sym<decltype(SDL_Quit)>(sdl,"SDL_Quit");
      SDL_SetWindowSize=sym<decltype(SDL_SetWindowSize)>(sdl,"SDL_SetWindowSize");
      SDL_MinimizeWindow=sym<decltype(SDL_MinimizeWindow)>(sdl,"SDL_MinimizeWindow");
      SDL_RestoreWindow=sym<decltype(SDL_RestoreWindow)>(sdl,"SDL_RestoreWindow");
      SDL_Extensions=sym<decltype(SDL_Extensions)>(sdl,"SDL_Vulkan_GetInstanceExtensions");
      SDL_CreateSurface=sym<decltype(SDL_CreateSurface)>(sdl,"SDL_Vulkan_CreateSurface");
      window=SDL_CreateWindow("Native NVK Vulkan presentation test",640,360,0x10000000ull|0x20ull);
      require(window!=nullptr,SDL_GetError());
   }
   uint32_t extension_count=0;const char *const *names=windowed?SDL_Extensions(&extension_count):nullptr;
   VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};app.pApplicationName="NVK Bridge native smoke";app.apiVersion=VK_API_VERSION_1_3;
   VkInstanceCreateInfo ci{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};ci.pApplicationInfo=&app;ci.enabledExtensionCount=extension_count;ci.ppEnabledExtensionNames=names;
   VkInstance instance{};check(create(&ci,nullptr,&instance),"CreateInstance");
   I(DestroyInstance);I(EnumeratePhysicalDevices);I(GetPhysicalDeviceProperties);I(GetPhysicalDeviceFeatures2);
   I(GetPhysicalDeviceMemoryProperties);I(EnumerateDeviceExtensionProperties);I(GetPhysicalDeviceQueueFamilyProperties);I(CreateDevice);
   check(EnumeratePhysicalDevices(instance,&n,nullptr),"physical count");require(n>0,"no NVK adapter");
   std::vector<VkPhysicalDevice> physicals(n);check(EnumeratePhysicalDevices(instance,&n,physicals.data()),"physical devices");
   auto physical=physicals.front();VkPhysicalDeviceProperties props{};GetPhysicalDeviceProperties(physical,&props);
   GetPhysicalDeviceMemoryProperties(physical,&memory_properties);
   printf("DEVICE %s vendor=0x%x device=0x%x\n",props.deviceName,props.vendorID,props.deviceID);require(props.vendorID==0x10de,"not NVIDIA");
   VkPhysicalDeviceVertexAttributeDivisorFeaturesEXT divisor{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VERTEX_ATTRIBUTE_DIVISOR_FEATURES_EXT};
   VkPhysicalDeviceDynamicRenderingFeatures dynamic{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DYNAMIC_RENDERING_FEATURES,&divisor};
   VkPhysicalDeviceSynchronization2Features sync{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SYNCHRONIZATION_2_FEATURES,&dynamic};
   VkPhysicalDeviceVulkan12Features f12{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES,&sync};
   VkPhysicalDeviceVulkan11Features f11{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES,&f12};
   VkPhysicalDeviceFeatures2 f{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2,&f11};GetPhysicalDeviceFeatures2(physical,&f);
   require(f.features.multiDrawIndirect && f.features.drawIndirectFirstInstance && f.features.samplerAnisotropy &&
      f11.shaderDrawParameters && f12.timelineSemaphore && f12.hostQueryReset && sync.synchronization2 &&
      dynamic.dynamicRendering && divisor.vertexAttributeInstanceRateDivisor,"Minecraft required feature missing");
   check(EnumerateDeviceExtensionProperties(physical,nullptr,&n,nullptr),"device extensions");
   extensions.resize(n);check(EnumerateDeviceExtensionProperties(physical,nullptr,&n,extensions.data()),"device extensions");
   for (const char *name:{"VK_KHR_swapchain","VK_KHR_dynamic_rendering","VK_KHR_push_descriptor","VK_KHR_synchronization2","VK_EXT_vertex_attribute_divisor"}) {
      require(std::any_of(extensions.begin(),extensions.end(),[&](auto &e){return !strcmp(e.extensionName,name);}),name);
      printf("REQUIRED_EXTENSION %s supported\n",name);
   }
   puts("PASS Minecraft required device features");
   GetPhysicalDeviceQueueFamilyProperties(physical,&n,nullptr);std::vector<VkQueueFamilyProperties> families(n);GetPhysicalDeviceQueueFamilyProperties(physical,&n,families.data());
   uint32_t family=UINT32_MAX;
   for (uint32_t j=0;j<n;j++) { printf("QUEUE_FAMILY %u flags=%u count=%u\n",j,families[j].queueFlags,families[j].queueCount);
      if (family==UINT32_MAX && (families[j].queueFlags&3)==3) family=j; }
   require(family!=UINT32_MAX,"graphics/compute queue");
   float priority=1;
   VkDeviceQueueCreateInfo q{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};q.queueFamilyIndex=family;q.queueCount=1;q.pQueuePriorities=&priority;
   const char *device_extensions[]={"VK_KHR_swapchain"};
   VkDeviceCreateInfo dc{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};dc.queueCreateInfoCount=1;dc.pQueueCreateInfos=&q;dc.enabledExtensionCount=1;dc.ppEnabledExtensionNames=device_extensions;
   VkDevice device{};check(CreateDevice(physical,&dc,nullptr,&device),"CreateDevice");
   auto gdpa=(PFN_vkGetDeviceProcAddr)gipa(instance,"vkGetDeviceProcAddr");
   D(DestroyDevice);D(GetDeviceQueue);D(DeviceWaitIdle);D(CreateCommandPool);D(AllocateCommandBuffers);D(BeginCommandBuffer);D(EndCommandBuffer);D(ResetCommandPool);
   D(CmdPipelineBarrier);D(CmdClearColorImage);D(QueueSubmit);D(CreateSemaphore);D(DestroySemaphore);D(CreateFence);D(WaitForFences);D(ResetFences);D(DestroyFence);D(DestroyCommandPool);
   VkQueue queue{};GetDeviceQueue(device,family,0,&queue);
   for (uint32_t j=0;j<_dyld_image_count();j++) {
      const char *name=_dyld_get_image_name(j);
      if (strstr(name,"libnvk_") || strstr(name,"libvulkan_nouveau") || strstr(name,"NVMTLDriver")) printf("MAPPED %s\n",name);
   }
   if (!windowed) { check(DeviceWaitIdle(device),"idle");DestroyDevice(device,nullptr);DestroyInstance(instance,nullptr);puts("PASS native device/API probe");return 0; }
   I(DestroySurfaceKHR);I(GetPhysicalDeviceSurfaceSupportKHR);I(GetPhysicalDeviceSurfaceCapabilitiesKHR);
   D(CreateSwapchainKHR);D(DestroySwapchainKHR);D(GetSwapchainImagesKHR);D(AcquireNextImageKHR);D(QueuePresentKHR);
   VkSurfaceKHR surface{};require(SDL_CreateSurface(window,instance,nullptr,&surface),SDL_GetError());
   VkBool32 support=VK_FALSE;check(GetPhysicalDeviceSurfaceSupportKHR(physical,family,surface,&support),"surface support");require(support,"graphics queue not presentable");
   VkCommandPoolCreateInfo pc{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};pc.queueFamilyIndex=family;pc.flags=VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
   VkCommandPool pool{};check(CreateCommandPool(device,&pc,nullptr,&pool),"pool");
   VkCommandBufferAllocateInfo ca{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};ca.commandPool=pool;ca.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY;ca.commandBufferCount=1;
   VkCommandBuffer cb{};check(AllocateCommandBuffers(device,&ca,&cb),"command buffer");
   VkSemaphoreCreateInfo si{VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO};VkSemaphore acquired{},rendered{};
   check(CreateSemaphore(device,&si,nullptr,&acquired),"acquired semaphore");check(CreateSemaphore(device,&si,nullptr,&rendered),"rendered semaphore");
   VkFenceCreateInfo fi{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};VkFence fence{};check(CreateFence(device,&fi,nullptr,&fence),"fence");
   VkSwapchainKHR chain{};std::vector<VkImage> images;std::vector<bool> initialized;
   auto recreate=[&] {
      check(DeviceWaitIdle(device),"recreate idle");
      VkSurfaceCapabilitiesKHR caps{};check(GetPhysicalDeviceSurfaceCapabilitiesKHR(physical,surface,&caps),"caps");
      VkSwapchainCreateInfoKHR sc{VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR};
      sc.surface=surface;sc.minImageCount=argc>=4?(uint32_t)atoi(argv[3]):3;
      sc.imageFormat=VK_FORMAT_B8G8R8A8_UNORM;sc.imageColorSpace=VK_COLOR_SPACE_SRGB_NONLINEAR_KHR;sc.imageExtent=caps.currentExtent;
      sc.imageArrayLayers=1;sc.imageUsage=VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT|VK_IMAGE_USAGE_TRANSFER_DST_BIT;
      sc.imageSharingMode=VK_SHARING_MODE_EXCLUSIVE;sc.preTransform=VK_SURFACE_TRANSFORM_IDENTITY_BIT_KHR;
      sc.compositeAlpha=VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR;sc.presentMode=VK_PRESENT_MODE_FIFO_KHR;sc.clipped=VK_TRUE;sc.oldSwapchain=chain;
      VkSwapchainKHR replacement{};check(CreateSwapchainKHR(device,&sc,nullptr,&replacement),"swapchain");
      if (chain) DestroySwapchainKHR(device,chain,nullptr);chain=replacement;
      check(GetSwapchainImagesKHR(device,chain,&n,nullptr),"image count");images.resize(n);initialized.assign(n,false);
      check(GetSwapchainImagesKHR(device,chain,&n,images.data()),"images");
      printf("SWAPCHAIN %ux%u images=%u\n",sc.imageExtent.width,sc.imageExtent.height,n);
   };
   alignas(8) unsigned char event[128];
   auto pump=[&]{while(SDL_PollEvent(event)){}};
   pump();recreate();
   unsigned frames=argc>=5?(unsigned)atoi(argv[4]):90;
   // A loader routes Vulkan calls through its selected ICD. Observe that already
   // loaded frontend, without accidentally loading a different one for the test.
   void *test_entry=entry;
   if (const char *path=getenv("NVKBRIDGE_TEST_FRONTEND")) {
      test_entry=dlopen(path,RTLD_NOW|RTLD_LOCAL|RTLD_FIRST|RTLD_NOLOAD);
      require(test_entry!=nullptr,"expected bridge was not loaded by the Vulkan loader");
   }
   if (getenv("NVKBRIDGE_VERIFY_PIXELS")) sym<void(*)(PresentObserver)>(test_entry,"nvkbridgeSetPresentObserver")(&verify_pixels);
   for (unsigned frame=0;frame<frames;frame++) {
      pump();
      if (frame==frames/3) { check(DeviceWaitIdle(device),"pre-resize idle");require(SDL_SetWindowSize(window,800,450),SDL_GetError());usleep(150000);pump();recreate(); }
      if (frame==frames*2/3) { check(DeviceWaitIdle(device),"pre-minimize idle");SDL_MinimizeWindow(window);usleep(200000);pump();SDL_RestoreWindow(window);usleep(200000);pump();recreate(); }
      uint32_t image=0;check(AcquireNextImageKHR(device,chain,UINT64_MAX,acquired,VK_NULL_HANDLE,&image),"acquire");
      check(ResetCommandPool(device,pool,0),"reset pool");VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};check(BeginCommandBuffer(cb,&begin),"begin");
      VkImageMemoryBarrier barrier{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
      barrier.oldLayout=initialized[image]?VK_IMAGE_LAYOUT_PRESENT_SRC_KHR:VK_IMAGE_LAYOUT_UNDEFINED;
      barrier.newLayout=VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;barrier.dstAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT;
      barrier.srcQueueFamilyIndex=barrier.dstQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED;
      barrier.image=images[image];barrier.subresourceRange={VK_IMAGE_ASPECT_COLOR_BIT,0,1,0,1};
      CmdPipelineBarrier(cb,VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,0,0,nullptr,0,nullptr,1,&barrier);
      VkClearColorValue color{{(frame%60)/59.0f,0.2f,1.0f-(frame%60)/59.0f,1.0f}};
      CmdClearColorImage(cb,images[image],VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,&color,1,&barrier.subresourceRange);
      barrier.oldLayout=VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;barrier.newLayout=VK_IMAGE_LAYOUT_PRESENT_SRC_KHR;
      barrier.srcAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT;barrier.dstAccessMask=0;
      CmdPipelineBarrier(cb,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT,0,0,nullptr,0,nullptr,1,&barrier);check(EndCommandBuffer(cb),"end");
      VkPipelineStageFlags stage=VK_PIPELINE_STAGE_TRANSFER_BIT;VkSubmitInfo sub{VK_STRUCTURE_TYPE_SUBMIT_INFO};
      sub.waitSemaphoreCount=1;sub.pWaitSemaphores=&acquired;sub.pWaitDstStageMask=&stage;sub.commandBufferCount=1;sub.pCommandBuffers=&cb;
      sub.signalSemaphoreCount=1;sub.pSignalSemaphores=&rendered;check(QueueSubmit(queue,1,&sub,fence),"render submit");
      VkPresentInfoKHR present{VK_STRUCTURE_TYPE_PRESENT_INFO_KHR};present.waitSemaphoreCount=1;present.pWaitSemaphores=&rendered;present.swapchainCount=1;present.pSwapchains=&chain;present.pImageIndices=&image;
      if(getenv("NVKBRIDGE_VERIFY_PIXELS")) { std::lock_guard<std::mutex> lock(expected_mutex);expected_frames.push_back(frame); }
      check(QueuePresentKHR(queue,&present),"present");check(WaitForFences(device,1,&fence,VK_TRUE,5000000000ull),"render fence");check(ResetFences(device,1,&fence),"reset fence");
      initialized[image]=true;
   }
   check(DeviceWaitIdle(device),"final idle");DestroySwapchainKHR(device,chain,nullptr);
   uint64_t stats[7]{};sym<void(*)(uint64_t *)>(test_entry,"nvkbridgeGetStats")(stats);
   printf("STATS copies=%llu bytes=%llu import_failures=%llu cpu_readback=%llu cpu_upload=%llu\n",
      stats[0],stats[1],stats[4],stats[5],stats[6]);require(stats[0]==frames && stats[4]==0 && !stats[5] && !stats[6],"copy accounting");
   if(auto present_stats=(void(*)(uint64_t *))dlsym(test_entry,"nvkbridgeGetPresentStats")) {
      uint64_t detail[16]{};present_stats(detail);
      printf("PRESENT_STATS frames=%llu total_ns=%llu drawable_ns=%llu prepare_ns=%llu record_ns=%llu submit_ns=%llu fence_ns=%llu publish_ns=%llu cleanup_ns=%llu created=%llu hits=%llu evicted=%llu errors=%llu bytes=%llu lock_ns=%llu\n",
         detail[0],detail[1],detail[2],detail[3],detail[4],detail[5],detail[6],detail[7],detail[8],detail[9],detail[10],detail[11],detail[12],detail[13],detail[14]);
   }
   printf("VERIFICATION frames=%llu separate_test_readback_bytes=%llu\n",verified_frames,verified_bytes);
   if (getenv("NVKBRIDGE_VERIFY_PIXELS")) require(verified_frames==frames,"verification frame count");
   require(expected_frames.empty(),"all accepted presentations must complete before idle returns");
   DestroyFence(device,fence,nullptr);DestroySemaphore(device,acquired,nullptr);DestroySemaphore(device,rendered,nullptr);
   DestroyCommandPool(device,pool,nullptr);DestroySurfaceKHR(instance,surface,nullptr);DestroyDevice(device,nullptr);DestroyInstance(instance,nullptr);
   SDL_DestroyWindow(window);SDL_Quit();puts("PASS native SDL presentation/resize/restore");return 0;
} catch (const std::exception &e) { fprintf(stderr,"FAIL %s\n",e.what());return 1; } } }
