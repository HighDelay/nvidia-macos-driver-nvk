#!/usr/bin/env python3
"""Exercise DVI selection and production mode publication with public NVIDIA headers."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--ogkm', type=Path, default=Path(os.environ.get('OGKM', Path.home() / 'ogkm615')))
args = parser.parse_args()
source = (ROOT / 'kexts/NVRMFB/fb/nvrm-fb.cpp').read_text()


def method(signature):
    start = source.index(signature)
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


init = method('bool NVRMNVDAFramebuffer::kapiInit(')
assert init.index('validateInitialDviMode();') < init.index('fW = fMode.timings.hVisible;')
assert init.index('validateInitialDviMode();') < init.index('fBootMode = fMode;')

fixture = r'''
#include <IOKit/graphics/IOGraphicsTypes.h>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <map>
#include <string>
#include <vector>
#include "nvrm-dvi-modes.h"
#include "nvrm-fb-safety.h"
#define detailedTimingModeID __reservedA[0]
#define FBLOG(...) ((void)0)
static void nvu_zero(void *p,size_t n){memset(p,0,n);}
struct Entry { NvKmsKapiDisplayMode mode; bool indexed, explicitValid; };
static std::vector<Entry> modes;
static unsigned queries,validations;
static bool originalValid,enumerationError,connectorAvailable=true;
static NvKmsConnectorType connectorType=NVKMS_CONNECTOR_TYPE_DVI_D;
static auto testSignalFormat=NVKMS_CONNECTOR_SIGNAL_FORMAT_TMDS;
static NvKmsKapiDisplayMode original;
static NvBool validate(NvKmsKapiDevice *,NvKmsKapiDisplay,const NvKmsKapiDisplayMode *mode){
    ++validations;
    if(!memcmp(mode,&original,sizeof original))return originalValid;
    for(const auto &e:modes)if(!memcmp(mode,&e.mode,sizeof *mode))return e.explicitValid;
    return NV_FALSE;
}
static int enumerate(NvKmsKapiDevice *,NvKmsKapiDisplay,NvU32 index,
                     NvKmsKapiDisplayMode *mode,NvBool *valid,NvBool *preferred){
    assert(index<256);++queries;
    if(index>=modes.size())return enumerationError?-1:0;
    *mode=modes[index].mode;*valid=modes[index].indexed;*preferred=index==0;
    return 1;
}
static NvBool connector(NvKmsKapiDevice *,NvKmsKapiConnector,NvKmsKapiConnectorInfo *out){
    out->type=connectorType;out->signalFormat=testSignalFormat;return connectorAvailable;
}
static NvKmsKapiDisplayMode makeMode(unsigned w,unsigned h,unsigned refresh,unsigned clock){
    NvKmsKapiDisplayMode mode={};auto &t=mode.timings;
    t.hVisible=w;t.vVisible=h;t.refreshRate=refresh;t.pixelClockHz=clock;
    t.hSyncStart=w+48;t.hSyncEnd=w+80;t.hTotal=w+160;
    t.vSyncStart=h+3;t.vSyncEnd=h+8;t.vTotal=h+39;
    t.flags.hSyncPos=t.flags.vSyncPos=1;
    return mode;
}
static NvKmsKapiDisplayMode failureMode(){
    auto mode=makeMode(1920,1080,60000,148500000);auto &t=mode.timings;
    t.hSyncStart=2008;t.hSyncEnd=2052;t.hTotal=2200;
    t.vSyncStart=1084;t.vSyncEnd=1089;t.vTotal=1125;
    return mode;
}
struct NVRMNVDAFramebuffer {
    NvKmsKapiFunctionsTable *fKms=nullptr;
    NvKmsKapiDevice *fDev=reinterpret_cast<NvKmsKapiDevice *>(1);
    NvKmsKapiDisplay fDisplay=0x100;NvKmsKapiConnector fConnector=1;
    NvKmsKapiDisplayMode fMode={},fKmsMode={};
    bool fHaveKmsMode=false,fDviModeValidation=false,fKmsModesLogged=false;
    unsigned fIndex=0,fEdidHits=0,fBootW=1920,fBootH=1080,fPitchAlign=256;
    unsigned fRasterMaxW=7680,fScalerMaxH=4320;
    NvU64 fMemBytes=256ull<<20;
    IODetailedTimingInformationV2 fDT[256]={};
    std::map<std::string,unsigned long long> properties;
    void setProperty(const char *k,bool v){properties[k]=v;}
    void setProperty(const char *k,unsigned long long v,unsigned){properties[k]=v;}
    void validateInitialDviMode();
    bool edidModeFor(const IODetailedTimingInformationV2 *,NvKmsKapiDisplayMode *);
    unsigned addKmsRefreshModes(unsigned);
    static void dtToKapi(const IODetailedTimingInformationV2 *, NvKmsKapiDisplayMode *);
};
@@METHODS@@
static IODetailedTimingInformationV2 timing(const NvKmsKapiDisplayMode &mode){
    IODetailedTimingInformationV2 d={};auto &t=mode.timings;
    d.pixelClock=t.pixelClockHz;d.horizontalActive=t.hVisible;d.verticalActive=t.vVisible;
    d.horizontalBlanking=t.hTotal-t.hVisible;d.verticalBlanking=t.vTotal-t.vVisible;
    return d;
}
int main(){
    NvKmsKapiFunctionsTable kms={};kms.getDisplayMode=enumerate;
    kms.validateDisplayMode=validate;kms.getConnectorInfo=connector;
    auto *dev=reinterpret_cast<NvKmsKapiDevice *>(1);
    original=failureMode();auto saved=original;
    const auto accepted=makeMode(1920,1080,75003,174570000);
    auto rejected=original;rejected.timings.refreshRate=59940;rejected.timings.pixelClockHz=148350000;
    const auto lower=makeMode(1280,720,60000,74250000);
    modes={{original,true,false},{rejected,true,false},{lower,true,true},{accepted,true,true}};
    NvKmsKapiDisplayMode replacement={};NvU32 index=999;
    assert(nvrmDviSelectSameRasterMode(&kms,dev,0x100,&original,&replacement,&index));
    assert(index==3&&!memcmp(&replacement,&accepted,sizeof accepted));
    assert(!memcmp(&original,&saved,sizeof saved)&&queries==5&&validations==4);
    originalValid=true;queries=0;
    assert(!nvrmDviSelectSameRasterMode(&kms,dev,0x100,&original,&replacement,&index)&&!queries);
    originalValid=false;
    auto sentinel=replacement;auto sentinelIndex=index;
    modes.pop_back();
    assert(!nvrmDviSelectSameRasterMode(&kms,dev,0x100,&original,&replacement,&index));
    assert(!memcmp(&sentinel,&replacement,sizeof sentinel)&&index==sentinelIndex);
    modes.push_back({accepted,true,true});enumerationError=true;
    assert(!nvrmDviSelectSameRasterMode(&kms,dev,0x100,&original,&replacement,&index));
    assert(!memcmp(&sentinel,&replacement,sizeof sentinel)&&index==sentinelIndex);
    enumerationError=false;
    auto nearer=makeMode(1920,1080,61000,149000000),tie=makeMode(1920,1080,61000,148900000);
    modes.push_back({nearer,true,true});modes.push_back({tie,true,true});
    assert(nvrmDviSelectSameRasterMode(&kms,dev,0x100,&original,&replacement,&index));
    assert(index==5&&replacement.timings.pixelClockHz==148900000);
    modes={{accepted,false,true},{rejected,true,false}};
    auto bad=accepted;bad.timings.flags.interlaced=1;modes.push_back({bad,true,true});
    bad=accepted;bad.timings.flags.doubleScan=1;modes.push_back({bad,true,true});
    bad=accepted;bad.timings.refreshRate=0;modes.push_back({bad,true,true});
    bad=accepted;bad.timings.pixelClockHz=0;modes.push_back({bad,true,true});
    assert(!nvrmDviSelectSameRasterMode(&kms,dev,0x100,&original,&replacement,&index));
    for(auto size:{std::pair<unsigned,unsigned>{2560,1440},{3840,2160},{5120,1440}}){
        original=makeMode(size.first,size.second,60000,300000000);
        auto other=makeMode(size.first,size.second,120000,600000000);
        modes={{original,true,false},{other,true,true}};
        assert(nvrmDviSelectSameRasterMode(&kms,dev,0x100,&original,&replacement,&index));
        assert(index==1&&!memcmp(&replacement,&other,sizeof other));
    }
    modes.assign(300,{lower,true,true});queries=0;
    assert(!nvrmDviSelectSameRasterMode(&kms,dev,0x100,&original,&replacement,&index)&&queries==256);
    assert(!nvrmDviSelectSameRasterMode(nullptr,dev,0x100,&original,&replacement,&index));
    assert(!nvrmDviSelectSameRasterMode(&kms,nullptr,0x100,&original,&replacement,&index));
    assert(!nvrmDviSelectSameRasterMode(&kms,dev,0,&original,&replacement,&index));
    assert(!nvrmDviSelectSameRasterMode(&kms,dev,0x100,nullptr,&replacement,&index));
    assert(!nvrmDviSelectSameRasterMode(&kms,dev,0x100,&original,nullptr,&index));
    assert(!nvrmDviSelectSameRasterMode(&kms,dev,0x100,&original,&replacement,nullptr));

    // Exercise the real framebuffer methods, including connector scope and the
    // EDID shortcut that previously reintroduced rejected timings.
    original=failureMode();modes={{original,true,false},{accepted,true,true}};
    for(auto type:{NVKMS_CONNECTOR_TYPE_DVI_D,NVKMS_CONNECTOR_TYPE_DVI_I,
                   NVKMS_CONNECTOR_TYPE_HDMI,NVKMS_CONNECTOR_TYPE_DP,NVKMS_CONNECTOR_TYPE_LVDS}){
        connectorType=type;queries=validations=0;
        NVRMNVDAFramebuffer fb;fb.fKms=&kms;fb.fMode=original;fb.validateInitialDviMode();
        const bool dvi=type==NVKMS_CONNECTOR_TYPE_DVI_D||type==NVKMS_CONNECTOR_TYPE_DVI_I;
        assert(fb.fDviModeValidation==dvi);
        if(dvi){
            assert(!memcmp(&fb.fMode,&accepted,sizeof accepted)&&fb.fHaveKmsMode);
            assert(!memcmp(&fb.fKmsMode,&accepted,sizeof accepted));
            assert(fb.properties.at("NVRMDviModeFallbackApplied")==1);
            assert(fb.properties.at("NVRMDviReplacementModeIndex")==1);
            assert(fb.properties.at("NVRMDviModeSelectionVersion")==1);
        }else{
            assert(!memcmp(&fb.fMode,&original,sizeof original)&&!fb.fHaveKmsMode);
            assert(!queries&&!validations&&fb.properties.empty());
        }
        NvKmsKapiDisplayMode out={};auto rejectedTiming=timing(original),goodTiming=timing(accepted);
        assert(fb.edidModeFor(&rejectedTiming,&out)==!dvi);
        assert(fb.edidModeFor(&goodTiming,&out)&&out.timings.pixelClockHz==174570000);
        unsigned count=fb.addKmsRefreshModes(0);assert(count==(dvi?1u:2u));
        if(dvi)assert(fb.fDT[0].pixelClock==174570000);
    }
    connectorType=NVKMS_CONNECTOR_TYPE_DVI_I;testSignalFormat=NVKMS_CONNECTOR_SIGNAL_FORMAT_VGA;
    NVRMNVDAFramebuffer analog;analog.fKms=&kms;analog.fMode=original;queries=validations=0;
    analog.validateInitialDviMode();assert(!analog.fDviModeValidation&&!queries&&!validations);
    testSignalFormat=NVKMS_CONNECTOR_SIGNAL_FORMAT_TMDS;connectorAvailable=false;
    analog.validateInitialDviMode();assert(!analog.fDviModeValidation);
    connectorAvailable=true;originalValid=true;
    NVRMNVDAFramebuffer valid;valid.fKms=&kms;valid.fMode=original;queries=0;
    valid.validateInitialDviMode();assert(valid.fDviModeValidation&&!queries&&!valid.fHaveKmsMode);
    assert(valid.properties.at("NVRMDviModeFallbackApplied")==0);
    kms.validateDisplayMode=nullptr;
    assert(!nvrmDviSelectSameRasterMode(&kms,dev,0x100,&original,&replacement,&index));
    puts("PASS: ITY timing replay; exact raster and both validations; valid-mode retention; nearest refresh; failure/bounds/null guards; 1440p/4K/ultrawide; production DVI-only startup, EDID matching and refresh publication; HDMI/DP/LVDS/analog unchanged");
}
'''
fixture = fixture.replace('@@METHODS@@', '\n'.join(method(name) for name in [
    'void NVRMNVDAFramebuffer::validateInitialDviMode(',
    'bool NVRMNVDAFramebuffer::edidModeFor(',
    'void NVRMNVDAFramebuffer::dtToKapi(',
    'unsigned NVRMNVDAFramebuffer::addKmsRefreshModes(']))
includes = [ROOT / 'kexts/NVRMFB/fb'] + [args.ogkm / name for name in [
    'src/nvidia/arch/nvalloc/unix/include',
    'src/nvidia-modeset/kapi/interface', 'src/nvidia-modeset/interface',
    'src/nvidia-modeset/include', 'src/nvidia-modeset/os-interface/include',
    'src/common/sdk/nvidia/inc', 'src/common/inc', 'src/common/shared/inc',
    'src/common/unix/common/inc', 'src/common/modeset']]
with tempfile.TemporaryDirectory(prefix='nv-dvi-modes-') as directory:
    out = Path(directory)
    (out / 'test.cpp').write_text(fixture)
    subprocess.run(['xcrun', 'clang++', '-std=c++17', '-O1', '-g', '-Wall', '-Wextra',
                    '-Werror', '-Wno-unused-parameter', '-Wno-unused-variable',
                    '-Wno-unused-function', '-fsanitize=address,undefined',
                    '-fno-sanitize-recover=all', *['-I' + str(p) for p in includes],
                    str(out / 'test.cpp'), '-o', str(out / 'test')], check=True)
    subprocess.run([str(out / 'test')], check=True, timeout=30)
