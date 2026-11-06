/*
 * NullMoth NVIDIA driver for macOS
 * SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
 */

#include <stdbool.h>
#include <dlfcn.h>

// Intel WebKit defaults MediaSourcePrefersDecompressionSession to false. Its
// software VP9 path nevertheless creates a decompression session. Switching
// between a display layer and renderless output then repeatedly flushes that
// session and stops the shared A/V clock, even with ample buffered media.
// Select WebKit's existing decoded-frame-preserving path for NVMTL Safari.
// This does not register codecs, choose a decoder, bypass protected-content
// handling, install script callbacks, or alter persisted Safari preferences.
static Class nvmtl_webkit_preferences_class;
static id nvmtl_webkit_video_feature;
static const void *nvmtl_webkit_video_key;
static void (*nvmtl_webkit_set_ephemeral_bool)(const void *, bool, const void *);
static uint64_t (*nvmtl_webkit_type_id)(const void *);
static uint64_t nvmtl_webkit_preferences_type_id;
static id (*nvmtl_webkit_get_preferences)(id, SEL);
static void (*nvmtl_webkit_set_preferences)(id, SEL, id);

static BOOL nvmtl_webkit_process(const char *name, NSString *bundle) {
    return name && !strcmp(name, "Safari") && [bundle isEqualToString:@"com.apple.Safari"];
}

static BOOL nvmtl_webkit_signature(Method method, const char *result, NSArray<NSString *> *arguments) {
    if (!method) return NO;
    NSMethodSignature *sig = [NSMethodSignature signatureWithObjCTypes:method_getTypeEncoding(method)];
    if (strcmp(sig.methodReturnType, result) || sig.numberOfArguments != arguments.count + 2) return NO;
    for (NSUInteger i = 0; i < arguments.count; ++i)
        if (strcmp([sig getArgumentTypeAtIndex:i + 2], arguments[i].UTF8String)) return NO;
    return YES;
}

static void nvmtl_webkit_apply(id preferences) {
    if (![NSThread isMainThread] || ![preferences isKindOfClass:nvmtl_webkit_preferences_class]) return;
    const void *ref = (__bridge const void *)preferences;
    if (nvmtl_webkit_type_id(ref) != nvmtl_webkit_preferences_type_id) return;
    SEL enabled = sel_registerName("_isEnabledForFeature:");
    if (((BOOL (*)(id, SEL, id))objc_msgSend)(preferences, enabled, nvmtl_webkit_video_feature)) return;
    // This exported WebKit API's final argument selects a key, not a memory
    // offset. It updates live pages with ephemeral=true, including preferences
    // with an autosave identifier. The regular feature setter can persist it.
    nvmtl_webkit_set_ephemeral_bool(ref, true, nvmtl_webkit_video_key);
}

static id nvmtl_webkit_preferences(id configuration, SEL selector) {
    id preferences = nvmtl_webkit_get_preferences(configuration, selector);
    nvmtl_webkit_apply(preferences);
    return preferences;
}

static void nvmtl_webkit_assign_preferences(id configuration, SEL selector, id preferences) {
    nvmtl_webkit_apply(preferences);
    nvmtl_webkit_set_preferences(configuration, selector, preferences);
}

static void nvmtl_webkit_visit_view(id view, Class webView) {
    if ([view isKindOfClass:webView]) {
        id configuration = ((id (*)(id, SEL))objc_msgSend)(view, sel_registerName("configuration"));
        // Configuration copies share their WebPreferences. The API update
        // therefore also reaches views created before NVMTL's first device.
        ((id (*)(id, SEL))objc_msgSend)(configuration, sel_registerName("preferences"));
        return;
    }
    if ([view respondsToSelector:sel_registerName("subviews")])
        for (id child in ((id (*)(id, SEL))objc_msgSend)(view, sel_registerName("subviews")))
            nvmtl_webkit_visit_view(child, webView);
}

