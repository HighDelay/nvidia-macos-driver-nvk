/* SPDX-License-Identifier: MIT */
#include <OpenGL/OpenGL.h>
#include <OpenGL/CGLIOSurface.h>
#include <OpenGL/gl3.h>
#include <IOSurface/IOSurface.h>
#include <CoreFoundation/CoreFoundation.h>
#include <dlfcn.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define CHECK(x) do { if(!(x)) { fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x);exit(1); } } while(0)
#ifndef PROBE_WIDTH
#define PROBE_WIDTH 128
#endif
#ifndef PROBE_HEIGHT
#define PROBE_HEIGHT 72
#endif
#ifndef PROBE_FRAMES
#define PROBE_FRAMES 240
#endif
enum { W=PROBE_WIDTH,H=PROBE_HEIGHT,FRAMES=PROBE_FRAMES };
static CGLPixelFormatObj format;
static void *library;
static int apple;
static void (*arm_wait)(int),(*release_wait)(void),(*watch_surface)(uint32_t);
static int (*wait_entered)(unsigned);
static unsigned (*surface_locks)(int);
static double now(void) { struct timespec ts;clock_gettime(CLOCK_MONOTONIC,&ts);return ts.tv_sec+ts.tv_nsec*1e-9; }
struct event { pthread_mutex_t mutex;pthread_cond_t cond;int ready,done; };
#define EVENT_INIT { PTHREAD_MUTEX_INITIALIZER,PTHREAD_COND_INITIALIZER,0,0 }
static void event_set(struct event *e,int done)
{
    pthread_mutex_lock(&e->mutex);if(done)e->done=1;else e->ready=1;
    pthread_cond_broadcast(&e->cond);pthread_mutex_unlock(&e->mutex);
}
static int event_wait(struct event *e,int done,unsigned ms)
{
    struct timespec ts;clock_gettime(CLOCK_REALTIME,&ts);
    ts.tv_sec+=ms/1000;ts.tv_nsec+=(long)(ms%1000)*1000000;
    if(ts.tv_nsec>=1000000000){++ts.tv_sec;ts.tv_nsec-=1000000000;}
    pthread_mutex_lock(&e->mutex);
    while(!(done?e->done:e->ready))if(pthread_cond_timedwait(&e->cond,&e->mutex,&ts))break;
    int result=done?e->done:e->ready;pthread_mutex_unlock(&e->mutex);return result;
}
static CGLContextObj context(CGLContextObj shared)
{
    CGLContextObj c=NULL;CHECK(!CGLCreateContext(format,shared,&c));CHECK(!CGLSetCurrentContext(c));
    const char *r=(const char *)glGetString(GL_RENDERER);CHECK(r);
    CHECK(apple?!strstr(r,"zink"):strstr(r,"zink")!=NULL);return c;
}
static IOSurfaceRef surface(void)
{
    int vals[]={W,H,4,W*4+64,(W*4+64)*H,0x42475241};
    const void *keys[]={kIOSurfaceWidth,kIOSurfaceHeight,kIOSurfaceBytesPerElement,kIOSurfaceBytesPerRow,kIOSurfaceAllocSize,kIOSurfacePixelFormat};
    CFNumberRef nums[6];for(unsigned i=0;i<6;++i)nums[i]=CFNumberCreate(NULL,kCFNumberIntType,vals+i);
    CFDictionaryRef props=CFDictionaryCreate(NULL,keys,(const void **)nums,6,&kCFTypeDictionaryKeyCallBacks,&kCFTypeDictionaryValueCallBacks);
    IOSurfaceRef s=IOSurfaceCreate(props);CHECK(s);CFRelease(props);for(unsigned i=0;i<6;++i)CFRelease(nums[i]);
    CHECK(!IOSurfaceLock(s,0,NULL));memset(IOSurfaceGetBaseAddress(s),0xcd,IOSurfaceGetAllocSize(s));CHECK(!IOSurfaceUnlock(s,0,NULL));return s;
}
static void pixel(IOSurfaceRef s,unsigned r,unsigned g,unsigned b)
{
    CHECK(!IOSurfaceLock(s,kIOSurfaceLockReadOnly,NULL));
    size_t row=IOSurfaceGetBytesPerRow(s);const unsigned char *p=IOSurfaceGetBaseAddress(s);
    for(unsigned y=0;y<H;++y) {
        for(unsigned x=0;x<W;++x) {const unsigned char *v=p+y*row+x*4;CHECK(v[0]==b && v[1]==g && v[2]==r && v[3]==255);}
        if(!apple)for(size_t x=W*4;x<row;++x)CHECK(p[y*row+x]==0xcd);
    }
    CHECK(!IOSurfaceUnlock(s,kIOSurfaceLockReadOnly,NULL));
}
static GLuint texture(IOSurfaceRef s,CGLContextObj c)
{
    GLuint t;glGenTextures(1,&t);glBindTexture(GL_TEXTURE_RECTANGLE,t);
    CHECK(!CGLTexImageIOSurface2D(c,GL_TEXTURE_RECTANGLE,GL_RGBA8,W,H,GL_BGRA,GL_UNSIGNED_INT_8_8_8_8_REV,s,0));return t;
}
static GLuint fbo(GLuint t,GLenum target)
{
    GLuint f;glGenFramebuffers(1,&f);glBindFramebuffer(GL_FRAMEBUFFER,f);
    glFramebufferTexture2D(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,target,t,0);
    CHECK(glCheckFramebufferStatus(GL_FRAMEBUFFER)==GL_FRAMEBUFFER_COMPLETE);glReadBuffer(GL_COLOR_ATTACHMENT0);return f;
}
static void clear(unsigned r,unsigned g,unsigned b)
{
    glClearColor(r/255.f,g/255.f,b/255.f,1);glClear(GL_COLOR_BUFFER_BIT);
}
struct task {
    CGLContextObj context;IOSurfaceRef surface;GLuint texture,framebuffer,upload;
    int lifetime,timeout,destroy;void *zink;double seconds;struct event event;
};
static void *publish(void *data)
{
    struct task *t=data;CHECK(!CGLSetCurrentContext(t->context));CHECK(glGetString(GL_RENDERER));
    glBindFramebuffer(GL_FRAMEBUFFER,t->framebuffer);GLuint query;glGenQueries(1,&query);
    glBeginQuery(GL_TIME_ELAPSED,query);clear(37,83,191);glEndQuery(GL_TIME_ELAPSED);
    if(t->lifetime){glBindFramebuffer(GL_FRAMEBUFFER,0);glDeleteFramebuffers(1,&t->framebuffer);glDeleteTextures(1,&t->texture);}
    void *(*current)(void)=dlsym(library,"nvk_zink_get_current");CHECK(current);t->zink=current();
    arm_wait(t->timeout?2:1);glFlush();
    if(!t->timeout) {
        GLuint64 result=0;glGetQueryObjectui64v(query,GL_QUERY_RESULT,&result);glDeleteQueries(1,&query);
        CHECK(glGetError()==GL_NO_ERROR);
    }
    CHECK(!CGLSetCurrentContext(NULL));event_set(&t->event,1);return NULL;
}
static void *independent(void *data)
{
    struct task *t=data;CHECK(!CGLSetCurrentContext(t->context));CHECK(glGetString(GL_RENDERER));
    event_set(&t->event,0);double start=now();
    glBindTexture(GL_TEXTURE_2D,t->upload);
    unsigned char pixels[16*16*4];memset(pixels,67,sizeof(pixels));
    glTexSubImage2D(GL_TEXTURE_2D,0,0,0,16,16,GL_RGBA,GL_UNSIGNED_BYTE,pixels);
    if(t->lifetime) {
        GLuint replacement=texture(t->surface,t->context),f=fbo(replacement,GL_TEXTURE_RECTANGLE);
        clear(53,107,211);glBindFramebuffer(GL_FRAMEBUFFER,0);glDeleteFramebuffers(1,&f);glDeleteTextures(1,&replacement);
    } else {
        glBindTexture(GL_TEXTURE_RECTANGLE,t->texture);
        glBindFramebuffer(GL_FRAMEBUFFER,t->framebuffer);clear(53,107,211);
    }
    GLsync fence=glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE,0);CHECK(fence);glFlush();
    GLenum result=glClientWaitSync(fence,GL_SYNC_FLUSH_COMMANDS_BIT,5000000000ull);
    CHECK(result==GL_ALREADY_SIGNALED || result==GL_CONDITION_SATISFIED);glDeleteSync(fence);
    CHECK(glGetError()==GL_NO_ERROR);t->seconds=now()-start;
    CHECK(!CGLSetCurrentContext(NULL));
    if(t->destroy){CGLDestroyContext(t->context);t->context=NULL;}
    event_set(&t->event,1);return NULL;
}
static void *dependent(void *data)
{
    struct task *t=data;CHECK(!CGLSetCurrentContext(t->context));CHECK(glGetString(GL_RENDERER));
    event_set(&t->event,0);glBindTexture(GL_TEXTURE_RECTANGLE,t->texture);
    glBindFramebuffer(GL_FRAMEBUFFER,t->framebuffer);unsigned char p[4];glReadPixels(4,7,1,1,GL_RGBA,GL_UNSIGNED_BYTE,p);
    CHECK(p[0]==37 && p[1]==83 && p[2]==191 && p[3]==255);CHECK(glGetError()==GL_NO_ERROR);
    CHECK(!CGLSetCurrentContext(NULL));event_set(&t->event,1);return NULL;
}
static void delayed(int lifetime,int timeout,int alias,int teardown)
{
    struct task p={.event=EVENT_INIT,.lifetime=lifetime,.timeout=timeout},u={.event=EVENT_INIT,.lifetime=lifetime,.destroy=teardown},d={.event=EVENT_INIT};
    p.context=context(NULL);u.context=context(p.context);d.context=context(p.context);
    CHECK(!CGLSetCurrentContext(p.context));p.surface=surface();p.texture=texture(p.surface,p.context);p.framebuffer=fbo(p.texture,GL_TEXTURE_RECTANGLE);glFinish();
    CHECK(!CGLSetCurrentContext(u.context));u.surface=surface();u.texture=texture(u.surface,u.context);u.framebuffer=fbo(u.texture,GL_TEXTURE_RECTANGLE);
    glGenTextures(1,&u.upload);glBindTexture(GL_TEXTURE_2D,u.upload);glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,16,16,0,GL_RGBA,GL_UNSIGNED_BYTE,NULL);glFinish();
    CHECK(!CGLSetCurrentContext(d.context));d.texture=alias?texture(p.surface,d.context):p.texture;d.framebuffer=fbo(d.texture,GL_TEXTURE_RECTANGLE);glFinish();
    if(lifetime){glDeleteFramebuffers(1,&d.framebuffer);glBindTexture(GL_TEXTURE_RECTANGLE,0);}
    CHECK(!CGLSetCurrentContext(NULL));watch_surface(IOSurfaceGetID(p.surface));
    pthread_t pt,ut,dt;CHECK(!pthread_create(&pt,NULL,publish,&p));CHECK(wait_entered(10000));
    CHECK(surface_locks(0)==1 && surface_locks(1)==0);CHECK(!event_wait(&p.event,1,1));
    int independent_progress=1,dependent_blocked=1;
    if(!timeout) {
        CHECK(!pthread_create(&ut,NULL,independent,&u));CHECK(event_wait(&u.event,0,5000));
        if(!lifetime){CHECK(!pthread_create(&dt,NULL,dependent,&d));CHECK(event_wait(&d.event,0,5000));}
        independent_progress=event_wait(&u.event,1,1000);
        if(!lifetime)dependent_blocked=!event_wait(&d.event,1,100);
    }
    release_wait();CHECK(!pthread_join(pt,NULL));
    if(timeout) {
        int (*flush)(void *)=dlsym(library,"nvk_zink_flush_iosurfaces");
        int (*make_current)(void *)=dlsym(library,"nvk_zink_make_current");CHECK(flush && make_current);
        CHECK(!flush(p.zink));CHECK(!make_current(p.zink));
        CGLDestroyContext(d.context);CGLDestroyContext(u.context);CGLDestroyContext(p.context);
        CHECK(surface_locks(0)==1 && surface_locks(1)==0);
        printf("PASS: uncertain publication retains its write lock and quarantines the screen\n");return;
    }
    CHECK(!pthread_join(ut,NULL));if(!lifetime)CHECK(!pthread_join(dt,NULL));
    pixel(p.surface,37,83,191);pixel(u.surface,53,107,211);
    CHECK(surface_locks(0)==1 && surface_locks(1)==1);
    CHECK(!CGLSetCurrentContext(p.context));glFlush();GLsync fence=glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE,0);CHECK(fence);glFlush();glDeleteSync(fence);
    CHECK(surface_locks(0)==1 && surface_locks(1)==1);
    printf("Independent upload/publication: %.3f ms; progressed during held fence: %d; dependent waited: %d\n",u.seconds*1000,independent_progress,dependent_blocked);
    CHECK(!CGLSetCurrentContext(NULL));CGLDestroyContext(d.context);if(u.context)CGLDestroyContext(u.context);CGLDestroyContext(p.context);CFRelease(p.surface);CFRelease(u.surface);
    CHECK(independent_progress && dependent_blocked);
    printf("PASS: delayed publication %s, exact pixels/padding, seeds and no duplicate export\n",
        lifetime?"with deletion and replacement":alias?"with separately imported aliases":teardown?"with unrelated context teardown":"with dependent and independent shared contexts");
}
struct stream {
    CGLContextObj producer,consumer;IOSurfaceRef output;GLuint frames[2],destination;
    pthread_mutex_t mutex;pthread_cond_t cond;unsigned produced,consumed;double seconds;
};
static void color(unsigned frame,unsigned *r,unsigned *g,unsigned *b) { *r=frame%251;*g=(frame*3+19)%251;*b=(frame*7+31)%251; }
static void *produce_frames(void *data)
{
    struct stream *s=data;CHECK(!CGLSetCurrentContext(s->producer));CHECK(glGetString(GL_RENDERER));
    GLuint query[8];glGenQueries(8,query);unsigned char *bytes=malloc(W*H*4);CHECK(bytes);double start=now();
    for(unsigned frame=0;frame<FRAMES;++frame) {
        pthread_mutex_lock(&s->mutex);while(frame>=s->consumed+2)pthread_cond_wait(&s->cond,&s->mutex);pthread_mutex_unlock(&s->mutex);
        unsigned r,g,b;color(frame,&r,&g,&b);
        for(unsigned i=0;i<W*H;++i){bytes[4*i]=r;bytes[4*i+1]=g;bytes[4*i+2]=b;bytes[4*i+3]=255;}
        glBindTexture(GL_TEXTURE_2D,s->frames[frame%2]);glTexSubImage2D(GL_TEXTURE_2D,0,0,0,W,H,GL_RGBA,GL_UNSIGNED_BYTE,bytes);
        if(frame>=8){GLuint64 ns=0;glGetQueryObjectui64v(query[frame%8],GL_QUERY_RESULT,&ns);}
        glBeginQuery(GL_TIME_ELAPSED,query[frame%8]);
        GLsync fence=glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE,0);CHECK(fence);
        glEndQuery(GL_TIME_ELAPSED);glFinish();glDeleteSync(fence);CHECK(glGetError()==GL_NO_ERROR);
        pthread_mutex_lock(&s->mutex);s->produced=frame+1;pthread_cond_broadcast(&s->cond);pthread_mutex_unlock(&s->mutex);
    }
    glDeleteQueries(8,query);free(bytes);s->seconds=now()-start;CHECK(!CGLSetCurrentContext(NULL));return NULL;
}
static void stream_test(void)
{
    struct stream s={.mutex=PTHREAD_MUTEX_INITIALIZER,.cond=PTHREAD_COND_INITIALIZER};
    s.producer=context(NULL);s.consumer=context(s.producer);CHECK(!CGLSetCurrentContext(s.producer));
    glGenTextures(2,s.frames);for(unsigned i=0;i<2;++i){glBindTexture(GL_TEXTURE_2D,s.frames[i]);glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,W,H,0,GL_RGBA,GL_UNSIGNED_BYTE,NULL);}glFinish();
    CHECK(!CGLSetCurrentContext(s.consumer));s.output=surface();GLuint t=texture(s.output,s.consumer);s.destination=fbo(t,GL_TEXTURE_RECTANGLE);GLuint input[2];
    for(unsigned i=0;i<2;++i)input[i]=fbo(s.frames[i],GL_TEXTURE_2D);glFinish();CHECK(!CGLSetCurrentContext(NULL));
    pthread_t thread;CHECK(!pthread_create(&thread,NULL,produce_frames,&s));CHECK(!CGLSetCurrentContext(s.consumer));CHECK(glGetString(GL_RENDERER));double start=now(),max_frame=0;
    for(unsigned frame=0;frame<FRAMES;++frame) {
        pthread_mutex_lock(&s.mutex);while(s.produced<=frame)pthread_cond_wait(&s.cond,&s.mutex);pthread_mutex_unlock(&s.mutex);double began=now();
        glBindFramebuffer(GL_READ_FRAMEBUFFER,input[frame%2]);glBindFramebuffer(GL_DRAW_FRAMEBUFFER,s.destination);
        glBlitFramebuffer(0,0,W,H,0,0,W,H,GL_COLOR_BUFFER_BIT,GL_NEAREST);glFlush();
        unsigned r,g,b;color(frame,&r,&g,&b);pixel(s.output,r,g,b);CHECK(glGetError()==GL_NO_ERROR);
        double duration=now()-began;if(duration>max_frame)max_frame=duration;
        pthread_mutex_lock(&s.mutex);s.consumed=frame+1;pthread_cond_broadcast(&s.cond);pthread_mutex_unlock(&s.mutex);
    }
    CHECK(!pthread_join(thread,NULL));printf("PASS: %u ordered shared-context video frames with uploads, timer queries, active-query fences and publication; wall %.3f s, producer %.3f s, maximum consumer %.3f ms\n",FRAMES,now()-start,s.seconds,max_frame*1000);
    glBindFramebuffer(GL_FRAMEBUFFER,0);glDeleteFramebuffers(2,input);glDeleteFramebuffers(1,&s.destination);glDeleteTextures(2,s.frames);glDeleteTextures(1,&t);glFlush();
    CHECK(!CGLSetCurrentContext(NULL));CGLDestroyContext(s.consumer);CGLDestroyContext(s.producer);CFRelease(s.output);
}
int main(int argc,char **argv)
{
    CHECK(argc>=3);setvbuf(stdout,NULL,_IOLBF,0);library=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL);CHECK(library);
    apple=argc>3 && !strcmp(argv[3],"--apple");
    CGLPixelFormatAttribute attrs[]={kCGLPFAOpenGLProfile,(CGLPixelFormatAttribute)kCGLOGLPVersion_GL4_Core,0};GLint count;
    CHECK(!CGLChoosePixelFormat(attrs,&format,&count));
    if(!strcmp(argv[2],"stress"))stream_test();
    else {
        arm_wait=dlsym(RTLD_DEFAULT,"zink_test_arm_wait");release_wait=dlsym(RTLD_DEFAULT,"zink_test_release_wait");wait_entered=dlsym(RTLD_DEFAULT,"zink_test_wait_entered");
        watch_surface=dlsym(RTLD_DEFAULT,"zink_test_watch_surface");surface_locks=dlsym(RTLD_DEFAULT,"zink_test_surface_locks");CHECK(arm_wait && release_wait && wait_entered && watch_surface && surface_locks);
        delayed(!strcmp(argv[2],"lifetime"),!strcmp(argv[2],"timeout"),!strcmp(argv[2],"alias"),!strcmp(argv[2],"teardown"));
    }
    CGLDestroyPixelFormat(format);return 0;
}
