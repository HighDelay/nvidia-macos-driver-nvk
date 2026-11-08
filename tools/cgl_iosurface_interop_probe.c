/* SPDX-License-Identifier: MIT
 * Real CGL -> Zink -> IOSurface transfers, including Avalonia's delete/flush
 * ordering. Run with the process-local GLD test shim; no app policy changes.
 */
#include <OpenGL/OpenGL.h>
#include <OpenGL/CGLIOSurface.h>
#include <OpenGL/gl3.h>
#include <IOSurface/IOSurface.h>
#include <CoreVideo/CoreVideo.h>
#include <CoreFoundation/CoreFoundation.h>
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHECK(x) do { if (!(x)) { fprintf(stderr,"FAIL line %d: %s (GL %x)\n",__LINE__,#x,glGetError()); exit(1); } } while (0)
enum { W=37,H=23 };
struct surface { IOSurfaceRef io; size_t row; };
static CGLPixelFormatObj pixel_format;
static CGLContextObj main_context;
static int require_apple;
static unsigned passed;
static void *bridge_library;
static void pass(const char *name) { CHECK(glGetError()==GL_NO_ERROR);printf("PASS: %s\n",name);++passed; }

static struct surface create_surface(void)
{
   size_t row=IOSurfaceAlignProperty(kIOSurfaceBytesPerRow,W*4+32);
   size_t bytes=IOSurfaceAlignProperty(kIOSurfaceAllocSize,row*H);
   const void *keys[]={kIOSurfaceWidth,kIOSurfaceHeight,kIOSurfaceBytesPerElement,
      kIOSurfaceBytesPerRow,kIOSurfaceAllocSize,kIOSurfacePixelFormat};
   int values[]={W,H,4,(int)row,(int)bytes,0x42475241};CFNumberRef nums[6];
   for(unsigned i=0;i<6;++i)nums[i]=CFNumberCreate(NULL,kCFNumberIntType,&values[i]);
   CFDictionaryRef properties=CFDictionaryCreate(NULL,keys,(const void **)nums,6,
      &kCFTypeDictionaryKeyCallBacks,&kCFTypeDictionaryValueCallBacks);
   struct surface s={IOSurfaceCreate(properties),row};CHECK(s.io);
   CFRelease(properties);for(unsigned i=0;i<6;++i)CFRelease(nums[i]);return s;
}
static void fill(struct surface s,unsigned r,unsigned g,unsigned b)
{
   CHECK(!IOSurfaceLock(s.io,0,NULL));unsigned char *p=IOSurfaceGetBaseAddress(s.io);CHECK(p);
   memset(p,0xcd,IOSurfaceGetAllocSize(s.io));
   for(unsigned y=0;y<H;++y)for(unsigned x=0;x<W;++x){
      unsigned char *v=p+y*s.row+4*x;v[0]=b;v[1]=g;v[2]=r;v[3]=255;
   }
   CHECK(!IOSurfaceUnlock(s.io,0,NULL));
}
static void cpu_pixel(struct surface s,unsigned x,unsigned y,unsigned r,unsigned g,unsigned b)
{
   CHECK(!IOSurfaceLock(s.io,kIOSurfaceLockReadOnly,NULL));
   unsigned char *p=(unsigned char *)IOSurfaceGetBaseAddress(s.io)+y*s.row+4*x;
   if(p[0]!=b||p[1]!=g||p[2]!=r||p[3]!=255){
      fprintf(stderr,"CPU pixel %u,%u: %u,%u,%u,%u expected %u,%u,%u,255\n",x,y,p[2],p[1],p[0],p[3],r,g,b);exit(1);
   }
   if(!require_apple)for(unsigned j=W*4;j<s.row;++j)CHECK(*((unsigned char *)IOSurfaceGetBaseAddress(s.io)+y*s.row+j)==0xcd);
   CHECK(!IOSurfaceUnlock(s.io,kIOSurfaceLockReadOnly,NULL));
}
static void gl_pixel(unsigned x,unsigned y,unsigned r,unsigned g,unsigned b)
{
   unsigned char p[4];glReadPixels(x,y,1,1,GL_RGBA,GL_UNSIGNED_BYTE,p);
   if(p[0]!=r||p[1]!=g||p[2]!=b||p[3]!=255){
      fprintf(stderr,"GL pixel %u,%u: %u,%u,%u,%u expected %u,%u,%u,255\n",x,y,p[0],p[1],p[2],p[3],r,g,b);exit(1);
   }
}
static CGLError import(struct surface s,CGLContextObj c,int w,int h,GLenum target,GLenum format,unsigned plane)
{
   return CGLTexImageIOSurface2D(c,target,GL_RGBA8,w,h,format,GL_UNSIGNED_INT_8_8_8_8_REV,s.io,plane);
}
static GLuint texture(struct surface s)
{
   GLuint t;glGenTextures(1,&t);glBindTexture(GL_TEXTURE_RECTANGLE,t);
   CHECK(import(s,main_context,W,H,GL_TEXTURE_RECTANGLE,GL_BGRA,0)==0);return t;
}
static GLuint framebuffer(GLuint t)
{
   GLuint f;glGenFramebuffers(1,&f);glBindFramebuffer(GL_FRAMEBUFFER,f);
   glFramebufferTexture2D(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_RECTANGLE,t,0);
   CHECK(glCheckFramebufferStatus(GL_FRAMEBUFFER)==GL_FRAMEBUFFER_COMPLETE);glReadBuffer(GL_COLOR_ATTACHMENT0);return f;
}
static void clear(unsigned r,unsigned g,unsigned b)
{
   glClearColor(r/255.f,g/255.f,b/255.f,1);glClear(GL_COLOR_BUFFER_BIT);
}
static CGLContextObj context(CGLContextObj share)
{
   CGLContextObj c=NULL;CHECK(!CGLCreateContext(pixel_format,share,&c));CHECK(!CGLSetCurrentContext(c));
   const char *renderer=(const char *)glGetString(GL_RENDERER);CHECK(renderer);
   CHECK(require_apple ? !strstr(renderer,"zink") : strstr(renderer,"zink")!=NULL);
   return c;
}
static void import_export(void)
{
   struct surface s=create_surface();fill(s,19,73,211);GLuint t=texture(s),f=framebuffer(t);
   gl_pixel(1,2,19,73,211);gl_pixel(W-1,H-1,19,73,211);pass("padded input and channel order");
   clear(180,90,45);glEnable(GL_SCISSOR_TEST);glScissor(0,0,W,3);clear(7,23,71);glDisable(GL_SCISSOR_TEST);glFlush();
   cpu_pixel(s,1,1,7,23,71);cpu_pixel(s,W-2,H-2,180,90,45);pass("GPU export, row orientation and untouched padding");
   fill(s,31,63,127);glBindTexture(GL_TEXTURE_RECTANGLE,t);gl_pixel(3,5,31,63,127);pass("CPU update after texture rebind");
   fill(s,83,41,17);glBindFramebuffer(GL_FRAMEBUFFER,f);gl_pixel(3,5,83,41,17);pass("CPU update after framebuffer rebind");
   uint32_t seed=IOSurfaceGetSeed(s.io);glFlush();CHECK(seed==IOSurfaceGetSeed(s.io));pass("read-only flush preserves external seed");
   clear(24,48,96);glFinish();cpu_pixel(s,4,7,24,48,96);pass("glFinish publishes writes");
   clear(33,66,99);GLsync fence=glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE,0);CHECK(fence);glFlush();
   CHECK(glClientWaitSync(fence,0,5000000000ull)!=GL_WAIT_FAILED);glDeleteSync(fence);
   cpu_pixel(s,4,7,33,66,99);pass("GL fence includes external publication");
   glBindTexture(GL_TEXTURE_RECTANGLE,t);const unsigned char upload[]={201,67,29,255};
   glTexSubImage2D(GL_TEXTURE_RECTANGLE,0,4,7,1,1,GL_RGBA,GL_UNSIGNED_BYTE,upload);glFlush();
   cpu_pixel(s,4,7,201,67,29);cpu_pixel(s,5,7,33,66,99);pass("partial texture upload publishes without damaging other pixels");
   glBindFramebuffer(GL_FRAMEBUFFER,0);glDeleteFramebuffers(1,&f);glDeleteTextures(1,&t);glFlush();CFRelease(s.io);
}
static void lifetime(void)
{
   for(unsigned i=0;i<32;++i){
      struct surface s=create_surface();fill(s,1,2,3);GLuint t=texture(s),f=framebuffer(t);
      clear(10+i,20+i,30+i);
      /* Avalonia removes its objects before the flush which publishes them. */
      glBindFramebuffer(GL_FRAMEBUFFER,0);glDeleteFramebuffers(1,&f);glDeleteTextures(1,&t);glFlush();
      cpu_pixel(s,2,3,10+i,20+i,30+i);CFRelease(s.io);
   }
   pass("delete-before-flush over 32 surface lifetimes");
   struct surface a=create_surface(),b=create_surface();fill(a,1,2,3);fill(b,4,5,6);
   GLuint t=texture(a),f=framebuffer(t);clear(101,102,103);
   glBindTexture(GL_TEXTURE_RECTANGLE,t);CHECK(!import(b,main_context,W,H,GL_TEXTURE_RECTANGLE,GL_BGRA,0));
   cpu_pixel(a,1,2,101,102,103);glBindFramebuffer(GL_FRAMEBUFFER,f);gl_pixel(1,2,4,5,6);
   clear(111,112,113);glFlush();cpu_pixel(b,1,2,111,112,113);pass("same-name surface replacement flushes old image");
   glBindTexture(GL_TEXTURE_RECTANGLE,t);glTexImage2D(GL_TEXTURE_RECTANGLE,0,GL_RGBA8,W,H,0,GL_RGBA,GL_UNSIGNED_BYTE,NULL);
   glBindFramebuffer(GL_FRAMEBUFFER,f);clear(201,202,203);glFlush();cpu_pixel(b,1,2,111,112,113);
   pass("texture redefinition detaches shared storage");
   glBindFramebuffer(GL_FRAMEBUFFER,0);glDeleteFramebuffers(1,&f);glDeleteTextures(1,&t);glFlush();CFRelease(a.io);CFRelease(b.io);
}
static void sharing(void)
{
   struct surface s=create_surface();fill(s,28,56,112);GLuint t=texture(s);
   CGLContextObj other=context(main_context);GLuint f=framebuffer(t);gl_pixel(2,3,28,56,112);
   clear(54,108,216);glFlush();cpu_pixel(s,2,3,54,108,216);pass("shared-context render and publication");
   glBindFramebuffer(GL_FRAMEBUFFER,0);glDeleteFramebuffers(1,&f);
   CHECK(!CGLSetCurrentContext(main_context));
   GLuint marker;glGenTextures(1,&marker);glBindTexture(GL_TEXTURE_RECTANGLE,marker);
   CHECK(!CGLSetCurrentContext(other));glBindTexture(GL_TEXTURE_RECTANGLE,t);
   CHECK(!CGLSetCurrentContext(main_context));
   CHECK(!import(s,other,W,H,GL_TEXTURE_RECTANGLE,GL_BGRA,0));CHECK(CGLGetCurrentContext()==main_context);
   GLint bound=0;glGetIntegerv(GL_TEXTURE_BINDING_RECTANGLE,&bound);CHECK((GLuint)bound==marker);
   pass("noncurrent CGL import restores both contexts");
   CHECK(!CGLSetCurrentContext(NULL));CHECK(!import(s,other,W,H,GL_TEXTURE_RECTANGLE,GL_BGRA,0));CHECK(CGLGetCurrentContext()==NULL);
   CHECK(!CGLSetCurrentContext(main_context));pass("noncurrent import restores an empty binding");
   CGLDestroyContext(other);glDeleteTextures(1,&marker);glDeleteTextures(1,&t);glFlush();CFRelease(s.io);
}
static void invalid_parameters(void)
{
   struct surface s=create_surface();fill(s,10,20,30);GLuint t=texture(s),f=framebuffer(t);
   CHECK(import(s,main_context,0,H,GL_TEXTURE_RECTANGLE,GL_BGRA,0)!=0);
   CHECK(import(s,main_context,W+1,H,GL_TEXTURE_RECTANGLE,GL_BGRA,0)!=0);
   CHECK(import(s,main_context,W,H,GL_TEXTURE_2D,GL_BGRA,0)!=0);
   CHECK(import(s,main_context,W,H,GL_TEXTURE_RECTANGLE,GL_BGRA,1)!=0);
   CHECK(import(s,main_context,W,H,GL_TEXTURE_RECTANGLE,GL_DEPTH_COMPONENT,0)!=0);
   gl_pixel(2,3,10,20,30);GLint bound=0;glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING,&bound);CHECK((GLuint)bound==f);
   pass("invalid imports preserve texture and framebuffer state");
   glBindFramebuffer(GL_FRAMEBUFFER,0);glDeleteFramebuffers(1,&f);glDeleteTextures(1,&t);glFlush();CFRelease(s.io);
}
static GLuint shader(GLenum kind,const char *text)
{
   GLuint s=glCreateShader(kind);glShaderSource(s,1,&text,NULL);glCompileShader(s);
   GLint ok=0;glGetShaderiv(s,GL_COMPILE_STATUS,&ok);CHECK(ok);return s;
}
static void sampling(void)
{
   struct surface a=create_surface(),b=create_surface();fill(a,9,81,243);fill(b,0,0,0);
   GLuint src=texture(a),dst=texture(b),f=framebuffer(dst),vao;
   glBindTexture(GL_TEXTURE_RECTANGLE,src);glTexParameteri(GL_TEXTURE_RECTANGLE,GL_TEXTURE_MIN_FILTER,GL_NEAREST);
   glTexParameteri(GL_TEXTURE_RECTANGLE,GL_TEXTURE_MAG_FILTER,GL_NEAREST);
   GLuint vs=shader(GL_VERTEX_SHADER,"#version 150\nvoid main(){vec2 p[3]=vec2[3](vec2(-1,-1),vec2(3,-1),vec2(-1,3));gl_Position=vec4(p[gl_VertexID],0,1);}");
   GLuint fs=shader(GL_FRAGMENT_SHADER,"#version 150\nuniform sampler2DRect source;out vec4 color;void main(){color=texture(source,gl_FragCoord.xy);}");
   GLuint program=glCreateProgram();glAttachShader(program,vs);glAttachShader(program,fs);glLinkProgram(program);
   GLint linked=0;glGetProgramiv(program,GL_LINK_STATUS,&linked);CHECK(linked);glUseProgram(program);
   glUniform1i(glGetUniformLocation(program,"source"),0);glGenVertexArrays(1,&vao);glBindVertexArray(vao);
   glViewport(0,0,W,H);glDrawArrays(GL_TRIANGLES,0,3);glFlush();cpu_pixel(b,4,7,9,81,243);
   pass("shader samples IOSurface and renders into another");
   struct surface c=create_surface();fill(c,13,39,117);glBindTexture(GL_TEXTURE_RECTANGLE,src);
   CHECK(!import(c,main_context,W,H,GL_TEXTURE_RECTANGLE,GL_BGRA,0));
   glDrawArrays(GL_TRIANGLES,0,3);glFlush();cpu_pixel(b,4,7,13,39,117);pass("surface replacement invalidates sampler views");
   glUseProgram(0);glBindVertexArray(0);glDeleteVertexArrays(1,&vao);glDeleteProgram(program);glDeleteShader(vs);glDeleteShader(fs);
   glBindFramebuffer(GL_FRAMEBUFFER,0);glDeleteFramebuffers(1,&f);glDeleteTextures(1,&src);glDeleteTextures(1,&dst);glFlush();
   CFRelease(a.io);CFRelease(b.io);CFRelease(c.io);
}
static void planes(void)
{
   CFDictionaryRef empty=CFDictionaryCreate(NULL,NULL,NULL,0,&kCFTypeDictionaryKeyCallBacks,&kCFTypeDictionaryValueCallBacks);
   const void *key=kCVPixelBufferIOSurfacePropertiesKey,*value=empty;
   CFDictionaryRef props=CFDictionaryCreate(NULL,&key,&value,1,&kCFTypeDictionaryKeyCallBacks,&kCFTypeDictionaryValueCallBacks);
   CVPixelBufferRef pixel=NULL;CHECK(CVPixelBufferCreate(NULL,40,24,kCVPixelFormatType_420YpCbCr8BiPlanarFullRange,props,&pixel)==kCVReturnSuccess);
   CFRelease(props);CFRelease(empty);IOSurfaceRef s=CVPixelBufferGetIOSurface(pixel);CHECK(s && IOSurfaceGetPlaneCount(s)==2);
   CHECK(!IOSurfaceLock(s,0,NULL));
   for(unsigned plane=0;plane<2;++plane){
      size_t w=IOSurfaceGetWidthOfPlane(s,plane),h=IOSurfaceGetHeightOfPlane(s,plane),row=IOSurfaceGetBytesPerRowOfPlane(s,plane);
      unsigned char *p=IOSurfaceGetBaseAddressOfPlane(s,plane);
      for(size_t y=0;y<h;++y)for(size_t x=0;x<w;++x){p[y*row+x*(plane?2:1)]=plane?91:37;if(plane)p[y*row+2*x+1]=171;}
   }
   CHECK(!IOSurfaceUnlock(s,0,NULL));
   for(unsigned plane=0;plane<2;++plane){
      GLuint t;glGenTextures(1,&t);glBindTexture(GL_TEXTURE_RECTANGLE,t);
      size_t w=IOSurfaceGetWidthOfPlane(s,plane),h=IOSurfaceGetHeightOfPlane(s,plane);
      CHECK(!CGLTexImageIOSurface2D(main_context,GL_TEXTURE_RECTANGLE,plane?GL_RG8:GL_R8,(GLsizei)w,(GLsizei)h,
         plane?GL_RG:GL_RED,GL_UNSIGNED_BYTE,s,plane));
      GLuint f=framebuffer(t);unsigned char p[4];glReadPixels(2,3,1,1,GL_RGBA,GL_UNSIGNED_BYTE,p);
      CHECK(p[0]==(plane?91:37) && p[1]==(plane?171:0) && p[2]==0 && p[3]==255);
      clear(61,123,187);glFlush();CHECK(!IOSurfaceLock(s,kIOSurfaceLockReadOnly,NULL));
      const unsigned char *base=IOSurfaceGetBaseAddressOfPlane(s,plane);size_t row=IOSurfaceGetBytesPerRowOfPlane(s,plane);
      CHECK(base[3*row+2*(plane?2:1)]==61);if(plane)CHECK(base[3*row+5]==123);
      CHECK(!IOSurfaceUnlock(s,kIOSurfaceLockReadOnly,NULL));
      glBindFramebuffer(GL_FRAMEBUFFER,0);glDeleteFramebuffers(1,&f);glDeleteTextures(1,&t);glFlush();
   }
   CFRelease(pixel);pass("NV12 plane offsets, R8/RG8 input and GPU output");
}
static void client_state(void)
{
   struct surface s=create_surface();fill(s,23,47,95);GLuint t,pbo;
   glGenTextures(1,&t);glBindTexture(GL_TEXTURE_RECTANGLE,t);
   glTexParameteri(GL_TEXTURE_RECTANGLE,GL_TEXTURE_MIN_FILTER,GL_LINEAR);
   glGenBuffers(1,&pbo);glBindBuffer(GL_PIXEL_UNPACK_BUFFER,pbo);glBufferData(GL_PIXEL_UNPACK_BUFFER,4096,NULL,GL_STATIC_DRAW);
   glPixelStorei(GL_UNPACK_ROW_LENGTH,123);glPixelStorei(GL_UNPACK_SKIP_ROWS,7);glPixelStorei(GL_UNPACK_SKIP_PIXELS,11);
   glViewport(3,5,17,19);CHECK(!import(s,main_context,W,H,GL_TEXTURE_RECTANGLE,GL_BGRA,0));
   GLint v[4]={0};glGetIntegerv(GL_PIXEL_UNPACK_BUFFER_BINDING,v);CHECK((GLuint)v[0]==pbo);
   glGetIntegerv(GL_UNPACK_ROW_LENGTH,v);CHECK(v[0]==123);glGetIntegerv(GL_UNPACK_SKIP_ROWS,v);CHECK(v[0]==7);
   glGetIntegerv(GL_UNPACK_SKIP_PIXELS,v);CHECK(v[0]==11);glGetIntegerv(GL_VIEWPORT,v);CHECK(v[0]==3 && v[1]==5 && v[2]==17 && v[3]==19);
   glGetTexParameteriv(GL_TEXTURE_RECTANGLE,GL_TEXTURE_MIN_FILTER,v);CHECK(v[0]==GL_LINEAR);
   glBindBuffer(GL_PIXEL_UNPACK_BUFFER,0);glPixelStorei(GL_UNPACK_ROW_LENGTH,0);glPixelStorei(GL_UNPACK_SKIP_ROWS,0);glPixelStorei(GL_UNPACK_SKIP_PIXELS,0);
   glDeleteBuffers(1,&pbo);GLuint f=framebuffer(t);gl_pixel(2,3,23,47,95);
   pass("CGL import preserves viewport, texture parameters and unpack PBO state");
   glBindFramebuffer(GL_FRAMEBUFFER,0);glDeleteFramebuffers(1,&f);glDeleteTextures(1,&t);glFlush();CFRelease(s.io);
}
static void noncurrent_destroy(void)
{
   struct surface s=create_surface();fill(s,1,2,3);CGLContextObj c=context(NULL);
   GLuint t;glGenTextures(1,&t);glBindTexture(GL_TEXTURE_RECTANGLE,t);
   CHECK(!import(s,c,W,H,GL_TEXTURE_RECTANGLE,GL_BGRA,0));framebuffer(t);clear(27,81,243);
   CHECK(!CGLSetCurrentContext(main_context));CHECK(glGetString(GL_VERSION));
   void *(*current)(void)=dlsym(bridge_library,"nvk_zink_get_current");CHECK(current);
   void *previous=current();
   CGLDestroyContext(c);CHECK(CGLGetCurrentContext()==main_context);CHECK(current()==previous);
   cpu_pixel(s,2,3,27,81,243);pass("noncurrent destruction drains retained surface writes");CFRelease(s.io);
}
static void *public_proc(void *library,const char *name) { return dlsym(library,name); }
static void capabilities(void)
{
   GLint major=0,minor=0,count=0;glGetIntegerv(GL_MAJOR_VERSION,&major);glGetIntegerv(GL_MINOR_VERSION,&minor);glGetIntegerv(GL_NUM_EXTENSIONS,&count);
   CHECK(major==4 && minor==1 && count>0);
   unsigned sm=0,sn=0;CHECK(sscanf((const char *)glGetString(GL_VERSION),"%u.%u",&sm,&sn)==2);CHECK(sm==(unsigned)major && sn==(unsigned)minor);
   GLfloat f=0;GLdouble d=0;GLint64 n=0;GLboolean b=GL_FALSE;
   glGetFloatv(GL_NUM_EXTENSIONS,&f);glGetDoublev(GL_NUM_EXTENSIONS,&d);glGetInteger64v(GL_NUM_EXTENSIONS,&n);glGetBooleanv(GL_NUM_EXTENSIONS,&b);
   CHECK(f==count && d==count && n==count && b==GL_TRUE);
   CHECK(!strcmp((const char *)glGetString(GL_SHADING_LANGUAGE_VERSION),"4.10 Mesa CGL"));
   int texture_storage=0;
   for(GLint i=0;i<count;++i){
      const char *ext=(const char *)glGetStringi(GL_EXTENSIONS,(GLuint)i);CHECK(ext);
      CHECK(strcmp(ext,"GL_ARB_clear_texture") && strcmp(ext,"GL_KHR_debug") && strcmp(ext,"GL_ARB_multi_draw_indirect"));
      if(!strcmp(ext,"GL_ARB_texture_storage"))texture_storage=1;
   }
   CHECK(texture_storage);
   CHECK(glGetStringi(GL_EXTENSIONS,(GLuint)count)==NULL);CHECK(glGetError()==GL_INVALID_VALUE);
   CHECK(glGetString(GL_EXTENSIONS)==NULL);CHECK(glGetError()==GL_INVALID_ENUM);
   void *(*resolve)(const char *)=dlsym(bridge_library,"nvk_zink_get_proc_address");CHECK(resolve);
   const GLubyte *(*raw)(GLenum)=resolve("glGetString");unsigned rm=0,rn=0;
   CHECK(sscanf((const char *)raw(GL_VERSION),"%u.%u",&rm,&rn)==2 && rm*10+rn>=sm*10+sn);
   pass("CGL capability strings, typed queries, extension filtering and GL errors agree");
   const char *skia=getenv("ZINK_TEST_SKIA_LIBRARY");if(!skia)return;
   void *library=dlopen(skia,RTLD_NOW|RTLD_LOCAL);CHECK(library);
   void *gl=dlopen("/System/Library/Frameworks/OpenGL.framework/Versions/A/Libraries/libGL.dylib",RTLD_NOW|RTLD_LOCAL);CHECK(gl);
   void *(*assemble)(void *,void *(*)(void *,const char *))=dlsym(library,"gr_glinterface_assemble_gl_interface");
   _Bool (*validate)(void *)=dlsym(library,"gr_glinterface_validate");void (*unref)(void *)=dlsym(library,"gr_glinterface_unref");
   void *(*create)(void *)=dlsym(library,"gr_direct_context_make_gl");void (*release)(void *)=dlsym(library,"gr_recording_context_unref");
   CHECK(assemble && validate && unref && create && release);void *interface=assemble(gl,public_proc);CHECK(interface && validate(interface));
   void *gpu=create(interface);CHECK(gpu);release(gpu);unref(interface);pass("Skia validates framework APIs and creates a GPU context");
}
int main(int argc,char **argv)
{
   CHECK(argc>=2);setvbuf(stdout,NULL,_IOLBF,0);bridge_library=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL);CHECK(bridge_library);
   int core32=0;for(int i=2;i<argc;++i){if(!strcmp(argv[i],"--apple"))require_apple=1;if(!strcmp(argv[i],"--core32"))core32=1;}
   CGLPixelFormatAttribute attrs[]={kCGLPFAOpenGLProfile,(CGLPixelFormatAttribute)(core32?kCGLOGLPVersion_GL3_Core:kCGLOGLPVersion_GL4_Core),0};
   GLint count=0;CHECK(!CGLChoosePixelFormat(attrs,&pixel_format,&count));main_context=context(NULL);
   printf("Renderer: %s\n",glGetString(GL_RENDERER));
   if(!require_apple)capabilities();
   import_export();lifetime();sharing();invalid_parameters();sampling();planes();client_state();noncurrent_destroy();
   CHECK(!CGLSetCurrentContext(NULL));CGLDestroyContext(main_context);CGLDestroyPixelFormat(pixel_format);
   printf("PASS: %u IOSurface interop cases\n",passed);return 0;
}
