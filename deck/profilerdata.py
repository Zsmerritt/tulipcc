# profilerdata.py -- pure logic behind the Debug > Profiler screen.
#
# Kept deliberately free of lvgl/tulip/esp32 imports (mirrors the
# shellmodel.py / homeshell.py split) so the percent math and the
# graceful-fallback-when-render_cyc-is-absent path unit-test under plain
# CPython. profiler.py does the actual tulip/esp32/gc calls and LVGL
# drawing on top of these helpers.

# Per-block cycle budget: 240 MHz (S3 CPU clock) * 256 samples per render
# block / 44100 Hz sample rate = the CPU cycles available to render one
# audio block before it's running late. Named constant so callers that
# can't query the clock (older firmware, or a host running these tests)
# still have a sane denominator.
CYCLES_PER_BLOCK = 240_000_000 * 256 // 44100   # = 1,393,197 (~1.39M)

BLOCK_SAMPLES = 256
SAMPLE_RATE_HZ = 44100

# esp32.idf_heap_info() region total, below which a region is internal SRAM
# rather than PSRAM (PSRAM regions run into the MBs). Matches the threshold
# already used by homeshell._debug_str's status-bar readout.
INTERNAL_MAX_BYTES = 400 * 1024


def block_budget_cycles(cpu_hz=None, block_samples=BLOCK_SAMPLES,
                        sample_rate=SAMPLE_RATE_HZ):
    """The per-block cycle budget. Computed from the live CPU clock when
    `cpu_hz` is given (e.g. machine.freq()), else the compiled-in constant
    -- so a firmware/board with a different clock still gets a correct
    denominator instead of a silently-wrong percent."""
    if cpu_hz:
        try:
            return int(cpu_hz) * block_samples // sample_rate
        except Exception:
            pass
    return CYCLES_PER_BLOCK


def pct_of_budget(cycles, budget=None):
    """`cycles` as a percent of the per-block budget. Clamped at 0 (a
    negative reading is bogus, not signal) but NOT capped above 100 -- a
    core reading over 100% is real signal (it missed its render deadline)
    and hiding that would defeat the point of the screen."""
    b = CYCLES_PER_BLOCK if budget is None else budget
    if not b or b <= 0:
        return 0.0
    v = (cycles * 100.0) / b
    return v if v > 0 else 0.0


def format_pct(v):
    return "%d%%" % round(v)


def core_load_lines(render_cyc_tuple, budget=None):
    """tulip.render_cyc()'s (core0_worst, core1_worst, core0_last,
    core1_last) cycle tuple -> formatted percent strings, one last/worst
    pair per core. Pure so it's testable without tulip.render_cyc actually
    existing on this host."""
    c0w, c1w, c0l, c1l = render_cyc_tuple
    return {
        'core0_last': format_pct(pct_of_budget(c0l, budget)),
        'core0_worst': format_pct(pct_of_budget(c0w, budget)),
        'core1_last': format_pct(pct_of_budget(c1l, budget)),
        'core1_worst': format_pct(pct_of_budget(c1w, budget)),
    }


def read_render_cyc(tulip_mod):
    """Best-effort tulip.render_cyc() read. render_cyc() ships with an
    UPCOMING firmware build -- the currently-installed one may not have it
    at all, or may raise. Returns the raw 4-tuple, or None when the binding
    is absent/broken so the caller can show a 'needs newer firmware'
    fallback instead of crashing the panel build."""
    fn = getattr(tulip_mod, 'render_cyc', None)
    if fn is None:
        return None
    try:
        return fn()
    except Exception:
        return None


def reset_worst(tulip_mod):
    """tulip.render_cyc(1) resets the firmware's worst-case counters, so the
    next 'worst' reading reflects RECENT load rather than all-time load.
    No-op (never raises) when the binding is absent."""
    fn = getattr(tulip_mod, 'render_cyc', None)
    if fn is None:
        return
    try:
        fn(1)
    except Exception:
        pass


def internal_sram_summary(regions):
    """`regions`: an iterable of esp32.idf_heap_info(esp32.HEAP_DATA)
    per-region tuples, shaped (total, free, largest_free_block, min_free)
    (confirmed against deck/mem_probe.py, the existing memory-budget probe
    that reads this same API on-device). Returns (free_total, largest_free)
    summed/maxed over the INTERNAL SRAM regions only (PSRAM regions are
    excluded via INTERNAL_MAX_BYTES).

    largest_free is the metric that matters: the deck has been bricked by
    internal-SRAM exhaustion, and a fragmented heap can have plenty of total
    free bytes with no single allocation-sized block left -- total free
    hides exactly the failure mode this screen exists to catch."""
    free_total = 0
    largest = 0
    for r in regions or ():
        if not r:
            continue
        total = r[0]
        if total >= INTERNAL_MAX_BYTES:
            continue          # PSRAM region, not internal SRAM
        if len(r) > 1:
            free_total += r[1]
        if len(r) > 2 and r[2] > largest:
            largest = r[2]
    return free_total, largest
