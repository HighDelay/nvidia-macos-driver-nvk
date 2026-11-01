// Deterministic desktop workloads that make WindowServer composite; used by windowserver_load_test.sh.
// clang -fobjc-arc -O2 tools/windowserver_workload.m -framework Cocoa -framework QuartzCore -o windowserver_workload
// windowserver_workload <move|drag|switch|windows|redraw|blur|layers|scroll|churn|alpha> <seconds>
#import <Cocoa/Cocoa.h>
#import <QuartzCore/QuartzCore.h>
#include <math.h>
#include <ApplicationServices/ApplicationServices.h>

static NSString *mode;
static double seconds;
static NSMutableArray<NSWindow *> *windows;
static CFTimeInterval start;
static NSUInteger frame;

@interface Fill : NSView
@property double hue;
@end
@implementation Fill
- (void)drawRect:(NSRect)r {
    NSGradient *g = [[NSGradient alloc] initWithStartingColor:[NSColor colorWithHue:fmod(self.hue, 1) saturation:0.7 brightness:0.9 alpha:1]
                                                  endingColor:[NSColor colorWithHue:fmod(self.hue + 0.4, 1) saturation:0.8 brightness:0.5 alpha:1]];
    [g drawInRect:self.bounds angle:fmod(self.hue * 360, 360)];
    NSDictionary *a = @{NSFontAttributeName: [NSFont systemFontOfSize:22], NSForegroundColorAttributeName: NSColor.whiteColor};
    [[NSString stringWithFormat:@"%@ frame %lu", mode, (unsigned long)frame] drawAtPoint:NSMakePoint(16, 16) withAttributes:a];
}
@end

static NSRect screenRect(void) { return NSScreen.mainScreen.visibleFrame; }

static NSWindow *makeWindow(NSRect r, NSWindowStyleMask style) {
    NSWindow *w = [[NSWindow alloc] initWithContentRect:r styleMask:style backing:NSBackingStoreBuffered defer:NO];
    w.releasedWhenClosed = NO;
    w.title = [NSString stringWithFormat:@"WindowServer load: %@", mode];
    [windows addObject:w];
    return w;
}

static void setupWindows(int count, BOOL blur) {
    NSRect s = screenRect();
    for (int i = 0; i < count; i++) {
        NSRect r = NSMakeRect(s.origin.x + 40 * i, s.origin.y + 30 * i, s.size.width * 0.35, s.size.height * 0.35);
        NSWindow *w = makeWindow(r, NSWindowStyleMaskTitled | NSWindowStyleMaskResizable | NSWindowStyleMaskClosable);
        if (blur) {
            w.opaque = NO;
            w.backgroundColor = NSColor.clearColor;
            NSVisualEffectView *v = [[NSVisualEffectView alloc] initWithFrame:w.contentView.bounds];
            v.blendingMode = NSVisualEffectBlendingModeBehindWindow;
            v.material = NSVisualEffectMaterialHUDWindow;
            v.state = NSVisualEffectStateActive;
            v.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
            w.contentView = v;
        } else {
            Fill *f = [[Fill alloc] initWithFrame:w.contentView.bounds];
            f.hue = i / (double)count;
            w.contentView = f;
        }
        [w orderFront:nil];
    }
}

static void setupLayers(void) {
    NSRect s = screenRect();
    NSWindow *w = makeWindow(NSInsetRect(s, s.size.width * 0.1, s.size.height * 0.1), NSWindowStyleMaskTitled | NSWindowStyleMaskResizable);
    NSView *v = w.contentView; v.wantsLayer = YES;
    v.layer.backgroundColor = NSColor.blackColor.CGColor;
    CGSize sz = v.bounds.size;
    for (int i = 0; i < 300; i++) {
        CALayer *l = [CALayer layer];
        double x = drand48() * sz.width, y = drand48() * sz.height;
        l.frame = CGRectMake(x, y, 24 + 40 * drand48(), 24 + 40 * drand48());
        l.cornerRadius = 8;
        l.backgroundColor = [NSColor colorWithHue:drand48() saturation:0.8 brightness:0.9 alpha:0.7].CGColor;
        l.shadowOpacity = i % 4 == 0 ? 0.6 : 0;
        [v.layer addSublayer:l];
        CABasicAnimation *p = [CABasicAnimation animationWithKeyPath:@"position"];
        p.toValue = [NSValue valueWithPoint:NSMakePoint(drand48() * sz.width, drand48() * sz.height)];
        CABasicAnimation *r = [CABasicAnimation animationWithKeyPath:@"transform.rotation.z"];
        r.toValue = @(M_PI * 2);
        CABasicAnimation *o = [CABasicAnimation animationWithKeyPath:@"opacity"];
        o.toValue = @0.1;
        CAAnimationGroup *g = [CAAnimationGroup animation];
        g.animations = @[p, r, o]; g.duration = 1 + 2 * drand48();
        g.autoreverses = YES; g.repeatCount = HUGE_VALF;
        [l addAnimation:g forKey:@"load"];
    }
    [w orderFront:nil];
}

