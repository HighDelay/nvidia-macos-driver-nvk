// Run the production RAM-scanout methods against bounded fake MMIO/DMA memory.
#include <algorithm>
#include <atomic>
#include <cassert>
#include <cerrno>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <random>
#include <map>
#include <string>
#include <vector>
#include "panel_policy.h"
#include "panel_scanout.h"
#include "panel_bridge_abi.h"

using IOReturn = int;
using SInt64 = long long;
using IOOptionBits = unsigned;
using IOByteCount = size_t;
constexpr int kIOReturnSuccess = 0, kIOReturnBadArgument = 1, kIOReturnBusy = 2,
    kIOReturnIOError = 3, kIOReturnNotReady = 4, kIOReturnUnsupported = 5, kIOReturnNoMemory = 6;
constexpr unsigned kIODirectionInOut = 1, kIOMemoryPhysicallyContiguous = 2,
    kIOMapWriteCombineCache = 0, kIOMemoryMapperNone = 0;
constexpr bool kOSBooleanTrue = true;
constexpr bool kOSBooleanFalse = false;
static void *kernel_task;
struct IOLock { std::mutex mutex; std::condition_variable changed; };
static void IOLockLock(IOLock *l) { l->mutex.lock(); }
static void IOLockUnlock(IOLock *l) { l->mutex.unlock(); }
constexpr int THREAD_UNINT = 0;
static void IOLockSleep(IOLock *l, void *, int) {
    std::unique_lock<std::mutex> lock(l->mutex, std::adopt_lock);
    l->changed.wait(lock); lock.release();
}
static void IOLockWakeup(IOLock *l, void *, bool) { l->changed.notify_all(); }
static std::atomic<uint64_t> fakeTime{0};
static uint64_t mach_absolute_time() { return fakeTime.fetch_add(100000); }
static void IOSleep(unsigned ms) { fakeTime.fetch_add(uint64_t(ms) * 1000000); }
static void nanoseconds_to_absolutetime(uint64_t n, uint64_t *r) { *r = n; }
static volatile SInt64 gIfbFlips, gIfbLate;
static void OSIncrementAtomic64(volatile SInt64 *v) { __atomic_fetch_add(v, 1, __ATOMIC_RELAXED); }
#define IFBLOG(...) do {} while (0)
#define IONew(type, count) static_cast<type *>(std::calloc(count, sizeof(type)))
#define IODelete(p, type, count) std::free(p)
#define OSSafeReleaseNULL(p) do { if (p) { (p)->release(); (p) = nullptr; } } while (0)

struct IOMemoryMap {
    void *address; size_t length;
    uintptr_t getVirtualAddress() const { return reinterpret_cast<uintptr_t>(address); }
    size_t getLength() const { return length; }
    void release() { delete this; }
};
struct IOBufferMemoryDescriptor {
    static int live, prepared;
    void *address = nullptr; size_t length = 0; bool wired = false;
    static IOBufferMemoryDescriptor *inTaskWithPhysicalMask(void *, unsigned, size_t bytes, uint64_t) {
        auto *m = new IOBufferMemoryDescriptor;
        m->length = bytes; assert(!posix_memalign(&m->address, 4096, bytes)); ++live;
        return m;
    }
    int prepare() { wired = true; ++prepared; return 0; }
    void complete() { assert(wired); wired = false; --prepared; }
    void release() { assert(!wired); std::free(address); --live; delete this; }
    IOMemoryMap *map(unsigned) { return new IOMemoryMap{address, length}; }
    uint64_t getPhysicalSegment(size_t off, size_t *lengthOut, unsigned) {
        *lengthOut = length - off; return reinterpret_cast<uint64_t>(address) + off;
    }
};
int IOBufferMemoryDescriptor::live = 0, IOBufferMemoryDescriptor::prepared = 0;
static constexpr uint32_t PLANE_CTL(int p) { return 0x70180 + uint32_t(p) * 0x1000; }
static constexpr uint32_t PLANE_SURF(int p) { return 0x7019c + uint32_t(p) * 0x1000; }
static constexpr uint32_t kEnable = 1u << 31;
struct FakePCI { void setProperty(const char *, bool) {} };
class IOService;

