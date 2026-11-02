#!/usr/bin/env python3
"""Run the production preview callbacks against Objective-C rendering doubles."""
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools/rotation'))
from test_support import method

source = (root / 'plugin/NVMTLPhotoBooth.m').read_text()
functions = '\n'.join(method(source, signature) for signature in (
    'static BOOL nvmtl_photobooth_process(',
    'static id nvmtl_photobooth_next_drawable(',
    'static BOOL nvmtl_photobooth_reflection(',
    'static void nvmtl_photobooth_render_preview(',
    'static BOOL nvmtl_photobooth_signature(',
    'static void nvmtl_photobooth_replace(',
))
fixture = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <CoreGraphics/CoreGraphics.h>
#import <objc/runtime.h>
#import <objc/message.h>
#include <assert.h>
#include <math.h>
#include <string.h>

@interface NVMTLTexture : NSObject @end
@implementation NVMTLTexture @end
@interface CIImage : NSObject
@property CGAffineTransform applied;
- (CIImage *)imageByApplyingTransform:(CGAffineTransform)t;
@end
@implementation CIImage
- (CIImage *)imageByApplyingTransform:(CGAffineTransform)t { CIImage *result=[CIImage new];result.applied=t;return result; }
@end
@interface Drawable : NSObject
@property id texture;
@end
@implementation Drawable @end
@interface Layer : NSObject
- (id)nextDrawable;
- (double)wrongReturn;
@end
@implementation Layer
- (id)nextDrawable { return @1; }
- (double)wrongReturn { return 0; }
@end
@interface ChildLayer : Layer @end
@implementation ChildLayer @end
@interface Context : NSObject
- (void)render:(CIImage *)i texture:(id)t command:(id)c bounds:(CGRect)b colorSpace:(CGColorSpaceRef)cs;
@end
@implementation Context
- (void)render:(CIImage *)i texture:(id)t command:(id)c bounds:(CGRect)b colorSpace:(CGColorSpaceRef)cs
 { (void)i;(void)t;(void)c;(void)b;(void)cs; }
@end

static id nextResult, receivedImage, receivedTexture, receivedCommand;
static CGRect receivedBounds;
static CGColorSpaceRef receivedColorSpace;
static id fakeNext(id layer, SEL selector) { (void)layer;(void)selector;return nextResult; }
static void fakeRender(id ctx,SEL sel,CIImage *i,id<MTLTexture> t,id c,CGRect b,CGColorSpaceRef cs)
 { (void)ctx;(void)sel;receivedImage=i;receivedTexture=t;receivedCommand=c;receivedBounds=b;receivedColorSpace=cs; }
