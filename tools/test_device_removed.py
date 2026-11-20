#!/usr/bin/env python3
"""Check that the production queue completes a post-loss buffer through the normal error pipeline."""
from pathlib import Path

root = Path(__file__).resolve().parents[1]
vk = (root / 'plugin/nvmtl_vk.c').read_text()
objects = (root / 'plugin/NVMTLObjects.m').read_text()
events = (root / 'plugin/NVMTLEventSync.m').read_text()
removed = vk.split('int nvmtl_vk_device_removed(void)', 1)[1].split('\n}', 1)[0]
assert 'nvmtl_vk_lost() && atomic_load(&g_up_ns) != 0' in removed
begin = objects.split('if (nvmtl_vk_cmd_begin(&_q, &cb->_c)) {', 1)[1].split('return nil;', 1)[0]
assert 'if (nvmtl_vk_device_removed())' in begin
assert 'cb->_recordingUnavailable = YES;' in begin and 'return cb;' in begin
assert 'nvmtlRecordEncodingError:nvmtl_device_removed_error()' in begin
assert 'MTLCommandBufferErrorDeviceRemoved' in events
submit = events.split('- (void)nvmtlSubmitWithEvents {', 1)[1].split('\n}', 1)[0]
assert 'if (!_recordingUnavailable) {\n            [self nvmtlSealPartWithSignal:' in submit
assert 'if (_cbError) { [self nvmtlFinishExecution:_cbError]; return YES; }' in events
assert '[nvmtl_quarantined_parts addObject:' in events
assert 'nvmtl_vk_stub_recording' not in vk
print('PASS: post-loss completion, initial refusal and in-flight quarantine')
