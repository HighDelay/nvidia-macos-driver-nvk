/* SPDX-License-Identifier: MIT */
/* Process-local Vulkan fault injection. Never install this in a driver package. */
#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>
#include <dlfcn.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <unordered_map>

static PFN_vkGetInstanceProcAddr real_gipa;
static PFN_vkGetDeviceProcAddr real_gdpa;
static VkInstance test_instance;
static std::once_flag initialized;
static std::mutex lock;
static VkPhysicalDeviceMemoryProperties properties;
struct Allocation { VkDeviceSize size; uint32_t type; };
static std::unordered_map<VkDeviceMemory, Allocation> allocations;
static unsigned mode, failures, bar_maps, system_maps;

static void initialize()
{
   std::call_once(initialized, [] {
      const char *path = std::getenv("ZINK_TEST_REAL_VULKAN");
      void *library = path ? dlopen(path, RTLD_NOW | RTLD_LOCAL | RTLD_FIRST) : nullptr;
      if (!library) { std::fprintf(stderr,"Test Vulkan load: %s\n",dlerror()); std::abort(); }
      real_gipa = reinterpret_cast<PFN_vkGetInstanceProcAddr>(dlsym(library,"vkGetInstanceProcAddr"));
      real_gdpa = reinterpret_cast<PFN_vkGetDeviceProcAddr>(dlsym(library,"vkGetDeviceProcAddr"));
      if (!real_gipa || !real_gdpa) std::abort();
   });
}

static void constrain_bar(VkPhysicalDeviceMemoryProperties *p)
{
   if (std::getenv("ZINK_TEST_NATIVE_MEMORY")) { properties = *p; return; }
   if (std::getenv("ZINK_TEST_FULL_BAR")) {
      VkDeviceSize vram = 0;
      for (unsigned i = 0; i < p->memoryHeapCount; ++i)
         if (p->memoryHeaps[i].flags & VK_MEMORY_HEAP_DEVICE_LOCAL_BIT)
            if (p->memoryHeaps[i].size > vram) vram = p->memoryHeaps[i].size;
      for (unsigned i = 0; i < p->memoryTypeCount; ++i)
         if (p->memoryTypes[i].propertyFlags & VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT)
            p->memoryHeaps[p->memoryTypes[i].heapIndex].size = vram;
      properties = *p;
      return;
   }
   /* Split the host-visible VRAM heap even on a full-BAR host. No real memory
    * is reserved or exhausted: only Zink's view of the aperture is changed. */
   const uint32_t bar = p->memoryHeapCount;
   bool added = false;
   for (unsigned i = 0; i < p->memoryTypeCount; ++i) {
      const auto flags = p->memoryTypes[i].propertyFlags;
      if ((flags & (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT)) ==
          (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT)) {
         if (bar >= VK_MAX_MEMORY_HEAPS) std::abort();
         p->memoryTypes[i].heapIndex = bar;
         added = true;
      }
   }
   if (added) {
      p->memoryHeaps[bar] = {256ull << 20, VK_MEMORY_HEAP_DEVICE_LOCAL_BIT};
      p->memoryHeapCount++;
   }
   properties = *p;
}

static void VKAPI_CALL memory_properties(VkPhysicalDevice p, VkPhysicalDeviceMemoryProperties *out)
{
   auto fn = reinterpret_cast<PFN_vkGetPhysicalDeviceMemoryProperties>(real_gipa(test_instance,"vkGetPhysicalDeviceMemoryProperties"));
   fn(p,out); constrain_bar(out);
}

static void VKAPI_CALL memory_properties2(VkPhysicalDevice p, VkPhysicalDeviceMemoryProperties2 *out)
{
   auto fn = reinterpret_cast<PFN_vkGetPhysicalDeviceMemoryProperties2>(real_gipa(test_instance,"vkGetPhysicalDeviceMemoryProperties2"));
   fn(p,out); constrain_bar(&out->memoryProperties);
}

static VkResult VKAPI_CALL allocate_memory(VkDevice d, const VkMemoryAllocateInfo *info,
                                           const VkAllocationCallbacks *cb, VkDeviceMemory *out)
{
   auto fn = reinterpret_cast<PFN_vkAllocateMemory>(real_gdpa(d,"vkAllocateMemory"));
   VkResult result = fn(d,info,cb,out);
   if (result == VK_SUCCESS) {
      std::lock_guard<std::mutex> guard(lock);
      allocations[*out] = {info->allocationSize,info->memoryTypeIndex};
   }
   return result;
}

