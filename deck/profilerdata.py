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
# NOTE: no digit-separator underscores in the literal below -- no other
# on-device deck module uses that PEP-515 syntax, and MicroPython's numeric
# literal support for it isn't something to gamble the build on.
CYCLES_PER_BLOCK = 240000000 * 256 // 44100   # = 1,393,197 (~1.39M)

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



# --- Debug > Profiler BAR helpers (horizontal fill bars next to the text
# readouts). Kept here, not in profiler.py, so the threshold/percent math
# is host-testable without lvgl -- same split as the percent math above.

BAR_GREEN = 'green'
BAR_AMBER = 'amber'
BAR_RED = 'red'


def bar_fill_pct(pct):
    """Clamp a (possibly >100, over-budget) percent to what a 0..100-wide
    bar can physically draw. The real, unclamped number still goes in the
    text label next to it -- this clamp is ONLY for the bar's fill width."""
    if pct <= 0:
        return 0.0
    return 100.0 if pct > 100.0 else pct


def load_bar_color(pct):
    """Core-load bar color, tri-state: green under 80% of the per-block
    budget, amber from 80-100%, red once a core has actually missed its
    render deadline (over 100%) -- the exact condition behind the audible
    crackle (see piano-clips-master finding). Unmistakable is the point: a
    core showing red is dropping frames RIGHT NOW, not just running hot."""
    if pct > 100.0:
        return BAR_RED
    if pct >= 80.0:
        return BAR_AMBER
    return BAR_GREEN


def mem_pct_free(free_bytes, total_bytes):
    """`free_bytes` as a percent of `total_bytes`, for a memory fill bar.
    Returns 0.0 when either input is missing/non-positive (a guard against
    a divide-by-zero or an unavailable reading, not a real 0% signal) so a
    memory source this build doesn't have just draws an empty bar instead
    of raising and blanking the whole panel."""
    if free_bytes is None or not total_bytes or total_bytes <= 0:
        return 0.0
    v = (free_bytes * 100.0) / total_bytes
    if v < 0.0:
        return 0.0
    return 100.0 if v > 100.0 else v


def internal_sram_total(regions):
    """Sum of TOTAL bytes (not free) over the internal-SRAM regions only --
    the denominator internal_sram_summary()'s free_total needs in order to
    become a percent for the Memory bar. Same PSRAM-exclusion rule as
    internal_sram_summary, kept as a separate function rather than
    widening that one's return shape, since profiler.py and its existing
    tests already depend on its (free_total, largest_free) 2-tuple."""
    total = 0
    for r in regions or ():
        if not r:
            continue
        if r[0] >= INTERNAL_MAX_BYTES:
            continue          # PSRAM region, not internal SRAM
        total += r[0]
    return total


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
