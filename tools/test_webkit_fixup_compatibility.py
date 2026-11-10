#!/usr/bin/env python3
"""Exercise OS, symbol, instruction and routing gates from the real helper."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
text = (ROOT/'kexts/NVWebKitFixup/nvwebkitfixup.cpp').read_text()
production = text[text.index('template<typename T>'):text.index('int diagnostics SYSCTL_HANDLER_ARGS')]
source = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <climits>
#include <vector>
#include "compatibility.hpp"
using mach_vm_address_t=uintptr_t;
using proc_t=void *;using vnode_t=void *;
namespace KernelVersion { enum { Sequoia=24,Tahoe=25 }; }
static unsigned kernelMajor=25;
static unsigned getKernelVersion(){return kernelMajor;}
template<typename T,size_t N> static constexpr size_t arrsize(const T (&)[N]) {return N;}
#define SYSLOG(...) ((void)0)
static uint8_t image[256];
static bool omitSymbol, routeSuccess=true, decoderSuccess=true;
static int routeCalls;
struct KernelPatcher {
    static constexpr size_t KernelID=0;
    struct RouteRequest {
        const char *symbol; uintptr_t from=0,to;uintptr_t *org;
        template<typename T,typename O> RouteRequest(const char *s,T t,O &o):
            symbol(s),to(reinterpret_cast<uintptr_t>(t)),org(reinterpret_cast<uintptr_t *>(&o)){}
    };
    uintptr_t solveSymbol(size_t id,const char *name,uintptr_t=0,size_t=0){
        if(omitSymbol)return 0;
        if(id==KernelID)return 1;
        return reinterpret_cast<uintptr_t>(image)+(strstr(name,"mmap")?128:0);
    }
    void clearError(){}
    bool routeMultipleLong(size_t,RouteRequest *routes,size_t n,uintptr_t base,size_t size){
        assert(n==2&&base==reinterpret_cast<uintptr_t>(image)&&size==sizeof(image));
        assert(routes[0].from==base&&routes[1].from==base+128);
        routeCalls++;
        if(routeSuccess)for(size_t i=0;i<n;i++)*routes[i].org=1;
        return routeSuccess;
    }
};
struct Disassembler {
    static size_t quickInstructionSize(uintptr_t address,size_t min){
        assert(min==14);if(!decoderSuccess)return 0;
        return address==reinterpret_cast<uintptr_t>(image)?20:16;
    }
};
struct IORegistryEntry {
    static IORegistryEntry *getRegistryRoot(){static IORegistryEntry root;return &root;}
    void setProperty(const char *,bool){}
};
static struct {size_t loadIndex=1;} sandbox;
static proc_t (*currentProcess)();
static const char *(*processIdentity)(proc_t);
static int (*platformBinary)(proc_t),(*validSignature)(proc_t);
static vnode_t (*fileVnode)(void *);
static int (*vnodePath)(vnode_t,char *,int *);
using Open=int (*)(void *,vnode_t,void *,int);
using Map=int (*)(void *,void *,void *,int,int,uint64_t,int *);
static Open originalOpen;static Map originalMap;
static int checkOpen(void *,vnode_t,void *,int){return 0;}
static int checkMap(void *,void *,void *,int,int,uint64_t,int *){return 0;}
static bool ready,registered=true;
static const char *status="waiting";
PRODUCTION
static const uint8_t openCode[]={0x55,0x48,0x89,0xe5,0x41,0x57,0x41,0x56,0x41,0x55,0x41,0x54,0x53,0x48,0x81,0xec,0xd8,0x01,0,0};
static const uint8_t mapCode[]={0x55,0x48,0x89,0xe5,0x41,0x57,0x41,0x56,0x53,0x48,0x81,0xec,0xa8,0x01,0,0};
static void reset(){
    memset(image,0,sizeof(image));memcpy(image,openCode,sizeof(openCode));memcpy(image+128,mapCode,sizeof(mapCode));
    ready=false;routeCalls=0;originalOpen=nullptr;originalMap=nullptr;status="waiting";
    omitSymbol=false;routeSuccess=true;decoderSuccess=true;registered=true;
}
int main(){
    using namespace NVWebKitCompatibility;
    for(unsigned os:{0U,23U,26U,UINT_MAX})assert(!kernel(os));
    assert(kernel(24)&&kernel(25));
    for(auto hook:{Hook::Open,Hook::Map}){
        const auto code=hook==Hook::Open?openCode:mapCode;
        const size_t n=hook==Hook::Open?sizeof(openCode):sizeof(mapCode);
        assert(prefix(code,n,hook)==n&&prefix(nullptr,n,hook)==0);
        for(size_t length=0;length<n;length++){
            std::vector<uint8_t> shortCode(code,code+length);
            assert(!prefix(shortCode.data(),shortCode.size(),hook));
        }
        for(size_t offset=0;offset<n-4;offset++)for(unsigned byte=0;byte<256;byte++){
            if(byte==code[offset])continue;
            std::vector<uint8_t> altered(code,code+n);altered[offset]=uint8_t(byte);
            assert(!prefix(altered.data(),altered.size(),hook));
        }
        for(uint32_t frame:{0U,1U,7U,8U,0x80U,0x268U,0x10000U,0x10008U,0x80000000U,UINT_MAX}){
            std::vector<uint8_t> altered(code,code+n);
            for(unsigned b=0;b<4;b++)altered[n-4+b]=uint8_t(frame>>(b*8));
            assert(bool(prefix(altered.data(),altered.size(),hook))==(frame&&frame<=0x10000&&!(frame&7)));
        }
    }
    assert(!inImage(0,0,Lookahead)&&!inImage(999,1000,128));
    assert(!inImage(1000,1000,Lookahead-1)&&inImage(1000,1000,Lookahead));
    assert(inImage(1082,1000,128)&&!inImage(1083,1000,128));
    assert(!inImage(UINTPTR_MAX,1000,128));
    assert(!inImage(UINTPTR_MAX-20,UINTPTR_MAX-20,128));
    assert(!inImage(1000,1000,SIZE_MAX));
    KernelPatcher p;const auto base=reinterpret_cast<uintptr_t>(image);
    for(unsigned os:{24U,25U}){
        reset();kernelMajor=os;kernelReady(nullptr,p);assert(!strcmp(status,"kernel symbols ready"));
        sandboxReady(nullptr,p,1,base,sizeof(image));assert(ready&&routeCalls==1);
    }
    for(unsigned os:{23U,26U}){
        reset();kernelMajor=os;kernelReady(nullptr,p);sandboxReady(nullptr,p,1,base,sizeof(image));assert(!ready&&!routeCalls);
    }
    kernelMajor=25;
    reset();registered=false;kernelReady(nullptr,p);assert(!strcmp(status,"waiting"));
    reset();omitSymbol=true;kernelReady(nullptr,p);sandboxReady(nullptr,p,1,base,sizeof(image));assert(!ready&&!routeCalls);
    reset();kernelReady(nullptr,p);sandboxReady(nullptr,p,2,base,sizeof(image));assert(!ready&&!routeCalls);
    reset();kernelReady(nullptr,p);sandboxReady(nullptr,p,1,base,128+Lookahead-1);assert(!ready&&!routeCalls);
    reset();kernelReady(nullptr,p);image[0]=0xe9;sandboxReady(nullptr,p,1,base,sizeof(image));assert(!ready&&!routeCalls);
    reset();kernelReady(nullptr,p);image[128+4]=0xe8;sandboxReady(nullptr,p,1,base,sizeof(image));assert(!ready&&!routeCalls);
    reset();kernelReady(nullptr,p);decoderSuccess=false;sandboxReady(nullptr,p,1,base,sizeof(image));assert(!ready&&!routeCalls);
    reset();kernelReady(nullptr,p);routeSuccess=false;sandboxReady(nullptr,p,1,base,sizeof(image));assert(!ready&&routeCalls==1);
    puts("PASS: Darwin 24/25, bounded complete relocatable prefixes, frame-size variants, symbol/decoder/routing failure gates");
}
'''
with tempfile.TemporaryDirectory(prefix='nvwk-compatibility-') as directory:
    folder = Path(directory)
    (folder/'test.cpp').write_text(source.replace('PRODUCTION', production))
    subprocess.run(['xcrun', 'clang++', '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                    '-I'+str(ROOT/'kexts/NVWebKitFixup'), str(folder/'test.cpp'), '-o', str(folder/'test')], check=True)
    subprocess.run([str(folder/'test')], check=True)
