/* SPDX-License-Identifier: MIT
 * Exercise public framework glTexStorage entrypoints through an isolated CGL
 * adapter. Does not install a GLD or change the application's default renderer.
 */
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl3.h>
#include <OpenGL/gl3ext.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define CHECK(v) do {if(!(v)){fprintf(stderr,"FAIL %d: %s\n",__LINE__,#v);return 1;}}while(0)
int main(int argc,char **argv) {
    CHECK(argc==2);setenv("NVKZINK_DISABLE","1",1);
    void *library=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL);CHECK(library);
    int(*attach)(CGLContextObj,unsigned,unsigned)=(void *)dlsym(library,"nvk_cgl_attach");
    int(*detach)(CGLContextObj)=(void *)dlsym(library,"nvk_cgl_detach");CHECK(attach&&detach);
    CGLPixelFormatAttribute attrs[]={kCGLPFAOpenGLProfile,(CGLPixelFormatAttribute)kCGLOGLPVersion_3_2_Core,(CGLPixelFormatAttribute)0};
    CGLPixelFormatObj format=NULL;GLint count=0;CGLContextObj context=NULL;
    CHECK(CGLChoosePixelFormat(attrs,&format,&count)==kCGLNoError&&format&&count);
    CHECK(CGLCreateContext(format,NULL,&context)==kCGLNoError&&context);CGLReleasePixelFormat(format);
    CHECK(CGLSetCurrentContext(context)==kCGLNoError&&attach(context,32,32));
    printf("renderer=%s\n",glGetString(GL_RENDERER));CHECK(strstr((const char *)glGetString(GL_RENDERER),"zink"));
    GLint extensions=0;glGetIntegerv(GL_NUM_EXTENSIONS,&extensions);int storage=0;
    for(GLint i=0;i<extensions;i++)storage|=!strcmp((const char *)glGetStringi(GL_EXTENSIONS,(GLuint)i),"GL_ARB_texture_storage");
    CHECK(storage);
    const GLenum targets[]={GL_TEXTURE_2D,GL_TEXTURE_3D,GL_TEXTURE_2D_ARRAY};
    for(unsigned i=0;i<3;i++){
        GLuint texture;glGenTextures(1,&texture);glBindTexture(targets[i],texture);
        if(!i)glTexStorage2D(targets[i],3,GL_RGBA8,4,4);else glTexStorage3D(targets[i],3,GL_RGBA8,4,4,4);
        CHECK(glGetError()==GL_NO_ERROR);GLint immutable=0;glGetTexParameteriv(targets[i],GL_TEXTURE_IMMUTABLE_FORMAT,&immutable);CHECK(immutable);
        for(unsigned level=0;level<3;level++){
            unsigned side=4>>level,depth=!i?1:i==1?side:4;unsigned char pixels[4*4*4*4],readback[sizeof pixels];size_t bytes=side*side*depth*4;
            for(size_t p=0;p<bytes;p++)pixels[p]=(unsigned char)(p*7+level*43+i*17);
            if(!i)glTexSubImage2D(targets[i],level,0,0,side,side,GL_RGBA,GL_UNSIGNED_BYTE,pixels);
            else glTexSubImage3D(targets[i],level,0,0,0,side,side,depth,GL_RGBA,GL_UNSIGNED_BYTE,pixels);
            glGetTexImage(targets[i],level,GL_RGBA,GL_UNSIGNED_BYTE,readback);CHECK(glGetError()==GL_NO_ERROR&&!memcmp(pixels,readback,bytes));
        }
        if(!i){glTexImage2D(targets[i],0,GL_RGBA8,4,4,0,GL_RGBA,GL_UNSIGNED_BYTE,NULL);CHECK(glGetError()==GL_INVALID_OPERATION);}
        glDeleteTextures(1,&texture);
    }
    CHECK(detach(context));CGLSetCurrentContext(NULL);CGLReleaseContext(context);
    puts("PASS: GL_ARB_texture_storage; 2D, 3D and array immutable storage; every mip read back; redefinition rejected");
    return 0;
}
