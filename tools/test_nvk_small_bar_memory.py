#!/usr/bin/env python3
"""Compare actual Mesa memory-type initialization before/after the small-BAR patch."""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

repo = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--mesa', type=Path, required=True, help='Patched Mesa source tree')
parser.add_argument('--headers', type=Path, default=Path.home() / 'Vulkan-Headers/include')
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=False)
relative = Path('src/nouveau/vulkan/nvk_physical_device.c')
current = (args.mesa / relative).read_text()
with tempfile.TemporaryDirectory(prefix='nvk-small-bar-') as temp:
    root = Path(temp)
    original_path = root / relative
    original_path.parent.mkdir(parents=True)
    original_path.write_text(current)
    with (repo / 'nvk/nvk-small-bar-memory.patch').open() as patch:
        subprocess.run(['patch', '-R', '-p1', '--fuzz=0', '--no-backup-if-mismatch'],
                       cwd=root, stdin=patch, check=True, capture_output=True)
    original = original_path.read_text()

def body(source):
    start = source.index('   if (pdev->info.vram_size_B > 0) {')
    end = source.index('   assert(pdev->mem_heap_count <=', start)
    return source[start:end]

harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <vulkan/vulkan.h>
#define MAXWELL_A 0xb097
#define NV_DEVICE_TYPE_DIS 0
#define NV_DEVICE_TYPE_SOC 1
struct nvk_physical_device;
struct nvk_memory_heap {
   uint64_t size;
   VkMemoryHeapFlags flags;
   uint64_t (*available)(struct nvk_physical_device *);
};
struct nvk_physical_device {
   struct { uint64_t vram_size_B, bar_size_B; uint32_t cls_eng3d, type; } info;
   uint32_t mem_heap_count, mem_type_count;
   struct nvk_memory_heap mem_heaps[3];
   VkMemoryType mem_types[3];
};
struct nvkmd_pdev { struct { bool has_get_vram_used; } kmd_info; };
static uint64_t nvk_get_vram_heap_available(struct nvk_physical_device *p) { return p->info.vram_size_B; }
static uint64_t nvk_get_sysmem_heap_available(struct nvk_physical_device *p) { (void)p; return 12ull << 30; }
__FUNCTIONS__
static void same(const struct nvk_physical_device *a, const struct nvk_physical_device *b) {
   assert(a->mem_heap_count == b->mem_heap_count);
   assert(a->mem_type_count == b->mem_type_count);
   for (unsigned i = 0; i < a->mem_heap_count; i++) {
      assert(a->mem_heaps[i].size == b->mem_heaps[i].size);
      assert(a->mem_heaps[i].flags == b->mem_heaps[i].flags);
      assert(a->mem_heaps[i].available == b->mem_heaps[i].available);
   }
   for (unsigned i = 0; i < a->mem_type_count; i++) {
      assert(a->mem_types[i].propertyFlags == b->mem_types[i].propertyFlags);
      assert(a->mem_types[i].heapIndex == b->mem_types[i].heapIndex);
   }
}
int main(void) {
   const uint64_t vram[] = {1ull << 30, 8ull << 30, 24ull << 30};
   const uint64_t bars[] = {0, 64ull << 20, 256ull << 20, 4ull << 30, 8ull << 30, 32ull << 30};
   unsigned total = 0, full_bar = 0, partial_bar = 0;
   for (unsigned v = 0; v < 3; v++) for (unsigned b = 0; b < 6; b++)
   for (unsigned generation = 0; generation < 2; generation++) for (unsigned budget = 0; budget < 2; budget++) {
      struct nvk_physical_device old = {0}, mac = {0}, other = {0};
      old.info.vram_size_B = vram[v]; old.info.bar_size_B = bars[b];
      old.info.cls_eng3d = MAXWELL_A - generation; old.info.type = NV_DEVICE_TYPE_DIS;
      mac.info = other.info = old.info;
      struct nvkmd_pdev kmd = { .kmd_info = { .has_get_vram_used = budget } };
      baseline(&old, &kmd, 12ull << 30);
      candidate_mac(&mac, &kmd, 12ull << 30);
      candidate_other(&other, &kmd, 12ull << 30);
      same(&old, &other); /* Non-Darwin behavior is preserved for every case. */
      if (bars[b] >= vram[v] || generation) {
         same(&old, &mac);
         if (!generation) full_bar++;
      } else {
         assert(mac.mem_heap_count == 2 && mac.mem_type_count == 2);
         assert(mac.mem_heaps[0].size == vram[v]);
         assert(mac.mem_heaps[0].flags == VK_MEMORY_HEAP_DEVICE_LOCAL_BIT);
         assert(mac.mem_heaps[1].size == 12ull << 30);
         assert(mac.mem_heaps[1].flags == 0);
         assert(mac.mem_types[0].propertyFlags == VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
         assert(mac.mem_types[0].heapIndex == 0);
         assert(mac.mem_types[1].propertyFlags == (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
                VK_MEMORY_PROPERTY_HOST_COHERENT_BIT | VK_MEMORY_PROPERTY_HOST_CACHED_BIT));
         assert(mac.mem_types[1].heapIndex == 1);
         partial_bar++;
      }
      total++;
   }
   for (unsigned type = 0; type < 2; type++) {
      struct nvk_physical_device old = {0}, mac = {0}, other = {0};
      old.info.type = type; old.info.cls_eng3d = MAXWELL_A;
      mac.info = other.info = old.info;
      struct nvkmd_pdev kmd = {0};
      baseline(&old, &kmd, 12ull << 30); candidate_mac(&mac, &kmd, 12ull << 30);
      candidate_other(&other, &kmd, 12ull << 30);
      same(&old, &mac); same(&old, &other); total++;
   }
   assert(full_bar && partial_bar);
   printf("PASS: %u memory layouts; %u full-BAR layouts unchanged; %u partial-BAR layouts use coherent system RAM; non-Darwin and no-VRAM layouts unchanged\n", total, full_bar, partial_bar);
}
'''
functions = []
for name, source, darwin in [('baseline', original, True), ('candidate_mac', current, True),
                             ('candidate_other', current, False)]:
    functions.append(('#define __APPLE__ 1\n' if darwin else '#undef __APPLE__\n') +
                     f'static void {name}(struct nvk_physical_device *pdev, '
                     'struct nvkmd_pdev *nvkmd, uint64_t heap_size) {\n' + body(source) + '}\n')
    if darwin:
        functions.insert(len(functions) - 1, '#undef __APPLE__\n')
source = args.output / 'memory-types.c'
source.write_text(harness.replace('__FUNCTIONS__', '\n'.join(functions)))
binary = args.output / 'memory-types'
compiler = shutil.which('clang') or shutil.which('cc')
if not compiler:
    raise SystemExit('A C compiler is required')
subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-I' + str(args.headers),
                str(source), '-o', str(binary)], check=True)
result = subprocess.run([str(binary)], check=True, text=True, capture_output=True)
(args.output / 'RESULTS.log').write_text(result.stdout)
print(result.stdout, end='')
