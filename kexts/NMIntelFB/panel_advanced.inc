/*
 * NullMoth NVIDIA driver for macOS
 * Copyright (c) 2026 NullMoth Systems.
 * SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
 *
 * Opt-in resume, display firmware and external test heads for the adopted Intel panel.
 * After startup, display register changes share the scanout lock.
 */

bool NMIntelFB::readVbt(struct nmr_edp *out, uint8_t **keep, uint32_t *keepLen)
{
    *keep = nullptr; *keepLen = 0;
    const uint32_t asls = fPCI->configRead32(0xFC);
    if (!asls) return false;
    IOMemoryDescriptor *od = IOMemoryDescriptor::withPhysicalAddress(asls, NMV_OPREGION_SIZE, kIODirectionIn);
    IOMemoryMap *om = od ? od->map() : nullptr;
    bool ok = false;
    if (om) {
        const uint8_t *op = (const uint8_t *)om->getVirtualAddress();
        uint32_t io = 0, is = 0; struct nmv_loc loc; struct nmv_edp e;
        if (nmv_locate(op, asls, &io, &is, &loc)) {
            if (loc.kind == 1) {
                ok = nmv_edp(op + io, is, nullptr, &e);
                if (!ok && is <= 65536 && (*keep = (uint8_t *)IOMalloc(is))) { memcpy(*keep, op + io, is); *keepLen = is; }
            }
            else if (loc.kind == 2) {
                IOMemoryDescriptor *vd = IOMemoryDescriptor::withPhysicalAddress((IOPhysicalAddress)loc.phys, loc.size, kIODirectionIn);
                IOMemoryMap *vm = vd ? vd->map() : nullptr;
                if (vm) {
                    const uint8_t *vb = (const uint8_t *)vm->getVirtualAddress();
                    ok = nmv_edp(vb, loc.size, nullptr, &e);
                    if (!ok && loc.size <= 65536 && (*keep = (uint8_t *)IOMalloc(loc.size))) { memcpy(*keep, vb, loc.size); *keepLen = loc.size; }
                }
                if (vm) vm->release();
                if (vd) vd->release();
            }
        }
        if (ok) { out->known = 1; out->low_vswing = (uint8_t)e.low_vswing; out->hobl = (uint8_t)e.hobl;
                  IFBLOG("hybrid: video BIOS: panel type %u, low-swing table %s, reduced-power table %s (VBT %u, BDB %u)", e.panel_type,
                         e.low_vswing ? "yes" : "no", e.hobl ? "yes" : "no", e.vbt_version, e.bdb_version); }
        else IFBLOG("hybrid: video BIOS: no panel type named; trying the panel's identifier");
        om->release();
    }
    if (od) od->release();
    return ok;
}

static NMIntelFB *gDmcTarget;
static int nmintelfb_dmc_write SYSCTL_HANDLER_ARGS
{
    if (!req->newptr) return 0;
    if (req->newlen < 1024 || req->newlen > 160 * 1024) return EINVAL;
    IOLockLock(gIfbLock);
    NMIntelFB *panel = gDmcTarget;
    if (panel) panel->retain();
    IOLockUnlock(gIfbLock);
    if (!panel) return ENXIO;

    uint8_t *blob = static_cast<uint8_t *>(IOMalloc(req->newlen));
    int err = blob ? SYSCTL_IN(req, blob, req->newlen) : ENOMEM;
    if (!err) err = panel->dmcAccept(blob, static_cast<uint32_t>(req->newlen));
    if (blob) IOFree(blob, req->newlen);
    panel->release();
    return err;
}
static int nmintelfb_dmc_want_read SYSCTL_HANDLER_ARGS
{
    char want[40] = "none";
    IOLockLock(gIfbLock);
    if (gDmcTarget) strlcpy(want, gDmcTarget->fDmcWant, sizeof want);
    IOLockUnlock(gIfbLock);
    return sysctl_handle_string(oidp, want, sizeof want, req);
}
SYSCTL_PROC(_debug, OID_AUTO, nmintelfb_dmc, CTLTYPE_OPAQUE | CTLFLAG_WR | CTLFLAG_LOCKED,
            nullptr, 0, nmintelfb_dmc_write, "-", "Intel display firmware upload (root only)");
