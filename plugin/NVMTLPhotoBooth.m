/*
 * NullMoth NVIDIA driver for macOS
 * SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
 */

#import <CoreImage/CoreImage.h>
#include <dlfcn.h>
#include <math.h>

// Photo Booth's legacy CIContext render-to-Metal call produces bottom-first
// rows, while its CAMetalLayer preview consumes top-first rows. Correct only
// its actual NVMTL drawables. In particular, do not change camera buffers,
// offscreen textures, saved photos, Core Image's public flipped semantics,
// WindowServer, or the application's Zink/OpenGL renderer selection.
static char nvmtl_photobooth_drawable_key;
static id (*nvmtl_photobooth_next)(id, SEL);
static void (*nvmtl_photobooth_render)(id, SEL, CIImage *, id<MTLTexture>, id, CGRect, CGColorSpaceRef);

static BOOL nvmtl_photobooth_process(const char *name, NSString *bundle) {
    return name && !strcmp(name, "Photo Booth") && [bundle isEqualToString:@"com.apple.PhotoBooth"];
}

static id nvmtl_photobooth_next_drawable(id layer, SEL selector) {
    id drawable = nvmtl_photobooth_next(layer, selector);
    id texture = drawable ? ((id (*)(id, SEL))objc_msgSend)(drawable, sel_registerName("texture")) : nil;
    if ([texture isKindOfClass:[NVMTLTexture class]])
        objc_setAssociatedObject(texture, &nvmtl_photobooth_drawable_key, @YES, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    return drawable;
}

static BOOL nvmtl_photobooth_reflection(id texture, CGRect bounds, CGAffineTransform *transform) {
    if (!texture || !objc_getAssociatedObject(texture, &nvmtl_photobooth_drawable_key) ||
        !isfinite(bounds.origin.x) || !isfinite(bounds.origin.y) ||
        !isfinite(bounds.size.width) || !isfinite(bounds.size.height) ||
        bounds.size.width <= 0 || bounds.size.height <= 0) return NO;
    const double y = 2 * bounds.origin.y + bounds.size.height;
    if (!isfinite(y)) return NO;
    *transform = (CGAffineTransform){1, 0, 0, -1, 0, y};
    return YES;
}

static void nvmtl_photobooth_render_preview(id context, SEL selector, CIImage *image,
        id<MTLTexture> texture, id command, CGRect bounds, CGColorSpaceRef colorSpace) {
    CGAffineTransform transform;
    if (image && nvmtl_photobooth_reflection(texture, bounds, &transform))
        image = [image imageByApplyingTransform:transform];
    nvmtl_photobooth_render(context, selector, image, texture, command, bounds, colorSpace);
}

static BOOL nvmtl_photobooth_signature(Method method, BOOL render) {
    if (!method) return NO;
    NSMethodSignature *sig = [NSMethodSignature signatureWithObjCTypes:method_getTypeEncoding(method)];
    if (!render) return sig.numberOfArguments == 2 && sig.methodReturnType[0] == '@';
    return sig.numberOfArguments == 7 && !strcmp(sig.methodReturnType, @encode(void)) &&
        [sig getArgumentTypeAtIndex:2][0] == '@' && [sig getArgumentTypeAtIndex:3][0] == '@' &&
        [sig getArgumentTypeAtIndex:4][0] == '@' &&
        !strcmp([sig getArgumentTypeAtIndex:5], @encode(CGRect)) && [sig getArgumentTypeAtIndex:6][0] == '^';
}

static void nvmtl_photobooth_replace(Class cls, SEL selector, Method method, IMP replacement) {
    // Do not replace an inherited superclass method for unrelated classes.
    if (!class_addMethod(cls, selector, replacement, method_getTypeEncoding(method)))
        method_setImplementation(class_getInstanceMethod(cls, selector), replacement);
}

static void nvmtl_photobooth_install(void) {
    if (!nvmtl_photobooth_process(getprogname(), [NSBundle mainBundle].bundleIdentifier)) return;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        Boolean valid = false;
        Boolean enabled = CFPreferencesGetAppBooleanValue(CFSTR("NVMTLPhotoBoothPreviewFlip"),
            CFSTR("com.apple.PhotoBooth"), &valid);
        if (valid && !enabled) return;
        Class layer = NSClassFromString(@"CAMetalLayer"), context = NSClassFromString(@"CIContext");
        SEL next = sel_registerName("nextDrawable");
        SEL render = sel_registerName("render:toMTLTexture:commandBuffer:bounds:colorSpace:");
        Method nextMethod = class_getInstanceMethod(layer, next), renderMethod = class_getInstanceMethod(context, render);
        if (!nvmtl_photobooth_signature(nextMethod, NO) || !nvmtl_photobooth_signature(renderMethod, YES)) {
            nvlog("Photo Booth preview: unfamiliar rendering API; leaving orientation unchanged");
            return;
        }
        // The runtime retains these IMPs. Keep their code and association key
        // alive even if a client explicitly closes its plugin handle.
        Dl_info info = {0};
        if (!dladdr((void *)nvmtl_photobooth_install, &info) || !info.dli_fname ||
            !dlopen(info.dli_fname, RTLD_LAZY | RTLD_LOCAL | RTLD_NODELETE)) return;
        nvmtl_photobooth_next = (void *)method_getImplementation(nextMethod);
        nvmtl_photobooth_render = (void *)method_getImplementation(renderMethod);
        nvmtl_photobooth_replace(layer, next, nextMethod, (IMP)nvmtl_photobooth_next_drawable);
        nvmtl_photobooth_replace(context, render, renderMethod, (IMP)nvmtl_photobooth_render_preview);
        syslog(LOG_NOTICE, "NullMoth: Photo Booth NVMTL preview orientation enabled; Zink selection unchanged");
    });
}
