/* Concurrent first storage-format queries through one isolated driver image.
 * Build: clang -O2 tools/test_format_capability_init_gpu.c -o format-init-gpu
 * Run each cold test in a fresh process: format-init-gpu /absolute/path/NVMTLDriver
 */
#include <dlfcn.h>
#include <pthread.h>
#include <stdatomic.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>

static _Atomic unsigned ready, start, failures;
static int (*query)(uint32_t);
static void *worker(void *unused) {
    (void)unused;
    atomic_fetch_add(&ready, 1);
    while (!atomic_load_explicit(&start, memory_order_acquire)) sched_yield();
    if (!query(64)) atomic_fetch_add(&failures, 1);
    return NULL;
}
int main(int argc, char **argv) {
    if (argc != 2 || argv[1][0] != '/') return 2;
    void *library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!library) { puts(dlerror()); return 2; }
    int (*init)(void) = dlsym(library, "nvmtl_vk_init");
    query = dlsym(library, "nvmtl_vk_format_is_storage");
    if (!query || !init || init()) return 3;
    enum { Threads = 12 }; pthread_t threads[Threads];
    for (unsigned i=0; i<Threads; i++) if (pthread_create(threads+i, NULL, worker, NULL)) return 4;
    while (atomic_load(&ready) != Threads) sched_yield();
    atomic_store_explicit(&start, 1, memory_order_release);
    for (unsigned i=0; i<Threads; i++) pthread_join(threads[i], NULL);
    if (!query(64)) { puts("SKIP: this GPU does not expose packed 10-bit storage images"); return 77; }
    printf("parallel_false=%u serial_storage=1 threads=%u\n", atomic_load(&failures), Threads);
    return atomic_load(&failures) ? 1 : 0;
}
