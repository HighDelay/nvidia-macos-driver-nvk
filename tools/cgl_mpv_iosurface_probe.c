/* SPDX-License-Identifier: MIT */
#define main synthetic_concurrency_main
#include "cgl_iosurface_concurrency_probe.c"
#undef main
#include <mpv/client.h>
#include <mpv/render_gl.h>

#define MPV_FUNCTIONS(X) \
    X(mpv_create) X(mpv_initialize) X(mpv_set_option_string) X(mpv_command_async) \
    X(mpv_get_property_string) X(mpv_free) X(mpv_terminate_destroy) \
    X(mpv_render_context_create) X(mpv_render_context_update) X(mpv_render_context_render) \
    X(mpv_render_context_free) X(mpv_render_context_set_update_callback) X(mpv_render_context_report_swap)
#define DECLARE(name) static __typeof__(&name) p_##name;
MPV_FUNCTIONS(DECLARE)
#undef DECLARE
static mpv_handle *mpv;
static mpv_render_context *render;
static pthread_mutex_t update_mutex=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t update_cond=PTHREAD_COND_INITIALIZER;
static int updated;
static void update(void *unused)
{
    (void)unused;pthread_mutex_lock(&update_mutex);updated=1;
    pthread_cond_broadcast(&update_cond);pthread_mutex_unlock(&update_mutex);
}
static void *resolve(void *gl,const char *name) { return dlsym(gl,name); }
struct playback {
    struct stream stream;
    GLuint producer_fbos[2];
    unsigned char reference[2][4][4];
    double render_seconds,publish_seconds,max_render,max_publish;
};
static const unsigned sample_x[]={1,W/3,W/2,W-2},sample_y[]={2,H/4,H/2,H-3};
static void *play(void *data)
{
    struct playback *p=data;struct stream *s=&p->stream;CHECK(!CGLSetCurrentContext(s->producer));CHECK(glGetString(GL_RENDERER));
    double deadline=now()+60;
    for(unsigned frame=0;frame<FRAMES;) {
        CHECK(now()<deadline);
        pthread_mutex_lock(&update_mutex);
        if(!updated) {
            struct timespec ts;clock_gettime(CLOCK_REALTIME,&ts);ts.tv_nsec+=50000000;
            if(ts.tv_nsec>=1000000000){++ts.tv_sec;ts.tv_nsec-=1000000000;}
            pthread_cond_timedwait(&update_cond,&update_mutex,&ts);
        }
        updated=0;pthread_mutex_unlock(&update_mutex);
        if(!(p_mpv_render_context_update(render)&MPV_RENDER_UPDATE_FRAME))continue;
        pthread_mutex_lock(&s->mutex);while(frame>=s->consumed+2)pthread_cond_wait(&s->cond,&s->mutex);pthread_mutex_unlock(&s->mutex);
        mpv_opengl_fbo target={(int)p->producer_fbos[frame%2],W,H,GL_RGBA16F};int flip=1,block=0,depth=16;
        mpv_render_param params[]={{MPV_RENDER_PARAM_OPENGL_FBO,&target},{MPV_RENDER_PARAM_FLIP_Y,&flip},
            {MPV_RENDER_PARAM_DEPTH,&depth},{MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME,&block},{0,NULL}};
        double started=now();CHECK(p_mpv_render_context_render(render,params)>=0);glFinish();
        double duration=now()-started;p->render_seconds+=duration;if(duration>p->max_render)p->max_render=duration;
        glBindFramebuffer(GL_FRAMEBUFFER,p->producer_fbos[frame%2]);glReadBuffer(GL_COLOR_ATTACHMENT0);
        for(unsigned i=0;i<4;++i)glReadPixels(sample_x[i],sample_y[i],1,1,GL_RGBA,GL_UNSIGNED_BYTE,p->reference[frame%2][i]);
        CHECK(glGetError()==GL_NO_ERROR);p_mpv_render_context_report_swap(render);
        pthread_mutex_lock(&s->mutex);s->produced=++frame;pthread_cond_broadcast(&s->cond);pthread_mutex_unlock(&s->mutex);
    }
    CHECK(!CGLSetCurrentContext(NULL));return NULL;
}
int main(int argc,char **argv)
{
    CHECK(argc>=4);setvbuf(stdout,NULL,_IOLBF,0);library=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL);CHECK(library);
    apple=argc>4 && !strcmp(argv[4],"--apple");void *mpv_library=dlopen(argv[2],RTLD_NOW|RTLD_LOCAL);
    if(!mpv_library){fprintf(stderr,"%s\n",dlerror());return 1;}
#define LOAD(name) p_##name=dlsym(mpv_library,#name);CHECK(p_##name);
    MPV_FUNCTIONS(LOAD)
