/* Separate diagnostic readback of the actual native presentation buffer. */
#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>
#import <Cocoa/Cocoa.h>
#import <QuartzCore/CAMetalLayer.h>
#include <OpenGL/gl3.h>
#include "nvk_zink.h"
#include "nvk_cgl.h"
#include <dlfcn.h>
#include <cstdio>
#include <cstdlib>
#include <stdexcept>
#include <cstring>
#include <initializer_list>
#include <chrono>
#include <vector>
#include <sys/sysctl.h>

static unsigned verified;
static unsigned host_verified;
static unsigned red = 255, green_value = 255;
static unsigned drawn_width, drawn_height;
static bool transparent;
static int reject(const nvk_zink_present_event *) { return 0; }
static int uncertain(const nvk_zink_present_event *) { return -1; }
static void require(bool ok, const char *what) { if (!ok) throw std::runtime_error(what); }
static void check_swap_interval(NSOpenGLView *view, CGLContextObj context, GLint interval)
{
   require([NSThread isMainThread], "Inspect native layer on main");
   GLint observed = -1;
   require(!CGLGetParameter(context,kCGLCPSwapInterval,&observed) && observed == interval,
           "CGL must retain the application's requested swap interval");
   CAMetalLayer *native = nil;
   for (CALayer *layer in view.layer.sublayers)
      if ([layer isKindOfClass:[CAMetalLayer class]]) {
         require(!native,"One native layer per view"); native = (CAMetalLayer *)layer;
      }
   require(native,"Native presentation layer must exist");
   require(native.displaySyncEnabled == (interval != 0),
           "Native layer must follow the CGL swap interval");
   printf("PASS swap interval %d: native display synchronization %s\n",interval,
          native.displaySyncEnabled ? "enabled" : "disabled");
}
static void check(VkResult status) { require(status == VK_SUCCESS, "Vulkan diagnostic operation failed"); }
static void draw_pattern(unsigned width, unsigned height)
{
   drawn_width = width; drawn_height = height;
   /* Different colors per frame catch reuse of an obsolete front/back image. */
   red = 64 + (verified % 8) * 19; green_value = 128 + (verified % 5) * 23;
   glViewport(0,0,width,height); glClearColor(red/255.0f,0,0,0); glClear(GL_COLOR_BUFFER_BIT);
   glEnable(GL_SCISSOR_TEST); glScissor(width/4,height/4,width/2,height/2);
   glClearColor(0,green_value/255.0f,0,0); glClear(GL_COLOR_BUFFER_BIT);
   glScissor(width-8,height-8,8,8); glClearColor(0,0,1,0); glClear(GL_COLOR_BUFFER_BIT); glDisable(GL_SCISSOR_TEST);
}
#define D(name) auto name = (PFN_vk##name)gdpa(device, "vk" #name); require(name, "vk" #name)
static int observe(const nvk_zink_present_event *event)
{
   bool uncertain = false;
   try {
      require(event->width == drawn_width && event->height == drawn_height,
              "Native drawable dimensions differ from the rendered frame");
      auto gdpa = (PFN_vkGetDeviceProcAddr)event->get_device_proc_addr;
      auto device = (VkDevice)event->device; auto queue = (VkQueue)event->queue;
      D(CreateBuffer); D(DestroyBuffer); D(GetBufferMemoryRequirements); D(AllocateMemory); D(FreeMemory);
      D(BindBufferMemory); D(CreateCommandPool); D(DestroyCommandPool); D(AllocateCommandBuffers);
      D(BeginCommandBuffer); D(EndCommandBuffer); D(CmdPipelineBarrier); D(CmdCopyBuffer);
      D(QueueSubmit); D(CreateFence); D(DestroyFence); D(WaitForFences); D(MapMemory); D(UnmapMemory);
      VkBufferCreateInfo info{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
      info.size = (uint64_t)event->row * event->height; info.usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT;
      VkBuffer target{}; check(CreateBuffer(device, &info, nullptr, &target));
      VkMemoryRequirements requirements{}; GetBufferMemoryRequirements(device, target, &requirements);
      uint32_t types = requirements.memoryTypeBits & event->host_coherent_types;
      require(types, "No coherent diagnostic memory type");
      VkMemoryAllocateInfo allocation{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
      allocation.allocationSize = requirements.size; allocation.memoryTypeIndex = __builtin_ctz(types);
      VkDeviceMemory memory{}; check(AllocateMemory(device, &allocation, nullptr, &memory));
      check(BindBufferMemory(device, target, memory, 0));
      VkCommandPoolCreateInfo pool_info{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
      pool_info.queueFamilyIndex = event->queue_family;
      VkCommandPool pool{}; check(CreateCommandPool(device, &pool_info, nullptr, &pool));
      VkCommandBufferAllocateInfo command_info{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
      command_info.commandPool = pool; command_info.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY; command_info.commandBufferCount = 1;
      VkCommandBuffer command{}; check(AllocateCommandBuffers(device, &command_info, &command));
      VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO}; check(BeginCommandBuffer(command, &begin));
      VkMemoryBarrier before{VK_STRUCTURE_TYPE_MEMORY_BARRIER};
      before.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT; before.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
      CmdPipelineBarrier(command, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 1, &before, 0, nullptr, 0, nullptr);
      VkBufferCopy region{0, 0, info.size}; CmdCopyBuffer(command, (VkBuffer)event->buffer, target, 1, &region);
      VkMemoryBarrier after{VK_STRUCTURE_TYPE_MEMORY_BARRIER};
      after.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT; after.dstAccessMask = VK_ACCESS_HOST_READ_BIT;
      CmdPipelineBarrier(command, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT, 0, 1, &after, 0, nullptr, 0, nullptr);
      check(EndCommandBuffer(command));
      VkFenceCreateInfo fence_info{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO}; VkFence fence{};
      check(CreateFence(device, &fence_info, nullptr, &fence));
      VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO}; submit.commandBufferCount = 1; submit.pCommandBuffers = &command;
      uncertain = true; check(QueueSubmit(queue, 1, &submit, fence));
      check(WaitForFences(device, 1, &fence, VK_TRUE, 5000000000ull)); uncertain = false;
      void *mapping = nullptr; check(MapMemory(device, memory, 0, VK_WHOLE_SIZE, 0, &mapping));
      unsigned mismatches = 0;
      for (unsigned y = 0; y < event->height; ++y) for (unsigned x = 0; x < event->width; ++x) {
         unsigned source_y = event->height - y - 1;
         /* Match glScissor(origin, extent), including integer truncation.
          * floor(3*h/4) differs from floor(h/4)+floor(h/2) when h%4==3.
          * AppKit can constrain a requested 960px window to an odd height. */
         bool green = x >= event->width/4 && x < event->width/4 + event->width/2 &&
                      source_y >= event->height/4 && source_y < event->height/4 + event->height/2;
         bool blue = x >= event->width-8 && source_y >= event->height-8;
         const unsigned char expected[] = {(unsigned char)(blue ? 255 : 0),
            (unsigned char)(green && !blue ? green_value : 0), (unsigned char)(green || blue ? 0 : red),
            (unsigned char)(transparent ? 0 : 255)};
         const unsigned char *pixel = (const unsigned char *)mapping + (size_t)y * event->row + x*4;
         if (memcmp(pixel, expected, 4) && ++mismatches <= 3)
            fprintf(stderr, "native pixel %u,%u: %u,%u,%u,%u expected %u,%u,%u,%u\n", x,y,
                    pixel[0],pixel[1],pixel[2],pixel[3],expected[0],expected[1],expected[2],expected[3]);
      }
      UnmapMemory(device, memory); DestroyCommandPool(device, pool, nullptr); DestroyFence(device, fence, nullptr);
      DestroyBuffer(device, target, nullptr); FreeMemory(device, memory, nullptr);
      require(!mismatches, "Native drawable copy has incorrect pixels/orientation/alpha");
      ++verified;
      host_verified += event->host_backing != 0;
      printf("PASS native copy %u: %ux%u pitch=%u backing=%s, all pixels verified\n",
             verified,event->width,event->height,event->row,event->host_backing ? "system pages" : "VRAM");
      return 1;
   } catch (const std::exception &error) {
      fprintf(stderr, "Native copy diagnostic: %s\n", error.what());
      return uncertain ? -1 : 0;
   }
}

int main(int argc, char **argv)
{
   @autoreleasepool { try {
      require(argc >= 3, "usage: native-probe libnvk_cgl.dylib nvk_icd.json [transparent] [automatic] [worker] [reject|uncertain] [default] [windows] [swap-interval] [pacing] [large-resize|odd-resize]");
      setvbuf(stdout, nullptr, _IOLBF, 0);
      bool automatic = false, worker = false, defaults = false, windows = false;
      bool swap_interval = false, pacing = false, large_resize = false, odd_resize = false;
      int failure = 0;
      for (int i = 3; i < argc; ++i) {
         if (!strcmp(argv[i], "transparent")) transparent = true;
         else if (!strcmp(argv[i], "automatic")) automatic = true;
         else if (!strcmp(argv[i], "worker")) worker = true;
         else if (!strcmp(argv[i], "reject")) failure = 1;
         else if (!strcmp(argv[i], "uncertain")) failure = -1;
         else if (!strcmp(argv[i], "default")) defaults = true;
         else if (!strcmp(argv[i], "windows")) windows = true;
         else if (!strcmp(argv[i], "swap-interval")) swap_interval = true;
         else if (!strcmp(argv[i], "pacing")) { pacing = true; swap_interval = true; }
         else if (!strcmp(argv[i], "large-resize")) large_resize = true;
         else if (!strcmp(argv[i], "odd-resize")) odd_resize = true;
         else require(false, "Unknown option");
      }
      if (defaults) unsetenv("NVKZINK_PRESENT"); else setenv("NVKZINK_PRESENT", "native", 1);
      const char *expected_build = getenv("ZINK_TEST_OS_BUILD");
      if (expected_build) {
         char observed[128]; size_t length = sizeof(observed);
         require(!sysctlbyname("kern.osversion",observed,&length,nullptr,0) &&
                 !strcmp(observed,expected_build),"Process-local OS build simulation must be active");
         printf("Process-local simulated OS build: %s; actual CGL layout is unchanged\n",observed);
      }
      setenv("VK_DRIVER_FILES", argv[2], 1);
      void *library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL); require(library, dlerror() ?: "CGL load");
      auto attach = (int(*)(CGLContextObj,unsigned,unsigned))dlsym(library, "nvk_cgl_attach");
      auto detach = (int(*)(CGLContextObj))dlsym(library, "nvk_cgl_detach");
      auto resize = (int(*)(CGLContextObj,unsigned,unsigned))dlsym(library, "nvk_cgl_resize");
      auto error = (const char*(*)())dlsym(library, "nvk_cgl_last_error");
      auto observer = (void(*)(nvk_zink_present_observer))dlsym(library, "nvk_zink_set_present_observer");
      require(attach && detach && resize && error && observer, "Required bridge API"); observer(observe);
      [NSApplication sharedApplication]; [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory]; [NSApp finishLaunching];
      NSOpenGLPixelFormatAttribute attributes[] = {NSOpenGLPFAOpenGLProfile, NSOpenGLProfileVersion4_1Core,
         NSOpenGLPFAColorSize,24,NSOpenGLPFAAlphaSize,8,NSOpenGLPFADoubleBuffer,0};
      NSOpenGLPixelFormat *format = [[NSOpenGLPixelFormat alloc] initWithAttributes:attributes];
      NSOpenGLView *view = [[NSOpenGLView alloc] initWithFrame:NSMakeRect(0,0,128,128) pixelFormat:format];
      NSWindow *window = [[NSWindow alloc] initWithContentRect:NSMakeRect(100,100,128,128)
         styleMask:NSWindowStyleMaskTitled|NSWindowStyleMaskResizable backing:NSBackingStoreBuffered defer:NO];
      window.title = @"Native Zink GPU handoff test"; window.contentView = view; [window orderFront:nil];
      NSOpenGLContext *context = view.openGLContext; [context makeCurrentContext]; [context update];
      CGLContextObj cgl = context.CGLContextObj; GLint opacity = !transparent;
      require(!CGLSetParameter(cgl,kCGLCPSurfaceOpacity,&opacity), "Opacity");
      GLint initial_interval = 1;
      if (swap_interval) require(!CGLSetParameter(cgl,kCGLCPSwapInterval,&initial_interval), "Initial swap interval");
      if (!automatic) require(attach(cgl,128,128), error());
      printf("Native test renderer: %s\n", glGetString(GL_RENDERER));
      auto present = (int(*)(CGLContextObj))dlsym(library,"nvk_cgl_present"); require(present,"Present API");
      std::vector<NSSize> sizes = {NSMakeSize(128,128),NSMakeSize(64,96),NSMakeSize(192,160),NSMakeSize(640,480)};
      if (large_resize) sizes = {NSMakeSize(128,128),NSMakeSize(854,480),NSMakeSize(1920,960),
         NSMakeSize(854,480),NSMakeSize(1920,1080),NSMakeSize(1280,720),
         NSMakeSize(1920,960),NSMakeSize(640,480)};
      if (odd_resize) sizes = {NSMakeSize(127,95),NSMakeSize(131,99),NSMakeSize(17,15),
         NSMakeSize(31,31),NSMakeSize(129,97),NSMakeSize(854,481),NSMakeSize(640,480)};
      for (const NSSize size : sizes) {
         [window setContentSize:size]; [context update];
         for (unsigned i=0;i<3;++i) {
            [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.05]];
            NSRect backing = view.wantsBestResolutionOpenGLSurface ? [view convertRectToBacking:view.bounds] : view.bounds;
            unsigned width=backing.size.width,height=backing.size.height;
            /* Automatic mode must pick up AppKit's new size itself. Calling
             * the bridge resize API here would hide a broken update hook. */
            if (!((large_resize || odd_resize) && automatic)) require(resize(cgl,width,height),error());
            draw_pattern(width,height);
            require(!glGetError(), "GL draw");
            if (i & 1) require(present(cgl),error());
            else require(!CGLFlushDrawable(cgl), "CGL swap"); require(!strcmp(error(),"none"),error());
         }
      }
      require(verified==sizes.size()*3,"Every native frame must be verified");
      if (swap_interval) {
         check_swap_interval(view,cgl,initial_interval);
         for (GLint interval : {0,1,0}) {
            unsigned before = verified;
            require(!CGLSetParameter(cgl,kCGLCPSwapInterval,&interval),"Change swap interval");
            draw_pattern(640,480); require(present(cgl),error());
            require(verified==before+1,"Swap-interval change must preserve the copied frame");
            check_swap_interval(view,cgl,interval);
         }
      }
      if (worker) {
         [NSOpenGLContext clearCurrentContext];
         [window setContentSize:NSMakeSize(80,112)]; [context update];
         dispatch_semaphore_t done = dispatch_semaphore_create(0);
         __block bool passed = false;
         dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED,0), ^{
            @autoreleasepool { try {
               require(!CGLSetCurrentContext(cgl), "Worker current");
               require(resize(cgl,80,112),error());
               unsigned expected = verified + 3;
               for (unsigned attempts=0; attempts<40 && verified<expected; ++attempts) {
                  draw_pattern(80,112);
                  require(present(cgl),error());
                  [NSThread sleepForTimeInterval:0.01];
               }
               require(verified==expected,"Worker frames must all reach the native drawable");
               if (swap_interval) for (GLint interval : {1,0,1,0}) {
                  unsigned before = verified;
                  require(!CGLSetParameter(cgl,kCGLCPSwapInterval,&interval),"Worker swap interval");
                  for (unsigned attempts=0; attempts<40 && verified==before; ++attempts) {
                     draw_pattern(80,112); require(present(cgl),error());
                     [NSThread sleepForTimeInterval:0.01];
                  }
                  require(verified==before+1,"Worker interval change must reach the native drawable");
                  /* The probe checks AppKit state on main while the worker
                   * keeps ownership of its CGL context. */
                  dispatch_sync(dispatch_get_main_queue(), ^{ check_swap_interval(view,cgl,interval); });
               }
               passed = true;
               CGLSetCurrentContext(nullptr);
            } catch (const std::exception &error) { fprintf(stderr,"Worker: %s\n",error.what()); }
            dispatch_semaphore_signal(done); }
         });
         unsigned waits = 0;
         while (dispatch_semaphore_wait(done,DISPATCH_TIME_NOW)) {
            require(++waits<600,"Worker must not wait indefinitely for AppKit");
            [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.05]];
         }
         require(passed,"Worker presentation"); [context makeCurrentContext];
      }
      if (windows) {
         GLuint shared_texture = 0; glGenTextures(1,&shared_texture); glBindTexture(GL_TEXTURE_2D,shared_texture);
         glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,4,4,0,GL_RGBA,GL_UNSIGNED_BYTE,nullptr);
         NSOpenGLContext *second = [[NSOpenGLContext alloc] initWithFormat:format shareContext:context];
         NSOpenGLView *second_view = [[NSOpenGLView alloc] initWithFrame:NSMakeRect(0,0,48,64) pixelFormat:format];
         second_view.openGLContext = second;
         NSWindow *second_window = [[NSWindow alloc] initWithContentRect:NSMakeRect(350,100,48,64)
            styleMask:NSWindowStyleMaskTitled backing:NSBackingStoreBuffered defer:NO];
         second_window.contentView = second_view; [second_window orderFront:nil];
         [second makeCurrentContext]; [second update]; CGLContextObj other = second.CGLContextObj;
         require(!CGLSetParameter(other,kCGLCPSurfaceOpacity,&opacity),"Second window opacity");
         GLint second_interval = 1;
         if (swap_interval) require(!CGLSetParameter(other,kCGLCPSwapInterval,&second_interval),"Second window swap interval");
         if (!automatic) require(attach(other,48,64),error());
         require(glIsTexture(shared_texture),"CGL sharing must preserve the actual GL object");
         for (unsigned i=0;i<3;++i) { require(resize(other,48,64),error());draw_pattern(48,64);require(present(other),error()); }
         if (swap_interval) { check_swap_interval(second_view,other,1); check_swap_interval(view,cgl,0); }
         require(detach(other),error()); [second_window orderOut:nil];
         [context makeCurrentContext]; draw_pattern(worker ? 80 : 640,worker ? 112 : 480);
         require(present(cgl),error()); glDeleteTextures(1,&shared_texture);
         puts("PASS: two native windows retain independent targets and shared GL objects");
      }
      if (pacing) {
         /* Measure actual presentation without the diagnostic readback. This
          * is a report, not a speed assertion: other GPU clients may run. */
         observer(nullptr);
         unsigned width = worker ? 80 : 640, height = worker ? 112 : 480;
         for (GLint interval : {0,1,0}) {
            require(!CGLSetParameter(cgl,kCGLCPSwapInterval,&interval),"Pacing swap interval");
            draw_pattern(width,height); require(present(cgl),error()); check_swap_interval(view,cgl,interval);
            unsigned frames = 0;
            auto start = std::chrono::steady_clock::now(); double seconds;
            do {
               @autoreleasepool {
                  draw_pattern(width,height); require(present(cgl),error()); ++frames;
               }
               seconds = std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
            } while (seconds < 1.0);
            printf("PACING interval=%d frames=%u seconds=%.3f fps=%.1f\n",interval,frames,seconds,frames/seconds);
         }
         observer(observe);
      }
      if (failure) {
         draw_pattern(worker ? 80 : 640,worker ? 112 : 480);
         observer(failure<0 ? uncertain : reject);
         require(!present(cgl),"Rejected or uncertain work must never report presentation success");
         require(strcmp(error(),"none"),"Copy failure must be reported");
         observer(observe);
         if (failure<0) require(!present(cgl),"Uncertain share group must refuse further presentation");
         else require(present(cgl),error());
         printf("PASS: %s presentation does not publish or reuse uncertain resources\n", failure<0?"uncertain":"rejected");
      }
      observer(nullptr); require(detach(cgl),error());
      if (getenv("ZINK_TEST_NO_FAMILY_VRAM"))
         require(host_verified==verified,"Every frame must use the GPU system-page fallback under injected VRAM refusal");
      [NSOpenGLContext clearCurrentContext]; [window orderOut:nil];
      puts("PASS: native drawable GPU copy, orientation, opacity and resize; no Apple GL presentation blit");
      return 0;
   } catch (const std::exception &error) { fprintf(stderr,"FAIL: %s\n",error.what()); return 1; } }
}