class NMIntelFB {
public:
    std::vector<uint64_t> registers = std::vector<uint64_t>((16u << 20) / 8);
    std::vector<unsigned char> original = std::vector<unsigned char>(320 * 200 * 4, 0x5a);
    FakePCI pci;
    FakePCI *fPCI = &pci;
    volatile uint8_t *fRegs = reinterpret_cast<volatile uint8_t *>(registers.data());
    IOMemoryMap *fMMIO = new IOMemoryMap{registers.data(), 16u << 20};
    IOMemoryMap *fSurfMap = new IOMemoryMap{original.data(), original.size()};
    IOLock lock;
    IOLock *fRamLock = &lock;
    nmintelfb::FrameGate fFrameGate;
    IOBufferMemoryDescriptor *fRam[2] = {};
    IOMemoryMap *fRamMap[2] = {};
    unsigned long long *fRamPages[2] = {}, *fOldPte[2] = {};
    uint32_t fRamGgtt[2] = {}, fSurfLow = 0;
    unsigned fRamCount = 0, fFront = 0, fLeaseBuffer = 0;
    int fRamMode = 0;
    bool fRamMapped[2] = {}, fQuarantineRetained = false, fMatchedConsumer = true, fStopping = false;
    SInt64 fRamFlips = 0, fRamLate = 0;
    uint64_t fApertureLen = 256ull << 20, fFrameNs = 16666667;
    uint32_t fWidth = 320, fHeight = 200, fRowBytes = 1280, fSurf = 0x100000;
    int fPipe = 0;
    uint8_t fVer = 13;
    bool stalled = false;
    bool otherIntelAccelerator = false;
    unsigned retained = 0;
    IOService *provider = nullptr;
    IOService *getProvider() const { return provider; }
    std::map<std::string, bool> properties;
    void retain() { ++retained; }
    void setProperty(const char *name, bool value) { properties[name] = value; }
    void setProperty(const char *, unsigned, int) {}
    uint32_t rd(uint32_t off) const { assert(off + 4 <= fMMIO->length); return *reinterpret_cast<const uint32_t *>(reinterpret_cast<const char *>(registers.data()) + off); }
    void wr(uint32_t off, uint32_t value) {
        assert(off + 4 <= fMMIO->length);
        *reinterpret_cast<uint32_t *>(reinterpret_cast<char *>(registers.data()) + off) = value;
        if (off == PLANE_SURF(0) && !stalled)
            *reinterpret_cast<uint32_t *>(reinterpret_cast<char *>(registers.data()) + off + 0x10) = value;
    }
    volatile uint64_t *gtt() { return reinterpret_cast<volatile uint64_t *>(fRegs + 0x800000); }
    NMIntelFB() {
        wr(PLANE_CTL(0), kEnable); wr(PLANE_SURF(0), fSurf);
        for (unsigned i = 0; i < (original.size() + 4095) / 4096; ++i)
            gtt()[(fSurf >> 12) + i] = 0x10000001ull + uint64_t(i) * 4096;
    }
    ~NMIntelFB() {
        freeRam();
        // Tests can model a later hardware reset to dispose quarantined storage.
        // Assertions check that production freeRam did not release it beforehand.
        for (unsigned b = 0; b < 2; ++b) {
            OSSafeReleaseNULL(fRamMap[b]);
            if (fRam[b]) { fRam[b]->complete(); OSSafeReleaseNULL(fRam[b]); }
            std::free(fRamPages[b]); std::free(fOldPte[b]);
        }
        fSurfMap->release(); fMMIO->release();
    }
    void quarantineRam(); bool flipLatched(uint32_t); bool ramPtes(unsigned, bool); bool ramMap(unsigned);
    int ramSetLocked(int); int ramSet(int); int ramMode();
    bool ramHardwareEligible() const { return !otherIntelAccelerator; }
    IOReturn beginFrame(NMIntelFBFrame *); IOReturn endFrame(const NMIntelFBFrame *); void freeRam();
    void activateBridge();
};

#include "scanout.inc"