static char nvmtl_photobooth_drawable_key;
static id (*nvmtl_photobooth_next)(id,SEL)=fakeNext;
static void (*nvmtl_photobooth_render)(id,SEL,CIImage*,id<MTLTexture>,id,CGRect,CGColorSpaceRef)=fakeRender;
@@FUNCTIONS@@
int main(void) { @autoreleasepool {
 assert(nvmtl_photobooth_process("Photo Booth",@"com.apple.PhotoBooth"));
 assert(!nvmtl_photobooth_process("OBS",@"com.apple.PhotoBooth"));
 assert(!nvmtl_photobooth_process("WindowServer",@"com.apple.PhotoBooth"));
 assert(!nvmtl_photobooth_process("Photo Booth",@"com.example.other"));
 assert(!nvmtl_photobooth_process(NULL,@"com.apple.PhotoBooth"));
 assert(!nvmtl_photobooth_process("Photo Booth",nil));
 CGRect b={{13,17},{100,40}};CGAffineTransform tr;
 NVMTLTexture *offscreen=[NVMTLTexture new],*preview=[NVMTLTexture new];
 NSObject *foreign=[NSObject new];Drawable *drawable=[Drawable new];
 nextResult=drawable;drawable.texture=foreign;
 assert(nvmtl_photobooth_next_drawable(nil,NULL)==drawable);
 assert(!nvmtl_photobooth_reflection(foreign,b,&tr));
 drawable.texture=preview;assert(nvmtl_photobooth_next_drawable(nil,NULL)==drawable);
 assert(nvmtl_photobooth_reflection(preview,b,&tr));
 assert(tr.a==1 && tr.b==0 && tr.c==0 && tr.d==-1 && tr.tx==0 && tr.ty==74);
 assert(tr.d*17+tr.ty==57 && tr.d*57+tr.ty==17);
 assert(!nvmtl_photobooth_reflection(offscreen,b,&tr));
 assert(!nvmtl_photobooth_reflection(nil,b,&tr));
 CGRect invalid[]={{{0,0},{0,40}},{{0,0},{1,-1}},{{0,NAN},{1,1}},{{0,0},{INFINITY,1}},{{0,DBL_MAX},{1,DBL_MAX}}};
 for(unsigned i=0;i<sizeof invalid/sizeof *invalid;i++)assert(!nvmtl_photobooth_reflection(preview,invalid[i],&tr));
 CIImage *image=[CIImage new];NSObject *command=[NSObject new];
 CGColorSpaceRef colorSpace=(CGColorSpaceRef)(uintptr_t)1;
 nvmtl_photobooth_render_preview(nil,NULL,image,(id)preview,command,b,colorSpace);
 assert(receivedImage!=image && [receivedImage applied].d==-1 && [receivedImage applied].ty==74);
 assert(receivedTexture==preview && receivedCommand==command && receivedColorSpace==colorSpace);
 assert(!memcmp(&receivedBounds,&b,sizeof b));
 nvmtl_photobooth_render_preview(nil,NULL,image,(id)offscreen,command,b,colorSpace);
 assert(receivedImage==image);
 nvmtl_photobooth_render_preview(nil,NULL,image,(id)foreign,command,b,colorSpace);
 assert(receivedImage==image);
 nvmtl_photobooth_render_preview(nil,NULL,nil,(id)preview,command,b,colorSpace);
 assert(receivedImage==nil);
 nextResult=nil;assert(nvmtl_photobooth_next_drawable(nil,NULL)==nil);
 Method next=class_getInstanceMethod([Layer class],@selector(nextDrawable));
 Method render=class_getInstanceMethod([Context class],@selector(render:texture:command:bounds:colorSpace:));
 assert(nvmtl_photobooth_signature(next,NO) && nvmtl_photobooth_signature(render,YES));
 assert(!nvmtl_photobooth_signature(NULL,YES));
 assert(!nvmtl_photobooth_signature(next,YES));
 assert(!nvmtl_photobooth_signature(class_getInstanceMethod([Layer class],@selector(wrongReturn)),NO));
 nextResult=drawable;
 nvmtl_photobooth_replace([ChildLayer class],@selector(nextDrawable),next,(IMP)fakeNext);
 assert([[[Layer new]nextDrawable]isEqual:@1] && [[ChildLayer new]nextDrawable]==drawable);
 puts("PASS: Photo Booth identity, preview-only marking, reflection/crop, pass-through arguments, invalid rectangles, API signatures and inherited-method isolation");
}}
'''.replace('@@FUNCTIONS@@', functions)
# Foundation may load Core Image indirectly. Give doubles distinct runtime
# names so the fixture cannot shadow an Apple class in the same process.
fixture = fixture.replace('CIImage', 'NMPhotoBoothProbeImage').replace('NVMTLTexture', 'NMPhotoBoothProbeTexture')

with tempfile.TemporaryDirectory(prefix='nm-photobooth-') as directory:
    p = Path(directory)
    (p / 'probe.m').write_text(fixture)
    subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-O1', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=all', str(p / 'probe.m'),
                    '-framework', 'Foundation', '-o', str(p / 'probe')], check=True)
    subprocess.run([str(p / 'probe')], check=True, timeout=30)
