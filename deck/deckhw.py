# deckhw.py -- the deck's firmware-capability SHIM (task #86, DECOUPLING.md).
#
# ONE choke point for every FORK-ONLY firmware surface the deck touches. The UI
# and engine import `deckhw` and call its wrappers / read `deckhw.CAPS` instead
# of probing `tulip.*` / `amy.*` / `midi.*` inline with ad-hoc
# hasattr/getattr/try-except idioms scattered across ~8 files.
#
# Design (DECOUPLING.md sec 4a): mirror the pattern modtulip.c already uses to
# degrade render_cyc -> None off-ESP. Probe each binding ONCE at import, cache a
# callable-or-None, and expose a stable Python interface whose behaviour is
# DEFINED on stock firmware (a documented fallback, never an AttributeError).
#
# Every wrapper here is a ~3-line function that already existed inline somewhere
# in the deck; this file MOVES that logic to a single testable place. The two
# host-test profiles (test_deck.py: `stock` builds the mock tulip/amy/midi
# WITHOUT the fork symbols, `fork` WITH them) are the oracle: a wrapper that
# still raised under `stock` would fail its test, so the shim can't pass by
# silently forwarding a missing binding.
#
# Nothing fork-specific about the STOCK tulip API (amy_ticks_ms, screen_size,
# midi_out, board, run, ...) belongs here -- only the fork-only surfaces.

import tulip

# amy / midi are needed by a few wrappers (batch send, MPE). They are always
# present in the deck runtime and in the host-test mocks, but import
# defensively so `import deckhw` can never be the thing that fails to load.
try:
    import amy as _amy
except Exception:                                   # pragma: no cover
    _amy = None
try:
    import midi as _midi
except Exception:                                   # pragma: no cover
    _midi = None


# --- one-time presence probe -------------------------------------------------
# Cache the CALLABLE (or None) for each fork-only binding. Presence is fixed for
# the life of a boot, so probing once is correct; the wrappers still INVOKE the
# cached callable every call to read live values.
def _probe(mod, name):
    return getattr(mod, name, None) if mod is not None else None


_render_cyc      = _probe(tulip, 'render_cyc')
_amy_level       = _probe(tulip, 'amy_level')
_flash_freq      = _probe(tulip, 'flash_freq')
_flash_fence_auto = _probe(tulip, 'flash_fence_auto')
_flash_fence     = _probe(tulip, 'flash_fence')
_display_partial = _probe(tulip, 'display_partial')
_display_vsync   = _probe(tulip, 'display_vsync')
_num_midi_devices = _probe(tulip, 'num_midi_devices')
_midi_activity   = _probe(tulip, 'midi_activity')
_midi_in_drops   = _probe(tulip, 'midi_in_drops')
_midi_routes     = _probe(tulip, 'midi_routes')
_piano_partials  = _probe(tulip, 'piano_partials')
_amy_send_batch  = _probe(tulip, 'amy_send_batch')
_configure_mpe   = _probe(_midi, 'configure_mpe')


# --- capability object (DECOUPLING.md sec 4b) --------------------------------
# The UI reads CAPS to HIDE dead features instead of showing an inert control.
class Caps:
    profiling      = _render_cyc is not None       # tulip.render_cyc
    level_meter    = _amy_level is not None         # tulip.amy_level
    c_midi_router  = _midi_routes is not None       # tulip.midi_routes
    batch_send     = (_amy_send_batch is not None   # tulip.amy_send_batch +
                      and _amy is not None          #   amy.override_send
                      and hasattr(_amy, 'override_send'))
    display_tuning = (_display_partial is not None  # display_partial / _vsync
                      or _display_vsync is not None)
    flash_fence    = _flash_fence_auto is not None  # tulip.flash_fence_auto
    flash_freq     = _flash_freq is not None        # tulip.flash_freq
    num_devices    = _num_midi_devices is not None  # tulip.num_midi_devices
    piano_tuning   = _piano_partials is not None    # tulip.piano_partials
    mpe            = _configure_mpe is not None      # midi.configure_mpe


CAPS = Caps()


# --- profiling: render-cycle counters ----------------------------------------
# Lifted verbatim from profilerdata.read_render_cyc / reset_worst.
def render_cyc():
    """tulip.render_cyc()'s (core0_worst, core1_worst, core0_last, core1_last)
    cycle 4-tuple, or None when the binding is absent/raises -- so the Profiler
    panel shows a 'needs newer firmware' fallback instead of crashing."""
    if _render_cyc is None:
        return None
    try:
        return _render_cyc()
    except Exception:
        return None


def render_cyc_reset():
    """tulip.render_cyc(1) -- reset the firmware's worst-case counters so the
    next 'worst' reading reflects RECENT load. No-op (never raises) on stock."""
    if _render_cyc is None:
        return
    try:
        _render_cyc(1)
    except Exception:
        pass


