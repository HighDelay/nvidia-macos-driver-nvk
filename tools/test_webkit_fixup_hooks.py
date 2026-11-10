#!/usr/bin/env python3
"""Exercise the production MAC callbacks with simulated signing/vnode state."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
text = (ROOT/'kexts/NVWebKitFixup/nvwebkitfixup.cpp').read_text()
body = text[text.index('proc_t (*currentProcess)'):text.index('template<typename T>')]
source = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <cerrno>
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/fcntl.h>
#include "policy.hpp"
struct vnode_attr {
    uint32_t va_uid=0,va_mode=0755,va_flags=SF_RESTRICTED;
    dev_t va_rdev=makedev(3,2);
    bool va_uid_supported=true,va_mode_supported=true,va_flags_supported=true,va_rdev_supported=true;
};
struct Node { const char *path; int type=1,error=0; vnode_attr attr; };
using vnode_t=Node *;using proc_t=void *;
enum {VREG=1,VCHR=4};
#define VATTR_INIT(p) (*(p)=vnode_attr{})
#define VATTR_WANTED(p,n) ((void)(p))
#define VATTR_IS_SUPPORTED(p,n) ((p)->n##_supported)
static int attrCalls;
static void *vfs_context_current(){return nullptr;}
static int vnode_getattr(Node *v,vnode_attr *a,void *){attrCalls++;*a=v->attr;return v->error;}
static int vnode_vtype(Node *v){return v->type;}
PRODUCTION
static int openCalls,mapCalls,nativeResult=EPERM;
static bool lowerProtection,platform=true,valid=true;
static const char *identity="com.apple.WebKit.GPU";
static proc_t proc(){return (void *)1;}
static const char *identifier(proc_t){return identity;}
static int isPlatform(proc_t){return platform;}
static int isValid(proc_t){return valid;}
static vnode_t vnode(void *p){return (Node *)p;}
static int path(Node *p,char *b,int *n){
    size_t bytes=strlen(p->path)+1;if(bytes>(size_t)*n)return ENOSPC;
    memcpy(b,p->path,bytes);*n=(int)bytes;return 0;
}
static int nativeOpen(void *,Node *,void *,int){openCalls++;return nativeResult;}
static int nativeMap(void *,void *,void *,int,int,uint64_t,int *max){
    mapCalls++;if(lowerProtection&&max)*max&=~4;return nativeResult;
}
int main(){
    currentProcess=proc;processIdentity=identifier;platformBinary=isPlatform;validSignature=isValid;
    fileVnode=vnode;vnodePath=path;originalOpen=nativeOpen;originalMap=nativeMap;
    Node image{"/Library/GPUBundles/NVMTLDriver.bundle/Contents/MacOS/NVMTLDriver"};
    Node null{"/dev/null"};null.type=VCHR;null.attr.va_mode=0666;
    int max=7;
    assert(checkMap(nullptr,&image,nullptr,5,2,0,&max)==EPERM&&mapCalls==1&&attrCalls==0);
    ready=true;
    assert(checkMap(nullptr,&image,nullptr,5,2,0,&max)==0&&mapCalls==2&&mapGrants==1&&max==7);
    assert(checkOpen(nullptr,&null,nullptr,FREAD)==0&&openCalls==1&&readGrants==1);
    assert(checkOpen(nullptr,&null,nullptr,FREAD|FWRITE)==EPERM&&readGrants==1);
    nativeResult=EACCES;assert(checkMap(nullptr,&image,nullptr,5,2,0,&max)==EACCES);
    nativeResult=0;int before=attrCalls;assert(checkMap(nullptr,&image,nullptr,5,2,0,&max)==0&&attrCalls==before);
    nativeResult=EPERM;
    for(bool *flag : {&platform,&valid}){*flag=false;before=attrCalls;
        assert(checkMap(nullptr,&image,nullptr,5,2,0,&max)==EPERM&&attrCalls==before);*flag=true;}
    identity="com.apple.WebKit.GPU.fake";
    assert(checkMap(nullptr,&image,nullptr,5,2,0,&max)==EPERM);identity="com.apple.WebKit.GPU";
    image.attr.va_uid=501;assert(checkMap(nullptr,&image,nullptr,5,2,0,&max)==EPERM);image.attr.va_uid=0;
    image.attr.va_mode=0775;assert(checkMap(nullptr,&image,nullptr,5,2,0,&max)==EPERM);image.attr.va_mode=0755;
    image.attr.va_flags=0;assert(checkMap(nullptr,&image,nullptr,5,2,0,&max)==EPERM);image.attr.va_flags=SF_RESTRICTED;
    image.attr.va_flags_supported=false;assert(checkMap(nullptr,&image,nullptr,5,2,0,&max)==EPERM);image.attr.va_flags_supported=true;
    image.error=EIO;assert(checkMap(nullptr,&image,nullptr,5,2,0,&max)==EPERM);image.error=0;
    image.type=VCHR;assert(checkMap(nullptr,&image,nullptr,5,2,0,&max)==EPERM);image.type=VREG;
    lowerProtection=true;max=7;assert(checkMap(nullptr,&image,nullptr,5,2,0,&max)==EPERM&&max==3);lowerProtection=false;
    max=7;assert(checkMap(nullptr,&image,nullptr,7,2,0,&max)==EPERM);
    null.attr.va_rdev=makedev(3,1);assert(checkOpen(nullptr,&null,nullptr,FREAD)==EPERM);
    null.attr.va_rdev=makedev(3,2);null.type=VREG;assert(checkOpen(nullptr,&null,nullptr,FREAD)==EPERM);
    assert(mapGrants==1&&readGrants==1&&identityRefusals==3&&trustRefusals>=8&&protectionRefusals==1);
    puts("PASS: native results/maxprot preserved; signed client, vnode ownership/type/flags and null-device trust enforced");
}
'''
with tempfile.TemporaryDirectory(prefix='nvwk-hooks-') as directory:
    folder = Path(directory)
    (folder/'test.cpp').write_text('#include <initializer_list>\n'+source.replace('PRODUCTION',body))
    subprocess.run(['xcrun','clang++','-std=c++17','-O1','-g','-Wall','-Wextra','-Werror',
                    '-Wno-unused-variable','-Wno-unused-function','-Wno-missing-field-initializers',
                    '-fsanitize=address,undefined','-fno-sanitize-recover=all',
                    '-I'+str(ROOT/'kexts/NVWebKitFixup'),str(folder/'test.cpp'),'-o',str(folder/'test')],check=True)
    subprocess.run([str(folder/'test')],check=True)
