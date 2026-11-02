#!/usr/bin/env python3
"""Exercise production command-buffer callback ordering and wait completion without a GPU."""
import pathlib
import subprocess
import tempfile

root = pathlib.Path(__file__).resolve().parents[1]
events = (root / "plugin/NVMTLEventSync.m").read_text()
dispatch = events[events.index("- (void)nvmtlDispatchHandlers") : events.index("- (void)nvmtlCollectDeviceTiming", events.index("- (void)nvmtlDispatchHandlers"))]
finish_callbacks = events[events.index("- (void)nvmtlFinishCallbacks") : events.index("static unsigned nvmtl_gsub_cap", events.index("- (void)nvmtlFinishCallbacks"))]
objects = (root / "plugin/NVMTLObjects.m").read_text()
add_handlers = objects[objects.index("- (void)addCompletedHandler:") : objects.index("- (void)enqueue", objects.index("- (void)addCompletedHandler:"))]
waits = objects[objects.index("- (void)waitUntilCompleted") : objects.index("- (MTLCommandBufferStatus)status", objects.index("- (void)waitUntilCompleted"))]

fixture = r'''
#import <Foundation/Foundation.h>
#include <assert.h>
#include <dispatch/dispatch.h>
#include <stdatomic.h>

typedef long MTLCommandBufferStatus;
enum { MTLCommandBufferStatusNotEnqueued = 0, MTLCommandBufferStatusScheduled = 2,
       MTLCommandBufferStatusCompleted = 4 };
typedef void (^MTLCommandBufferHandler)(id);

@interface NVMTLCommandQueue : NSObject { @public id _completionQueue; dispatch_queue_t _callbackOrderQueue; } @end
@implementation NVMTLCommandQueue @end
@interface NVMTLCommandBuffer : NSObject {
@public NVMTLCommandQueue *_queue; NSCondition *_completionCondition; dispatch_group_t _callbackGroup, _scheduledGroup;
    NSMutableArray *_handlers, *_scheduledHandlers; BOOL _wasScheduled, _executionDone, _completionReady, _scheduleAfterPublish; MTLCommandBufferStatus _cbstatus;
    unsigned _nHandlers, _nWait;
}
@end
@implementation NVMTLCommandBuffer
'''

checks = r'''
@end
static void waitFor( dispatch_semaphore_t sem) { assert(dispatch_semaphore_wait(sem, dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC)) == 0); }
int main(void) { @autoreleasepool {
    NVMTLCommandQueue *queue = [NVMTLCommandQueue new];
    queue->_completionQueue = dispatch_queue_create("nvmtl.test.callbacks", DISPATCH_QUEUE_SERIAL);
    NVMTLCommandBuffer *cb = [NVMTLCommandBuffer new]; cb->_queue = queue;
    cb->_completionCondition = [NSCondition new]; cb->_callbackGroup = dispatch_group_create(); cb->_scheduledGroup = dispatch_group_create();
    dispatch_semaphore_t scheduled = dispatch_semaphore_create(0), completed = dispatch_semaphore_create(0);
    NSMutableArray *order = [NSMutableArray new];
    [cb addScheduledHandler:^(id done) { (void)done; [order addObject:@"scheduled"]; dispatch_semaphore_signal(scheduled); }];
    [cb addCompletedHandler:^(id done) { (void)done; [order addObject:@"completed"]; dispatch_semaphore_signal(completed); }];

    [cb nvmtlMarkScheduled];
    waitFor(scheduled);
    assert(order.count == 1 && [order[0] isEqual:@"scheduled"]);
    [cb nvmtlFinishCallbacks];
    [cb waitUntilCompleted]; // must include the callback dispatch, not only the status transition
    waitFor(completed);
    assert(order.count == 2 && [order[1] isEqual:@"completed"]);

    // A second buffer on the same queue cannot overtake the first buffer's callback batch.
    NVMTLCommandBuffer *first = [NVMTLCommandBuffer new], *second = [NVMTLCommandBuffer new];
    first->_queue = second->_queue = queue;
    for (NVMTLCommandBuffer *b in @[first, second]) {
        b->_completionCondition = [NSCondition new]; b->_callbackGroup = dispatch_group_create(); b->_scheduledGroup = dispatch_group_create();
    }
    NSMutableArray *serialOrder = [NSMutableArray new]; dispatch_semaphore_t done = dispatch_semaphore_create(0);
    [first addCompletedHandler:^(id b) { (void)b; [serialOrder addObject:@"first"]; }];
    [second addCompletedHandler:^(id b) { (void)b; [serialOrder addObject:@"second"]; dispatch_semaphore_signal(done); }];
    [first nvmtlMarkScheduled]; [first nvmtlFinishCallbacks];
    [second nvmtlMarkScheduled]; [second nvmtlFinishCallbacks];
    [second waitUntilCompleted]; waitFor(done);
    assert(serialOrder.count == 2 && [serialOrder[0] isEqual:@"first"] && [serialOrder[1] isEqual:@"second"]);
    puts("PASS: scheduled/completed waits include callback delivery; queue callback batches preserve submission order");
} }
'''

with tempfile.TemporaryDirectory() as directory:
    test = pathlib.Path(directory) / "test.m"
    exe = pathlib.Path(directory) / "test"
    test.write_text(fixture + dispatch + finish_callbacks + add_handlers + waits + checks)
    subprocess.run(["clang", "-fobjc-arc", "-framework", "Foundation", str(test), "-o", str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