#undef LOAD
    void *gl=dlopen("/System/Library/Frameworks/OpenGL.framework/Versions/A/Libraries/libGL.dylib",RTLD_NOW|RTLD_LOCAL);CHECK(gl);
    CGLPixelFormatAttribute attrs[]={kCGLPFAOpenGLProfile,(CGLPixelFormatAttribute)kCGLOGLPVersion_GL4_Core,0};GLint count;
    CHECK(!CGLChoosePixelFormat(attrs,&format,&count));
    struct playback p={.stream={.mutex=PTHREAD_MUTEX_INITIALIZER,.cond=PTHREAD_COND_INITIALIZER}};
    struct stream *s=&p.stream;s->producer=context(NULL);s->consumer=context(s->producer);CHECK(!CGLSetCurrentContext(s->producer));
    printf("Renderer: %s\n",glGetString(GL_RENDERER));
    if(apple)printf("Apple reference publication includes glFinish before CPU IOSurface validation\n");
    mpv=p_mpv_create();CHECK(mpv);
    const char *options[][2]={{"config","no"},{"vo","libmpv"},{"ao","null"},{"audio","no"},{"hwdec","no"},
        {"load-scripts","no"},{"ytdl","no"},{"input-default-bindings","no"},{"loop-file","inf"},{"terminal","yes"},{"msg-level","all=warn"}};
    for(unsigned i=0;i<sizeof(options)/sizeof(options[0]);++i)CHECK(p_mpv_set_option_string(mpv,options[i][0],options[i][1])>=0);
    CHECK(p_mpv_initialize(mpv)>=0);char *version=p_mpv_get_property_string(mpv,"mpv-version");CHECK(version);printf("libmpv: %s\n",version);p_mpv_free(version);
    mpv_opengl_init_params init={resolve,gl};int advanced=1;
    mpv_render_param create[]={{MPV_RENDER_PARAM_API_TYPE,MPV_RENDER_API_TYPE_OPENGL},{MPV_RENDER_PARAM_OPENGL_INIT_PARAMS,&init},
        {MPV_RENDER_PARAM_ADVANCED_CONTROL,&advanced},{0,NULL}};
    CHECK(p_mpv_render_context_create(&render,mpv,create)>=0);
    p_mpv_render_context_set_update_callback(render,update,NULL);
    glGenTextures(2,s->frames);
    for(unsigned i=0;i<2;++i){glBindTexture(GL_TEXTURE_2D,s->frames[i]);glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA16F,W,H,0,GL_RGBA,GL_HALF_FLOAT,NULL);p.producer_fbos[i]=fbo(s->frames[i],GL_TEXTURE_2D);}glFinish();
    CHECK(!CGLSetCurrentContext(s->consumer));s->output=surface();GLuint t=texture(s->output,s->consumer);s->destination=fbo(t,GL_TEXTURE_RECTANGLE);GLuint input[2];
    for(unsigned i=0;i<2;++i)input[i]=fbo(s->frames[i],GL_TEXTURE_2D);glFinish();CHECK(!CGLSetCurrentContext(NULL));
    const char *command[]={"loadfile",argv[3],NULL};CHECK(p_mpv_command_async(mpv,1,command)>=0);
    pthread_t worker;CHECK(!pthread_create(&worker,NULL,play,&p));CHECK(!CGLSetCurrentContext(s->consumer));CHECK(glGetString(GL_RENDERER));double started=now();
    unsigned changes=0;unsigned char previous[4]={0};
    for(unsigned frame=0;frame<FRAMES;++frame) {
        pthread_mutex_lock(&s->mutex);while(s->produced<=frame)pthread_cond_wait(&s->cond,&s->mutex);pthread_mutex_unlock(&s->mutex);
        double began=now();glBindFramebuffer(GL_READ_FRAMEBUFFER,input[frame%2]);glBindFramebuffer(GL_DRAW_FRAMEBUFFER,s->destination);
        glBlitFramebuffer(0,0,W,H,0,0,W,H,GL_COLOR_BUFFER_BIT,GL_NEAREST);glFlush();
        if(apple)glFinish();
        double duration=now()-began;p.publish_seconds+=duration;if(duration>p.max_publish)p.max_publish=duration;
        CHECK(!IOSurfaceLock(s->output,kIOSurfaceLockReadOnly,NULL));const unsigned char *base=IOSurfaceGetBaseAddress(s->output);size_t row=IOSurfaceGetBytesPerRow(s->output);
        for(unsigned i=0;i<4;++i) {
            const unsigned char *v=base+sample_y[i]*row+sample_x[i]*4,*r=p.reference[frame%2][i];
            CHECK(v[0]==r[2] && v[1]==r[1] && v[2]==r[0] && v[3]==r[3]);
            if(i==2){if(memcmp(previous,v,4))++changes;memcpy(previous,v,4);}
        }
        CHECK(!IOSurfaceUnlock(s->output,kIOSurfaceLockReadOnly,NULL));CHECK(glGetError()==GL_NO_ERROR);
        pthread_mutex_lock(&s->mutex);s->consumed=frame+1;pthread_cond_broadcast(&s->cond);pthread_mutex_unlock(&s->mutex);
    }
    CHECK(!pthread_join(worker,NULL));CHECK(changes>FRAMES/3);
    printf("PASS: %u real libmpv frames at %ux%u; %u changing frames; exact GPU-to-IOSurface sample pixels; wall %.3f s; mean/max render %.3f/%.3f ms; mean/max publication %.3f/%.3f ms\n",
        FRAMES,W,H,changes,now()-started,p.render_seconds*1000/FRAMES,p.max_render*1000,p.publish_seconds*1000/FRAMES,p.max_publish*1000);
    CHECK(!CGLSetCurrentContext(s->producer));p_mpv_render_context_set_update_callback(render,NULL,NULL);p_mpv_render_context_free(render);p_mpv_terminate_destroy(mpv);
    CHECK(!CGLSetCurrentContext(NULL));CGLDestroyContext(s->consumer);CGLDestroyContext(s->producer);CGLDestroyPixelFormat(format);CFRelease(s->output);return 0;
}
