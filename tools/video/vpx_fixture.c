/* SPDX-License-Identifier: MIT
 * Synthetic VP8/VP9 IVF fixtures and independent software reference frames.
 * Test dependency: libvpx. Never registers a system codec or opens a camera.
 */
#include <vpx/vp8cx.h>
#include <vpx/vp8dx.h>
#include <vpx/vpx_encoder.h>
#include <vpx/vpx_decoder.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void require(int ok,const char *message) {
    if (!ok) { fprintf(stderr,"FAIL: %s\n",message);exit(1); }
}
static uint32_t le32(const uint8_t *p) { return p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24; }
static void put16(uint8_t *p,unsigned n) {p[0]=n;p[1]=n>>8;}
static void put32(uint8_t *p,uint32_t n) {for(unsigned i=0;i<4;i++)p[i]=n>>(8*i);}
static void put64(uint8_t *p,uint64_t n) {for(unsigned i=0;i<8;i++)p[i]=n>>(8*i);}
static unsigned reference(vpx_codec_ctx_t *decoder,const uint8_t *data,size_t bytes,FILE *out) {
    require(bytes<=UINT32_MAX,"compressed frame size");
    require(vpx_codec_decode(decoder,data,(unsigned)bytes,NULL,0)==VPX_CODEC_OK,"software decode");
    vpx_codec_iter_t iterator=NULL;vpx_image_t *image;unsigned frames=0;
    while((image=vpx_codec_get_frame(decoder,&iterator))) {
        unsigned bpp=(image->fmt&VPX_IMG_FMT_HIGHBITDEPTH)?2:1;
        for(unsigned p=0;p<3;p++) {
            unsigned xs=p?image->x_chroma_shift:0,ys=p?image->y_chroma_shift:0;
            unsigned width=(image->d_w+(1u<<xs)-1)>>xs,height=(image->d_h+(1u<<ys)-1)>>ys;
            for(unsigned y=0;y<height;y++)require(fwrite(image->planes[p]+(size_t)y*image->stride[p],bpp,width,out)==width,"write software pixels");
        }
        frames++;
    }
    return frames;
}
static void fill(vpx_image_t *image,unsigned frame,unsigned depth) {
    unsigned bpp=depth>8?2:1;
    for(unsigned p=0;p<3;p++) {
        unsigned w=p?(image->d_w+1)/2:image->d_w,h=p?(image->d_h+1)/2:image->d_h;
        for(unsigned y=0;y<h;y++)for(unsigned x=0;x<w;x++) {
            unsigned value=(32+(x*7+y*13+frame*17+p*53+(x/31)*29)%190)<<(depth-8);
            if(bpp==2)((uint16_t *)(image->planes[p]+(size_t)y*image->stride[p]))[x]=value;
            else image->planes[p][(size_t)y*image->stride[p]+x]=value;
        }
    }
}
int main(int argc,char **argv) {
    setvbuf(stdout,NULL,_IOLBF,0);
    printf("Software reference: libvpx %s\n",vpx_codec_version_str());
    require(argc>=2,"usage: vpx-fixture encode vp8|vp9 W H FRAMES DEPTH OUTPUT.ivf OUTPUT.yuv | decode INPUT.ivf OUTPUT.yuv");
    if(!strcmp(argv[1],"decode")) {
        require(argc==4,"decode argument count");FILE *input=fopen(argv[2],"rb"),*output=fopen(argv[3],"wb");
        require(input&&output,"open IVF and software output");uint8_t header[32];
        require(fread(header,1,32,input)==32&&!memcmp(header,"DKIF",4),"read IVF header");
        vpx_codec_iface_t *iface=!memcmp(header+8,"VP80",4)?vpx_codec_vp8_dx():!memcmp(header+8,"VP90",4)?vpx_codec_vp9_dx():NULL;
        require(iface!=NULL,"VP8 or VP9 IVF");
        vpx_codec_ctx_t decoder={0};require(vpx_codec_dec_init(&decoder,iface,NULL,0)==VPX_CODEC_OK,"initialize software decoder");
        unsigned frames=0;uint8_t fh[12];
        while(fread(fh,1,12,input)==12) {
            size_t n=le32(fh);require(n&&n<=64*1024*1024,"bounded IVF frame");uint8_t *data=malloc(n);require(data!=NULL,"allocate compressed input");
            require(fread(data,1,n,input)==n,"read IVF payload");frames+=reference(&decoder,data,n,output);free(data);
        }
        require(!ferror(input),"read input status");require(fclose(output)==0,"close software output");fclose(input);vpx_codec_destroy(&decoder);
        require(frames>0,"decoded frames");printf("PASS: %u software frames\n",frames);return 0;
    }
    require(!strcmp(argv[1],"encode")&&argc==10,"encode argument count");
    int vp9=!strcmp(argv[2],"vp9");require(vp9||!strcmp(argv[2],"vp8"),"supported codec");
    unsigned w=(unsigned)atoi(argv[3]),h=(unsigned)atoi(argv[4]),n=(unsigned)atoi(argv[5]),depth=(unsigned)atoi(argv[6]);
    require(w>=16&&h>=16&&w<=4096&&h<=4096&&n>0&&n<=120,"fixture dimensions/frame count");
    require(depth==8||(vp9&&(depth==10||depth==12)),"fixture bit depth");
    require(!strcmp(argv[9],"--synthetic"),"explicit synthetic fixture mode");
    vpx_codec_iface_t *encoder_iface=vp9?vpx_codec_vp9_cx():vpx_codec_vp8_cx();
    vpx_codec_enc_cfg_t cfg;require(vpx_codec_enc_config_default(encoder_iface,&cfg,0)==VPX_CODEC_OK,"encoder defaults");
    cfg.g_w=w;cfg.g_h=h;cfg.g_timebase.num=1;cfg.g_timebase.den=30;cfg.g_threads=4;cfg.g_lag_in_frames=0;
    cfg.g_profile=depth>8?2:0;cfg.g_bit_depth=(vpx_bit_depth_t)depth;cfg.g_input_bit_depth=depth;
    cfg.rc_end_usage=VPX_Q;cfg.rc_min_quantizer=12;cfg.rc_max_quantizer=24;cfg.kf_max_dist=8;
    vpx_codec_ctx_t encoder={0},decoder={0};
    require(vpx_codec_enc_init(&encoder,encoder_iface,&cfg,depth>8?VPX_CODEC_USE_HIGHBITDEPTH:0)==VPX_CODEC_OK,"initialize software encoder");
    require(vpx_codec_control(&encoder,VP8E_SET_CPUUSED,8)==VPX_CODEC_OK,"encoder speed");
    require(vpx_codec_control(&encoder,VP8E_SET_CQ_LEVEL,20)==VPX_CODEC_OK,"constant quality");
    if(vp9)require(vpx_codec_control(&encoder,VP9E_SET_FRAME_PARALLEL_DECODING,0)==VPX_CODEC_OK,"probability adaptation");
    else require(vpx_codec_control(&encoder,VP8E_SET_TOKEN_PARTITIONS,VP8_EIGHT_TOKENPARTITION)==VPX_CODEC_OK,"VP8 partitions");
    require(vpx_codec_dec_init(&decoder,vp9?vpx_codec_vp9_dx():vpx_codec_vp8_dx(),NULL,0)==VPX_CODEC_OK,"initialize reference decoder");
    vpx_image_t image;require(vpx_img_alloc(&image,depth>8?VPX_IMG_FMT_I42016:VPX_IMG_FMT_I420,w,h,32)!=NULL,"allocate image");
    FILE *ivf=fopen(argv[7],"wb"),*raw=fopen(argv[8],"wb");require(ivf&&raw,"open fixture outputs");
    uint8_t header[32]={0};memcpy(header,"DKIF",4);put16(header+6,32);memcpy(header+8,vp9?"VP90":"VP80",4);
    put16(header+12,w);put16(header+14,h);put32(header+16,30);put32(header+20,1);put32(header+24,n);
    require(fwrite(header,1,32,ivf)==32,"write IVF header");unsigned packets=0,shown=0;
    for(unsigned frame=0;frame<=n;frame++) {
        if(frame<n)fill(&image,frame,depth);
        vpx_codec_err_t status=vpx_codec_encode(&encoder,frame<n?&image:NULL,frame,1,0,VPX_DL_GOOD_QUALITY);
        if(status!=VPX_CODEC_OK)fprintf(stderr,"Encoder: %s (%s)\n",vpx_codec_error(&encoder),vpx_codec_error_detail(&encoder));
        require(status==VPX_CODEC_OK,"encode synthetic image");
        vpx_codec_iter_t iterator=NULL;const vpx_codec_cx_pkt_t *pkt;
        while((pkt=vpx_codec_get_cx_data(&encoder,&iterator)))if(pkt->kind==VPX_CODEC_CX_FRAME_PKT) {
            require(pkt->data.frame.sz<=UINT32_MAX,"bounded encoded packet");uint8_t fh[12];put32(fh,(uint32_t)pkt->data.frame.sz);put64(fh+4,pkt->data.frame.pts);
            require(fwrite(fh,1,12,ivf)==12&&fwrite(pkt->data.frame.buf,1,pkt->data.frame.sz,ivf)==pkt->data.frame.sz,"write encoded frame");
            shown+=reference(&decoder,pkt->data.frame.buf,pkt->data.frame.sz,raw);packets++;
        }
    }
    require(packets==n&&shown==n,"all synthetic frames encoded and decoded");
    require(fclose(ivf)==0&&fclose(raw)==0,"close fixture outputs");vpx_img_free(&image);vpx_codec_destroy(&encoder);vpx_codec_destroy(&decoder);
    printf("PASS: %u %s frames, %ux%u, %u-bit, independent software reference\n",shown,argv[2],w,h,depth);return 0;
}