static void policyTests() {
    using namespace nmintelfb;
    assert(surfaceFits(320, 200, 1280, 4096, 4096 + 256000));
    assert(!surfaceFits(320, 200, 1280, UINT64_MAX - 1, UINT64_MAX));
    assert(!surfaceFits(320, 200, 1279, 0, UINT64_MAX));
    assert(!surfaceFits(16385, 200, 65540, 0, UINT64_MAX));
    assert(allocationSize(1, 65536, 0) == 65536);
    assert(!allocationSize(UINT64_MAX, UINT64_MAX, 0));
    assert(!allocationSize(65537, 131072, 1));
    assert(!allocationSize(1, 65536, UINT64_MAX));
    assert(decimalKnob("45", 2, 9) == 45);
    assert(decimalKnob("4294967295", 10, 9) == UINT32_MAX);
    assert(decimalKnob("4294967296", 10, 9) == 9);
    assert(decimalKnob("1x", 2, 9) == 9);
    assert(decimalKnob("", 0, 9) == 9);
    assert(nvrmGuardOff(true, 0, true));
    assert(nvrmGuardOff(false, 2, false));
    assert(!nvrmGuardOff(false, 2, true));
    assert(pwmDuty(UINT32_MAX, 100, false) == UINT32_MAX);
    assert(pwmDuty(1000, 25, true) == 750);
    assert(ggttRangeFits(16u << 20, 256u << 20, (256u << 20) - 4096, 1));
    assert(!ggttRangeFits(16u << 20, 256u << 20, 256u << 20, 1));
    assert(!ggttRangeFits(8u << 20, 256u << 20, 0, 1));
    FrameGate gate; uint64_t first, second;
    assert(gate.acquire(true, first)); assert(!gate.acquire(true, second));
    assert(!gate.finish(first + 1, true)); assert(gate.busy());
    assert(gate.finish(first, true)); assert(gate.acquire(true, second));
    assert(second != first); assert(!gate.finish(first, false));
    assert(gate.finish(second, false)); assert(!gate.canChangeMode());
    assert(!gate.acquire(true, first));
    std::mt19937 rng(0x4e4d);
    for (unsigned trial = 0; trial < 5000; ++trial) {
        const unsigned count = 1 + rng() % 300, pages = rng() % 320, align = 1u << (rng() % 6);
        std::vector<uint64_t> entries(count);
        for (auto &x : entries) x = rng() % 5 ? 0 : 1;
        uint64_t expected = UINT64_MAX;
        if (pages && pages <= count) for (unsigned n = align; n <= count - pages; n += align)
            if (std::all_of(entries.begin() + n, entries.begin() + n + pages, [](auto x) { return x == 0; })) { expected = n; break; }
        assert(emptyGgttRange(entries.data(), count, pages, align) == expected);
    }
}

