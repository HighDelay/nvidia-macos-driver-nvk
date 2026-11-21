#!/usr/bin/env python3
"""Exercise the native rotation mode contract with actual IOGraphics structures."""
from test_support import FB, method, run

names = ['void NVRMNVDAFramebuffer::dtSurfaceSize(', 'static void dtRasterTurned(',
         'bool NVRMNVDAFramebuffer::dtIsCurrent(', 'bool NVRMNVDAFramebuffer::dtIsBootRaster(',
         'void NVRMNVDAFramebuffer::dtToKapi(', 'IOReturn NVRMNVDAFramebuffer::getInformationForDisplayMode(',
         'IOReturn NVRMNVDAFramebuffer::getTimingInfoForDisplayMode(',
         'IOReturn NVRMNVDAFramebuffer::validateDetailedTiming(',
         'IOReturn NVRMNVDAFramebuffer::setDetailedTimings(',
         'IOReturn NVRMNVDAFramebuffer::getCurrentDisplayMode(',
         'IOReturn NVRMNVDAFramebuffer::getPixelInformation(',
         'IOReturn NVRMNVDAFramebuffer::setDisplayMode(']
methods = '\n'.join(method(FB, name) for name in names)
enumeration = '\n'.join(method(FB, name).replace(' override', '') for name in [
    'IOItemCount getDisplayModeCount()', 'IOReturn getDisplayModes(', 'bool listBootMode() const'])