static void VKAPI_CALL free_memory(VkDevice d, VkDeviceMemory memory, const VkAllocationCallbacks *cb)
{
   { std::lock_guard<std::mutex> guard(lock); allocations.erase(memory); }
   reinterpret_cast<PFN_vkFreeMemory>(real_gdpa(d,"vkFreeMemory"))(d,memory,cb);
}

static bool fail_map(VkDeviceMemory memory)
{
   std::lock_guard<std::mutex> guard(lock);
   const auto it = allocations.find(memory);
   if (it == allocations.end()) std::abort();
   const auto flags = properties.memoryTypes[it->second.type].propertyFlags;
   const bool bar = flags & VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT;
   if (mode) {
      if (bar) ++bar_maps; else ++system_maps;
      /* Ignore small, unrelated descriptor/constant maps. Target fresh large
       * upload allocations, including Gallium's persistent stream uploader. */
      if (it->second.size >= (1u << 20) && (mode == 2 || (mode == 1 && bar))) {
         ++failures;
         std::fprintf(stderr,"INJECTED: vkMapMemory(%llu bytes, %s) -> OUT_OF_DEVICE_MEMORY\n",
                      it->second.size,bar ? "BAR VRAM" : "system RAM");
         return true;
      }
   }
   return false;
}

static VkResult VKAPI_CALL map_memory(VkDevice d, VkDeviceMemory memory, VkDeviceSize offset,
                                      VkDeviceSize size, VkMemoryMapFlags flags, void **out)
{
   if (fail_map(memory)) { *out = nullptr; return VK_ERROR_OUT_OF_DEVICE_MEMORY; }
   return reinterpret_cast<PFN_vkMapMemory>(real_gdpa(d,"vkMapMemory"))(d,memory,offset,size,flags,out);
}

static VkResult VKAPI_CALL map_memory2(VkDevice d, const VkMemoryMapInfo *info, void **out)
{
   if (fail_map(info->memory)) { *out = nullptr; return VK_ERROR_OUT_OF_DEVICE_MEMORY; }
   return reinterpret_cast<PFN_vkMapMemory2>(real_gdpa(d,"vkMapMemory2"))(d,info,out);
}

extern "C" void zinkMemoryTestArm(unsigned value)
{
   std::lock_guard<std::mutex> guard(lock);
   mode = value; failures = bar_maps = system_maps = 0;
}
extern "C" void zinkMemoryTestStats(unsigned out[3])
{
   std::lock_guard<std::mutex> guard(lock);
   out[0] = failures; out[1] = bar_maps; out[2] = system_maps;
}
extern "C" VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetInstanceProcAddr(VkInstance,const char *);
extern "C" VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetDeviceProcAddr(VkDevice,const char *);

static PFN_vkVoidFunction lookup(const char *name)
{
#define PROC(n, f) if (!std::strcmp(name,"vk" #n)) return reinterpret_cast<PFN_vkVoidFunction>(f)
   PROC(GetInstanceProcAddr,vkGetInstanceProcAddr); PROC(GetDeviceProcAddr,vkGetDeviceProcAddr);
   PROC(GetPhysicalDeviceMemoryProperties,memory_properties);
   PROC(GetPhysicalDeviceMemoryProperties2,memory_properties2);
   PROC(GetPhysicalDeviceMemoryProperties2KHR,memory_properties2);
   PROC(AllocateMemory,allocate_memory); PROC(FreeMemory,free_memory);
   PROC(MapMemory,map_memory); PROC(MapMemory2,map_memory2); PROC(MapMemory2KHR,map_memory2);
#undef PROC
   return nullptr;
}

extern "C" VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetInstanceProcAddr(VkInstance i,const char *name)
{
   initialize(); if (!name) return nullptr;
   if (i) test_instance = i;
   if (auto fn = lookup(name)) return fn;
   return real_gipa(i,name);
}
extern "C" VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetDeviceProcAddr(VkDevice d,const char *name)
{
   initialize(); if (!name) return nullptr;
   if (auto fn = lookup(name)) return fn;
   return real_gdpa(d,name);
}