static NMIntelFBFrame begin(NMIntelFB &fb) {
    NMIntelFBFrame frame = {}; frame.version = NMINTELFB_FRAME_VERSION;
    assert(fb.beginFrame(&frame) == 0); return frame;
}
static void finish(NMIntelFB &fb, NMIntelFBFrame &frame, unsigned result) {
    frame.result = result; assert(fb.endFrame(&frame) == 0);
}
static void scanoutTests() {
    {
        NMIntelFB fb; auto initial = fb.registers;
        assert(fb.ramSet(2) == 2);
        auto frame = begin(fb); assert(frame.mode == 2 && frame.pages && frame.pageCount);
        assert(fb.ramSet(0) == -EBUSY);
        std::memset(frame.kva, 0x38, size_t(frame.length));
        finish(fb, frame, NMINTELFB_CPU_READY);
        auto next = begin(fb); assert(next.kva != frame.kva);
        finish(fb, next, NMINTELFB_CANCEL);
        assert(fb.ramSet(0) == 0);
        assert(std::all_of(fb.original.begin(), fb.original.end(), [](auto x) { return x == 0x38; }));
        assert(std::equal(fb.registers.begin() + (0x800000 / 8), fb.registers.end(), initial.begin() + (0x800000 / 8)));
        assert(fb.ramSet(2) == 2); assert(fb.ramSet(1) == 1); assert(fb.ramSet(0) == 0);
    }
    {
        NMIntelFB fb; fb.fMatchedConsumer = false; auto initial = fb.registers;
        assert(fb.ramSet(2) == -ENOTSUP); assert(fb.registers == initial);
        fb.fMatchedConsumer = true; fb.fVer = 20;
        assert(fb.ramSet(2) == -ENOTSUP); assert(fb.registers == initial);
        fb.fVer = 13; fb.otherIntelAccelerator = true;
        assert(fb.ramSet(2) == -ENOTSUP); assert(fb.registers == initial);
    }
    {
        NMIntelFB fb;
        std::fill(fb.registers.begin() + (0x800000 / 8), fb.registers.end(), 1);
        auto initial = fb.registers;
        assert(fb.ramSet(2) < 0 && fb.ramMode() == 0);
        assert(std::equal(fb.registers.begin() + (0x800000 / 8), fb.registers.end(), initial.begin() + (0x800000 / 8)));
    }
    {
        NMIntelFB fb; assert(fb.ramSet(2) == 2);
        auto frame = begin(fb); fb.stalled = true;
        finish(fb, frame, NMINTELFB_GPU_READY);
        NMIntelFBFrame other = {}; other.version = NMINTELFB_FRAME_VERSION;
        assert(fb.beginFrame(&other) == kIOReturnBusy && !other.token);
        assert(!fb.fFrameGate.busy()); assert(fb.fRamLate > 0);
        fb.stalled = false; fb.wr(PLANE_SURF(0), fb.rd(PLANE_SURF(0)));
        other = begin(fb); finish(fb, other, NMINTELFB_CANCEL);
    }
    {
        NMIntelFB fb; assert(fb.ramSet(2) == 2);
        auto frame = begin(fb); frame.result = NMINTELFB_GPU_UNCERTAIN;
        assert(fb.endFrame(&frame) == kIOReturnIOError && fb.retained == 1);
        const int prepared = IOBufferMemoryDescriptor::prepared;
        fb.freeRam(); assert(IOBufferMemoryDescriptor::prepared == prepared);
        assert(fb.ramSet(0) == -EIO);
        assert(fb.beginFrame(&frame) == kIOReturnNotReady);
    }
    {
        NMIntelFB fb; assert(fb.ramSet(2) == 2);
        auto frame = begin(fb); finish(fb, frame, NMINTELFB_GPU_READY);
        fb.stalled = true; fb.wr(PLANE_SURF(0) + 0x10, 0);
        assert(fb.ramSet(0) == -EIO && fb.retained == 1);
        const int prepared = IOBufferMemoryDescriptor::prepared;
        fb.freeRam(); assert(IOBufferMemoryDescriptor::prepared == prepared);
    }
    {
        NMIntelFB fb; assert(fb.ramSet(2) == 2);
        fb.gtt()[fb.fSurf >> 12] = 0x12345001; // Another owner changed a mapping.
        NMIntelFBFrame frame = {}; frame.version = NMINTELFB_FRAME_VERSION;
        assert(fb.beginFrame(&frame) == kIOReturnIOError && fb.retained == 1);
        assert(fb.gtt()[fb.fSurf >> 12] == 0x12345001);
    }
    assert(IOBufferMemoryDescriptor::live == 0 && IOBufferMemoryDescriptor::prepared == 0);
}

struct LeaseStats { int begins = 0, ends = 0, deleted = 0; unsigned last = 99; };
class OSSymbol {
public:
    static bool failEnd, failBegin;
    const char *text;
    explicit OSSymbol(const char *s) : text(s) {}
    static const OSSymbol *withCStringNoCopy(const char *s) {
        if ((failEnd && !std::strcmp(s, NMINTELFB_FN_END)) ||
            (failBegin && !std::strcmp(s, NMINTELFB_FN_BEGIN))) return nullptr;
        return new OSSymbol(s);
    }
    void release() const { delete this; }
};
bool OSSymbol::failEnd = false, OSSymbol::failBegin = false;
class IOService {
public:
    LeaseStats &stats; int refs = 1; bool native = true, legacy = false, blocked = false;
    std::map<std::string, bool> properties{{"nm-intel-panel", true}};
    explicit IOService(LeaseStats &s) : stats(s) {}
    void *metaCast(const char *) { return native ? this : nullptr; }
    void *getProperty(const char *name) { return properties[name] ? this : nullptr; }
    void setProperty(const char *name, bool value) { properties[name] = value; }
    void retain() { ++refs; }
    void release() { if (!--refs) { ++stats.deleted; delete this; } }
    int callPlatformFunction(const OSSymbol *s, bool, void *p, void *, void *, void *) {
        auto *frame = static_cast<NMIntelFBFrame *>(p);
        if (!std::strcmp(s->text, NMINTELFB_FN_BEGIN)) {
            ++stats.begins;
            return legacy ? kIOReturnUnsupported : blocked ? kIOReturnBusy : 0;
        }
        ++stats.ends; stats.last = frame->result; return 0;
    }
};
#include "intel_frame_lease.inc"

