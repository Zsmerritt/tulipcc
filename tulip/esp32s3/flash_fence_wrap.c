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

esp_err_t __real_esp_partition_write(const esp_partition_t *partition,
                                     size_t dst_offset, const void *src, size_t size);
esp_err_t __real_esp_partition_write_raw(const esp_partition_t *partition,
                                         size_t dst_offset, const void *src, size_t size);
esp_err_t __real_esp_partition_erase_range(const esp_partition_t *partition,
                                           size_t offset, size_t size);

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
        int guard = 0;
        // two block boundaries: every render that started before the fence
        // was visible has finished. Bounded in case AMY isn't running.
        while (amy_global.total_blocks < b + 2 && guard++ < FENCE_WAIT_MAX_TICKS) {
            vTaskDelay(1);
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
        const esp_timer_create_args_t args = {
            .callback = fence_drop_cb,
            .name = "flash_fence",
        };
        if (esp_timer_create(&args, (esp_timer_handle_t *)&fence_drop_timer) != ESP_OK) {
            amy_flash_fence = 0;            // no timer: drop immediately
            return;
        }
    }
    esp_timer_stop(fence_drop_timer);
    if (esp_timer_start_once(fence_drop_timer, FENCE_DROP_DELAY_US) != ESP_OK) {
        amy_flash_fence = 0;
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
