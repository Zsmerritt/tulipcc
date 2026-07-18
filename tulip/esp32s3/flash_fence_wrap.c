// flash_fence_wrap.c -- the flash fence moved into the C storage layer.
//
// THE CRASH CLASS: a flash program/erase suspends the SPI cache; any AMY
// render fetching mmap'd PCM during that window hard-faults both cores
// (TG1WDT). The first fix exposed tulip.flash_fence() to Python and made
// deckcfg/decklog opt in around their writes -- but a correctness property
// enforced by convention in N call sites is enforced in zero (review E-2 /
// C-2): Files delete, editor saves, screenshots, tulip.upgrade, NVS commits
// and every future write path stayed unfenced.
//
// THIS FILE closes it at the only layer that sees every write: the IDF
// partition API, via linker --wrap (see esp32_common.cmake). littlefs
// (micropython's esp32_partition writeblocks), OTA, and NVS all funnel
// through esp_partition_write/write_raw/erase_range, so no caller can
// forget the fence again -- including code that hasn't been written yet.
//
// WHY NOT CONFIG_SPI_FLASH_AUTO_SUSPEND INSTEAD? (investigated 2026-07-17;
// do not re-propose without re-checking these two facts.) It is the obvious
// hardware answer -- a cache miss during a program/erase would STALL ~50-100us
// instead of faulting, making this whole file belt-and-braces -- and the S3
// does support it (soc_caps: SOC_SPI_MEM_SUPPORT_AUTO_SUSPEND=1), so Kconfig
// will happily accept the option. It still must NOT be enabled here:
//
//   1. Our flash is Macronix octal (mfr 0xC2, OPI, 32MB) => driven by IDF's
//      spi_flash_chip_mxic_opi.c, whose get_caps() advertises only
//      CAP_32MB_SUPPORT and says verbatim: "flash-suspend is not supported
//      yet. // IDF-3852". Espressif has not done the work for this part.
//   2. Enabling it anyway is not a no-op. esp_flash_api.c's suspend setup only
//      WARNS when CAP_SUSPEND is absent ("Suspend and resume may not supported
//      for this flash model yet.") and then calls sus_setup() regardless -- and
//      mxic_opi wires sus_setup to the GENERIC suspend command config. So the
//      option would start issuing unvalidated suspend/resume opcodes to this
//      part during program/erase. On a chip already running 120MHz octal DDR
//      -- which IDF's own Kconfig help says "will crash randomly" after a ~20C
//      swing -- that is the last place to send unvalidated opcodes.
//
// Re-evaluate only if IDF-3852 closes and mxic_opi_get_caps starts returning
// SPI_FLASH_CHIP_CAP_SUSPEND. Until then this fence is load-bearing, not
// belt-and-braces.
//
// HANDSHAKE, not a timed guess: render_pcm samples the fence once per
// block, so after raising it we wait until amy_global.total_blocks has
// advanced by 2 -- every render that started before the fence was visible
// has finished (render+fill are one pipeline). The old Python rule was
// "sleep >= 12ms and hope"; this is the same bound made exact, and it
// waits only as long as it must.
//
// BATCHING: littlefs issues many small writes per transaction. Raising +
// handshaking per 4KB block would cost ~12ms each, so release is DEFERRED
// ~20ms via esp_timer: a burst of writes pays ONE handshake, and the fence
// drops shortly after the burst ends. The 20ms of extra PCM-fetch silence
// after the last write is inaudible next to the write itself.

#include "py/mpconfig.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_partition.h"
#include "esp_timer.h"
#include "esp_err.h"

#include "amy.h"    // amy_flash_fence, amy_global.total_blocks

// The whole fence design assumes CODE keeps executing from the PSRAM
// cache while a flash op suspends the flash cache (review FW-15) -- make
// un-setting these a build error, not a mystery crash.
#include "sdkconfig.h"
#if !defined(CONFIG_SPIRAM_FETCH_INSTRUCTIONS) || !defined(CONFIG_SPIRAM_RODATA)
#error "flash fence requires CONFIG_SPIRAM_FETCH_INSTRUCTIONS + CONFIG_SPIRAM_RODATA (code must run from PSRAM during flash ops)"
#endif

esp_err_t __real_esp_partition_write(const esp_partition_t *partition,
                                     size_t dst_offset, const void *src, size_t size);
esp_err_t __real_esp_partition_write_raw(const esp_partition_t *partition,
                                         size_t dst_offset, const void *src, size_t size);
esp_err_t __real_esp_partition_erase_range(const esp_partition_t *partition,
                                           size_t offset, size_t size);
// chip-level API (FW-8): esp.flash_write/flash_erase bypass the partition
// layer. Declared with void* to avoid dragging esp_flash types here.
esp_err_t __real_esp_flash_write(void *chip, const void *buffer, uint32_t address, uint32_t length);
esp_err_t __real_esp_flash_erase_region(void *chip, uint32_t start, uint32_t len);

static portMUX_TYPE fence_mux = portMUX_INITIALIZER_UNLOCKED;
static volatile int fence_depth = 0;
static esp_timer_handle_t fence_drop_timer = NULL;