static std::map<std::string, int> knobs;
static std::vector<std::pair<std::string, int>> knobWrites;
static bool autoEnabled = true;
static std::string failWrite;
static NMIntelFB *stopOnRead;
static unsigned ifb_knob(const char *, unsigned fallback) { return autoEnabled ? fallback : 0; }
static void ifb_trail(const char *) {}
static int sysctlbyname(const char *name, void *out, size_t *, void *in, size_t) {
    auto i = knobs.find(name);
    if (i == knobs.end()) return ENOENT;
    if (out) {
        *static_cast<int *>(out) = i->second;
        if (stopOnRead && !std::strcmp(name, "debug.nmintelfb_agdc")) stopOnRead->fStopping = true;
    }
    if (in) {
        if (failWrite == name) return EIO;
        i->second = *static_cast<int *>(in);
        knobWrites.emplace_back(name, i->second);
    }
    return 0;
}
#include "intel_auto_bridge.inc"

static void autoTests() {
    LeaseStats stats; IOService provider(stats);
    NMIntelFB fb; fb.provider = &provider;
    const auto reset = [&] {
        knobs = {{"debug.nvaccel_igpu",0},{"debug.nvaccelfb",0},
                 {"debug.nmintelfb_agdc",0},{"debug.nvaccel_iop",0}};
        knobWrites.clear(); failWrite.clear(); autoEnabled = true; stopOnRead = nullptr;
        fb.fStopping = false; provider.properties.clear(); provider.properties["nm-intel-panel"] = true;
    };
    reset(); fb.activateBridge();
    const std::vector<std::pair<std::string,int>> expected = {
        {"debug.nvaccel_igpu",1},{"debug.nvaccelfb",3},
        {"debug.nmintelfb_agdc",1},{"debug.nvaccel_iop",1}};
    assert(knobWrites == expected && provider.properties["NMIntelFBActive"]);
    reset(); autoEnabled = false; fb.activateBridge(); assert(knobWrites.empty());
    reset(); provider.properties["nm-intel-panel"] = false; fb.activateBridge(); assert(knobWrites.empty());
    reset(); knobs.erase("debug.nvaccelfb"); fb.activateBridge();
    assert(knobWrites.empty() && provider.properties["NMIntelFBFailed"]);
    reset(); failWrite = "debug.nmintelfb_agdc"; fb.activateBridge();
    assert(knobWrites.size() == 2 && provider.properties["NMIntelFBFailed"]);
    reset(); stopOnRead = &fb; fb.activateBridge();
    assert(!knobs["debug.nvaccel_iop"] && provider.properties["NMIntelFBFailed"]);
}

static void leaseTests() {
    LeaseStats s;
    auto *fb = new IOService(s);
    { NVIntelFrameLease lease(fb); assert(lease.active()); fb->release(); }
    assert(s.deleted == 1 && s.ends == 1 && s.last == NMINTELFB_CANCEL);
    s = {}; fb = new IOService(s);
    { NVIntelFrameLease lease(fb); assert(!lease.finish(NMINTELFB_GPU_UNCERTAIN)); }
    assert(s.ends == 1 && s.last == NMINTELFB_GPU_UNCERTAIN); fb->release();
    s = {}; fb = new IOService(s); OSSymbol::failEnd = true;
    { NVIntelFrameLease lease(fb); assert(lease.blocked() && !lease.active()); }
    assert(!s.begins && !s.ends); OSSymbol::failEnd = false; fb->release();
    s = {}; fb = new IOService(s); fb->legacy = true;
    { NVIntelFrameLease lease(fb); assert(!lease.blocked() && !lease.active()); }
    assert(!s.ends); fb->release();
    s = {}; fb = new IOService(s); OSSymbol::failBegin = true;
    { NVIntelFrameLease lease(fb); assert(lease.blocked() && !lease.active()); }
    assert(!s.begins && !s.ends); OSSymbol::failBegin = false; fb->release();
}

int main() {
    policyTests(); scanoutTests(); leaseTests(); autoTests();
    std::puts("PASS panel bounds, 5000 GGTT cases, production scanout transitions, quarantine, paired leases and activation");
}
