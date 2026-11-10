#!/usr/bin/env python3
"""Check real HEVC arithmetic with hostile and valid reference-picture sequences."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT/'vendor/nvenc/src'
HEADERS = Path(os.environ['NVDEC_HEADERS'])
PROGRAM = r'''
#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include "hevcparse.c"
static Br r;
static unsigned pos;
static void bit(unsigned v) { r.buf[pos/8] |= (v&1) << (7-pos%8); pos++; }
static void golomb(unsigned v) {
    unsigned n=0; for(unsigned k=v+1;k>>=1;)n++;
    for(unsigned i=0;i<n;i++)bit(0);
    for(int i=(int)n;i>=0;i--)bit((v+1)>>i);
}
static void reset(void) { memset(&r,0,sizeof r);pos=0; }
static void finish(void) { r.n=(pos+7)/8; }
int main(void) {
    HevcStRps sets[64]={0},out={0};
    reset();golomb(0);golomb(9);
    for(unsigned i=0;i<9;i++){golomb(0x0fffffff);bit(1);}finish();
    assert(st_ref_pic_set(&r,sets,0,1,&out,NULL)<0); // repeated positive deltas must not overflow
    reset();golomb(9);golomb(0);
    for(unsigned i=0;i<9;i++){golomb(0x0fffffff);bit(1);}finish();
    assert(st_ref_pic_set(&r,sets,0,1,&out,NULL)<0); // repeated negative deltas
    reset();golomb(0x10000000);finish();
    assert(ue(&r)==0 && r.err); // do not silently saturate invalid syntax
    sets[0].num_positive=1;sets[0].delta_poc[0]=INT_MAX;
    reset();bit(1);bit(0);golomb(0);bit(1);bit(1);finish();
    assert(st_ref_pic_set(&r,sets,1,2,&out,NULL)<0); // predicted reference addition
    memset(sets,0,sizeof sets);sets[0].num_negative=1;sets[0].delta_poc[0]=-2;
    reset();bit(1);bit(0);golomb(0);bit(1);bit(1);finish();
    assert(st_ref_pic_set(&r,sets,1,2,&out,NULL)==0);
    assert(out.num_negative==1 && out.num_positive==1 && out.delta_poc[0]==-1 && out.delta_poc[1]==1);
    HevcSps s={.width=64,.height=64,.chroma_format_idc=1,.bit_depth_luma=8,.bit_depth_chroma=8,
        .log2_max_poc_lsb=8,.log2_min_cb=3,.log2_max_cb=6,.log2_min_tb=3,.log2_max_tb=5};
    assert(sps_semantics_ok(&s)); // an 8x8 transform may equal the minimum coding-block size
    s.log2_min_tb=4;assert(!sps_semantics_ok(&s));s.log2_min_tb=3;
    HevcSlice sl={.nal_type=1,.temporal_id=0,.poc_lsb=0};
    HevcPocState state={.prev_poc_tid0=255};
    assert(hevc_poc(&state,&s,&sl,false)==256 && state.prev_poc_tid0==256);
    state.prev_poc_tid0=INT_MAX;
    assert(hevc_poc(&state,&s,&sl,false)==INT_MIN && state.prev_poc_tid0==INT_MAX);
    state.prev_poc_tid0=INT_MIN+1;sl.poc_lsb=255;
    assert(hevc_poc(&state,&s,&sl,false)==INT_MIN && state.prev_poc_tid0==INT_MIN+1);
    puts("PASS: cumulative/predicted POC overflow refused, valid RPS/wrap and equal transform size retained");
}
'''
with tempfile.TemporaryDirectory(prefix='nvenc-poc-bounds-') as name:
    tmp=Path(name);(tmp/'test.c').write_text(PROGRAM)
    subprocess.run(['clang','-O1','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all',
                    '-I'+str(SOURCE),'-I'+str(HEADERS),str(tmp/'test.c'),'-o',str(tmp/'test')],check=True)
    subprocess.run([str(tmp/'test')],check=True,timeout=10)