static NSScrollView *scrollView;
static void setupScroll(void) {
    NSRect s = screenRect();
    NSWindow *w = makeWindow(NSInsetRect(s, s.size.width * 0.15, s.size.height * 0.05), NSWindowStyleMaskTitled | NSWindowStyleMaskResizable);
    scrollView = [[NSScrollView alloc] initWithFrame:w.contentView.bounds];
    scrollView.hasVerticalScroller = YES;
    scrollView.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
    NSTextView *t = [[NSTextView alloc] initWithFrame:scrollView.bounds];
    NSMutableString *text = [NSMutableString string];
    for (int i = 0; i < 4000; i++)
        [text appendFormat:@"%04d  The quick brown fox jumps over the lazy dog — WindowServer scroll workload line.\n", i];
    t.string = text;
    t.font = [NSFont monospacedSystemFontOfSize:14 weight:NSFontWeightRegular];
    scrollView.documentView = t;
    w.contentView = scrollView;
    [w orderFront:nil];
}

// Real mouse drag of our own title bar; needs Accessibility for the launching terminal.
static BOOL dragDown, dragAllowed;
static CGPoint dragPos;
static void postMouse(CGEventType type, CGPoint p) {
    CGEventRef e = CGEventCreateMouseEvent(NULL, type, p, kCGMouseButtonLeft);
    CGEventPost(kCGHIDEventTap, e);
    CFRelease(e);
}
static CGPoint titleBarPoint(NSWindow *w) {
    NSRect f = w.frame, main = NSScreen.screens[0].frame;
    return CGPointMake(NSMidX(f), NSMaxY(main) - (NSMaxY(f) - 12));  // AppKit -> global top-left coordinates
}

static NSArray<NSRunningApplication *> *switchApps;
static NSRunningApplication *originalFront;
static NSUInteger switchHits;

static void tick(void) {
    frame++;
    double t = CACurrentMediaTime() - start;
    NSRect s = screenRect();
    if ([mode isEqualToString:@"move"]) {
        NSWindow *w = windows[0];
        NSRect f = w.frame;
        f.origin.x = NSMinX(s) + (s.size.width - f.size.width) * (0.5 + 0.5 * sin(t * 1.7));
        f.origin.y = NSMinY(s) + (s.size.height - f.size.height) * (0.5 + 0.5 * cos(t * 1.1));
        [w setFrameOrigin:f.origin];
    } else if ([mode isEqualToString:@"drag"]) {
        if (!dragAllowed) {  // without event permission fall back to moving the window directly
            NSRect f = windows[0].frame;
            [windows[0] setFrameOrigin:NSMakePoint(NSMidX(s) - f.size.width / 2 + 300 * sin(t * 1.5), NSMidY(s) - f.size.height / 2 + 200 * sin(t * 1.1))];
        } else if (!dragDown) {
            dragPos = titleBarPoint(windows[0]);
            postMouse(kCGEventLeftMouseDown, dragPos);
            dragDown = YES;
        } else {
            CGPoint p = CGPointMake(dragPos.x + 300 * sin(t * 1.5), dragPos.y + 200 * sin(t * 1.1));
            postMouse(kCGEventLeftMouseDragged, p);
        }
    } else if ([mode isEqualToString:@"switch"]) {
        if (frame % 30 == 0 && switchApps.count) {
            NSRunningApplication *a = switchApps[(frame / 30) % switchApps.count];
            // LaunchServices activation is honored where activateWithOptions: is refused by cooperative activation.
            NSWorkspaceOpenConfiguration *c = [NSWorkspaceOpenConfiguration configuration];
            c.activates = YES;
            [NSWorkspace.sharedWorkspace openApplicationAtURL:a.bundleURL configuration:c completionHandler:^(NSRunningApplication *r, NSError *e) {
                if (!e) dispatch_async(dispatch_get_main_queue(), ^{ switchHits++; });
            }];
        }
    } else if ([mode isEqualToString:@"windows"] || [mode isEqualToString:@"blur"]) {
        NSUInteger n = windows.count;
        for (NSUInteger i = 0; i < n; i++) {
            double a = t * 1.3 + i * 2 * M_PI / n;
            double w = s.size.width * (0.25 + 0.1 * sin(t + i)), h = s.size.height * (0.25 + 0.1 * cos(t * 0.7 + i));
            double cx = NSMidX(s) + cos(a) * s.size.width * 0.25, cy = NSMidY(s) + sin(a) * s.size.height * 0.2;
            [windows[i] setFrame:NSMakeRect(cx - w / 2, cy - h / 2, w, h) display:YES];
        }
    } else if ([mode isEqualToString:@"redraw"]) {
        Fill *f = (Fill *)windows[0].contentView;
        f.hue = t * 0.2;
        f.needsDisplay = YES;
    } else if ([mode isEqualToString:@"scroll"]) {
        NSClipView *c = scrollView.contentView;
        double maxY = NSHeight(scrollView.documentView.frame) - NSHeight(c.bounds);
        double y = fmod(t * 1500, maxY * 2); if (y > maxY) y = maxY * 2 - y;
        [c scrollToPoint:NSMakePoint(0, y)];
        [scrollView reflectScrolledClipView:c];
    } else if ([mode isEqualToString:@"churn"]) {
        if (frame % 3 == 0) {
            NSWindow *w = makeWindow(NSMakeRect(s.origin.x + drand48() * s.size.width * 0.7, s.origin.y + drand48() * s.size.height * 0.7,
                                                200 + 400 * drand48(), 150 + 300 * drand48()),
                                     NSWindowStyleMaskTitled | NSWindowStyleMaskClosable);
            Fill *f = [[Fill alloc] initWithFrame:w.contentView.bounds]; f.hue = drand48();
            w.contentView = f;
            [w orderFront:nil];
        }
        while (windows.count > 12) { [windows[0] close]; [windows removeObjectAtIndex:0]; }
    } else if ([mode isEqualToString:@"alpha"]) {
        for (NSUInteger i = 0; i < windows.count; i++) {
            windows[i].alphaValue = 0.25 + 0.7 * fabs(sin(t * 2 + i));
            if (frame % 30 == i) [windows[i] orderFront:nil];
        }
    }
}

