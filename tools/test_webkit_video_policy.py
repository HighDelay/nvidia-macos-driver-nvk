#!/usr/bin/env python3
"""Exercise Safari's video/filter adapter with the real WebKit preferences API.

No driver installation, user preference writes, video playback or network access.
The test loads the production adapter into this process only.
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "plugin/NVMTLWebKit.m"

LIBRARY = r'''
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#import <objc/message.h>
#include <stdint.h>
#include <syslog.h>
static void nvlog(const char *format, ...) { (void)format; }
#include "NVMTLWebKit.m"
void nm_webkit_test_install(void) { nvmtl_webkit_install_on_main(); }
BOOL nm_webkit_test_process(const char *name, NSString *bundle) { return nvmtl_webkit_process(name, bundle); }
BOOL nm_webkit_test_signature(Method method, const char *type, NSArray *args) { return nvmtl_webkit_signature(method, type, args); }
'''

PROBE = r'''
#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>
#import <objc/runtime.h>
#import <objc/message.h>
#include <assert.h>
#include <dlfcn.h>
#include <stdbool.h>
static id feature(NSString *key) {
 for(id f in ((id(*)(id,SEL))objc_msgSend)([WKPreferences class],sel_registerName("_features")))
  if([((id(*)(id,SEL))objc_msgSend)(f,sel_registerName("key")) isEqual:key]) return f;
 return nil;
}
static BOOL enabled(id prefs,id f) {
 return ((BOOL(*)(id,SEL,id))objc_msgSend)(prefs,sel_registerName("_isEnabledForFeature:"),f);
}
static void set(id prefs,id f,BOOL value) {
 ((void(*)(id,SEL,BOOL,id))objc_msgSend)(prefs,sel_registerName("_setEnabled:forFeature:"),value,f);
}
@interface NMUnrelatedConfiguration : NSObject
- (id)preferences;
@end
@implementation NMUnrelatedConfiguration
- (id)preferences { return @1; }
@end
int main(int argc,char **argv) { @autoreleasepool {
 assert(argc==4); [NSApplication sharedApplication];
 BOOL video=atoi(argv[2]),filters=atoi(argv[3]);
 id mode=feature(@"MediaSourcePrefersDecompressionSession");
 id filterMode=feature(@"GraphicsContextFiltersEnabled");
 id protectedMode=feature(@"VideoRendererUseDecompressionSessionForProtected");
 assert(mode&&filterMode&&protectedMode);
 WKWebViewConfiguration *before=[WKWebViewConfiguration new];
 BOOL nativeVideo=enabled(before.preferences,mode),nativeFilters=enabled(before.preferences,filterMode);
 NSArray *unchangedKeys=@[@"UseGPUProcessForWebGLEnabled",@"UseGPUProcessForCanvasRenderingEnabled",@"UseGPUProcessForDOMRenderingEnabled",@"WebGLEnabled",@"AcceleratedFiltersEnabled"];
 NSMutableArray *unchangedValues=[NSMutableArray new];
 for(NSString *key in unchangedKeys){id f=feature(key);assert(f);[unchangedValues addObject:@(enabled(before.preferences,f))];}
 set(before.preferences,mode,NO);
 set(before.preferences,filterMode,YES);
 BOOL originalProtected=enabled(before.preferences,protectedMode);
 before.preferences.minimumFontSize=17;
 WKWebView *view=[[WKWebView alloc]initWithFrame:NSMakeRect(0,0,320,200) configuration:before];
 NSWindow *window=[[NSWindow alloc]initWithContentRect:NSMakeRect(0,0,320,200) styleMask:NSWindowStyleMaskTitled backing:NSBackingStoreBuffered defer:NO];
 window.releasedWhenClosed=NO;window.contentView=view;
 assert(!enabled(view.configuration.preferences,mode));
 NSDictionary *defaultsBefore=[[NSUserDefaults standardUserDefaults]dictionaryRepresentation];
 void *library=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL);assert(library);
 void(*install)(void)=dlsym(library,"nm_webkit_test_install");
 BOOL(*process)(const char*,NSString*)=dlsym(library,"nm_webkit_test_process");
 BOOL(*signature)(Method,const char*,NSArray*)=dlsym(library,"nm_webkit_test_signature");
 assert(install&&process&&signature);
 assert(process("Safari",@"com.apple.Safari"));
 assert(!process("Safari",@"org.example.Other"));
 assert(!process("WindowServer",@"com.apple.Safari"));
 assert(!process("com.apple.WebKit.GPU",@"com.apple.Safari"));
 assert(!process("QuickTime Player",@"com.apple.QuickTimePlayerX"));
 assert(!process("Microsoft Edge Helper (GPU)",@"com.apple.Safari"));
 assert(!process(NULL,@"com.apple.Safari") && !process("Safari",nil));
 assert(signature(class_getInstanceMethod([WKWebViewConfiguration class],@selector(preferences)),@encode(id),@[]));
 assert(!signature(class_getInstanceMethod([WKWebViewConfiguration class],@selector(setPreferences:)),@encode(id),@[]));
 assert(!signature(class_getInstanceMethod([WKPreferences class],@selector(minimumFontSize)),@encode(id),@[]));
 assert(!signature(NULL,@encode(id),@[]));
 install();
 install(); // Repeated initialization must not recursively hook itself.
 assert(enabled(before.preferences,mode)==video);
 assert(enabled(view.configuration.preferences,mode)==video);
 assert(enabled(before.preferences,filterMode)==!filters);
 assert(enabled(view.configuration.preferences,filterMode)==!filters);
 assert(before.preferences==view.configuration.preferences);
 assert(enabled(before.preferences,protectedMode)==originalProtected);
 assert(before.preferences.minimumFontSize==17);
 assert([defaultsBefore isEqual:[[NSUserDefaults standardUserDefaults]dictionaryRepresentation]]);
 WKWebViewConfiguration *after=[WKWebViewConfiguration new];
 assert(enabled(after.preferences,mode)==(video?YES:nativeVideo));
 assert(enabled(after.preferences,filterMode)==(filters?NO:nativeFilters));
 WKPreferences *replacement=[WKPreferences new];
 set(replacement,mode,NO);replacement.minimumFontSize=23;
 set(replacement,filterMode,YES);
 after.preferences=replacement;
 assert(enabled(replacement,mode)==video && after.preferences==replacement);
 assert(enabled(replacement,filterMode)==!filters);
 assert(after.preferences.minimumFontSize==23);
 assert(enabled(replacement,protectedMode)==originalProtected);
 ((void(*)(id,SEL,id))objc_msgSend)(after,@selector(setPreferences:),nil); // Preserve WebKit's lazy creation contract.
 assert(enabled(after.preferences,mode)==(video?YES:nativeVideo));
 assert(enabled(after.preferences,filterMode)==(filters?NO:nativeFilters));
 assert([[[NMUnrelatedConfiguration new]preferences]isEqual:@1]);

 // Named C-API preferences normally autosave setters. The adapter must use
 // WebKit's ephemeral update, including when a configuration receives one.
 const char *path=class_getImageName([WKPreferences class]);
 void *webkit=dlopen(path,RTLD_LAZY|RTLD_LOCAL|RTLD_NOLOAD);assert(webkit);
 const void *(*string)(const char*)=dlsym(webkit,"WKStringCreateWithUTF8CString");
 const void *(*create)(const void*)=dlsym(webkit,"WKPreferencesCreateWithIdentifier");
 void(*release)(const void*)=dlsym(webkit,"WKRelease");
 assert(string&&create&&release);
 NSString *identifier=[@"NMVideoPolicy" stringByAppendingString:[NSUUID UUID].UUIDString];
 const void *key=string(identifier.UTF8String),*ref=create(key);assert(ref);
 id named=(__bridge id)ref;assert([named isKindOfClass:[WKPreferences class]]);
 NSDictionary *namedBefore=[[NSUserDefaults standardUserDefaults]dictionaryRepresentation];
 after.preferences=named;
 assert(enabled(after.preferences,mode)==(video?YES:nativeVideo));
 assert(enabled(after.preferences,filterMode)==(filters?NO:nativeFilters));
 assert([namedBefore isEqual:[[NSUserDefaults standardUserDefaults]dictionaryRepresentation]]);
 for(NSUInteger i=0;i<unchangedKeys.count;i++){
  id f=feature(unchangedKeys[i]);BOOL expected=[unchangedValues[i]boolValue];
  assert(enabled(before.preferences,f)==expected);
  assert(enabled(view.configuration.preferences,f)==expected);
  assert(enabled(after.preferences,f)==expected);
 }
 release(ref);release(key);dlclose(webkit);
 [window close];
 // Runtime methods still point into this image after the caller closes it.
 dlclose(library);
 assert(enabled([WKWebViewConfiguration new].preferences,mode)==(video?YES:nativeVideo));
 assert(enabled([WKWebViewConfiguration new].preferences,filterMode)==(filters?NO:nativeFilters));
 printf("PASS: video=%d filters=%d; API signatures, Safari-only identity, live/new/copied/nil/replaced preferences, idempotency, protected-content flag, WebGL/GPU-process settings, independent opt-outs, unrelated classes, ephemeral named preferences and pinned hook lifetime\n",video,filters);
}return 0;}
'''


with tempfile.TemporaryDirectory(prefix="nvmtl-webkit-policy-") as directory:
    root = Path(directory)
    (root / "adapter.m").write_text(LIBRARY)
    (root / "probe.m").write_text(PROBE)
    common = ["xcrun", "clang", "-arch", "x86_64", "-mmacosx-version-min=15.0", "-fobjc-arc", "-O1",
              "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
              "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
    subprocess.run(common + ["-dynamiclib", "-I" + str(ADAPTER.parent), str(root / "adapter.m"),
                            "-framework", "Foundation", "-o", str(root / "adapter.dylib")], check=True)
    subprocess.run(common + [str(root / "probe.m"), "-framework", "Cocoa", "-framework", "WebKit",
                            "-o", str(root / "probe")], check=True)
    for video, filters in [(True, True), (False, True), (True, False), (False, False)]:
        env = dict(os.environ)
        # The installed release driver can be loaded by AppKit before our test
        # adapter. Keep this probe's explicit opt-outs through its env cleanup.
        env["NVMTL_PROCESS_ENV"] = "1"
        for name, enabled in [("NVMTL_WEBKIT_STABLE_VIDEO", video), ("NVMTL_WEBKIT_FILTERS", filters)]:
            env.pop(name, None)
            if not enabled:
                env[name] = "0"
        subprocess.run([str(root / "probe"), str(root / "adapter.dylib"), str(int(video)), str(int(filters))],
                       env=env, check=True, timeout=45)
