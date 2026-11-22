/*
 * NullMoth NVIDIA driver for macOS
 * Copyright (c) 2026 NullMoth Systems.
 * SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
 */

// The selected framebuffers keep their individual NVKMS heads and audio
// identities. The group gate serializes their scanout while the display lock
// can be dropped to wait for a flip.
bool NVRMNVDAFramebuffer::planSurround(const unsigned *indices, unsigned count)
{
    struct Modes {
        NvrmSurMode dimensions[kNvrmSurMaxMembers][kNvrmSurMaxModes];
        struct NvKmsKapiDisplayMode modes[kNvrmSurMaxMembers][kNvrmSurMaxModes];
    };
    auto *available = IONew(Modes, 1);
    if (!available) return false;
    unsigned lengths[kNvrmSurMaxMembers] = {};
    for (unsigned k = 0; k < count; ++k) {
        auto *fb = gFBs[indices[k]];
        for (NvU32 i = 0; i < 256 && lengths[k] < kNvrmSurMaxModes; ++i) {
            struct NvKmsKapiDisplayMode mode = {};
            NvBool valid = NV_FALSE, preferred = NV_FALSE;
            const int rc = fKms->getDisplayMode(fDev, fb->fDisplay, i, &mode, &valid, &preferred);
            if (rc < 0) break;
            const auto &t = mode.timings;
            if (!rc || !valid || t.flags.interlaced || t.flags.doubleScan || !t.pixelClockHz ||
                !nvrmKmsRasterValid(t.hVisible, t.vVisible) ||
                t.hTotal <= t.hVisible || t.vTotal <= t.vVisible ||
                t.hSyncStart < t.hVisible || t.hSyncEnd < t.hSyncStart || t.hSyncEnd > t.hTotal ||
                t.vSyncStart < t.vVisible || t.vSyncEnd < t.vSyncStart || t.vSyncEnd > t.vTotal)
                continue;
            const uint64_t pixels = uint64_t(t.hTotal) * t.vTotal;
            const unsigned hz = static_cast<unsigned>((t.pixelClockHz + pixels / 2) / pixels);
            if (hz < 23 || hz > 500) continue;
            const unsigned slot = lengths[k]++;
            available->dimensions[k][slot] = {t.hVisible, t.vVisible, hz};
            available->modes[k][slot] = mode;
        }
    }

    unsigned picks[kNvrmSurMaxMembers], width = 0, height = 0, hz = 0;
    bool ok = nvrmSurroundPlan(available->dimensions, lengths, count, picks, &width, &height, &hz);
    NvU32 pitch = 0;
    ok = ok && nvrmKmsRasterValid(width, height) && nvrmCheckedPitch(width, fPitchAlign, &pitch) &&
        uint64_t(pitch) * height <= fMemBytes;
    if (ok) {
        unsigned x = 0;
        for (unsigned k = 0; k < count; ++k) {
            auto *fb = gFBs[indices[k]];
            const unsigned w = available->dimensions[k][picks[k]].w;
            fSur[k] = {fb, fb->fDisplay, fb->fHead, x, w, available->modes[k][picks[k]]};
            x += w;
        }
        fSurN = count;
        const auto &t = fSur[0].mode.timings;
        fSurTiming = {};
        fSurTiming.horizontalActive = width;
        fSurTiming.verticalActive = height;
        fSurTiming.horizontalBlanking = t.hTotal - t.hVisible;
        fSurTiming.verticalBlanking = t.vTotal - t.vVisible;
        fSurTiming.horizontalSyncOffset = t.hSyncStart - t.hVisible;
        fSurTiming.horizontalSyncPulseWidth = t.hSyncEnd - t.hSyncStart;
        fSurTiming.verticalSyncOffset = t.vSyncStart - t.vVisible;
        fSurTiming.verticalSyncPulseWidth = t.vSyncEnd - t.vSyncStart;
        fSurTiming.horizontalSyncConfig = t.flags.hSyncPos ? 1 : 0;
        fSurTiming.verticalSyncConfig = t.flags.vSyncPos ? 1 : 0;
        fSurTiming.signalConfig = kIODigitalSignal;
        fSurTiming.detailedTimingModeID = 0x40000u | hz;
        const uint64_t pixels = uint64_t(width + fSurTiming.horizontalBlanking) *
                                (height + fSurTiming.verticalBlanking);
        fSurTiming.pixelClock = fSurTiming.minPixelClock = fSurTiming.maxPixelClock = pixels * hz;
    }
    IODelete(available, Modes, 1);
    return ok;
}