int main(int argc, char **argv) { @autoreleasepool {
    if (argc < 3) { fprintf(stderr, "usage: %s <move|drag|switch|windows|redraw|blur|layers|scroll|churn|alpha> <seconds>\n", argv[0]); return 2; }
    mode = @(argv[1]); seconds = atof(argv[2]);
    if (seconds <= 0) return 2;
    srand48(42);
    windows = [NSMutableArray array];
    NSApplication *app = NSApplication.sharedApplication;
    app.activationPolicy = NSApplicationActivationPolicyRegular;
    if ([mode isEqualToString:@"windows"]) setupWindows(10, NO);
    else if ([mode isEqualToString:@"blur"]) setupWindows(8, YES);
    else if ([mode isEqualToString:@"alpha"]) {
        setupWindows(10, NO);
        for (NSWindow *w in windows) w.opaque = NO;
    } else if ([mode isEqualToString:@"redraw"]) {
        NSWindow *w = makeWindow(screenRect(), NSWindowStyleMaskTitled);
        w.contentView = [[Fill alloc] initWithFrame:w.contentView.bounds];
        [w orderFront:nil];
    } else if ([mode isEqualToString:@"layers"]) setupLayers();
    else if ([mode isEqualToString:@"scroll"]) setupScroll();
    else if ([mode isEqualToString:@"move"] || [mode isEqualToString:@"drag"]) {
        NSRect sr = screenRect();
        NSWindow *w = makeWindow(NSMakeRect(NSMidX(sr) - 450, NSMidY(sr) - 300, 900, 600), NSWindowStyleMaskTitled | NSWindowStyleMaskResizable);
        w.contentView = [[Fill alloc] initWithFrame:w.contentView.bounds];
        [w orderFront:nil];
        dragAllowed = CGPreflightPostEventAccess();
        if ([mode isEqualToString:@"drag"] && !dragAllowed)
            fprintf(stderr, "drag: no event-posting permission (grant Accessibility to the terminal app); falling back to direct window moves\n");
        else if ([mode isEqualToString:@"drag"])
            fprintf(stderr, "drag: dragging the title bar with real mouse events\n");
    } else if ([mode isEqualToString:@"switch"]) {
        originalFront = NSWorkspace.sharedWorkspace.frontmostApplication;
        NSMutableArray *apps = [NSMutableArray array];
        for (NSRunningApplication *a in NSWorkspace.sharedWorkspace.runningApplications)
            if (a.activationPolicy == NSApplicationActivationPolicyRegular && !a.hidden && a.processIdentifier != getpid()) [apps addObject:a];
        switchApps = apps;
        fprintf(stderr, "switch: cycling %lu running apps\n", (unsigned long)apps.count);
    }
    else if (![mode isEqualToString:@"churn"]) { fprintf(stderr, "unknown mode %s\n", argv[1]); return 2; }
    if (!switchApps) [app activateIgnoringOtherApps:YES];
    start = CACurrentMediaTime();
    NSTimer *timer = [NSTimer timerWithTimeInterval:1.0 / 60 repeats:YES block:^(NSTimer *tm) {
        if (CACurrentMediaTime() - start >= seconds) {
            [tm invalidate];
            if (dragDown) postMouse(kCGEventLeftMouseUp, dragPos);
            if (switchApps) {
                fprintf(stderr, "switch: %lu app activations completed\n", (unsigned long)switchHits);
                [originalFront activateWithOptions:0];
            }
            [app terminate:nil];
            return;
        }
        tick();
    }];
    [NSRunLoop.mainRunLoop addTimer:timer forMode:NSRunLoopCommonModes];
    [app run];
    return 0;
}}
