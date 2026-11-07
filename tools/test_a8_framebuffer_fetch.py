#!/usr/bin/env python3
"""Check A8 framebuffer input remapping, descriptor isolation and malformed input."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = r'''
#include <assert.h>
#include <stdio.h>
#define NVMTL_FETCH_SET 3
#define NVMTL_FETCH_BINDING 192
#define NVMTL_NCOL 8
#include "nvmtl_a8_fetch.h"
#define I(op,n) (((n)<<16)|(op))
static const uint32_t module[] = {
    0x07230203,0x00010300,0,27,0,
    I(17,2),1, I(17,2),40, I(14,3),0,1,
    I(15,6),4,20,0x6e69616d,0,12, I(16,3),20,7,
    I(71,4),10,34,3, I(71,4),10,33,192, I(71,4),10,43,0,
    I(71,4),11,34,3, I(71,4),11,33,193, I(71,4),11,43,1,
    I(71,4),12,30,0,
    I(19,2),1, I(33,3),2,1, I(22,3),3,32,
    I(23,4),4,3,4, I(21,4),5,32,1, I(23,4),6,5,2,
    I(25,9),7,3,6,0,0,0,2,0, I(32,4),8,0,7, I(32,4),9,3,4,
    I(59,4),8,10,0, I(59,4),8,11,0, I(59,4),9,12,3,
    I(43,4),5,13,0, I(44,5),6,14,13,13,
    I(54,5),1,20,0,2, I(248,2),21,
    I(61,4),7,22,10, I(61,4),7,23,11,
    I(98,5),4,24,22,14, I(98,5),4,25,23,14,
    I(129,5),4,26,24,25, I(62,3),12,26, I(253,1), I(56,1)
};
static void verify(const uint32_t *input, size_t bytes, unsigned mask, unsigned expected) {
    uint32_t *out = (void *)1; size_t len = 1;
    int rc = nvmtl_spirv_a8_fetch(input,bytes,mask,&out,&len);
    assert(rc == (expected ? 1 : 0));
    if (!expected) { assert(out == NULL && !len); return; }
    assert(len == bytes + 36*expected && out[3] == input[3]+expected);
    unsigned shuffles=0,reads=0;
    for(size_t i=5;i<len/4;i+=out[i]>>16) {
        unsigned op=out[i]&65535;
        if(op==98) reads++;
        if(op==79) {
            assert((out[i]>>16)==9 && out[i+1]==4);
            assert(out[i+2]==24 || out[i+2]==25);
            assert(mask & (1u<<(out[i+2]-24)));
            assert(out[i+3]>=input[3] && out[i+3]==out[i+4]);
            assert(out[i+5]==1 && out[i+6]==1 && out[i+7]==1 && out[i+8]==0);
            shuffles++;
        }
    }
    assert(reads==2 && shuffles==expected); free(out);
}
int main(int argc,char **argv) {
    uint32_t copy[sizeof module/4]; memcpy(copy,module,sizeof copy);
    verify(module,sizeof module,0,0); verify(module,sizeof module,1,1);
    verify(module,sizeof module,2,1); verify(module,sizeof module,3,2);
    verify(module,sizeof module,4,0); assert(!memcmp(copy,module,sizeof copy));
    for(size_t i=5;i<sizeof copy/4;i+=copy[i]>>16)
        if((copy[i]&65535)==71 && copy[i+1]==10 && copy[i+2]==34) copy[i+3]=2;
    verify(copy,sizeof copy,1,0); verify(copy,sizeof copy,3,1);
    for(size_t bytes=0;bytes<sizeof module;bytes++) {
        uint32_t *out=NULL;size_t len=0;
        int rc=nvmtl_spirv_a8_fetch(module,bytes,3,&out,&len);
        assert(rc>=-1 && rc<=1);free(out);
    }
    const unsigned positions[]={0,3,5,7,9,17,21,25};
    for(unsigned i=0;i<sizeof positions/sizeof *positions;i++) {
        memcpy(copy,module,sizeof copy);copy[positions[i]]=0xffffffff;
        uint32_t *out=NULL;size_t len=0;
        int rc=nvmtl_spirv_a8_fetch(copy,sizeof copy,3,&out,&len);
        assert(rc>=-1 && rc<=1);free(out);
    }
    if(argc==3) {
        FILE *f=fopen(argv[1],"wb");assert(f);fwrite(module,1,sizeof module,f);fclose(f);
        uint32_t *out=NULL;size_t bytes=0;assert(nvmtl_spirv_a8_fetch(module,sizeof module,3,&out,&bytes)==1);
        f=fopen(argv[2],"wb");assert(f);fwrite(out,1,bytes,f);fclose(f);free(out);
    }
    puts("PASS: A8 attachment selection, RGBA isolation, immutable input, malformed/truncated modules");
}
'''
with tempfile.TemporaryDirectory(prefix='nvmtl-a8-fetch-') as directory:
    source,binary=Path(directory)/'test.c',Path(directory)/'test'
    source.write_text(FIXTURE)
    subprocess.run(['xcrun','clang','-O1','-g','-fsanitize=address,undefined','-I'+str(ROOT/'plugin'),str(source),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