#define FENCE_DROP_DELAY_US  20000   // burst window: writes this close share one handshake
#define FENCE_WAIT_MAX_TICKS 25      // bound the handshake (AMY parked/not started)

static void fence_drop_cb(void *arg) {
    (void)arg;
    portENTER_CRITICAL(&fence_mux);
    if (fence_depth == 0) {
        amy_flash_fence = 0;
    }
    portEXIT_CRITICAL(&fence_mux);
}

// The ONLY way anything outside this file may lower the fence: it re-checks
// depth under the mux, so a Python override (tulip.flash_fence(0)) or a
// fallback path here can't drop the fence out from under a wrapped flash op
// that is mid-write -- which is exactly the dual-core WDT crash the fence
// exists to prevent.
void tulip_flash_fence_manual_drop(void) {
    portENTER_CRITICAL(&fence_mux);
    if (fence_depth == 0) {
        amy_flash_fence = 0;
    }
    portEXIT_CRITICAL(&fence_mux);
}

void tulip_flash_fence_acquire(void) {
    bool need_handshake = false;
    portENTER_CRITICAL(&fence_mux);
    fence_depth++;
    if (fence_depth == 1) {
        if (!amy_flash_fence) {
            // fence was genuinely down: a render block may be mid-fetch
            amy_flash_fence = 1;
            need_handshake = true;
        }
        // else: still up from a deferred drop -- renders are already
        // skipping fetches, no wait needed
    }
    portEXIT_CRITICAL(&fence_mux);
    if (fence_drop_timer) {
        esp_timer_stop(fence_drop_timer);   // cancel a pending drop (ok if none)
    }
    if (need_handshake) {
        uint32_t b = amy_global.total_blocks;
        // b == 0: AMY hasn't rendered yet (early boot -- NVS/wifi writes
        // land before audio starts). No fetches can race; don't burn the
        // 25ms bounded wait per boot-time write burst.
        if (b != 0) {
            int guard = 0;
            // two block boundaries: every render that started before the
            // fence was visible has finished. Bounded in case AMY parked.
            while (amy_global.total_blocks < b + 2 && guard++ < FENCE_WAIT_MAX_TICKS) {
                vTaskDelay(1);
            }
        }
    }
}

void tulip_flash_fence_release(void) {
    portENTER_CRITICAL(&fence_mux);
    if (fence_depth > 0) {
        fence_depth--;
    }
    bool drop = (fence_depth == 0);
    portEXIT_CRITICAL(&fence_mux);
    if (!drop) {
        return;
    }
    if (fence_drop_timer == NULL) {
        // race-free creation (review FW-11): exactly one task claims the
        // build; a concurrent releaser just drops immediately this once
        static volatile uint8_t creating = 0;
        uint8_t expected = 0;
        if (!__atomic_compare_exchange_n(&creating, &expected, 1, false,
                                         __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
            // depth-rechecking drop, not a bare store: between our depth==0
            // read above and here, another task can have acquired (depth 1)
            // and skipped its handshake because the fence still read 1 --
            // a bare store would then drop the fence during ITS write.
            tulip_flash_fence_manual_drop();
            return;
        }
        const esp_timer_create_args_t args = {
            .callback = fence_drop_cb,
            .name = "flash_fence",
        };
        esp_timer_handle_t t = NULL;
        if (esp_timer_create(&args, &t) != ESP_OK) {
            creating = 0;
            tulip_flash_fence_manual_drop();  // no timer: drop now, if safe
            return;
        }
        fence_drop_timer = t;
    }
    esp_timer_stop(fence_drop_timer);
    if (esp_timer_start_once(fence_drop_timer, FENCE_DROP_DELAY_US) != ESP_OK) {
        tulip_flash_fence_manual_drop();   // no deferred drop coming: drop now, if safe
    }
}

esp_err_t __wrap_esp_partition_write(const esp_partition_t *partition,
                                     size_t dst_offset, const void *src, size_t size) {
    tulip_flash_fence_acquire();
    esp_err_t r = __real_esp_partition_write(partition, dst_offset, src, size);
    tulip_flash_fence_release();
    return r;
}

esp_err_t __wrap_esp_partition_write_raw(const esp_partition_t *partition,
                                         size_t dst_offset, const void *src, size_t size) {
    tulip_flash_fence_acquire();
    esp_err_t r = __real_esp_partition_write_raw(partition, dst_offset, src, size);
    tulip_flash_fence_release();
    return r;
}

esp_err_t __wrap_esp_partition_erase_range(const esp_partition_t *partition,
                                           size_t offset, size_t size) {
    tulip_flash_fence_acquire();
    esp_err_t r = __real_esp_partition_erase_range(partition, offset, size);
    tulip_flash_fence_release();
    return r;
}

esp_err_t __wrap_esp_flash_write(void *chip, const void *buffer, uint32_t address, uint32_t length) {
    tulip_flash_fence_acquire();
    esp_err_t r = __real_esp_flash_write(chip, buffer, address, length);
    tulip_flash_fence_release();
    return r;
}

esp_err_t __wrap_esp_flash_erase_region(void *chip, uint32_t start, uint32_t len) {
    tulip_flash_fence_acquire();
    esp_err_t r = __real_esp_flash_erase_region(chip, start, len);
    tulip_flash_fence_release();
    return r;
}