void NVRMNVDAFramebuffer::surroundReplicate(struct NvKmsKapiRequestedModeSetConfig *cfg)
{
    const auto master = cfg->headRequestedConfig[fHead];
    for (unsigned k = 0; k < fSurN; ++k) {
        const auto &member = fSur[k];
        auto &head = cfg->headRequestedConfig[member.head];
        head = master;
        cfg->headsMask |= 1u << member.head;
        if (!head.modeSetConfig.bActive) continue;
        head.modeSetConfig.displays[0] = member.display;
        head.modeSetConfig.mode = member.mode;
        head.modeSetConfig.vrrEnabled = NV_FALSE;
        head.flags.vrrEnabledChanged = head.flags.modeChanged || head.flags.activeChanged;
        if (head.flags.modeChanged) {
            head.modeSetConfig.colorBpc = member.fb->fDviModeValidation ?
                NV_KMS_DPY_ATTRIBUTE_CURRENT_COLOR_BPC_8 : member.fb->fOutputBpc;
            head.modeSetConfig.colorFormat = member.fb->fDviModeValidation ? 0 : member.fb->fOutputFormat;
            if (member.fb->fOutputRange) nvrmSetColorRange(head, member.fb->fOutputRange, 0);
        }
        head.cursorRequestedConfig.surface = nullptr;
        head.cursorRequestedConfig.flags.surfaceChanged = head.flags.modeChanged;
        head.cursorRequestedConfig.flags.dstXYChanged = NV_FALSE;
        auto &layer = head.layerRequestedConfig[NVKMS_KAPI_LAYER_PRIMARY_IDX];
        if (!layer.config.surface) continue;
        layer.config.srcX = (NvU16)member.x;
        layer.config.srcWidth = (NvU16)member.w;
        layer.config.dstWidth = (NvU16)member.w;
        layer.config.dstHeight = (NvU16)member.mode.timings.vVisible;
        layer.flags.srcXYChanged = layer.flags.srcWHChanged = layer.flags.dstWHChanged = NV_TRUE;
    }
}