# --- audio: output peak-level meter ------------------------------------------
# Lifted from homeshell._meter_fraction / deckcfg.quiet_now.
def amy_level():
    """AMY's real output peak (0..~1), or None on stock firmware -- callers
    fall back to a voices-in-use estimate / time-based quiet heuristic and hide
    the chip meter (CAPS.level_meter)."""
    if _amy_level is None:
        return None
    try:
        return _amy_level()
    except Exception:
        return None


# --- flash tooling: clock readback + write fence -----------------------------
def flash_freq():
    """Compiled SPI-flash clock of the running image, e.g. '80m', or None when
    the binding is absent (flashmode then reads its stamped constant / default).
    NOTE: flashmode.flash_freq() keeps its own dynamic probe for now (it layers
    a flashbuild.FLASH_FREQ fallback); this is the generic shim entry."""
    if _flash_freq is None:
        return None
    try:
        v = _flash_freq()
        return str(v) if v else None
    except Exception:
        return None


def flash_fence_auto():
    """True iff this firmware fences EVERY partition write itself (the C storage
    layer's exact block-boundary handshake). When True, callers just write."""
    return _flash_fence_auto is not None


def flash_fence(on):
    """Raise (on=1) / drop (on=0) the manual flash-write fence. No-op, returns
    False, when the binding is absent (oldest firmware -> quiet-gate path)."""
    if _flash_fence is None:
        return False
    try:
        _flash_fence(on)
        return True
    except Exception:
        return False


# --- display tuning ----------------------------------------------------------
def display_partial(v):
    """Toggle partial-render. No-op on stock; settings hides the toggle when
    not CAPS.display_tuning."""
    if _display_partial is None:
        return False
    try:
        _display_partial(1 if v else 0)
        return True
    except Exception:
        return False


def display_vsync(v):
    """Toggle vsync. No-op on stock (see display_partial)."""
    if _display_vsync is None:
        return False
    try:
        _display_vsync(1 if v else 0)
        return True
    except Exception:
        return False


# --- MIDI: device count, router, activity/drops ------------------------------
def num_midi_devices():
    """USB-MIDI device count. Falls back to 1 (single-device assumption) on
    firmware whose midi_out predates the multi-device binding."""
    if _num_midi_devices is None:
        return 1
    try:
        return _num_midi_devices()
    except Exception:
        return 1


def has_c_router():
    """True if this firmware carries the C MIDI router (tulip.midi_routes).
    When False the forwarder drives its pure-Python MIDI tap instead."""
    return _midi_routes is not None


def midi_routes(masks, py_mask, tap):
    """Upload the C router's channel masks. Returns True on success; False when
    the binding is absent (forwarder switches to the Python tap) or the upload
    raises."""
    if _midi_routes is None:
        return False
    try:
        _midi_routes(masks, py_mask, tap)
        return True
    except Exception:
        return False


def midi_activity():
    """C-router message counter (monotonic). Returns 0 on stock -- screensaver
    treats an unchanging value as 'no MIDI wake', its correct idle behaviour."""
    if _midi_activity is None:
        return 0
    try:
        return _midi_activity()
    except Exception:
        return 0


def midi_in_drops():
    """The C router's (dropped, ring_state) drop tuple, or None when absent so
    the watchdog skips its ring-recovery path entirely on stock."""
    if _midi_in_drops is None:
        return None
    try:
        return _midi_in_drops()
    except Exception:
        return None


# --- piano partial-count tuning ----------------------------------------------
def piano_partials(n):
    """Set the piano partial count. No-op on stock; returns whether applied."""
    if _piano_partials is None:
        return False
    try:
        _piano_partials(int(n))
        return True
    except Exception:
        return False


# --- batched AMY wire send ---------------------------------------------------
# NOTE (Phase 5): the STATEFUL BatchSend context manager -- which swaps
# amy.override_send to collect messages and flushes them here -- stays in
# forwarder.py for now; migrating that intricate, forwarder-coupled logic is
# sequenced to Phase 5 in DECOUPLING.md. This is the thin value wrapper for the
# single flush call.
def amy_send_batch(text):
    """Flush a '\\n'-joined batch of AMY wire messages in ONE MP->C call.
    Returns True when the batch binding handled it; False on stock so the caller
    replays the messages one-by-one via amy.send()."""
    if _amy_send_batch is None:
        return False
    try:
        _amy_send_batch(text)
        return True
    except Exception:
        return False


# --- MPE ---------------------------------------------------------------------
def mpe_configure(members, bend=48, master=None):
    """Configure fork MPE (midi.configure_mpe). Returns True when applied;
    False on stock/no-MPE firmware so the forwarder uses its mono/poly path."""
    if _configure_mpe is None:
        return False
    try:
        if master is None:
            _configure_mpe(members, bend)
        else:
            _configure_mpe(members, bend, master=master)
        return True
    except Exception:
        return False