SYSCTL_PROC(_debug, OID_AUTO, nmintelfb_dmc_want, CTLTYPE_STRING | CTLFLAG_RD | CTLFLAG_LOCKED,
            nullptr, 0, nmintelfb_dmc_want_read, "A", "Intel display firmware filename");

struct nmr_ops NMIntelFB::panelOps()
{
    return {
        this,
        [](void *ctx, uint32_t offset) {
            auto *panel = static_cast<NMIntelFB *>(ctx);
            return offset <= panel->fMMIO->getLength() - 4 ? panel->rd(offset) : ~0u;
        },
        [](void *ctx, uint32_t offset, uint32_t value) {
            auto *panel = static_cast<NMIntelFB *>(ctx);
            if (offset <= panel->fMMIO->getLength() - 4) panel->wr(offset, value);
        },
        [](void *, unsigned us) { if (us >= 1000) IOSleep((us + 999) / 1000); else IODelay(us); },
        [](void *, const char *step) { IFBLOG("hybrid: %s", step); },
        nullptr,
    };
}

struct nmd_step NMIntelFB::dmcStep() const
{
    const uint32_t gmd = fVer == 14 ? rd(0x510A0u) : 0;
    return nmd_stepping(fVer, fPCI->configRead16(kIOPCIConfigDeviceID),
                        fPCI->configRead8(kIOPCIConfigRevisionID), gmd);
}

bool NMIntelFB::dmcApply()
{
    if (!fDmcOn || !fDmcBlob) return true;
    const auto stepping = dmcStep();
    struct nmd_fw fw;
    if (nmd_parse(fDmcBlob, fDmcLen, stepping.st, stepping.sub, nmd_max_words(fVer), &fw) != NMD_OK)
        return false;
    constexpr uint32_t cap = 1u << 15;
    auto *plan = IONew(struct nmd_write, cap);
    if (!plan) return false;
    const uint32_t count = nmd_load_plan(&fw, fVer, plan, cap);
    bool valid = count != 0;
    const auto end = fMMIO->getLength() - 4;
    for (unsigned i = 0; valid && i < count; ++i)
        valid = plan[i].reg <= end && !(plan[i].reg & 3u);
    if (valid) for (unsigned i = 0; i < count; ++i) wr(plan[i].reg, plan[i].val);
    IODelete(plan, struct nmd_write, cap);
    if (valid) IFBLOG("hybrid: display firmware loaded with events disabled");
    return valid;
}

int NMIntelFB::dmcAccept(const uint8_t *blob, uint32_t len)
{
    IOLockLock(fRamLock);
    int status = ENXIO;
    if (!__atomic_load_n(&fStopping, __ATOMIC_ACQUIRE) && fDmcOn && !fPanelSleeping) {
        struct nmd_fw fw;
        const auto stepping = dmcStep();
        status = EINVAL;
        if (nmd_parse(blob, len, stepping.st, stepping.sub, nmd_max_words(fVer), &fw) == NMD_OK) {
            auto *copy = static_cast<uint8_t *>(IOMalloc(len));
            status = ENOMEM;
            if (copy) {
                memcpy(copy, blob, len);
                auto *old = fDmcBlob;
                const auto oldLen = fDmcLen;
                fDmcBlob = copy;
                fDmcLen = len;
                if (dmcApply()) {
                    if (old) IOFree(old, oldLen);
                    status = 0;
                } else {
                    fDmcBlob = old;
                    fDmcLen = oldLen;
                    IOFree(copy, len);
                    status = EIO;
                }
            }
        }
    }
    IOLockUnlock(fRamLock);
    return status;
}

volatile uint64_t *NMIntelFB::ggtt() const
{
    if (!fMMIO || fMMIO->getLength() != (16ull << 20) || (fVer != 12 && fVer != 13))
        return nullptr;
    return reinterpret_cast<volatile uint64_t *>(fRegs + 0x800000);
}

