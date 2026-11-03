/* SPDX-License-Identifier: MIT */
#import <Cocoa/Cocoa.h>
#import <OpenGL/gl.h>
#import <OpenGL/glext.h>
#import <CoreGraphics/CoreGraphics.h>
#include "nvk_zink.h"
#include <dlfcn.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* Observe our own composited window: GL readback alone cannot detect a
 * successful draw that never reaches the display. Requires screen-recording
 * permission; exit 77 means the permission was not already granted. */
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); exit(1); } } while (0)
static const float colors[][3] = {{1,0,0},{0,1,0},{0,0,1},{1,1,0},{0,1,1},{1,0,1}};
static unsigned native_copies;
static int observe(const struct nvk_zink_present_event *event)
{
   (void)event; ++native_copies; return 1;
}
static void *bridge_library(void)
{
   for (uint32_t i=0; i<_dyld_image_count(); ++i) {
      const char *path=_dyld_get_image_name(i), *name=path?strrchr(path,'/'):NULL;
      if (name && !strcmp(name,"/libnvk_cgl.dylib")) return dlopen(path,RTLD_LAZY|RTLD_NOLOAD);
   }
   return NULL;
}
static void pump(void)
{
   [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.2]];
}
static void draw(unsigned left, unsigned right)
{
   glDisable(GL_SCISSOR_TEST);
   glClearColor(colors[right][0], colors[right][1], colors[right][2], 1);
   glClear(GL_COLOR_BUFFER_BIT);
   glEnable(GL_SCISSOR_TEST); glScissor(0, 0, 160, 160);
   glClearColor(colors[left][0], colors[left][1], colors[left][2], 1);
   glClear(GL_COLOR_BUFFER_BIT); glDisable(GL_SCISSOR_TEST);
}
static int matches(const unsigned char *p, unsigned color)
{
   return abs(p[0] - (int)(colors[color][0]*255)) < 12 &&
          abs(p[1] - (int)(colors[color][1]*255)) < 12 &&
          abs(p[2] - (int)(colors[color][2]*255)) < 12;
}
static void expect_window(NSWindow *window, unsigned left, unsigned right)
{
   if (![NSThread isMainThread]) {
      dispatch_sync(dispatch_get_main_queue(), ^{ expect_window(window,left,right); });
      return;
   }
   pump();
   CGImageRef (*capture)(CGRect, CGWindowListOption, CGWindowID, CGWindowImageOption) =
      dlsym(RTLD_DEFAULT, "CGWindowListCreateImage");
   CHECK(capture);
   CGImageRef image = capture(CGRectNull, kCGWindowListOptionIncludingWindow,
      (CGWindowID)window.windowNumber, kCGWindowImageBoundsIgnoreFraming);
   CHECK(image);
   size_t width = CGImageGetWidth(image), height = CGImageGetHeight(image), row = width*4;
   unsigned char *pixels = calloc(height, row); CHECK(pixels);
   CGColorSpaceRef cs = CGColorSpaceCreateDeviceRGB();
   CGContextRef bitmap = CGBitmapContextCreate(pixels, width, height, 8, row, cs,
      kCGImageAlphaPremultipliedLast | kCGBitmapByteOrder32Big); CHECK(bitmap);
   CGContextDrawImage(bitmap, CGRectMake(0,0,width,height), image);
   unsigned good = 0, total = 0;
   for (size_t y=height/3; y<height*2/3; ++y)
      for (size_t x=width/8; x<width*7/8; ++x) {
         good += matches(pixels+y*row+x*4, x<width/2 ? left : right); ++total;
      }
   printf("compositor expected=%u,%u matched=%u/%u\n", left, right, good, total);
   CGContextRelease(bitmap); CGColorSpaceRelease(cs); CGImageRelease(image); free(pixels);
   CHECK(good > total*99/100);
}
static void expect_back(unsigned color)
{
   unsigned char pixel[4]; glReadBuffer(GL_BACK);
   glReadPixels(240,80,1,1,GL_RGBA,GL_UNSIGNED_BYTE,pixel);
   CHECK(matches(pixel,color)); CHECK(glGetError()==GL_NO_ERROR);
}
int main(int argc, char **argv)
{
   @autoreleasepool {
      if (argc != 2) { fprintf(stderr,"usage: %s flush|finish|flush-apple|finish-apple|double|worker|noncurrent|other|apple-other|late-view\n",argv[0]); return 2; }
      setvbuf(stdout,NULL,_IOLBF,0);
      if (!CGPreflightScreenCaptureAccess()) { puts("SKIP: screen-recording permission not granted"); return 77; }
      BOOL noncurrent = !strcmp(argv[1],"noncurrent") || !strcmp(argv[1],"other") || !strcmp(argv[1],"apple-other");
      BOOL other = !strcmp(argv[1],"other") || !strcmp(argv[1],"apple-other");
      BOOL apple = !strcmp(argv[1],"apple-other");
      BOOL late_view = !strcmp(argv[1],"late-view"), early_swap = !strcmp(argv[1],"early-swap");
      BOOL doubled = !strcmp(argv[1],"double") || noncurrent || late_view || early_swap;
      BOOL worker = !strcmp(argv[1],"worker");
      const char *selected_mode=getenv("NVKZINK_PRESENT");
      char *saved_mode=selected_mode?strdup(selected_mode):NULL;
      BOOL forced_gpu=selected_mode && !strcmp(selected_mode,"gpu");
      /* Suppress native view registration during initial attachment. The
       * first swap below runs on a worker without a registered native view. */
      if (late_view) CHECK(setenv("NVKZINK_PRESENT","gpu",1)==0);
      void (*flush)(void) = NULL;
      if (!doubled) {
         const char *symbol = !strcmp(argv[1],"flush") || worker ? "glFlush" :
            !strcmp(argv[1],"finish") ? "glFinish" :
            !strcmp(argv[1],"flush-apple") ? "glFlushRenderAPPLE" :
            !strcmp(argv[1],"finish-apple") ? "glFinishRenderAPPLE" : NULL;
         CHECK(symbol); flush=dlsym(RTLD_DEFAULT,symbol); CHECK(flush);
      }
      [NSApplication sharedApplication]; [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
      NSOpenGLPixelFormatAttribute attrs[] = {NSOpenGLPFAAccelerated,
         NSOpenGLPFAColorSize,24,NSOpenGLPFAAlphaSize,8,0,0};
      if (doubled) attrs[5]=NSOpenGLPFADoubleBuffer;
      NSOpenGLPixelFormat *format=[[NSOpenGLPixelFormat alloc] initWithAttributes:attrs]; CHECK(format);
      NSOpenGLContext *initial=nil;
      if (early_swap) {
         initial=[[NSOpenGLContext alloc] initWithFormat:format shareContext:nil]; CHECK(initial);
         [initial makeCurrentContext]; CHECK(strstr((const char *)glGetString(GL_RENDERER),"zink"));
         void *lib=bridge_library(); CHECK(lib);
         int (*early_present)(CGLContextObj)=dlsym(lib,"nvk_cgl_present"); CHECK(early_present);
         GLint backing[2]={0}; CHECK(CGLGetParameter(initial.CGLContextObj,kCGLCPSurfaceBackingSize,backing)==kCGLNoError);
         printf("unattached backing=%d,%d\n",backing[0],backing[1]);
         CHECK(early_present(initial.CGLContextObj)); dlclose(lib);
      }
      NSOpenGLView *view=[[NSOpenGLView alloc] initWithFrame:NSMakeRect(0,0,320,160) pixelFormat:format];
      if (initial) view.openGLContext=initial;
      NSWindow *window=[[NSWindow alloc] initWithContentRect:NSMakeRect(50,50,320,160)
         styleMask:NSWindowStyleMaskTitled backing:NSBackingStoreBuffered defer:NO];
      window.title=@"Zink front-buffer regression probe"; window.contentView=view; [window orderFront:nil]; pump();
      NSOpenGLContext *context=view.openGLContext; [context makeCurrentContext]; [context update];
      const char *renderer=(const char *)glGetString(GL_RENDERER); printf("renderer=%s mode=%s\n",renderer,argv[1]);
      CHECK(renderer && strstr(renderer,"zink"));
      void *library=bridge_library();
      CHECK(library);
      int (*present)(CGLContextObj)=dlsym(library,"nvk_cgl_present"); CHECK(present);
      /* Older negative controls lack this accessor, but still fail the
       * compositor check. With the repair, verify restoration before any
       * public GL adapter could lazily repair the Mesa binding itself. */
      void *(*mesa_current)(void)=dlsym(library,"nvk_zink_get_current");
      if (early_swap) {
         void (*set_observer)(nvk_zink_present_observer)=dlsym(library,"nvk_zink_set_present_observer"); CHECK(set_observer);
         set_observer(observe); glViewport(0,0,320,160);
         for (unsigned frame=0;frame<3;++frame) {
            draw(frame*2,frame*2+1); glFinish(); [NSOpenGLContext clearCurrentContext];
            CHECK(present(context.CGLContextObj)); CHECK(CGLGetCurrentContext()==NULL);
            expect_window(window,frame*2,frame*2+1); [context makeCurrentContext];
         }
         CHECK(native_copies==3); set_observer(NULL); [window orderOut:nil];
         [NSOpenGLContext clearCurrentContext]; dlclose(library); free(saved_mode);
         puts("PASS: swaps before view attachment do not poison later native presentation"); return 0;
      }
      if (late_view) {
         CHECK(saved_mode ? setenv("NVKZINK_PRESENT",saved_mode,1)==0 : unsetenv("NVKZINK_PRESENT")==0);
         void (*set_observer)(nvk_zink_present_observer)=dlsym(library,"nvk_zink_set_present_observer"); CHECK(set_observer);
         set_observer(observe); [NSOpenGLContext clearCurrentContext];
         dispatch_semaphore_t done=dispatch_semaphore_create(0);
         NSThread *thread=[[NSThread alloc] initWithBlock:^{ @autoreleasepool {
            [context makeCurrentContext]; glViewport(0,0,320,160); draw(0,1);
            CHECK(CGLFlushDrawable(context.CGLContextObj)==kCGLNoError);
            [NSOpenGLContext clearCurrentContext]; dispatch_semaphore_signal(done);
         }}];
         [thread start]; unsigned waits=0;
         while (dispatch_semaphore_wait(done,DISPATCH_TIME_NOW)) { CHECK(++waits<100); pump(); }
         CHECK(native_copies==0); expect_window(window,0,1);
         [context makeCurrentContext]; [context update]; /* Register the view on main. */
         draw(2,3); CHECK(present(context.CGLContextObj)); expect_window(window,2,3);
         CHECK(native_copies==(forced_gpu?0u:1u));
         draw(4,5); CHECK(CGLFlushDrawable(context.CGLContextObj)==kCGLNoError); expect_window(window,4,5);
         CHECK(native_copies==(forced_gpu?0u:2u)); set_observer(NULL);
         [window orderOut:nil]; [NSOpenGLContext clearCurrentContext]; dlclose(library); free(saved_mode);
         puts("PASS: late native view registration; explicit GPU policy preserved"); return 0;
      }
      free(saved_mode);
      GLint double_state=-1; glGetIntegerv(GL_DOUBLEBUFFER,&double_state); CHECK(!!double_state==!!doubled);
      NSOpenGLContext *sentinel=nil;
      if (other) {
         if (apple) CHECK(setenv("NVKZINK_DISABLE","1",1)==0);
         sentinel=[[NSOpenGLContext alloc] initWithFormat:format shareContext:nil]; CHECK(sentinel);
         [sentinel makeCurrentContext];
         const char *name=(const char *)glGetString(GL_RENDERER); CHECK(name);
         CHECK(apple ? !strstr(name,"zink") : !!strstr(name,"zink"));
         glViewport(9,7,23,29); glClearColor(.25,.5,.75,1);
         CHECK(glGetError()==GL_NO_ERROR);
         if (apple) CHECK(setenv("NVKZINK_DISABLE","0",1)==0);
         [context makeCurrentContext];
      }
      void (^render)(void) = ^{
      glViewport(3,5,310,150); GLint before[4],after[4]; glGetIntegerv(GL_VIEWPORT,before);
      if (noncurrent) {
         for (unsigned frame=0; frame<3; ++frame) {
            draw(frame*2,frame*2+1); glFinish();
            if (other) [sentinel makeCurrentContext]; else [NSOpenGLContext clearCurrentContext];
            CGLContextObj previous=CGLGetCurrentContext();
            CHECK(previous==(other?sentinel.CGLContextObj:NULL));
            void *previous_mesa=mesa_current?mesa_current():NULL;
            if (frame==1) CHECK(CGLFlushDrawable(context.CGLContextObj)==kCGLNoError);
            else if (frame==2) CHECK(present(context.CGLContextObj));
            else [context flushBuffer];
            CHECK(CGLGetCurrentContext()==previous);
            CHECK(!mesa_current || mesa_current()==previous_mesa);
            if (other) {
               GLint viewport[4]; glGetIntegerv(GL_VIEWPORT,viewport);
               const GLint expected[]={9,7,23,29}; CHECK(!memcmp(viewport,expected,sizeof(expected)));
               GLfloat clear[4]; glGetFloatv(GL_COLOR_CLEAR_VALUE,clear);
               CHECK(clear[0]==.25f && clear[1]==.5f && clear[2]==.75f && clear[3]==1);
               CHECK(glGetError()==GL_NO_ERROR);
            }
            expect_window(window,frame*2,frame*2+1);
            [context makeCurrentContext];
            glGetIntegerv(GL_VIEWPORT,after); CHECK(!memcmp(before,after,sizeof(before))); CHECK(glGetError()==GL_NO_ERROR);
         }
         return;
      }
      if (!doubled) {
         for (unsigned frame=0; frame<3; ++frame) {
            draw(frame*2,frame*2+1); flush();
            /* Native setup can defer the first worker acquire to AppKit.
             * Retry without another draw: the dirty notification must survive. */
            if (worker) for (unsigned retry=0; retry<4; ++retry) { usleep(50000); flush(); }
            expect_window(window,frame*2,frame*2+1);
            glGetIntegerv(GL_VIEWPORT,after); CHECK(!memcmp(before,after,sizeof(before)));
            CHECK(glGetError()==GL_NO_ERROR);
         }
         /* Invalid flushes inside Begin/End must stay in the GL error stream. */
         glBegin(GL_POINTS); glFlush(); glEnd(); CHECK(glGetError()==GL_INVALID_OPERATION);
         flush(); expect_window(window,4,5);
      } else {
         draw(0,1); CHECK(CGLFlushDrawable(context.CGLContextObj)==kCGLNoError); expect_window(window,0,1);
         draw(5,5); glFlush(); expect_window(window,0,1); /* BACK is not a present. */
         glDrawBuffer(GL_FRONT); glReadBuffer(GL_BACK);
         draw(2,3); glFlush(); expect_window(window,2,3);
         GLint draw_buffer=0,read_buffer=0; glGetIntegerv(GL_DRAW_BUFFER,&draw_buffer); glGetIntegerv(GL_READ_BUFFER,&read_buffer);
         CHECK(draw_buffer==GL_FRONT && read_buffer==GL_BACK); expect_back(5); /* No front/back rotation. */
         glDrawBuffer(GL_BACK); CHECK(CGLFlushDrawable(context.CGLContextObj)==kCGLNoError); expect_window(window,5,5);
         GLuint fbo,rb; glGenFramebuffersEXT(1,&fbo); glBindFramebufferEXT(GL_FRAMEBUFFER_EXT,fbo);
         glGenRenderbuffersEXT(1,&rb); glBindRenderbufferEXT(GL_RENDERBUFFER_EXT,rb);
         glRenderbufferStorageEXT(GL_RENDERBUFFER_EXT,GL_RGBA8,320,160);
         glFramebufferRenderbufferEXT(GL_FRAMEBUFFER_EXT,GL_COLOR_ATTACHMENT0_EXT,GL_RENDERBUFFER_EXT,rb);
         CHECK(glCheckFramebufferStatusEXT(GL_FRAMEBUFFER_EXT)==GL_FRAMEBUFFER_COMPLETE_EXT);
         draw(0,0); glFinish(); expect_window(window,5,5); /* Offscreen flush is not a present. */
         GLint saved_fbo=0; glGetIntegerv(GL_FRAMEBUFFER_BINDING_EXT,&saved_fbo); CHECK(saved_fbo==(GLint)fbo);
         glBindFramebufferEXT(GL_FRAMEBUFFER_EXT,0); glDeleteFramebuffersEXT(1,&fbo); glDeleteRenderbuffersEXT(1,&rb);
         glDrawBuffer(GL_BACK); draw(4,4); glFlush(); expect_window(window,5,5);
         CHECK(CGLFlushDrawable(context.CGLContextObj)==kCGLNoError); expect_window(window,4,4);
         glGetIntegerv(GL_VIEWPORT,after); CHECK(!memcmp(before,after,sizeof(before))); CHECK(glGetError()==GL_NO_ERROR);
      }
      };
      if (worker) {
         [NSOpenGLContext clearCurrentContext];
         dispatch_semaphore_t done=dispatch_semaphore_create(0);
         NSThread *thread=[[NSThread alloc] initWithBlock:^{ @autoreleasepool {
            [context makeCurrentContext]; render(); [NSOpenGLContext clearCurrentContext];
            dispatch_semaphore_signal(done);
         }}];
         [thread start];
         unsigned waits=0;
         while (dispatch_semaphore_wait(done,DISPATCH_TIME_NOW)) { CHECK(++waits<100); pump(); }
      } else render();
      [window orderOut:nil]; [NSOpenGLContext clearCurrentContext];
      dlclose(library);
      puts("PASS: composited front-buffer presentation and GL state");
      return 0;
   }
}