fixture = r'''
#include <IOKit/graphics/IOGraphicsTypes.h>
#include <IOKit/IOReturn.h>
#include <cassert>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <vector>
#include <initializer_list>
#include "nvrm-rotation.h"
#include "nvrm-fb-safety.h"
#define detailedTimingModeID __reservedA[0]
using NvU32=uint32_t;using NvU64=uint64_t;
enum {kModeId=1};
#define FBLOG(...) ((void)0)
static void nvu_zero(void *p,size_t n){memset(p,0,n);}
static void nvu_copy(void *p,const void *q,size_t n){memcpy(p,q,n);}
struct OSObject{virtual ~OSObject(){};};
struct OSData:OSObject{
 std::vector<unsigned char> bytes;
 explicit OSData(const IODetailedTimingInformationV2 &d):bytes(sizeof d){memcpy(bytes.data(),&d,sizeof d);}
 size_t getLength(){return bytes.size();}const void *getBytesNoCopy(){return bytes.data();}
};
struct OSArray:OSObject{std::vector<OSData> values;unsigned getCount(){return values.size();}
 OSObject *getObject(unsigned i){return &values.at(i);}};
#define OSDynamicCast(C,p) dynamic_cast<C *>(p)
struct NvKmsKapiDisplayModeTimings{
 NvU32 pixelClockHz=0,hVisible=0,hSyncStart=0,hSyncEnd=0,hTotal=0,vVisible=0,vSyncStart=0,vSyncEnd=0,vTotal=0,refreshRate=0;
 struct {bool hSyncPos=false,hSyncNeg=false,vSyncPos=false,vSyncNeg=false;}flags;
 };
struct NvKmsKapiDisplayMode{NvKmsKapiDisplayModeTimings timings;};
struct Kms{bool allowed=true;bool validateDisplayMode(void *,unsigned,NvKmsKapiDisplayMode *){return allowed;}};
struct NVRMNVDAFramebuffer{
 bool fRotationEnabled=true,fModeSet=true,fBootInDT=false;unsigned fIndex=0,fNDT=0,fValidateCalls=0,fValidateOk=0,fEdidHits=0;
 bool fHdrActive=false,fVrrSupported=false;
 unsigned fVrrMinHz=0,fVrrMaxHz=0;
 bool hdrAllowed() const {return false;}
 static const unsigned kMaxMacTimings=224;
 IODetailedTimingInformationV2 fDT[256]{},fCurDT{};
 IODisplayModeID fCurId=1;UInt32 fTransform=0,fCurTransform=0;
 NvU32 fW=1920,fH=1080,fRasterW=1920,fRasterH=1080,fBootW=1920,fBootH=1080,fScalerMaxW=7680,fScalerMaxH=4320;
 NvU32 fPitch=7680,fBootPitch=7680,fPitchAlign=256;
 UInt16 fEdidMmW=480,fEdidMmH=270;NvKmsKapiDisplayMode fMode{},fBootMode{};Kms kms,*fKms=&kms;
 void *fDev=this;unsigned fDisplay=1,publishes=0,switches=0,audioRefreshes=0;
 void dtSurfaceSize(const IODetailedTimingInformationV2 *,NvU32 *,NvU32 *)const;
 bool dtIsCurrent(const IODetailedTimingInformationV2 &)const;bool dtIsBootRaster(const IODetailedTimingInformationV2 &)const;
 static void dtToKapi(const IODetailedTimingInformationV2 *,NvKmsKapiDisplayMode *);
 IOReturn getInformationForDisplayMode(IODisplayModeID,IODisplayModeInformation *);
 IOReturn getTimingInfoForDisplayMode(IODisplayModeID,IOTimingInformation *);
 IOReturn validateDetailedTiming(void *,IOByteCount);IOReturn setDetailedTimings(OSArray *);
 IOReturn getCurrentDisplayMode(IODisplayModeID *,IOIndex *);IOReturn setDisplayMode(IODisplayModeID,IOIndex);
 IOReturn getPixelInformation(IODisplayModeID,IOIndex,IOPixelAperture,IOPixelInformation *);
 int dtIndex(IODisplayModeID id)const{for(unsigned i=0;i<fNDT;i++)if((IODisplayModeID)fDT[i].detailedTimingModeID==id)return i;return -1;}
 std::vector<IODetailedTimingInformationV2> extraTimings;
 unsigned addKmsRefreshModes(unsigned n){for(const auto &d:extraTimings){if(n==256)break;fDT[n++]=d;}return n;}
 @@ENUMERATION@@
 void setProperty(const char *,OSObject *){}void setProperty(const char *,unsigned long long,unsigned){}
 void removeProperty(const char *){}void publishRotatedSizes(){}void publishStartupTiming(){++publishes;}
 void publishCursorGeometry(const IODetailedTimingInformationV2 *){}
 void refreshDisplayAudioProperties(){++audioRefreshes;}
 bool edidModeFor(const IODetailedTimingInformationV2 *,NvKmsKapiDisplayMode *){return false;}
 bool applyMode(){return true;}
 IOReturn switchMode(IODisplayModeID id,const NvKmsKapiDisplayMode &mode,NvU32 w,NvU32 h,UInt32 transform,bool hdr=false){
  fHdrActive=hdr;
  ++switches;fCurId=id;fMode=mode;fW=w;fH=h;fRasterW=mode.timings.hVisible;fRasterH=mode.timings.vVisible;fCurTransform=transform;return 0;
 }
};
@@METHODS@@
static IODetailedTimingInformationV2 timing(unsigned w,unsigned h,unsigned hz,unsigned transform,unsigned id){
 IODetailedTimingInformationV2 d{};d.horizontalActive=w;d.verticalActive=h;
 d.horizontalBlanking=160;d.verticalBlanking=45;d.horizontalSyncOffset=32;d.horizontalSyncPulseWidth=32;
 d.verticalSyncOffset=3;d.verticalSyncPulseWidth=5;d.horizontalSyncConfig=1;d.verticalSyncConfig=1;
 d.pixelClock=(uint64_t)(w+160)*(h+45)*hz;d.detailedTimingModeID=id;d.scalerFlags=transform;
 if(transform){d.horizontalScaled=(transform&kIOScaleSwapAxes)?h:w;d.verticalScaled=(transform&kIOScaleSwapAxes)?w:h;}
 return d;
}
static void adopt(NVRMNVDAFramebuffer &f,const IODetailedTimingInformationV2 &d){
 f.fNDT=1;f.fDT[0]=d;assert(f.setDisplayMode(d.detailedTimingModeID,0)==0);
}
static void stableCurrent(NVRMNVDAFramebuffer &f,IODisplayModeID expected){
 const unsigned switches=f.switches;const auto saved=f.fCurDT;
 for(unsigned query=0;query<32;++query){
  IODisplayModeID reported=0;IOIndex depth=99;
  assert(f.getCurrentDisplayMode(&reported,&depth)==0&&reported==expected&&depth==0);
  IOTimingInformation ti{};assert(f.getTimingInfoForDisplayMode(reported,&ti)==0);
  assert(ti.flags&kIODetailedTimingValid);
  assert((IODisplayModeID)ti.detailedInfo.v2.detailedTimingModeID==reported);
  assert(f.dtIsCurrent(ti.detailedInfo.v2));
  IODisplayModeInformation mi{};assert(f.getInformationForDisplayMode(reported,&mi)==0);
  assert(mi.refreshRate&&mi.nominalWidth==(saved.horizontalScaled?saved.horizontalScaled:saved.horizontalActive));
  assert(mi.nominalHeight==(saved.verticalScaled?saved.verticalScaled:saved.verticalActive));
  IOPixelInformation pi{};assert(f.getPixelInformation(reported,depth,kIOFBSystemAperture,&pi)==0);
  assert(pi.activeWidth==mi.nominalWidth&&pi.activeHeight==mi.nominalHeight);
  assert(f.getCurrentDisplayMode(nullptr,nullptr)==0);
 }
 assert(f.switches==switches&&f.fCurId==expected&&memcmp(&f.fCurDT,&saved,sizeof saved)==0);
}
int main(){
 unsigned cases=0;
 for(auto size:{std::pair<unsigned,unsigned>{1280,720},{1920,1080},{2560,1440},{3440,1440},{3840,2160},{5120,1440},{7680,4320}})
 for(unsigned transform:{0u,0x30u,0x60u,0x50u}){
  NVRMNVDAFramebuffer f;f.fBootW=size.first;f.fBootH=size.second;f.fTransform=transform;
  auto d=timing(size.first,size.second,size.first==7680?24:75,transform,0x1234);
  IOFBDisplayModeDescription desc{};desc.timingInfo.detailedInfo.v2=d;
  assert(f.validateDetailedTiming(&desc,sizeof desc)==0);
  assert(desc.info.flags&kDisplayModeAcceleratorBackedFlag);
  adopt(f,d);assert(f.fW==size.first&&f.fH==size.second&&f.fCurTransform==transform);
  assert(f.dtIsCurrent(d));auto other=d;other.verticalBlanking++;assert(!f.dtIsCurrent(other));
  other=d;other.horizontalSyncOffset++;assert(!f.dtIsCurrent(other));
  other=d;other.scalerFlags^=kIOScaleInvertX;assert(!f.dtIsCurrent(other));
  if(transform){other=d;other.horizontalScaled--;assert(!f.dtIsCurrent(other));}
  IOTimingInformation info{};assert(f.getTimingInfoForDisplayMode(d.detailedTimingModeID,&info)==0);
  assert(info.detailedInfo.v2.horizontalActive==size.first&&info.detailedInfo.v2.verticalActive==size.second);
  auto rebuilt=d;rebuilt.detailedTimingModeID=0x9999;OSArray array;array.values.emplace_back(rebuilt);
  unsigned switches=f.switches;assert(f.setDetailedTimings(&array)==0);
  stableCurrent(f,0x9999);assert(f.switches==switches&&f.fCurDT.detailedTimingModeID==0x9999);
  // The previous ID remains a usable alias without becoming the current ID.
  IOTimingInformation alias{};assert(f.getTimingInfoForDisplayMode(0x1234,&alias)==0);
  assert(alias.detailedInfo.v2.detailedTimingModeID==0x1234&&f.dtIsCurrent(alias.detailedInfo.v2));
  unsigned audio=f.audioRefreshes;
  assert(f.setDisplayMode(0x9999,0)==0&&f.switches==switches&&f.audioRefreshes==audio+1);
  // Another replacement, then an unchanged list, must not revive an older ID.
  rebuilt.detailedTimingModeID=0xaaaa;OSArray again;again.values.emplace_back(rebuilt);
  assert(f.setDetailedTimings(&again)==0);stableCurrent(f,0xaaaa);
  assert(f.getTimingInfoForDisplayMode(0x9999,&alias)==0&&f.dtIsCurrent(alias.detailedInfo.v2));
  assert(f.setDetailedTimings(&again)==0);stableCurrent(f,0xaaaa);
  audio=f.audioRefreshes;
  assert(f.setDisplayMode(0xaaaa,1)==kIOReturnUnsupportedMode&&f.audioRefreshes==audio);stableCurrent(f,0xaaaa);
  ++cases;
 }
 // Native unrotated timings must produce a complete rotated copy, not mirror-only synthetic modes.
 NVRMNVDAFramebuffer portrait;portrait.fTransform=0x30;
 auto d=timing(1920,1080,144,0,0x10000);portrait.fNDT=1;portrait.fDT[0]=d;
 IOTimingInformation ti{};assert(portrait.getTimingInfoForDisplayMode(0x10000,&ti)==0);
 assert((ti.flags&kIOScalingInfoValid)&&ti.detailedInfo.v2.horizontalScaled==1920&&ti.detailedInfo.v2.verticalScaled==1080);
 IODisplayModeInformation mi{};assert(portrait.getInformationForDisplayMode(0x10000,&mi)==0);
 assert(mi.flags&kDisplayModeAcceleratorBackedFlag);
 // Malformed/unknown transforms and non-full rotated desktops remain refused.
 auto invalid=timing(1920,1080,75,0x30,0x10000);invalid.horizontalScaled--;
 assert(portrait.validateDetailedTiming(&invalid,sizeof invalid)==kIOReturnUnsupportedMode);
 invalid=timing(1920,1080,75,0x10,0x10000);
 assert(portrait.validateDetailedTiming(&invalid,sizeof invalid)==kIOReturnUnsupportedMode);
 invalid=timing(1920,1080,75,0x30,0x10000);invalid.horizontalScaled=0;
 assert(portrait.validateDetailedTiming(&invalid,sizeof invalid)==kIOReturnUnsupportedMode);
 // More than 64 modes from macOS survive; reserve entries beyond 224 for driver/active aliases.
 OSArray many;for(unsigned i=0;i<240;++i)many.values.emplace_back(timing(1920,1080,60,0,100+i));
 assert(portrait.setDetailedTimings(&many)==0&&portrait.fNDT==224);
 // Disabled/non-Tahoe behavior retains the original physical/scaled sizes and flags.
 NVRMNVDAFramebuffer disabled;disabled.fRotationEnabled=false;
 auto scaled=timing(1920,1080,60,0,10);scaled.horizontalScaled=2560;scaled.verticalScaled=1440;
 disabled.fNDT=1;disabled.fDT[0]=scaled;assert(disabled.setDisplayMode(10,0)==0);
 assert(disabled.fW==2560&&disabled.fH==1440);
 assert(disabled.getInformationForDisplayMode(10,&mi)==0&&!(mi.flags&kDisplayModeAcceleratorBackedFlag));
 assert(disabled.validateDetailedTiming(&scaled,sizeof scaled)==0);
 scaled.horizontalScaled=5000;assert(disabled.validateDetailedTiming(&scaled,sizeof scaled)==kIOReturnUnsupportedMode);
 auto rotated=timing(1920,1080,60,0x30,11);
 assert(disabled.validateDetailedTiming(&rotated,sizeof rotated)==kIOReturnUnsupportedMode);
 // One head's mode rebuild cannot overwrite another head's mode, transform or physical size.
 NVRMNVDAFramebuffer first,second;adopt(first,timing(1920,1080,75,0x30,8));adopt(second,timing(2560,1440,165,0,9));
 auto saved=second.fCurDT;OSArray change;change.values.emplace_back(timing(1920,1080,75,0x30,44));first.setDetailedTimings(&change);
 assert(second.fCurId==9&&!second.fCurTransform&&memcmp(&saved,&second.fCurDT,sizeof saved)==0);
 // A reused previous ID must continue to describe its new timing, not be shadowed by an alias.
 NVRMNVDAFramebuffer reused;auto active=timing(1920,1080,75,0x60,0x80002001);
 adopt(reused,active);OSArray reuse;reuse.values.emplace_back(timing(1920,1080,60,0,0x80002001));
 auto renumbered=active;renumbered.detailedTimingModeID=0x80003000;reuse.values.emplace_back(renumbered);
 assert(reused.setDetailedTimings(&reuse)==0&&reused.fNDT==2);stableCurrent(reused,0x80003000);
 assert(reused.fDT[reused.dtIndex(0x80002001)].pixelClock!=active.pixelClock);
 assert(reused.setDisplayMode(0x80002001,0)==0&&reused.fCurTransform==0&&reused.fMode.timings.refreshRate==60000);
 // If the active timing disappears, preserve it rather than silently report the boot timing.
 NVRMNVDAFramebuffer missing;auto boot=timing(1920,1080,60,0,0x80001000);
 NVRMNVDAFramebuffer::dtToKapi(&boot,&missing.fBootMode);adopt(missing,active);OSArray absent;
 absent.values.emplace_back(timing(1920,1080,60,0,0x80001000));
 assert(missing.setDetailedTimings(&absent)==0&&missing.fNDT==2);stableCurrent(missing,0x80002001);
 // If that ID is reused too, allocate an unoccupied per-head ID without overwriting supplied entries.
 OSArray collision;collision.values.emplace_back(timing(1920,1080,60,0,0x80002001));
 collision.values.emplace_back(timing(1920,1080,60,0,0x20000));
 collision.values.emplace_back(timing(1920,1080,60,0,0x20001));
 assert(missing.setDetailedTimings(&collision)==0&&missing.fNDT==4);stableCurrent(missing,0x20002);
 assert(missing.fDT[missing.dtIndex(0x80002001)].pixelClock!=active.pixelClock);
 OSArray empty;assert(missing.setDetailedTimings(&empty)==0&&missing.fNDT==1);stableCurrent(missing,0x20002);
 // Clearing the list keeps the active identity; enumeration republishes it with complete metadata.
 assert(missing.setDetailedTimings(nullptr)==0&&missing.fNDT==0&&missing.fCurId==0x20002);
 std::vector<IODisplayModeID> listed(missing.getDisplayModeCount());
 assert(!listed.empty()&&missing.getDisplayModes(listed.data())==0);
 unsigned activeEntries=0;for(auto id:listed)if(id==0x20002)++activeEntries;
 assert(activeEntries==1);stableCurrent(missing,0x20002);
 // Initial boot-mode adoption still uses the firmware timing, without a modeset.
 NVRMNVDAFramebuffer starting;starting.fModeSet=false;starting.fBootMode=missing.fBootMode;
 OSArray initial;initial.values.emplace_back(boot);
 assert(starting.setDetailedTimings(&initial)==0&&starting.fCurId==(IODisplayModeID)boot.detailedTimingModeID);
 assert(starting.fBootInDT&&starting.switches==0);
 // Full generated tables sacrifice one extra NVKMS mode, never a macOS entry or the active mode.
 NVRMNVDAFramebuffer full;adopt(full,active);OSArray packed;
 for(unsigned i=0;i<224;++i)packed.values.emplace_back(timing(1920,1080,60,0,0x80010000+i));
 for(unsigned i=0;i<32;++i)full.extraTimings.push_back(timing(1920,1080,60,0,0x10000+i*256));
 assert(full.setDetailedTimings(&packed)==0&&full.fNDT==256);stableCurrent(full,0x80002001);
 for(unsigned i=0;i<224;++i)assert(full.fDT[i].detailedTimingModeID==0x80010000+i);
 // No alias capacity is needed to adopt an existing matching entry, including the last generated slot.
 NVRMNVDAFramebuffer matchFull;adopt(matchFull,active);matchFull.extraTimings=full.extraTimings;
 matchFull.extraTimings.back()=renumbered;
 assert(matchFull.setDetailedTimings(&packed)==0&&matchFull.fNDT==256);stableCurrent(matchFull,0x80003000);
 // A matching current ID wins over an earlier equivalent ID, and refreshes its saved descriptor.
 OSArray sameId;sameId.values.emplace_back(active);renumbered.minPixelClock=renumbered.pixelClock;
 sameId.values.emplace_back(renumbered);assert(matchFull.setDetailedTimings(&sameId)==0);
 stableCurrent(matchFull,0x80003000);assert(matchFull.fCurDT.minPixelClock==renumbered.pixelClock);
 // All four heads stay independent during both logged Dell sequences (180 and 270 degrees).
 for(unsigned transform:{0x60u,0x50u}){
  NVRMNVDAFramebuffer heads[4];
  for(unsigned i=0;i<4;++i){heads[i].fIndex=i;adopt(heads[i],timing(1920,1080,i==0?240:60,i==2?transform:0,0x80002001));}
  auto target=heads[2].fCurDT;target.detailedTimingModeID=0x80003000;OSArray update;update.values.emplace_back(target);
  assert(heads[2].setDetailedTimings(&update)==0);
  for(unsigned i=0;i<4;++i)stableCurrent(heads[i],i==2?0x80003000:0x80002001);
 }
 printf("PASS: %u native size/refresh/angle cases through 8K; repeat-query metadata, successive rebuilds, aliases/ID reuse, missing/full tables, enumeration and four independent heads\n",cases);
}
'''
run(fixture.replace('@@METHODS@@', methods).replace('@@ENUMERATION@@', enumeration))