void NMIntelFB::resumeCapture()
{
    int enable = 0;
    const bool present = PE_parse_boot_argn("nmhybridresume", &enable, sizeof enable);
    if (!nmr_enabled(present, enable)) return;
    const bool xe2 = fVer == 20 || fVer == 30;
    int enableXe2 = 0;
    if (xe2 && (!PE_parse_boot_argn("nmhybrid20", &enableXe2, sizeof enableXe2) || enableXe2 != 1))
        return;
    if (!((fVer >= 12 && fVer <= 14) || xe2) || fPipe < 0 || fPipe > 3) return;

    auto *snap = IONewZero(struct nmr_snap, 1);
    if (!snap) return;
    auto ops = panelOps();
    struct nmr_edp edp = {};
    uint8_t *vbt = nullptr;
    uint32_t vbtLen = 0;
    readVbt(&edp, &vbt, &vbtLen);
    const int rc = fVer >= 14 ? nmr20_snapshot(&ops, snap, static_cast<unsigned>(fPipe), &edp, fVer)
                             : nmr_snapshot(&ops, snap, static_cast<unsigned>(fPipe), fVer, &edp);
    if (rc == NMR_OK && vbt) {
        struct nmv_edp panel;
        uint8_t id[10];
        auto aux = ops;
        if (fVer >= 14) {
            aux.aux_ctl_or = NMX_AUX_POWER_REQ;
            nmx_aux_power(&ops, snap->topo.port, 1);
        }
        const bool got = nmr_edid_id(&aux, snap->topo.port, id) == 0;
        if (fVer >= 14) nmx_aux_power(&ops, snap->topo.port, 0);
        if (got && nmv_edp(vbt, vbtLen, id, &panel)) {
            snap->edp = {1, static_cast<uint8_t>(panel.low_vswing), static_cast<uint8_t>(panel.hobl)};
        }
    }
    if (vbt) IOFree(vbt, vbtLen);
    if (rc != NMR_OK) {
        IFBLOG("hybrid: resume unavailable: %s", nmr_result_name(rc));
        IODelete(snap, struct nmr_snap, 1);
        return;
    }

    if (fVer <= 13) {
        const unsigned pages = static_cast<unsigned>((uint64_t(fRowBytes) * fHeight + 4095) / 4096);
        auto *table = ggtt();
        if (table && nmintelfb::ggttRangeFits(fMMIO->getLength(), fApertureLen, fSurf, pages))
            fFwPte = IONew(uint64_t, pages);
        if (!fFwPte) {
            IODelete(snap, struct nmr_snap, 1);
            return;
        }
        fFwPteCount = pages;
        for (unsigned i = 0; i < pages; ++i) fFwPte[i] = table[(fSurf >> 12) + i];
    }
    fSnap = snap;
    IFBLOG("hybrid: firmware panel state captured for resume");
}

bool NMIntelFB::resumeRun()
{
    // PCI and the MMIO BAR must be accessible before replaying any state.
    if (fPCI->configRead16(kIOPCIConfigVendorID) != 0x8086 || rd(TRANSCONF(fPipe)) == ~0u)
        return false;
    if (!dmcApply()) IFBLOG("hybrid: display firmware reload failed");
    if (fSnap) {
        if (fRamMode) {
            for (unsigned i = 0; i < static_cast<unsigned>(fRamMode); ++i) {
                if (!ramPtes(i, true)) { quarantineRam(); return false; }
            }
        } else if (fFwPte) {
            auto *table = ggtt() + (fSurf >> 12);
            for (unsigned i = 0; i < fFwPteCount; ++i) table[i] = fFwPte[i];
            (void)table[fFwPteCount - 1];
            wr(0x101008, 1);
        }
        const auto ops = panelOps();
        const int rc = fVer >= 14 ? nmr20_resume(&ops, fSnap) : nmr_resume(&ops, fSnap, fVer);
        IFBLOG("hybrid: resume: %s", nmr_result_name(rc));
        if (rc != NMR_OK && rc != NMR_ALREADY_LIT) return false;
        if (fRamMode == 2) {
            wr(PLANE_SURF(fPipe), fRamGgtt[fFront] | fSurfLow);
            if (!flipLatched(fRamGgtt[fFront])) { quarantineRam(); return false; }
        }
    }
    if (fPwmPeriod && rd(kBlcPwmFreq) == fPwmPeriod && (rd(kBlcPwmCtl) & kBlcPwmEnable))
        wr(kBlcPwmDuty, fPwmDuty);
    return true;
}

