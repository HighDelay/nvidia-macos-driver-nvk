/* SPDX-License-Identifier: MIT */
/* Exercise Minecraft-style transient PBO uploads and failed Gallium maps. */
#include "nvk_zink.h"
#include <OpenGL/gl3.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef GL_MAP_PERSISTENT_BIT
#define GL_MAP_PERSISTENT_BIT 0x0040
#define GL_MAP_COHERENT_BIT 0x0080
#define GL_CLIENT_MAPPED_BUFFER_BARRIER_BIT 0x00004000
#endif

static void require(int ok, const char *what)
{
   if (!ok) { fprintf(stderr,"FAIL: %s\n",what); exit(1); }
}
#define GLPROC(ret, name, args) ret (*name) args = (void *)get_proc(#name); require(name != NULL,#name)

int main(int argc, char **argv)
{
   require(argc == 4,"usage: buffer-upload-probe libnvk_zink.dylib test-vulkan.dylib upload|full-bar|map-failure|read-failure|persistent|persistent-coherent|full-bar-persistent");
   setvbuf(stdout,NULL,_IOLBF,0);
   void *test = dlopen(argv[2],RTLD_NOW | RTLD_LOCAL);
   require(test != NULL,"load process-local fault injector");
   void (*arm)(unsigned) = (void *)dlsym(test,"zinkMemoryTestArm");
   void (*stats)(unsigned[3]) = (void *)dlsym(test,"zinkMemoryTestStats");
   require(arm && stats,"fault injector API");
   void *library = dlopen(argv[1],RTLD_NOW | RTLD_LOCAL);
   require(library != NULL,"load Zink runtime");
   struct nvk_zink_context *(*create)(unsigned,unsigned) = (void *)dlsym(library,"nvk_zink_create");
   int (*make_current)(struct nvk_zink_context *) = (void *)dlsym(library,"nvk_zink_make_current");
   void (*destroy)(struct nvk_zink_context *) = (void *)dlsym(library,"nvk_zink_destroy");
   nvk_zink_proc (*get_proc)(const char *) = (void *)dlsym(library,"nvk_zink_get_proc_address");
   require(create && make_current && destroy && get_proc,"Zink API");
   struct nvk_zink_context *context = create(64,64);
   require(context && make_current(context),"native Zink context");
   GLPROC(const GLubyte *,glGetString,(GLenum));
   GLPROC(GLenum,glGetError,(void));
   GLPROC(void,glGenBuffers,(GLsizei,GLuint *));
   GLPROC(void,glBindBuffer,(GLenum,GLuint));
   GLPROC(void,glBufferData,(GLenum,GLsizeiptr,const void *,GLenum));
   GLPROC(void,glBufferStorage,(GLenum,GLsizeiptr,const void *,GLbitfield));
   GLPROC(void *,glMapBufferRange,(GLenum,GLintptr,GLsizeiptr,GLbitfield));
   GLPROC(void,glFlushMappedBufferRange,(GLenum,GLintptr,GLsizeiptr));
   GLPROC(void,glMemoryBarrier,(GLbitfield));
   GLPROC(GLboolean,glUnmapBuffer,(GLenum));
   GLPROC(void,glDeleteBuffers,(GLsizei,const GLuint *));
   GLPROC(void,glGenTextures,(GLsizei,GLuint *));
   GLPROC(void,glBindTexture,(GLenum,GLuint));
   GLPROC(void,glTexImage2D,(GLenum,GLint,GLint,GLsizei,GLsizei,GLint,GLenum,GLenum,const void *));
   GLPROC(void,glTexSubImage2D,(GLenum,GLint,GLint,GLint,GLsizei,GLsizei,GLenum,GLenum,const void *));
   GLPROC(void,glGetTexImage,(GLenum,GLint,GLenum,GLenum,void *));
   GLPROC(void,glDeleteTextures,(GLsizei,const GLuint *));
   GLPROC(void,glFinish,(void));
   printf("GL_RENDERER=%s\n",glGetString(GL_RENDERER));
   require(!glGetError(),"context has no GL error");

   /* 8 KiB bypasses both the <4 KiB CPU shadow and >=8196-byte staging
    * policies. Its direct map can pin a whole 2 MiB BAR slab. */
   int small = strstr(argv[3],"small-") != NULL;
   const size_t buffer_bytes = small ? 8192 : 32u << 20;
   const size_t map_bytes = small ? 8192 : 16u << 20;
   const GLenum usage = strstr(argv[3],"small-static") ? GL_STATIC_DRAW :
                        strstr(argv[3],"small-dynamic") ? GL_DYNAMIC_DRAW : GL_STREAM_DRAW;
   int persistent = strstr(argv[3],"persistent") != NULL;
   int coherent = !strcmp(argv[3],"persistent-coherent");
   int full_bar = !strncmp(argv[3],"full-bar",8);
   int hardware = !strncmp(argv[3],"hardware",8);
   /* PBO creation always requests PIPE_USAGE_STAGING in Mesa. Use ARRAY_BUFFER
    * for the mutable cases, then consume the same storage as a PBO below. */
   GLenum allocation_target = small ? GL_ARRAY_BUFFER : GL_PIXEL_UNPACK_BUFFER;
   GLuint pbo; glGenBuffers(1,&pbo); glBindBuffer(allocation_target,pbo);
   if (persistent)
      glBufferStorage(GL_PIXEL_UNPACK_BUFFER,buffer_bytes,NULL,
                      GL_MAP_WRITE_BIT | GL_MAP_PERSISTENT_BIT | (coherent ? GL_MAP_COHERENT_BIT : 0));
   else
      glBufferData(allocation_target,buffer_bytes,NULL,usage);
   glBindBuffer(GL_PIXEL_UNPACK_BUFFER,pbo);
   require(!glGetError(),"allocate transient PBO");
   int failure = !strcmp(argv[3],"map-failure") || !strcmp(argv[3],"read-failure");
   const GLbitfield map_flags = !strcmp(argv[3],"read-failure") ? GL_MAP_READ_BIT :
      persistent ? GL_MAP_WRITE_BIT | GL_MAP_PERSISTENT_BIT |
                   (coherent ? GL_MAP_COHERENT_BIT : GL_MAP_FLUSH_EXPLICIT_BIT) :
                   GL_MAP_WRITE_BIT | GL_MAP_UNSYNCHRONIZED_BIT;
   arm(failure ? 2 : full_bar || hardware ? 3 : 1);
   unsigned char *mapping = glMapBufferRange(GL_PIXEL_UNPACK_BUFFER,0,map_bytes,map_flags);
   unsigned observed[3]; stats(observed);
   if (failure) {
      require(!mapping,"injected failed map must return NULL");
      require(glGetError() == GL_OUT_OF_MEMORY,"failed map must report GL_OUT_OF_MEMORY");
      require(observed[0] > 0,"fault must reach a real Vulkan map");
      printf("PASS: %s survived %u injected map failures and returned GL_OUT_OF_MEMORY\n",argv[3],observed[0]);
      arm(0);
      mapping = glMapBufferRange(GL_PIXEL_UNPACK_BUFFER,0,map_bytes,
                                GL_MAP_WRITE_BIT | GL_MAP_UNSYNCHRONIZED_BIT);
   }
   require(mapping != NULL,"map upload after BAR restriction/failure");
   memset(mapping,0,map_bytes);
   const unsigned side = small ? 8 : 256, face_bytes = side * side * 4;
   for (unsigned face = 0; face < 6; ++face) {
      unsigned char *pixels = mapping + face * face_bytes;
      for (unsigned p = 0; p < side * side; ++p) {
         pixels[p*4] = 24 + face*32;
         pixels[p*4+1] = p % side;
         pixels[p*4+2] = p / side;
         pixels[p*4+3] = 255;
      }
   }
   if (persistent) {
      if (!coherent) glFlushMappedBufferRange(GL_PIXEL_UNPACK_BUFFER,0,map_bytes);
      glMemoryBarrier(GL_CLIENT_MAPPED_BUFFER_BARRIER_BIT);
   } else {
      require(glUnmapBuffer(GL_PIXEL_UNPACK_BUFFER),"unmap upload");
   }
   glBindBuffer(GL_PIXEL_UNPACK_BUFFER,0);
   GLuint cube; glGenTextures(1,&cube); glBindTexture(GL_TEXTURE_CUBE_MAP,cube);
   unsigned char *pixels = calloc(1,face_bytes); require(pixels != NULL,"readback buffer");
   for (unsigned face = 0; face < 6; ++face) {
      GLenum target = GL_TEXTURE_CUBE_MAP_POSITIVE_X + face;
      glTexImage2D(target,0,GL_RGBA8,side,side,0,GL_RGBA,GL_UNSIGNED_BYTE,NULL);
      glBindBuffer(GL_PIXEL_UNPACK_BUFFER,pbo);
      glTexSubImage2D(target,0,0,0,side,side,GL_RGBA,GL_UNSIGNED_BYTE,(void *)(size_t)(face*face_bytes));
      glBindBuffer(GL_PIXEL_UNPACK_BUFFER,0);
      glGetTexImage(target,0,GL_RGBA,GL_UNSIGNED_BYTE,pixels);
      require(!glGetError(),"GPU cubemap upload/readback");
      for (unsigned p = 0; p < side * side; ++p)
         require(pixels[p*4] == 24 + face*32 && pixels[p*4+1] == p%side &&
                 pixels[p*4+2] == p/side && pixels[p*4+3] == 255,"cubemap pixels");
   }
   glFinish();
   if (!failure) {
      stats(observed);
      require(!observed[0],"uploads must avoid unavailable large BAR mappings");
      if (hardware) {
         printf("PASS: actual memory properties; %u BAR maps, %u system maps; six cubemap faces verified\n",observed[1],observed[2]);
      } else if (full_bar) {
         require(observed[1] > 0,"full-BAR uploads must retain their device-local mapping preference");
         printf("PASS: full BAR; %u BAR maps; %zu-byte upload; six cubemap faces verified\n",observed[1],map_bytes);
      } else {
         require(observed[2] > 0,"uploads must exercise system-memory mappings");
         printf("PASS: simulated 256 MiB BAR; %u system maps; %zu-byte upload; six cubemap faces verified\n",observed[2],map_bytes);
      }
   } else {
      puts("PASS: mapping recovery and six GPU-uploaded cubemap faces verified");
   }
   if (persistent) {
      glBindBuffer(GL_PIXEL_UNPACK_BUFFER,pbo);
      require(glUnmapBuffer(GL_PIXEL_UNPACK_BUFFER),"unmap persistent upload after GPU completion");
      glBindBuffer(GL_PIXEL_UNPACK_BUFFER,0);
      puts("PASS: persistent mapping remained live through GPU cubemap uploads");
   }
   arm(0); free(pixels); glDeleteTextures(1,&cube); glDeleteBuffers(1,&pbo);
   require(!glGetError(),"GL cleanup");
   require(make_current(NULL),"release context"); destroy(context);
   dlclose(library); dlclose(test);
   return 0;
}