static void nvmtl_webkit_install_on_main(void) {
    Class preferences = objc_getClass("WKPreferences"), configuration = objc_getClass("WKWebViewConfiguration");
    Class webView = objc_getClass("WKWebView"), appClass = objc_getClass("NSApplication");
    SEL features = sel_registerName("_features"), getter = sel_registerName("preferences");
    SEL setter = sel_registerName("setPreferences:");
    Method getMethod = class_getInstanceMethod(configuration, getter);
    Method setMethod = class_getInstanceMethod(configuration, setter);
    if (!webView || !appClass ||
        !nvmtl_webkit_signature(class_getClassMethod(preferences, features), @encode(id), @[]) ||
        !nvmtl_webkit_signature(class_getInstanceMethod(preferences, sel_registerName("_isEnabledForFeature:")), @encode(BOOL), @[@"@"]) ||
        !nvmtl_webkit_signature(getMethod, @encode(id), @[]) ||
        !nvmtl_webkit_signature(setMethod, @encode(void), @[@"@"])) return;

    id feature = nil;
    for (id candidate in ((id (*)(id, SEL))objc_msgSend)(preferences, features)) {
        if (!nvmtl_webkit_signature(class_getInstanceMethod([candidate class], sel_registerName("key")), @encode(id), @[])) continue;
        id key = ((id (*)(id, SEL))objc_msgSend)(candidate, sel_registerName("key"));
        if ([key isKindOfClass:[NSString class]] && [key isEqualToString:@"MediaSourcePrefersDecompressionSession"]) {
            feature = candidate;
            break;
        }
    }
    if (!feature) return; // An older or changed WebKit keeps its own behavior.

    const char *path = class_getImageName(preferences);
    void *webkit = path ? dlopen(path, RTLD_LAZY | RTLD_LOCAL | RTLD_NOLOAD) : NULL;
    if (!webkit) return;
    void (*setBool)(const void *, bool, const void *) = dlsym(webkit, "WKPreferencesSetBoolValueForKeyForTesting");
    const void *(*makeString)(const char *) = dlsym(webkit, "WKStringCreateWithUTF8CString");
    uint64_t (*typeID)(const void *) = dlsym(webkit, "WKGetTypeID");
    uint64_t (*preferencesTypeID)(void) = dlsym(webkit, "WKPreferencesGetTypeID");
    const void *key = setBool && makeString && typeID && preferencesTypeID ? makeString("MediaSourcePrefersDecompressionSession") : NULL;
    if (!key) { dlclose(webkit); return; }
    // Pin both images: the runtime keeps our IMPs and their WebKit key alive.
    Dl_info info = {0};
    if (!dladdr((void *)nvmtl_webkit_install_on_main, &info) || !info.dli_fname ||
        !dlopen(info.dli_fname, RTLD_LAZY | RTLD_LOCAL | RTLD_NODELETE)) {
        void (*release)(const void *) = dlsym(webkit, "WKRelease");
        if (release) release(key);
        dlclose(webkit);
        return;
    }
    nvmtl_webkit_preferences_class = preferences;
    nvmtl_webkit_video_feature = feature;
    nvmtl_webkit_video_key = key;
    nvmtl_webkit_set_ephemeral_bool = setBool;
    nvmtl_webkit_type_id = typeID;
    nvmtl_webkit_preferences_type_id = preferencesTypeID();
    nvmtl_webkit_get_preferences = (void *)method_getImplementation(getMethod);
    nvmtl_webkit_set_preferences = (void *)method_getImplementation(setMethod);
    // Install on this class, never on an inherited Foundation implementation.
    if (!class_addMethod(configuration, getter, (IMP)nvmtl_webkit_preferences, method_getTypeEncoding(getMethod)))
        method_setImplementation(class_getInstanceMethod(configuration, getter), (IMP)nvmtl_webkit_preferences);
    if (!class_addMethod(configuration, setter, (IMP)nvmtl_webkit_assign_preferences, method_getTypeEncoding(setMethod)))
        method_setImplementation(class_getInstanceMethod(configuration, setter), (IMP)nvmtl_webkit_assign_preferences);

    id app = ((id (*)(id, SEL))objc_msgSend)(appClass, sel_registerName("sharedApplication"));
    for (id window in ((id (*)(id, SEL))objc_msgSend)(app, sel_registerName("windows")))
        nvmtl_webkit_visit_view(((id (*)(id, SEL))objc_msgSend)(window, sel_registerName("contentView")), webView);
    nvlog("Safari video: retaining MediaSource decoder/frames through visibility changes; codec registration unchanged");
}

static void nvmtl_webkit_install(void) {
    if (!nvmtl_webkit_process(getprogname(), [NSBundle mainBundle].bundleIdentifier)) return;
    const char *option = getenv("NVMTL_WEBKIT_STABLE_VIDEO");
    if (option && !strcmp(option, "0")) return;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        // Metal creation can be inside WebKit initialization or on a worker.
        // Do not reenter the framework or its preferences on that stack.
        dispatch_async(dispatch_get_main_queue(), ^{ nvmtl_webkit_install_on_main(); });
    });
}