struct NMIntelFB::ExtHead {
    IOBufferMemoryDescriptor *buffer;
    uint64_t first;
    unsigned pages;
    struct nmo mode;
};

void *NMIntelFB::extCreate(void *, unsigned, unsigned)
{
    auto *head = IONewZero(ExtHead, 1);
    if (!head) return nullptr;
    head->buffer = IOBufferMemoryDescriptor::inTaskWithOptions(kernel_task, kIODirectionInOut,
                                                               1920u * 1080u * 4u, 4096);
    if (head->buffer && head->buffer->prepare() == kIOReturnSuccess) return head;
    OSSafeReleaseNULL(head->buffer);
    IODelete(head, ExtHead, 1);
    return nullptr;
}

int NMIntelFB::extBringUp(void *ctx, void *object, unsigned port)
{
    auto *panel = static_cast<NMIntelFB *>(ctx);
    auto *head = static_cast<ExtHead *>(object);
    // The test output needs an unowned pipe and a display-buffer range.
    // Use one external pipe so its buffer cannot overlap another test output.
    unsigned pipe = 4;
    for (unsigned i = 0; i < 4; ++i) {
        if (i == static_cast<unsigned>(panel->fPipe)) continue;
        if (panel->rd(TRANSCONF(i)) & kEnable) return -1;
        if (pipe == 4) pipe = i;
    }
    const unsigned ddi = 3 + port;
    if (pipe == 4 || (panel->rd(NMR_DDI_BUF_CTL(ddi)) & NMR_DDI_ENABLE))
        return -1;

    const size_t bytes = head->buffer->getLength();
    const unsigned pages = static_cast<unsigned>((bytes + 4095) / 4096);
    auto *table = panel->ggtt();
    const uint64_t entries = panel->fApertureLen / 4096 < (1ull << 20) ?
                             panel->fApertureLen / 4096 : (1ull << 20);
    const uint64_t first = nmintelfb::emptyGgttRange(table, entries, pages, 512);
    if (first == UINT64_MAX || !nmintelfb::ggttRangeFits(panel->fMMIO->getLength(),
                                                      panel->fApertureLen, first * 4096, pages))
        return -1;

    const auto ops = panel->panelOps();
    const uint32_t setting = (panel->rd(NMR_DSSM) >> 29) & 7u;
    if (setting > 2) return -1;
    const unsigned ref = setting == 0 ? 24000u : setting == 1 ? 19200u : 38400u;
    if (nmo_init(&ops, &head->mode, panel->fVer, port, pipe, ref)) return -1;
    const uint32_t dbuf = panel->rd(NMR_PLANE_WM_BASE(panel->fPipe) + 0x3c);
    const unsigned total = nmw_dbuf_total(panel->fVer);
    const unsigned ownStart = dbuf & 0xfffu, ownEnd = ((dbuf >> 16) & 0xfffu) + 1;
    if (ownEnd > total || ownEnd <= ownStart) return -1;
    unsigned start, end;
    nmw_free_run(total, ownStart, ownEnd, &start, &end);
    if (start >= end) return -1;

    // Keep the pages mapped until teardown confirms the pipe is off.
    head->first = first;
    for (unsigned i = 0; i < pages; ++i) {
        IOByteCount length = 0;
        const auto address = head->buffer->getPhysicalSegment(IOByteCount(i) * 4096, &length, kIOMemoryMapperNone);
        const uint64_t pte = length >= 4096 ? nmh_ggtt_pte(address) : 0;
        if (!pte) return -1;
        table[first + i] = pte;
        ++head->pages;
    }
    (void)table[first + pages - 1];
    panel->wr(0x101008, 1);
    bzero(head->buffer->getBytesNoCopy(), bytes);
    if (head->buffer->performOperation(kIOMemoryIncoherentIOFlush, 0, bytes) != kIOReturnSuccess) return -1;
    const int rc = nmo_enable(&ops, &head->mode, static_cast<uint32_t>(first * 4096),
                              static_cast<uint32_t>(bytes), start, end);
    if (rc) { IFBLOG("hybrid: Type-C test head: %s", nmo_err_name(rc)); return -1; }

    static const uint32_t colors[] = {0xffffff, 0xffff00, 0x00ffff, 0x00ff00, 0xff00ff, 0xff0000, 0x0000ff, 0};
    auto *pixels = static_cast<uint32_t *>(head->buffer->getBytesNoCopy());
    const unsigned width = head->mode.t.hactive, height = head->mode.t.vactive, pitch = head->mode.pitch / 4;
    for (unsigned y = 0; y < height; ++y)
        for (unsigned x = 0; x < width; ++x) pixels[size_t(y) * pitch + x] = colors[x * 8 / width];
    return head->buffer->performOperation(kIOMemoryIncoherentIOFlush, 0, bytes) == kIOReturnSuccess ? 0 : -1;
}

