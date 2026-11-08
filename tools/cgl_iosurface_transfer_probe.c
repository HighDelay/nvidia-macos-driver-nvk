/* SPDX-License-Identifier: MIT
 * Exercise the half-float CAOpenGLLayer output used by Stremio, including
 * HDR values, publication fences, row padding and frame-to-frame changes.
 * Use the process-local GLD lookup shim; no system renderer policy changes.
 */
#include <OpenGL/OpenGL.h>
#include <OpenGL/CGLIOSurface.h>
#include <OpenGL/gl3.h>
#include <IOSurface/IOSurface.h>
#include <CoreVideo/CoreVideo.h>
#include <CoreFoundation/CoreFoundation.h>
#include <dlfcn.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define CHECK(x) do { if (!(x)) { fprintf(stderr,"FAIL line %d: %s (GL %x)\n",__LINE__,#x,glGetError()); exit(1); } } while (0)
static double seconds(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return t.tv_sec+t.tv_nsec*1e-9; }
static GLuint shader(GLenum kind,const char *source)
{
   GLuint s=glCreateShader(kind); glShaderSource(s,1,&source,NULL); glCompileShader(s);
   GLint ok=0; glGetShaderiv(s,GL_COMPILE_STATUS,&ok); CHECK(ok); return s;
}
static void pixel(const unsigned char *base,size_t row,unsigned x,unsigned y,int half,const float expected[4])
{
   const unsigned char *p=base+y*row+x*(half?8:4);
   for(unsigned c=0;c<4;++c) {
      float value;
      if(half) { _Float16 component; memcpy(&component,p+2*c,2); value=(float)component; }
      else value=p[c==0?2:c==2?0:c]/255.f;
      if(!isfinite(value) || fabsf(value-expected[c])>0.005f) {
         fprintf(stderr,"pixel %u,%u component %u: %g expected %g\n",x,y,c,value,expected[c]); exit(1);
      }
   }
}
static int compare(const void *a,const void *b) { double x=*(const double *)a,y=*(const double *)b; return (x>y)-(x<y); }
static void transfer(CGLContextObj ctx,unsigned w,unsigned h,unsigned frames,int half,int padded,int apple)
{
   const char *renderer=(const char *)glGetString(GL_RENDERER); CHECK(renderer);
   CHECK(apple ? strstr(renderer,"zink")==NULL : strstr(renderer,"zink")!=NULL);
   unsigned bytes_per_pixel=half?8:4;
   size_t row=IOSurfaceAlignProperty(kIOSurfaceBytesPerRow,w*bytes_per_pixel+(padded?64:0));
   size_t size=IOSurfaceAlignProperty(kIOSurfaceAllocSize,row*h);
   const void *keys[]={kIOSurfaceWidth,kIOSurfaceHeight,kIOSurfaceBytesPerElement,
      kIOSurfaceBytesPerRow,kIOSurfaceAllocSize,kIOSurfacePixelFormat};
   int values[]={(int)w,(int)h,(int)bytes_per_pixel,(int)row,(int)size,
      half?kCVPixelFormatType_64RGBAHalf:kCVPixelFormatType_32BGRA};
   CFNumberRef numbers[6];
   for(unsigned i=0;i<6;++i) numbers[i]=CFNumberCreate(NULL,kCFNumberIntType,&values[i]);
   CFDictionaryRef properties=CFDictionaryCreate(NULL,keys,(const void **)numbers,6,
      &kCFTypeDictionaryKeyCallBacks,&kCFTypeDictionaryValueCallBacks);
   IOSurfaceRef io=IOSurfaceCreate(properties); CHECK(io); CFRelease(properties);
   for(unsigned i=0;i<6;++i) CFRelease(numbers[i]);
   CHECK(!IOSurfaceLock(io,0,NULL)); memset(IOSurfaceGetBaseAddress(io),0xcd,size); CHECK(!IOSurfaceUnlock(io,0,NULL));
   GLuint texture,fbo,vao; glGenTextures(1,&texture); glBindTexture(GL_TEXTURE_RECTANGLE,texture);
   double import_start=seconds();
   CHECK(!CGLTexImageIOSurface2D(ctx,GL_TEXTURE_RECTANGLE,half?GL_RGBA16F:GL_RGBA8,w,h,
      half?GL_RGBA:GL_BGRA,half?GL_HALF_FLOAT:GL_UNSIGNED_INT_8_8_8_8_REV,io,0));
   double import_ms=(seconds()-import_start)*1000;
   glGenFramebuffers(1,&fbo); glBindFramebuffer(GL_FRAMEBUFFER,fbo);
   glFramebufferTexture2D(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_RECTANGLE,texture,0);
   CHECK(glCheckFramebufferStatus(GL_FRAMEBUFFER)==GL_FRAMEBUFFER_COMPLETE);
   GLuint vs=shader(GL_VERTEX_SHADER,"#version 150\nvoid main(){vec2 p[3]=vec2[3](vec2(-1,-1),vec2(3,-1),vec2(-1,3));gl_Position=vec4(p[gl_VertexID],0,1);}");
   GLuint fs=shader(GL_FRAGMENT_SHADER,"#version 150\nuniform vec4 value;out vec4 color;void main(){color=value;}");
   GLuint program=glCreateProgram(); glAttachShader(program,vs); glAttachShader(program,fs); glLinkProgram(program);
   GLint linked=0; glGetProgramiv(program,GL_LINK_STATUS,&linked); CHECK(linked); glUseProgram(program);
   GLint uniform=glGetUniformLocation(program,"value"); CHECK(uniform>=0);
   glGenVertexArrays(1,&vao); glBindVertexArray(vao); glViewport(0,0,w,h);
   double *times=calloc(frames,sizeof(*times)); CHECK(times); double sum=0,maximum=0,publish=0;
   uint32_t seed=IOSurfaceGetSeed(io);
   for(unsigned frame=0;frame<frames+4;++frame) {
      const float color[]={half?(frame&1?3.f:2.f):(frame&1?.25f:.75f),.5f,.25f,1.f};
      const float lower[]={.25f,1.f,half?2.f:.5f,1.f};
      glUniform4fv(uniform,1,color); glDrawArrays(GL_TRIANGLES,0,3);
      glEnable(GL_SCISSOR_TEST); glScissor(0,0,w,3); glUniform4fv(uniform,1,lower); glDrawArrays(GL_TRIANGLES,0,3); glDisable(GL_SCISSOR_TEST);
      double start=seconds(); glFlush(); double elapsed=(seconds()-start)*1000;
      CHECK(glGetError()==GL_NO_ERROR);
      if(!apple) CHECK(seed!=IOSurfaceGetSeed(io)); seed=IOSurfaceGetSeed(io);
      CHECK(!IOSurfaceLock(io,kIOSurfaceLockReadOnly,NULL)); const unsigned char *base=IOSurfaceGetBaseAddress(io); CHECK(base);
      double ready=(seconds()-start)*1000;
      pixel(base,row,1,1,half,lower); pixel(base,row,w/2,h/2,half,color); pixel(base,row,w-2,h-2,half,color);
      if(!apple && frame==frames+3)
         for(unsigned y=0;y<h;++y) for(size_t x=w*bytes_per_pixel;x<row;++x) CHECK(base[y*row+x]==0xcd);
      CHECK(!IOSurfaceUnlock(io,kIOSurfaceLockReadOnly,NULL));
      if(frame>=4) { times[frame-4]=elapsed; sum+=elapsed; publish+=ready; if(elapsed>maximum) maximum=elapsed; }
   }
   qsort(times,frames,sizeof(*times),compare);
   printf("PASS renderer=%s format=%s size=%ux%u row=%zu frames=%u import_ms=%.3f flush_mean_ms=%.3f flush_p50_ms=%.3f flush_p95_ms=%.3f flush_max_ms=%.3f publish_mean_ms=%.3f HDR_pixels_and_padding=correct\n",
      renderer,half?"RGBA16F":"BGRA8",w,h,row,frames,import_ms,sum/frames,times[frames/2],times[(frames*95)/100],maximum,publish/frames);
   free(times); glUseProgram(0); glDeleteProgram(program); glDeleteShader(vs); glDeleteShader(fs);
   glBindVertexArray(0); glDeleteVertexArrays(1,&vao); glBindFramebuffer(GL_FRAMEBUFFER,0);
   glDeleteFramebuffers(1,&fbo); glDeleteTextures(1,&texture); glFinish(); CFRelease(io);
}
int main(int argc,char **argv)
{
   CHECK(argc>=5); setvbuf(stdout,NULL,_IOLBF,0);
   CHECK(dlopen(argv[1],RTLD_NOW|RTLD_LOCAL));
   unsigned w=(unsigned)atoi(argv[2]),h=(unsigned)atoi(argv[3]),frames=(unsigned)atoi(argv[4]);
   CHECK(w>=8 && w<=4096 && h>=8 && h<=2160 && frames>=8 && frames<=10000);
   int half=0,padded=0,apple=0,core32=0,resize=0;
   for(int i=5;i<argc;++i) {
      if(!strcmp(argv[i],"--half")) half=1;
      else if(!strcmp(argv[i],"--padded")) padded=1;
      else if(!strcmp(argv[i],"--apple")) apple=1;
      else if(!strcmp(argv[i],"--core32")) core32=1;
      else if(!strcmp(argv[i],"--resize")) resize=1;
      else CHECK(0);
   }
   CGLPixelFormatAttribute attrs[]={kCGLPFAOpenGLProfile,
      (CGLPixelFormatAttribute)(core32?kCGLOGLPVersion_GL3_Core:kCGLOGLPVersion_GL4_Core),0};
   CGLPixelFormatObj pf=NULL; CGLContextObj ctx=NULL; GLint count=0;
   CHECK(!CGLChoosePixelFormat(attrs,&pf,&count)); CHECK(!CGLCreateContext(pf,NULL,&ctx)); CHECK(!CGLSetCurrentContext(ctx));
   if(resize) transfer(ctx,37,23,8,half,1,apple);
   transfer(ctx,w,h,frames,half,padded,apple);
   if(resize) transfer(ctx,257,135,8,half,1,apple);
   CHECK(!CGLSetCurrentContext(NULL)); CGLDestroyContext(ctx); CGLDestroyPixelFormat(pf); return 0;
}