bool NVRMNVDAFramebuffer::switchSurround()
{
    const unsigned width = fSurTiming.horizontalActive, height = fSurTiming.verticalActive;
    NvU32 pitch = 0;
    if (!nvrmCheckedPitch(width, fPitchAlign, &pitch)) return false;

    // Wait for each independent head before its first grouped commit. Old
    // home scanouts stay allocated so they can be restored after unplug.
    for (unsigned k = 0; k < fSurN; ++k) {
        auto *fb = fSur[k].fb;
        if (fb->fSubmissionFaulted ||
            (fb->fSubmissionSeen && !fb->waitFlipDone(kFlipStuckUs)))
            return false;
    }
    // Waiting drops the display lock, so recheck the connector state.
    for (unsigned k = 0; k < fSurN; ++k) {
        auto *fb = fSur[k].fb;
        if (OSAddAtomic(0, &fb->fStopping) || fb->fDpyLost || fb->fHeadSlept || !fb->fPowerOn ||
            !gDpy[fb->fIndex].connected)
            return false;
    }

    auto *cfg = IONewZero(struct NvKmsKapiRequestedModeSetConfig, 1);
    auto *reply = IONewZero(struct NvKmsKapiModeSetReplyConfig, 1);
    struct NvKmsKapiCreateSurfaceParams params = {};
    params.planes[0].memory = fMem;
    params.planes[0].pitch = pitch;
    params.width = width;
    params.height = height;
    params.format = NvKmsSurfaceMemoryFormatX8R8G8B8;
    auto *surface = cfg && reply ? fKms->createSurface(fDev, &params) : nullptr;
    bool accepted = false;
    if (surface) {
        cfg->headsMask = 1u << fHead;
        auto &head = cfg->headRequestedConfig[fHead];
        nvrmHeadConfigBaseline(&head, fGammaOn && fGamma ? olutFromGamma() : nullptr);
        head.modeSetConfig.bActive = NV_TRUE;
        head.modeSetConfig.numDisplays = 1;
        head.flags.activeChanged = head.flags.displaysChanged = head.flags.modeChanged = NV_TRUE;
        auto &layer = head.layerRequestedConfig[NVKMS_KAPI_LAYER_PRIMARY_IDX];
        layer.config.surface = surface;
        layer.config.srcWidth = (NvU16)width;
        layer.config.srcHeight = (NvU16)height;
        layer.config.dstHeight = (NvU16)height;
        layer.config.compParams.compMode = NVKMS_COMPOSITION_BLENDING_MODE_OPAQUE;
        layer.config.minPresentInterval = 1;
        layer.flags.surfaceChanged = layer.flags.srcXYChanged = layer.flags.srcWHChanged =
            layer.flags.dstXYChanged = layer.flags.dstWHChanged = NV_TRUE;

        fSurBuilding = true;
        if (submitConfig(cfg, reply, NV_FALSE, false, false, false, 0))
            accepted = submitConfig(cfg, reply, NV_TRUE, true, false, false, 0);
        fSurBuilding = false;
        if (!accepted) fKms->destroySurface(fDev, surface);
    }
    if (cfg) IODelete(cfg, struct NvKmsKapiRequestedModeSetConfig, 1);
    if (reply) IODelete(reply, struct NvKmsKapiModeSetReplyConfig, 1);
    if (!accepted) return false;

    fSurHome = {fSurf, fMode, fCurDT, fW, fH, fPitch, fRasterW, fRasterH, fCurTransform, fCurId, fHdrActive, fScan30};
    fSurf = fFrontSurf = surface;
    fW = fRasterW = width;
    fH = fRasterH = height;
    fPitch = pitch;
    fMode = fSur[0].mode;
    fCurDT = fSurTiming;
    fCurId = (IODisplayModeID)fSurTiming.detailedTimingModeID;
    fHdrActive = fScan30 = fCurShownKnown = false;
    fCurTransform = 0;
    fModeSet = true;
    __atomic_store_n(&fSurActive, true, __ATOMIC_RELEASE);
    publishTimingCaps();
    publishStartupTiming();
    publishCursorGeometry(nullptr);
    publishFlipWait();
    for (unsigned k = 0; k < fSurN; ++k) {
        auto *fb = fSur[k].fb;
        fb->retain();
        if (k) {
            fb->fHidden = true;
            fb->fSubmissionSeen = false;
            fb->fSubmissionHeads = 0;
            fb->fModeSet = false;
        }
        fb->notifyConnectionChanged(100);
    }
    FBLOG("Surround formed: %u heads, %ux%u", fSurN, width, height);
    return true;
}