void NMIntelFB::extTakeDown(void *ctx, void *object, unsigned)
{
    auto *panel = static_cast<NMIntelFB *>(ctx);
    auto *head = static_cast<ExtHead *>(object);
    const auto ops = panel->panelOps();
    nmo_teardown(&ops, &head->mode);
    if (!head->pages) return;
    const auto pipe = head->mode.pipe;
    if (!nmr_wait(&ops, TRANSCONF(pipe), kEnable | (1u << 30), 0, 20000)) {
        panel->quarantineRam();
        // An unconfirmed teardown may still scan this buffer. Retain it to reboot.
        head->buffer = nullptr;
        return;
    }
    auto *table = panel->ggtt();
    for (unsigned i = 0; i < head->pages; ++i) table[head->first + i] = 0;
    (void)table[head->first + head->pages - 1];
    panel->wr(0x101008, 1);
}

void NMIntelFB::extRelease(void *, void *object)
{
    auto *head = static_cast<ExtHead *>(object);
    if (head->buffer) { head->buffer->complete(); head->buffer->release(); }
    IODelete(head, ExtHead, 1);
}

void NMIntelFB::tcStart()
{
    unsigned testHead = 0, observe = 0;
    const bool external = PE_parse_boot_argn("nmhybridext", &testHead, sizeof testHead) && testHead == 1;
    if (!external && (!PE_parse_boot_argn("nmhybridtc", &observe, sizeof observe) || observe != 1)) return;
    if (fVer != 12 && fVer != 13) return;
    const auto ops = panelOps();
    for (unsigned port = 0; port < 4; ++port) {
        if (nmt_init(&ops, &fTcPort[fTcN], fVer, port, 0) == 0) ++fTcN;
    }
    if (!fTcN || !external || !ggtt()) return;
    nmhd_init(&fHeads);
    fHeadOps = {
        this, [](void *) { return 0; }, [](void *) {}, extCreate, extBringUp,
        [](void *, void *, unsigned) { IFBLOG("hybrid: Type-C test head displaying color bars"); return 0; },
        [](void *, void *) {}, extTakeDown, extRelease,
    };
    fExtOn = nmhd_panel_up(&fHeads, &fHeadOps) == 0;
}

void NMIntelFB::tcScan()
{
    const auto ops = panelOps();
    for (unsigned i = 0; i < fTcN; ++i) {
        auto &port = fTcPort[i];
        const int event = nmt_poll(&ops, &port, &fTcLast[i]);
        if (!event) continue;
        const unsigned mode = nmt_mask_to_mode(fTcLast[i]);
        IFBLOG("hybrid: Type-C port %u changed, live mode %u", port.tc + 1, mode);
        if (!fExtOn) continue;
        nmhd_unplug(&fHeads, &fHeadOps, port.tc);
        if (mode == NMT_DPALT && !quarantined() && !nmhd_count(&fHeads))
            nmhd_plug(&fHeads, &fHeadOps, port.tc);
    }
}

void NMIntelFB::tcStop()
{
    if (fExtOn) {
        for (unsigned i = 0; i < fTcN; ++i) nmhd_unplug(&fHeads, &fHeadOps, fTcPort[i].tc);
    }
    bzero(fTcLast, sizeof fTcLast);
}

void NMIntelFB::schedulePanelCall(unsigned delayMs)
{
    uint64_t deadline;
    clock_interval_to_deadline(delayMs, kMillisecondScale, &deadline);
    if (!thread_call_enter_delayed(fPanelCall, deadline)) ++fPanelCalls;
}