bool NVRMNVDAFramebuffer::releaseSurround()
{
    if (!rmGpuReady()) return false;
    auto *cfg = IONewZero(struct NvKmsKapiRequestedModeSetConfig, 1);
    auto *reply = IONewZero(struct NvKmsKapiModeSetReplyConfig, 1);
    bool detached = false;
    const bool complete = !fSubmissionSeen || waitFlipDone(kFlipStuckUs);
    if (cfg && reply) {
        for (unsigned k = 0; k < fSurN; ++k) {
            const auto h = fSur[k].head;
            cfg->headsMask |= 1u << h;
            auto &head = cfg->headRequestedConfig[h];
            head.flags.activeChanged = head.flags.displaysChanged = NV_TRUE;
        }
        detached = fKms->applyModeSetConfig(fDev, cfg, reply, NV_TRUE) &&
            reply->flipResult == NV_KMS_FLIP_RESULT_SUCCESS;
    }
    if (cfg) IODelete(cfg, struct NvKmsKapiRequestedModeSetConfig, 1);
    if (reply) IODelete(reply, struct NvKmsKapiModeSetReplyConfig, 1);
    if (!detached) return false;

    if (complete) fKms->destroySurface(fDev, fSurf);
    else {
        // An unplug can strand a completion notifier. The disable lets the
        // other heads recover. Keep surfaces with uncertain completion pinned.
        fSurQuarantined = true;
        for (auto &cached : fFlipCache) cached = {};
        fFlipCacheN = 0;
    }
    fSurf = fFrontSurf = fSurHome.surface;
    fMode = fSurHome.mode;
    fCurDT = fSurHome.timing;
    fW = fSurHome.w; fH = fSurHome.h; fPitch = fSurHome.pitch;
    fRasterW = fSurHome.rasterW; fRasterH = fSurHome.rasterH;
    fCurTransform = fSurHome.transform;
    fCurId = fSurHome.id;
    fHdrActive = fSurHome.hdr;
    fScan30 = fSurHome.scan30;
    fNDT = 0;
    fBootInDT = fModeSet = fSubmissionSeen = fSubmissionFaulted = false;
    __atomic_store_n(&fSurActive, false, __ATOMIC_RELEASE);
    fSubmissionHeads = 0;
    publishTimingCaps();
    publishStartupTiming();
    publishCursorGeometry(nullptr);
    publishFlipWait();
    for (unsigned k = 0; k < fSurN; ++k) {
        auto *fb = fSur[k].fb;
        fb->fHidden = false;
        if (!OSAddAtomic(0, &fb->fStopping) && !fb->fDpyLost) {
            if (fb->fPowerOn && !fb->fHeadSlept) (void)fb->applyMode();
            else fb->fHeadSlept = false; // enableController sets the mode on reopen.
            fb->notifyConnectionChanged(100);
        }
        if (k) fb->release();
    }
    fSurN = 0;
    FBLOG("Surround released; remaining heads use individual modes");
    release();
    return true;
}

void NVRMNVDAFramebuffer::surroundCheck()
{
    if (!gSurroundMask) return;
    NVRMDisplayGate display;
    if (!display.valid()) return;
    NVRMHeadGate group(startLock(), gSurroundGate, true);
    if (!group.valid()) return;
    unsigned indices[kNvrmSurMaxMembers];
    const unsigned count = nvrmSurroundMembers(gSurroundMask, gNDpy > 0 ? (unsigned)gNDpy : 0, indices);
    if (count < 2 || count != (unsigned)__builtin_popcount(gSurroundMask)) return;
    auto *master = gFBs[indices[0]];
    if (!master || !master->rmGpuReady()) return;
    unsigned ready = 0;
    for (unsigned k = 0; k < count; ++k) {
        auto *fb = gFBs[indices[k]];
        if (fb && fb->fOwnsHead && fb->fSurf && !fb->fConsoleAperture && !fb->fConsoleCopied && fb->fHead < 8 &&
            !OSAddAtomic(0, &fb->fStopping) && !fb->fDpyLost && !fb->fEdidSwapped &&
            !fb->fCurTransform && !fb->fTransform && gDpy[indices[k]].connected)
            ++ready;
    }
    const auto action = nvrmSurroundNext(master->fSurActive, !master->fSurActive, count, ready);
    if (action == kSurDegrade) {
        master->releaseSurround();
    } else if (action == kSurReform && !master->fSurQuarantined && master->planSurround(indices, count)) {
        if (!master->switchSurround()) {
            master->fSurN = 0;
            FBLOG("Surround mode refused; the displays stay independent");
        }
    }
}