void NMIntelFB::panelFire(thread_call_param_t arg, thread_call_param_t)
{
    auto *panel = static_cast<NMIntelFB *>(arg);
    IOLockLock(panel->fRamLock);
    if (!__atomic_load_n(&panel->fStopping, __ATOMIC_ACQUIRE)) {
        if (panel->fPanelWake) {
            if (panel->fFrameGate.busy()) panel->schedulePanelCall(10);
            else {
                panel->fPanelWake = false;
                panel->fPanelSleeping = !panel->resumeRun();
            }
        }
        if (!panel->fPanelSleeping && panel->fTcN) {
            panel->tcScan();
            panel->schedulePanelCall(2000);
        }
    }
    --panel->fPanelCalls;
    IOLockWakeup(panel->fRamLock, &panel->fPanelCalls, false);
    IOLockUnlock(panel->fRamLock);
}

void NMIntelFB::advancedStart()
{
    unsigned resume = 0, dmc = 0, tc = 0, ext = 0;
    PE_parse_boot_argn("nmhybridresume", &resume, sizeof resume);
    PE_parse_boot_argn("nmhybriddmc", &dmc, sizeof dmc);
    PE_parse_boot_argn("nmhybridtc", &tc, sizeof tc);
    PE_parse_boot_argn("nmhybridext", &ext, sizeof ext);
    if (resume != 1 && dmc != 1 && tc != 1 && ext != 1) return;
    if (!fMMIO || fMMIO->getLength() < (2ull << 20) || !ramHardwareEligible()) return;
    fPanelCall = thread_call_allocate_with_options(panelFire, this, THREAD_CALL_PRIORITY_KERNEL, THREAD_CALL_OPTIONS_ONCE);
    if (!fPanelCall) return;
    resumeCapture();
    tcStart();
    fDmcWant = dmc == 1 ? nmd_file_for(fVer, fPCI->configRead16(kIOPCIConfigDeviceID)) : nullptr;
    if (fDmcWant) {
        IOLockLock(gIfbLock);
        if (!gDmcTarget) { gDmcTarget = this; fDmcOn = true; }
        IOLockUnlock(gIfbLock);
        if (fDmcOn) {
            sysctl_register_oid(&sysctl__debug_nmintelfb_dmc);
            sysctl_register_oid(&sysctl__debug_nmintelfb_dmc_want);
        }
    }
    if (fTcN) {
        IOLockLock(fRamLock);
        schedulePanelCall(2000);
        IOLockUnlock(fRamLock);
    }
}

void NMIntelFB::advancedPower(bool on)
{
    if (!fRamLock) return;
    IOLockLock(fRamLock);
    if (fPanelCall && !__atomic_load_n(&fStopping, __ATOMIC_ACQUIRE)) {
        if (!on) {
            fPanelSleeping = true;
            fPanelWake = false;
            tcStop();
        } else if (fPanelSleeping) {
            fPanelWake = true;
            schedulePanelCall(100);
        }
    }
    IOLockUnlock(fRamLock);
}

void NMIntelFB::advancedStop()
{
    if (!fRamLock || !fPanelCall) return;
    if (fDmcOn) {
        IOLockLock(gIfbLock);
        gDmcTarget = nullptr;
        IOLockUnlock(gIfbLock);
        sysctl_unregister_oid(&sysctl__debug_nmintelfb_dmc);
        sysctl_unregister_oid(&sysctl__debug_nmintelfb_dmc_want);
    }
    IOLockLock(fRamLock);
    if (thread_call_cancel(fPanelCall)) --fPanelCalls;
    while (fPanelCalls) IOLockSleep(fRamLock, &fPanelCalls, THREAD_UNINT);
    thread_call_free(fPanelCall);
    fPanelCall = nullptr;
    if (fExtOn) { nmhd_stop(&fHeads, &fHeadOps); fExtOn = false; }
    if (fSnap) { IODelete(fSnap, struct nmr_snap, 1); fSnap = nullptr; }
    if (fFwPte) { IODelete(fFwPte, uint64_t, fFwPteCount); fFwPte = nullptr; }
    if (fDmcBlob) { IOFree(fDmcBlob, fDmcLen); fDmcBlob = nullptr; }
    fDmcOn = false;
    if (fPanelSleeping && fRamMode) quarantineRam();
    IOLockUnlock(fRamLock);
}
