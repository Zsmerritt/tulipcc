# amyparams.py -- the AMY sound-design parameter schema + pure apply logic.
#
# Mirrors the online AMYboard editor's knob table
# (tulip/amyboardweb/stage/amy_parameters.js) so the deck and web stay in
# lockstep, and targets the real apply path: amy.send(**kwargs) and the
# module-level FX fns amy.reverb()/chorus()/echo() (see amy/amy/__init__.py).
#
# This module is PURE (no lvgl / deckui / amy import) so it unit-tests under
# CPython. parameditor.py renders controls from it; forwarder.py executes the
# call descriptors it produces against amy.
#
# FX granularity (per INSTRUMENT-EDITOR.md, confirmed against AMY): reverb /
# chorus / echo are GLOBAL per AMY instance -> modelled PER DEVICE (an FX bus).
# EQ + filter are per-synth -> modelled per instrument in PARAMS.

import math   # the log slider curve; present in MicroPython, and still pure

# The default patch's four oscillators (amy_parameters.js conventions).
OSC_CTL = 0     # control osc: VCA level, filter, envelopes
OSC_LFO = 1
OSC_A = 2
OSC_B = 3

# AMY coef-array slots (index into a coefs string). CONST is the base value;
# NOTE = keyboard tracking, EG1 = envelope depth, MOD = LFO depth.
COEF_CONST = 0
COEF_NOTE = 1
COEF_EG1 = 4
COEF_MOD = 5

WAVE_OPTIONS = ["SINE", "PULSE", "SAW_UP", "SAW_DOWN", "TRIANGLE", "NOISE",
                "PCM", "WAVETABLE", "ALGO"]
WAVE_VALUES = [0, 1, 3, 2, 4, 5, 7, 19, 8]


# --- where a param's TRUTH lives when the user has not set it ----------------
#
# Every control we draw shows a number. When the user has touched nothing, that
# number is either a FACT or a GUESS, and the editor may only print facts.
# Which it is was settled per-param against AMY's own `reset_osc_params()`
# (amy/src/amy.c) -- the state an osc is in before any patch touches it:
#
#   TRUTH_AMY   the schema default is EXACTLY AMY's reset default, so when no
#               patch sets the param the default IS what the engine has.
#               Verified: amp_coefs[COEF_CONST]=1.0 (level/oscA_level/
#               oscB_level=1.0), duty_coefs[COEF_CONST]=0.5 (oscA/B_duty=0.5),
#               pan_coefs[COEF_CONST]=0.5 (pan=0.5), resonance=0.7f
#               (resonance=0.7), portamento_alpha=0 (portamento=0), wave=SINE
#               (the three wave dropdowns=0), every other coef slot zeroed
#               (filter_kbd/filter_env/lfo_pitch/lfo_pwm/lfo_filter=0), and
#               logfreq_coefs[COEF_CONST]=0 which IS 440 Hz (ZERO_LOGFREQ_IN_HZ
#               in amy.h) -> oscA_freq/oscB_freq=440.
#   TRUTH_DECK  a deck-side construct no patch can set (the aux reverb send,
#               the device-global piano partial limit). The default is ours to
#               define, so it is true by construction.
#   TRUTH_PATCH the schema default matches NEITHER AMY's reset default NOR
#               anything any built-in patch bakes -- it is a number we invented.
#               Only these may be rendered as "patch default" when unresolved.
#               Verified divergent: filter_freq=1000 (AMY resets
#               filter_logfreq_coefs to 0 with filter_type=FILTER_NONE -- there
#               is no filter at all, let alone one at 1 kHz); lfo_freq=4 (AMY's
#               logfreq default is 440 Hz, and every juno patch bakes its own
#               0.5..20.195); the eight ADSR slots (AMY's bp0 default is a bare
#               key gate `0,1,0,0`, and bp1 is unset entirely).
#
# This is the rule that keeps the honesty marker HONEST in both directions: it
# marks the ten params we provably guess at, and it leaves the other fifteen
# printing numbers that are provably in force.
TRUTH_AMY = 'amy'
TRUTH_DECK = 'deck'
TRUTH_PATCH = 'patch'


# --- apply-spec builders (how a param maps onto amy.send) ---
def _osc(osc, arg, coef=None):
    """A value written to `arg` on `osc`; coef=None means a plain scalar, else
    it fills the given slot of a coef-array string."""
    return {'kind': 'osc', 'targets': [(osc, arg, coef)]}


def _multi(targets):
    """Same value written to several (osc, arg, coef) targets (e.g. an LFO depth
    that modulates both oscillators)."""
    return {'kind': 'osc', 'targets': list(targets)}


def _env(which, slot):
    return {'kind': 'env', 'env': which, 'slot': slot}   # which: amp|filter


def _eq(index):
    return {'kind': 'eq', 'index': index}


def _p(name, group, label, ptype, default, tier, apply, truth=TRUTH_AMY, **kw):
    d = {'name': name, 'group': group, 'label': label, 'type': ptype,
         'default': default, 'tier': tier, 'apply': apply, 'scale': 1,
         'truth': truth}
    d.update(kw)
    return d


def _slider(name, group, label, lo, hi, default, tier, apply, scale=1, unit='',
            truth=TRUTH_AMY, curve=None):
    return _p(name, group, label, 'slider', default, tier, apply, truth=truth,
              min=lo, max=hi, scale=scale, unit=unit, curve=curve)


def _wave(name, group, label, default, tier, apply):
    return _p(name, group, label, 'dropdown', default, tier, apply,
              options=WAVE_OPTIONS, option_values=WAVE_VALUES)


# --- the log (musical) slider curve ------------------------------------------
#
# Some params are multiplicative by nature and span decades in the patches we
# actually ship, which a linear knob cannot serve at BOTH ends:
#
#   * envelope times. A GM patch bakes a 60 000 ms decay and the junos go to
#     142 057 ms (Juno B-bank pads), while the values that need fine control are
#     tiny -- GM's 5 ms attack, the synth kits' 25 ms median. On the old linear
#     0..2000 ms decay slider 60 000 ms could not be set AT ALL: the knob pinned
#     at the stop, and because a tap on a pinned knob is a real touch event with
#     nothing to guard it, that tap silently rewrote the patch's baked 60 s
#     decay to 2000 ms. Widening to a linear 0..150 000 would have made the
#     opposite half unusable (~300 ms per pixel).
#   * filter cutoff. The junos bake 17.5 Hz .. 84 288 Hz against a 20..8000
#     slider -- 9 patches past the old maximum, i.e. the same pinned-knob trap,
#     which only became reachable once patch_params started seeding it.
#
# THE MAP. Positions 0..LOG_STEPS; the value at each is derived from the def's
# own min/max, so the microlabels and `validate()` keep telling the truth about
# what the control can reach:
#
#   value(pos) = max(lo + pos/scale, lo * (hi/lo)**(pos/LOG_STEPS))
#
# rounded onto the param's existing `scale` grid (whole ms / whole Hz here).
# The `max` is not a clamp but the whole trick: a pure exponential wants steps
# finer than the grid can express down low (at 1 ms, 2.4% is 0.024 ms), so it
# would map many positions onto one value and stop being invertible. Taking the
# max with a one-grid-step ramp gives a LINEAR run at the bottom -- 1 ms per
# step out to 228 ms, 1 Hz per step out to ~58 Hz, which is the finest AMY can
# express anyway (every bp time in patches.h and synthkits_data is a whole ms) --
# and hands over to the exponential (2.4%/step for times, 1.7%/step for Hz,
# both far under the ear's JND) exactly where the grid stops being the limit.
#
# The result is strictly increasing, so the map is a BIJECTION on its positions
# and every value it can store round-trips to the same position, and thence to
# the same value, forever. That is the property that matters: a re-render must
# never rewrite a stored setting. (A value the map cannot produce -- a patch's
# 60 000 ms, or a setting saved by the old linear slider -- displays its true
# number with the knob at the nearest position, and is still never written back:
# seeding only reads. See test_log_curve_*.)
LOG_STEPS = 500

# The pos-1 value for a curve whose minimum is 0: position 0 means exactly 0
# (a real, common setting -- 28 junos bake a 0 ms attack, and so does GM's
# one-shot recipe), and the exponential starts at 1 ms just above it.
LOG_ZERO_LO = 1


def _log_lo_s(d):
    """(lo_s, p0): the curve's first EXPONENTIAL value in slider units, and the
    position it sits at (1 when position 0 is reserved for a true zero)."""
    scale = d.get('scale', 1)
    if d['min'] <= 0:
        return int(round(LOG_ZERO_LO * scale)), 1
    return int(round(d['min'] * scale)), 0


def curve_steps(d):
    """The slider's maximum POSITION for a def (its minimum is always 0)."""
    if d.get('curve') == 'log':
        return LOG_STEPS
    scale = d.get('scale', 1)
    return int(round(d['max'] * scale)) - int(round(d['min'] * scale))


def curve_value(d, pos):
    """The param value (real units: ms, Hz, ...) at a slider POSITION."""
    scale = d.get('scale', 1)
    if d.get('curve') != 'log':
        raw = int(round(d['min'] * scale)) + pos
        return (raw / scale) if scale != 1 else raw
    return _log_sval(d, pos) / scale if scale != 1 else _log_sval(d, pos)


def _log_sval(d, pos):
    scale = d.get('scale', 1)
    lo_s, p0 = _log_lo_s(d)
    if pos <= p0 - 1:
        return int(round(d['min'] * scale))        # the reserved true zero
    hi_s = int(round(d['max'] * scale))
    r = math.log(float(hi_s) / lo_s) / (LOG_STEPS - p0)
    k = pos - p0
    return max(lo_s + k, int(round(lo_s * math.exp(r * k))))


def curve_pos(d, value):
    """The slider POSITION that best shows `value`.

    EXACT for any value the curve itself produced (the map is strictly
    increasing, so the search below lands on the one position that made it) --
    which is every value the user can store through this control. For anything
    else (a patch's 60 000 ms, a value saved by an older linear slider) it
    returns the nearest position; the caller keeps displaying and storing the
    TRUE value, and nothing writes the approximation back.
    """
    scale = d.get('scale', 1)
    n = curve_steps(d)
    try:
        v_s = int(round(float(value) * scale))
    except (TypeError, ValueError):
        return 0
    if d.get('curve') != 'log':
        return max(0, min(n, v_s - int(round(d['min'] * scale))))
    if v_s <= _log_sval(d, 0):
        return 0
    if v_s >= _log_sval(d, n):
        return n
    lo, hi = 0, n                       # strictly increasing -> binary search
    while lo < hi:
        mid = (lo + hi) // 2
        if _log_sval(d, mid) < v_s:
            lo = mid + 1
        else:
            hi = mid
    if _log_sval(d, lo) == v_s or lo == 0:
        return lo
    # straddled: pick the neighbour that is closer in RATIO, matching the
    # curve's own geometry (a linear tie-break would bias every straddle low)
    below, above = _log_sval(d, lo - 1), _log_sval(d, lo)
    return lo - 1 if (v_s * v_s <= below * above) else lo


# --- piano partial detail: the units, and the legacy migration -------------
#
# WHAT WENT WRONG. The old 'piano_quality' slider (8..40, default 40) set
# amy_partials_harmonic_limit, which is a harmonic INDEX cap, not a count of
# partials. AMY's interp_partials.c drops some higher harmonics through a
# static `use_this_partial_map`, and that map is SPARSE above harmonic 17 --
# only 24 of its 40 slots are 1. So the number on the slider was not the
# number of oscillators the piano rendered:
#
#     slider (harmonic limit):   8   15   20   24   29   40
#     partials actually rendered:8   15   18   19   21   24
#
# Two lies in one control: "20" bought 18, and the top third of the travel
# (35..40) did literally nothing because 24 is the ceiling.
#
# WHAT IT IS NOW. 'piano_detail' is the partial COUNT: 8..24, default 24
# (full detail), one oscillator per step, every value distinct and reachable.
# The forwarder converts it back to a harmonic limit through
# tulip.piano_partials_count() -> amy_partials_limit_for_count(), which walks
# the real map in C. The deck never mirrors the live map.
PIANO_DETAIL_MIN = 8
PIANO_DETAIL_MAX = 24        # == amy_partials_max_count() for today's map

# LEGACY MIGRATION TABLE -- a FROZEN SNAPSHOT of the prefix sums of
# amy/src/interp_partials.c's `use_this_partial_map` AS IT WAS when
# 'piano_quality' was the stored unit. _LEGACY_PARTIALS_BELOW[h] is the number
# of partials that a stored harmonic limit of h rendered, i.e. the count of 1s
# in use_this_partial_map[0:h].
#
# THIS TABLE IS DELIBERATELY FROZEN and must NOT be regenerated when the AMY
# map changes: it exists to reinterpret numbers that were WRITTEN under the old
# map, and those numbers do not change retroactively. (If the map ever does
# change, the LIVE path is unaffected -- it asks C -- and this table stays as
# a historical record. test_deck.py asserts it still matches the shipped map,
# which is the check that would flag such a change for a deliberate decision.)
_LEGACY_PARTIALS_BELOW = (
    0,                                            # limit 0 -> nothing
    1, 2, 3, 4, 5, 6, 7, 8, 9, 10,                # limits 1-10  (map all 1s)
    11, 12, 13, 14, 15, 16, 17, 17, 18, 18,       # limits 11-20 (h18, h20 off)
    18, 18, 19, 19, 20, 20, 20, 20, 21, 21,       # limits 21-30
    22, 22, 22, 22, 23, 23, 24, 24, 24, 24,       # limits 31-40
)                                                 # len 41: indices 0..40


def piano_detail_from_legacy(limit):
    """The 'piano_detail' partial COUNT equivalent to an old 'piano_quality'
    harmonic LIMIT, so a preset saved before the units changed keeps sounding
    exactly the same (40 -> 24, 24 -> 19, 20 -> 18, 15 -> 15, 8 -> 8).

    Clamped into the slider's range: the old slider's own floor was 8 and its
    ceiling rendered 24, so nothing real falls outside. Junk (None, a string,
    a hand-edited 999) yields the full-detail default rather than raising --
    a bad config value must never be able to stop a config from loading."""
    try:
        h = int(limit)
    except (TypeError, ValueError):
        return PIANO_DETAIL_MAX
    if h < 0:
        h = 0
    if h > 40:
        h = 40
    n = _LEGACY_PARTIALS_BELOW[h]
    if n < PIANO_DETAIL_MIN:
        n = PIANO_DETAIL_MIN
    if n > PIANO_DETAIL_MAX:
        n = PIANO_DETAIL_MAX
    return n


def legacy_limit_for_detail(count):
    """The harmonic LIMIT that renders `count` partials -- the inverse of
    piano_detail_from_legacy(), and the OLD-FIRMWARE fallback path only.

    On current firmware the forwarder calls tulip.piano_partials_count(), which
    asks AMY to invert its own live map. This exists solely for a deck whose
    Python has been updated ahead of its firmware, where only the old
    tulip.piano_partials(harmonic_limit) binding exists; there the frozen table
    is the best available answer and is exact for the map that firmware ships.
    Returns the SMALLEST limit reaching the count, matching AMY's
    amy_partials_limit_for_count()."""
    try:
        n = int(count)
    except (TypeError, ValueError):
        return 40
    if n < 1:
        n = 1
    for h in range(len(_LEGACY_PARTIALS_BELOW)):
        if _LEGACY_PARTIALS_BELOW[h] >= n:
            return h
    return 40


def migrate_params(params):
    """Rewrite one stored per-instrument params dict in place from any retired
    param names to their current ones, and return it.

    Called on EVERY path that reads persisted params (deckcfg config load,
    presets.load) so nothing downstream ever has to know the old names.
    Idempotent: re-running it on already-migrated params is a no-op, and an
    explicit new-units value always wins over a legacy one.

    Today that is exactly 'piano_quality' (harmonic limit) -> 'piano_detail'
    (partial count); see the units note above."""
    if not isinstance(params, dict):
        return params
    if 'piano_quality' in params:
        legacy = params.pop('piano_quality')
        if 'piano_detail' not in params:
            params['piano_detail'] = piano_detail_from_legacy(legacy)
    return params


# --- the parameter table (per-instrument synth params) ---
PARAMS = [
    # Amp / dynamics
    _slider('level', 'Amp', 'level', 0.001, 7, 1.0, 'basic',
            _osc(OSC_CTL, 'amp', COEF_CONST), scale=100),
    # AMY's pan is 0..1 and CLAMPED (lgain_of_pan/rgain_of_pan in amy.c floor
    # it at 0), with reset_osc_params setting pan_coefs[COEF_CONST]=0.5 for
    # centre. This slider used to run -1..1 with a 0.0 default, so its whole
    # left half sent values AMY clamps to hard-left, its centred-looking
    # default WAS hard-left, and the "0.00" it printed for an untouched pan
    # described neither the engine's state (0.5) nor anything reachable.
    # 0..1/0.5 is what the engine actually implements. Stored values are
    # unaffected: an old negative pan still displays and still sends its own
    # number (which AMY has always clamped to 0), it merely shows as pinned.
    _slider('pan', 'Amp', 'pan', 0.0, 1.0, 0.5, 'basic',
            _osc(None, 'pan'), scale=100),
    # NOT log, and no marker: no built-in patch bakes portamento (wire letter
    # 'm' appears in none of them) and AMY resets portamento_alpha to 0, so 0
    # is the truth, nothing ever seeds this past its stop, and glide time is
    # the one time param that is genuinely linear to the ear.
    _slider('portamento', 'Amp', 'portamento', 0, 1000, 0, 'advanced',
            _osc(None, 'portamento'), unit='ms'),

    # Oscillator A
    _wave('oscA_wave', 'Osc A', 'wave', 0, 'basic', _osc(OSC_A, 'wave')),
    _slider('oscA_level', 'Osc A', 'level', 0.001, 1.0, 1.0, 'basic',
            _osc(OSC_A, 'amp', COEF_CONST), scale=100, unit='%'),
    _slider('oscA_freq', 'Osc A', 'freq', 50, 2000, 440, 'advanced',
            _osc(OSC_A, 'freq', COEF_CONST), unit='Hz'),
    _slider('oscA_duty', 'Osc A', 'duty', 0.5, 0.99, 0.5, 'basic',
            _osc(OSC_A, 'duty', COEF_CONST), scale=100, unit='%'),

    # Oscillator B
    _wave('oscB_wave', 'Osc B', 'wave', 0, 'advanced', _osc(OSC_B, 'wave')),
    _slider('oscB_level', 'Osc B', 'level', 0.001, 1.0, 1.0, 'advanced',
            _osc(OSC_B, 'amp', COEF_CONST), scale=100, unit='%'),
    _slider('oscB_freq', 'Osc B', 'freq', 50, 2000, 440, 'advanced',
            _osc(OSC_B, 'freq', COEF_CONST), unit='Hz'),
    _slider('oscB_duty', 'Osc B', 'duty', 0.5, 0.99, 0.5, 'advanced',
            _osc(OSC_B, 'duty', COEF_CONST), scale=100, unit='%'),

    # Filter (VCF, on the control osc)
    # Range + curve set by the junos this seeds from: their baked cutoffs run
    # 17.527 Hz (B17 Perc. Pluck) to 84 288 Hz (B42 Harpsichord 2 -- above
    # Nyquist, the converter's idiom for "wide open"), so 9 of the 128 sat past
    # the old 8000 stop. min 16 / max 100000 contains every one of them, which
    # is what disarms the pinned knob; log makes the 17 Hz..84 kHz span
    # settable at 1.7 %/step. TRUTH_PATCH: 1000 Hz was never AMY's default
    # either -- an unpatched osc has filter_type=FILTER_NONE.
    _slider('filter_freq', 'Filter', 'cutoff', 16, 100000, 1000, 'basic',
            _osc(OSC_CTL, 'filter_freq', COEF_CONST), unit='Hz',
            truth=TRUTH_PATCH, curve='log'),
    _slider('resonance', 'Filter', 'resonance', 0.5, 16, 0.7, 'basic',
            _osc(OSC_CTL, 'resonance'), scale=10),
    _slider('filter_kbd', 'Filter', 'kbd track', 0, 1, 0, 'advanced',
            _osc(OSC_CTL, 'filter_freq', COEF_NOTE), scale=100),
    _slider('filter_env', 'Filter', 'env depth', -10, 10, 0, 'basic',
            _osc(OSC_CTL, 'filter_freq', COEF_EG1)),

    # Amp envelope (eg0 / bp0). One shared 0..150 000 ms log range across all
    # six envelope TIMES: the real values span 0 ms to 142 057 ms (juno bp0
    # decay) with attacks as short as GM's 5 ms, and 150 000 clears the largest
    # with headroom. See the LOG_STEPS comment for why this is the map.
    _slider('amp_attack', 'Amp Env', 'attack', 0, 150000, 0, 'basic',
            _env('amp', 'attack'), unit='ms', truth=TRUTH_PATCH, curve='log'),
    _slider('amp_decay', 'Amp Env', 'decay', 0, 150000, 100, 'basic',
            _env('amp', 'decay'), unit='ms', truth=TRUTH_PATCH, curve='log'),
    _slider('amp_sustain', 'Amp Env', 'sustain', 0, 1, 1.0, 'basic',
            _env('amp', 'sustain'), scale=100, unit='%', truth=TRUTH_PATCH),
    _slider('amp_release', 'Amp Env', 'release', 0, 150000, 100, 'basic',
            _env('amp', 'release'), unit='ms', truth=TRUTH_PATCH, curve='log'),

    # Filter envelope (eg1 / bp1)
    _slider('filt_attack', 'Filter Env', 'attack', 0, 150000, 0, 'advanced',
            _env('filter', 'attack'), unit='ms', truth=TRUTH_PATCH,
            curve='log'),
    _slider('filt_decay', 'Filter Env', 'decay', 0, 150000, 100, 'advanced',
            _env('filter', 'decay'), unit='ms', truth=TRUTH_PATCH,
            curve='log'),
    _slider('filt_sustain', 'Filter Env', 'sustain', 0, 1, 0, 'advanced',
            _env('filter', 'sustain'), scale=100, unit='%', truth=TRUTH_PATCH),
    _slider('filt_release', 'Filter Env', 'release', 0, 150000, 100,
            'advanced', _env('filter', 'release'), unit='ms',
            truth=TRUTH_PATCH, curve='log'),

    # LFO + modulation
    # max 25, not 20: Juno A73 Repeater bakes an LFO rate of 20.195 Hz, which
    # pinned the old stop. TRUTH_PATCH -- AMY's logfreq default is 440 Hz, so
    # the 4 Hz shown for an unset LFO was ours, not the engine's.
    _slider('lfo_freq', 'LFO', 'rate', 0.1, 25, 4, 'basic',
            _osc(OSC_LFO, 'freq', COEF_CONST), scale=10, unit='Hz',
            truth=TRUTH_PATCH),
    _wave('lfo_wave', 'LFO', 'wave', 0, 'advanced', _osc(OSC_LFO, 'wave')),
    _slider('lfo_pitch', 'LFO', 'to pitch', 0, 4, 0, 'basic',
            _multi([(OSC_A, 'freq', COEF_MOD), (OSC_B, 'freq', COEF_MOD)]),
            scale=100),
    _slider('lfo_pwm', 'LFO', 'to pwm', 0, 0.49, 0, 'advanced',
            _multi([(OSC_A, 'duty', COEF_MOD), (OSC_B, 'duty', COEF_MOD)]),
            scale=100, unit='%'),
    _slider('lfo_filter', 'LFO', 'to filter', 0, 4, 0, 'basic',
            _osc(OSC_CTL, 'filter_freq', COEF_MOD), scale=100),
    # NOTE: EQ is per-BUS (per-device), not per-synth (verified on AMY) -- it
    # lives in FX below, not here.

    # Per-instrument reverb SEND (aux room). Default DRY: built-in patches
    # bake no reverb, so the slider starts where the patch is. Applied via
    # the instrument's FX BUS (forwarder handles kind 'bus_send'), not
    # amy.send(synth=...) -- synth_send_calls skips it.
    _slider('reverb_send', 'FX', 'reverb send', 0.0, 1.0, 0.0, 'basic',
            {'kind': 'bus_send'}, scale=100, unit='%', truth=TRUTH_DECK),

    # Piano partial detail (OPT-8): a sustained piano voice renders up to
    # PIANO_DETAIL_MAX partial oscillators (~14% of a core per held note).
    # The slider IS that count -- lower trades subtle top-end air for real
    # polyphony headroom, and every step removes exactly one oscillator.
    # Defaults to the MAX (full detail): the user drags DOWN to buy CPU.
    # Device-global (the C engine has one limit); forwarder applies kind
    # 'piano_detail' via tulip.piano_partials_count(); synth_send_calls skips
    # it. See the units note above for why this replaced 'piano_quality'.
    _slider('piano_detail', 'FX', 'partial detail',
            PIANO_DETAIL_MIN, PIANO_DETAIL_MAX, PIANO_DETAIL_MAX, 'advanced',
            {'kind': 'piano_detail'}, unit='partials', truth=TRUTH_DECK),

    # Piano SUSTAIN: how long the note rings. The interp-partials engine bakes
    # the decay as breakpoint TIMES; tulip.piano_sustain() time-stretches them
    # so the note plays the same spectral trajectory more slowly (longer ring,
    # same timbre). The slider reads in SECONDS and DEFAULTS TO THE MAX (5 s):
    # a fresh piano rings long and the user drags DOWN to shorten -- there is
    # deliberately no infinite/hold toggle. Device-global (the C engine has one
    # multiplier); forwarder applies kind 'piano_sustain' via
    # piano_sustain_arg() -> tulip.piano_sustain(); synth_send_calls skips it.
    _slider('piano_sustain', 'FX', 'sustain', 0.0, 5.0, 5.0, 'basic',
            {'kind': 'piano_sustain'}, scale=10, unit='s', truth=TRUTH_DECK),
]


# --- piano SUSTAIN seconds -> engine stretch -------------------------------
#
# The 'piano_sustain' slider stores SECONDS; the engine (tulip.piano_sustain)
# takes a time-stretch multiplier * 1000 for the baked piano envelope. The
# natural, un-stretched piano rings ~1 s audibly, so as a FIRST GUESS the
# desired ring in seconds maps ~1:1 onto the stretch factor (5 s => stretch
# 5.0 => arg 5000). The C side clamps the arg to 250..8000 (0.25x..8x).
#
# THIS IS THE ONE TUNABLE CONSTANT. The orchestrator measures the real ring on
# the device at the 5 s default and adjusts _SUSTAIN_S_TO_STRETCH so the top of
# the slider lands on ~5 s of audible ring. Nothing else needs to change.
_SUSTAIN_S_TO_STRETCH = 1.0   # engine stretch factor per second of desired ring


def piano_sustain_arg(seconds):
    """Map the piano 'sustain' slider's SECONDS to tulip.piano_sustain()'s
    stretch*1000 integer argument. Pure + deterministic so the router and the
    tests agree. (The C binding re-clamps to 250..8000.)"""
    return int(round(float(seconds) * _SUSTAIN_S_TO_STRETCH * 1000.0))

# --- FM / DX7 operator schema (feature #98) -------------------------------
#
# A DX7-class instrument (catalog patches 128..255) is NOT the four-osc deck
# layout the PARAMS table above assumes. Loaded from patches.h it is an FM
# voice whose oscillators are, per amy/amy/fm.py's send_to_AMY and confirmed
# against the baked strings in amy/src/patches.h:
#
#     osc 0   ALGO parent (wave=8): `algorithm`, `feedback`, `algo_source`
#             (=2,3,4,5,6,7), plus the voice pitch env (bp0) and pitch-LFO
#             depth (freq coef slot MOD).
#     osc 1   the LFO (wave/`freq`).
#     osc 2..7  operators 1..6 -- each a sine with `ratio` (freq multiple),
#             output level (`amp` coef CONST) and its own amp envelope (bp0),
#             LFO-amp-modulated via mod_source=1.
#
# These params therefore address oscs 0..7 of the SOUNDING synth directly:
# forwarder._apply_params emits amy.send(synth=<n>, osc=<k>, ...), which is
# voice-relative (AMY copies the synth's osc template to each allocated
# voice) -- exactly the addressing the container-drum work established.
#
# They live in FM_PARAMS (a SEPARATE list, not PARAMS) on purpose: PARAMS is
# iterated to build the GENERIC grouped editor, the patch-string readers, and
# default_params(), none of which should ever see FM controls (they only
# belong to the curated DX7 view, curated.py). But PARAM_BY_NAME, the apply
# path (synth_send_calls) and the value/curve helpers must resolve them, so
# they are folded into PARAM_BY_NAME below.
#
# TRUTH: every FM param is TRUTH_PATCH. The deck loads a DX7 patch by NUMBER
# and cannot read its baked operator ratios/levels/envelopes/algorithm back
# at runtime (patchparams.py distils only the four-osc juno layout, not FM),
# so an untouched FM control has no fact to show -- it renders "patch default"
# and is never sent. Only the user's own edits layer on top of the baked
# patch, and a "Reset FM" clears them (rebuild_one reloads the patch string,
# restoring every baked operator value).
OSC_ALGO = 0                       # the ALGO parent osc (== OSC_CTL, named for FM)
# DX7 operators 1..6 -> AMY oscs, REVERSED: fm.py loads operators op6..op1 onto
# oscs 2..7 (`for i in range(6,0,-1)` then osc=2+index) and render_algo pairs
# algo.ops[col] (labeled op6..op1) with algo_source osc 2..7. So op N lives on
# osc (8 - N): op1->osc7 ... op6->osc2. This makes the editor's "OP N" page and
# dx7algos.role()/the algorithm diagram agree on which operator is which.
FM_OP_OSCS = (7, 6, 5, 4, 3, 2)    # index 0 = op1 -> osc7 ... index 5 = op6 -> osc2


def _bp_env(osc, pos):
    """One field of an 8-field (4-stage) breakpoint envelope written to `osc`'s
    bp0 -- the surface the AMYboard editor exposes as AMP ENV / PITCH ENV
    (T1,L1,T2,L2,T3,L3,T4,L4). `pos` is the field's slot in that string (0..7).

    synth_send_calls assembles the WHOLE bp0 "T1,L1,T2,L2,T3,L3,T4,L4" from the
    fields the user set, filling the rest from FM_BP_DEFAULTS. The deck can't
    read the operator's baked bp0 back, so a partial edit restates its siblings
    from a known NEUTRAL shape rather than inventing them -- the same discipline
    _adsr_string uses for the analog ADSR. AMY holds the envelope at the 3rd
    level (L3) until note-off, then plays the 4th segment as the release, so L3
    reads as the sustain. A 4-stage bp0 is what lets an operator do a delayed
    swell or a two-slope decay that the v1 single 'decay' could not."""
    return {'kind': 'bp_env', 'osc': osc, 'pos': pos}


# (suffix, label, bp0 position, lo, hi, default, scale, unit) for a 4-stage
# envelope's eight controls. Times are log-curved ms; levels are linear.
#
# Amp env neutral default: 5 ms attack to full, 100 ms decay to 0.8, hold
# (sustain) 0.8, 300 ms release to 0 -- a plain sustaining shape so touching
# any one stage leaves the rest musical.
_AMP_ENV_FIELDS = [
    ('t1', 'T1 attack',  0, 0, 150000, 5,   1,   'ms'),
    ('l1', 'L1 peak',    1, 0, 1,      1.0, 100, ''),
    ('t2', 'T2 decay',   2, 0, 150000, 100, 1,   'ms'),
    ('l2', 'L2',         3, 0, 1,      0.8, 100, ''),
    ('t3', 'T3',         4, 0, 150000, 0,   1,   'ms'),
    ('l3', 'L3 sustain', 5, 0, 1,      0.8, 100, ''),
    ('t4', 'T4 release', 6, 0, 150000, 300, 1,   'ms'),
    ('l4', 'L4 end',     7, 0, 1,      0.0, 100, ''),
]
# Pitch env neutral default: perfectly flat (every level = ratio 1.0, no time)
# so an untouched-but-partially-edited pitch env doesn't bend pitch by accident.
# Levels are frequency RATIOS around 1.0 (0..2), matching fm.py's pitch bp.
_PITCH_ENV_FIELDS = [
    ('t1', 'T1', 0, 0, 150000, 0,   1,   'ms'),
    ('l1', 'L1', 1, 0, 2,      1.0, 100, ''),
    ('t2', 'T2', 2, 0, 150000, 0,   1,   'ms'),
    ('l2', 'L2', 3, 0, 2,      1.0, 100, ''),
    ('t3', 'T3', 4, 0, 150000, 0,   1,   'ms'),
    ('l3', 'L3', 5, 0, 2,      1.0, 100, ''),
    ('t4', 'T4', 6, 0, 150000, 0,   1,   'ms'),
    ('l4', 'L4', 7, 0, 2,      1.0, 100, ''),
]


def _env_field_slider(name, group, spec, osc):
    suf, label, pos, lo, hi, dflt, scale, unit = spec
    return _slider(name, group, label, lo, hi, dflt, 'basic',
                   _bp_env(osc, pos), scale=scale, unit=unit,
                   truth=TRUTH_PATCH, curve=('log' if unit == 'ms' else None))


FM_PARAMS = [
    # --- FM Voice (osc 0 = ALGO parent, osc 1 = LFO) ---
    # algorithm is EDITABLE, not read-only: amy/src/algorithms.c reads
    # synth[osc]->algorithm every render and clamps it (`if (algorithm >=
    # NUM_ALGORITHMS) algorithm = 0`), and the operator connections (algo_source,
    # set at note-on) are unchanged by it, so a live change only reroutes the FM
    # matrix on the next buffer -- no stale state, no realloc, no crash. Shown as
    # "patch default" until set (the deck can't read the baked algorithm).
    # type 'action': the Voice page renders this as a button that opens the
    # algorithm PICKER MODAL (a node diagram, scroll 1..32) -- rack wires the
    # opener via ParamEditor.on_action. The apply spec is unchanged, so a
    # selection still sends 'algorithm' (the 'o' param) to osc 0 live.
    _p('fm_algorithm', 'FM', 'algorithm', 'action', 1, 'basic',
       _osc(OSC_ALGO, 'algorithm'), truth=TRUTH_PATCH, min=1, max=32),
    # feedback: DX7 0..7 maps (fm.py) to 0.00125*2**fb = 0.00125..0.16; a
    # little headroom past that. Scalar on the ALGO osc ('b' wire letter).
    _slider('fm_feedback', 'FM', 'feedback', 0, 0.5, 0, 'basic',
            _osc(OSC_ALGO, 'feedback'), scale=1000, truth=TRUTH_PATCH),
    # global pitch-LFO depth: freq coef MOD slot on the ALGO osc, matching
    # fm.py's freq='0,1,0,1,0,<pitch_lfo_amp>' (slot 5). Correct FM pitch mod
    # (unlike the analog lfo_pitch, which would hit only ops 1-2's freq).
    _slider('fm_lfo_pitch', 'FM', 'LFO to pitch', 0, 0.5, 0, 'basic',
            _osc(OSC_ALGO, 'freq', COEF_MOD), scale=1000, truth=TRUTH_PATCH),
    # LFO wave + LFO speed are the base schema's own 'lfo_wave'/'lfo_freq'
    # (they target OSC_LFO=1, which IS the DX7 LFO) -- reused by the curated
    # view, so not redefined here.
]
# Parent PITCH ENV (osc 0 bp0): fm.py sends bp0=pitchbp with freq eg0=1, so
# the parent's bp0 drives a pitch envelope over the whole voice.
for _pspec in _PITCH_ENV_FIELDS:
    FM_PARAMS.append(_env_field_slider('fm_pitch_%s' % _pspec[0], 'Pitch',
                                       _pspec, OSC_ALGO))
# --- Per-operator pages (6 ops): level, ratio, amp LFO + a 4-stage amp env ---
for _fm_op in range(1, 7):
    _fm_osc = FM_OP_OSCS[_fm_op - 1]
    _fm_grp = 'OP %d' % _fm_op
    FM_PARAMS.append(
        # ratio: DX7 coarse/fine frequency multiple (fm.py coarse_fine_ratio,
        # 0.5..~31). 0.01 grid catches the fine detune (e.g. 1.00125).
        _slider('fm_op%d_ratio' % _fm_op, _fm_grp, 'ratio', 0.1, 32, 1,
                'basic', _osc(_fm_osc, 'ratio'), scale=100, truth=TRUTH_PATCH))
    FM_PARAMS.append(
        # output level: operator amp CONST slot (fm.py op_amp = 2*linear, 0..2).
        _slider('fm_op%d_level' % _fm_op, _fm_grp, 'output', 0, 2, 1, 'basic',
                _osc(_fm_osc, 'amp', COEF_CONST), scale=100, truth=TRUTH_PATCH))
    FM_PARAMS.append(
        # amp LFO: operator amp MOD slot (fm.py amp='op_amp,0,0,1,0,<amp_lfo>',
        # slot 5). Composes with level -- both are coefs on this osc's amp, and
        # the empty slots keep the baked eg0=1 that wires the amp env.
        _slider('fm_op%d_amplfo' % _fm_op, _fm_grp, 'amp LFO', 0, 1, 0, 'basic',
                _osc(_fm_osc, 'amp', COEF_MOD), scale=100, truth=TRUTH_PATCH))
    for _aspec in _AMP_ENV_FIELDS:
        FM_PARAMS.append(_env_field_slider('fm_op%d_%s' % (_fm_op, _aspec[0]),
                                           _fm_grp, _aspec, _fm_osc))


# Neutral fallback for every bp0 field, keyed by osc -- so a partial envelope
# edit assembles a full, musical bp0 (see _bp_env / synth_send_calls).
FM_BP_DEFAULTS = {}
for _d in FM_PARAMS:
    _apd = _d['apply']
    if _apd['kind'] == 'bp_env':
        FM_BP_DEFAULTS.setdefault(_apd['osc'], [0] * 8)[_apd['pos']] = \
            _d['default']


PARAM_BY_NAME = {d['name']: d for d in PARAMS}
# FM controls resolve through PARAM_BY_NAME (apply path, value/curve helpers,
# curated view lookups) without polluting the generic PARAMS iteration.
PARAM_BY_NAME.update({d['name']: d for d in FM_PARAMS})


def _env_defaults(which):
    out = {}
    for d in PARAMS:
        ap = d['apply']
        if ap['kind'] == 'env' and ap['env'] == which:
            out[ap['slot']] = d['default']
    return out


# Per-envelope schema defaults, DERIVED from the table above rather than
# re-typed in _adsr_string -- the two had already drifted: bp1's sustain
# slider defaults to 0 but the hardcoded string default was 1, so an untouched
# filter sustain was sent as 1 while the editor showed 0.
ENV_DEFAULTS = {'amp': _env_defaults('amp'), 'filter': _env_defaults('filter')}


# --- reading the envelope a patch ACTUALLY bakes -----------------------------
#
# The editor used to seed its ADSR sliders from the schema defaults above
# (attack 0 / decay 100 / sustain 1 / release 100) for EVERY instrument. On the
# 0..2000 and 0..8000 ms ranges 100 ms renders a hair off the left stop, so a
# GM patch whose real envelope is `A5,1,60000,0.85,220,0` (attack 5 ms, a 60 s
# settle to 0.85, 220 ms release) displayed as "attack 0, decay 0, sustain
# full, release 0" -- values that patch never had. It cost a debugging session:
# the numbers looked authoritative and pointed away from the real cause.
#
# These helpers read the truth back off the patch string instead. Same
# principle as the FX layering below (_merge_fx): the PATCH is the baseline,
# the user's edits layer on top, and what we cannot read we do not invent.

def parse_bp(bp):
    """Parse an AMY breakpoint string in the ADSR shape `_adsr_string` emits --
    'A,1,D,S,R,0', optionally still carrying its wire letter ("A5,1,...") --
    into {'attack','decay','sustain','release'} (ms, ms, 0..1, ms).

    Returns {} for ANY other shape: absent, malformed, or a breakpoint set our
    four sliders cannot represent (a bare AD pair like '0,1.0,300,0.0', or a
    peak/end other than 1/0). {} means UNKNOWN and the editor labels it as
    such -- an envelope we cannot read must never be reported as numbers we
    made up. Strict by design: a wrong reading is worse than no reading.
    """
    if not bp:
        return {}
    s = str(bp).strip()
    if s and (('a' <= s[0] <= 'z') or ('A' <= s[0] <= 'Z')):
        s = s[1:]                       # drop the wire letter (bp0 'A'/bp1 'B')
    parts = s.split(',')
    if len(parts) != 6:
        return {}
    try:
        v = [float(p) for p in parts]
    except ValueError:
        return {}
    if v[1] != 1 or v[5] != 0:
        return {}                       # not the ADSR encoding we round-trip
    return {'attack': v[0], 'decay': v[2], 'sustain': v[3], 'release': v[4]}


def wire_blocks(patch_string):
    """{osc: {wire_letter: raw_value_string}} for an AMY patch string.

    A patch string is letter-prefixed numeric fields grouped into 'v<n>' osc
    blocks ("v0w7p512b2A5,1,60000,0.85,220,0Z"). Later writes to the same
    (osc, letter) win, matching how AMY applies the string left to right.
    """
    s = str(patch_string or '')
    out = {}
    osc = None
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if ('a' <= c <= 'z') or ('A' <= c <= 'Z'):
            j = i + 1
            while j < n and (s[j].isdigit() or s[j] in '.,-+'):
                j += 1
            val = s[i + 1:j]
            if c == 'v':
                try:
                    osc = int(val)
                except ValueError:
                    osc = None
            elif osc is not None:
                out.setdefault(osc, {})[c] = val
            i = j
        else:
            i += 1
    return out


def _wire_bp(patch_string, letter):
    """The osc-0 value of wire field `letter`, else None."""
    return wire_blocks(patch_string).get(OSC_CTL, {}).get(letter)


# AMY wire letters for the amy.send kwargs our apply specs target (amy/src
# parse.c's command switch; cross-checked against amy/amy/__init__.py's kwarg
# table). `coef` says the field is a comma-separated COMBO-COEF list, so a
# param with a coef slot reads that slot out of it.
_WIRE = {
    'amp': ('a', True), 'freq': ('f', True), 'duty': ('d', True),
    'filter_freq': ('F', True), 'pan': ('Q', True),
    'resonance': ('R', False), 'wave': ('w', False),
    'portamento': ('m', False),
}

# Wave 20 is AMY's SILENT: "a control osc for applying filter and env without
# contributing waveform" (amy.h). It is the signature of the four-osc layout
# this schema is built on -- OSC_CTL silent, OSC_LFO/OSC_A/OSC_B making the
# sound -- which the juno bank and the amyboard default patch use.
#
# The DX7 bank does NOT (its osc 0 is w8/ALGO and oscs 2..7 are FM operators),
# nor does the piano (w11/INTERP_PARTIALS) or GM (w7/PCM). On those, "Osc A
# level" is our label for a thing that is not an Osc A, so reading oscs 1..3
# would be a category error dressed as a fact -- e.g. DX7 operator 2's level
# runs to 2.0, which this schema's 0..1 "oscA_level" cannot even hold. So we
# read OSC_CTL everywhere and oscs 1..3 only where the layout provably matches.
WAVE_SILENT = 20


def _reads_deck_layout(blocks):
    return blocks.get(OSC_CTL, {}).get('w') == str(WAVE_SILENT)


def _coef_slot(csv, slot):
    parts = (csv or '').split(',')
    if slot >= len(parts) or parts[slot] == '':
        return None
    try:
        return float(parts[slot])
    except ValueError:
        return None


def patch_params_from_string(patch_string):
    """{param_name: value} for every schema param an AMY patch string
    demonstrably SETS -- the generalisation of the bp0/bp1 read below.

    Driven by the PARAMS table's own apply specs, so a param is read from
    exactly the (osc, kwarg, coef slot) it would be written to. A name is
    present ONLY when the patch really carries it: what we cannot read we do
    not invent, and the editor marks that case rather than printing a guess.
    """
    blocks = wire_blocks(patch_string)
    if not blocks:
        return {}
    deck_layout = _reads_deck_layout(blocks)
    out = {}
    for d in PARAMS:
        ap = d['apply']
        if ap['kind'] == 'env':
            continue                    # composite bp strings: handled below
        if ap['kind'] != 'osc':
            continue                    # bus_send / piano_detail: not in a patch
        # targets[0] for a _multi param (an LFO depth written to BOTH oscs):
        # they carry one value by construction, so osc A's is the value.
        osc, arg, coef = ap['targets'][0]
        if osc is None:
            osc = OSC_CTL               # scalars ride the control osc
        if osc != OSC_CTL and not deck_layout:
            continue
        wire = _WIRE.get(arg)
        if wire is None:
            continue
        letter, is_coef = wire
        raw = blocks.get(osc, {}).get(letter)
        if raw is None:
            continue
        # a non-coef field is its own value; a coef list with no slot named
        # (a bare 'pan') means COEF_CONST, which is slot 0 either way
        v = _coef_slot(raw, coef if (is_coef and coef is not None) else 0)
        if v is None:
            continue
        if d['type'] == 'dropdown':
            v = int(v)
            if v not in d.get('option_values', []):
                continue                # a wave this schema cannot show
        out[d['name']] = v
    for which, letter in (('amp', 'A'), ('filter', 'B')):
        e = parse_bp(blocks.get(OSC_CTL, {}).get(letter))
        for slot, v in e.items():
            name = _env_param_name(which, slot)
            if name:
                out[name] = v
    return out


def _patch_string(instr):
    """The AMY patch string the deck loads for an instrument, or None when the
    deck does not build one (so cannot know it)."""
    if not instr:
        return None
    t = instr.get('type')
    try:
        if t == 'gm':
            import gm
            return gm.patch_string(int(instr.get('patch', 0)))
        if t == 'gm2':
            import gmbig
            return gmbig.patch_string(int(instr.get('patch', 0)))
    except Exception as e:
        import decklog
        decklog.dbg("amyparams: patch_string(%s) failed: %r"
                    % (instr.get('type'), e))
        return None        # e.g. a program the gm2 font does not cover
    return None


def patch_params(instr):
    """{param_name: value} an instrument's baked patch ACTUALLY applies, or {}.

    Two sources, both reading the SAME patch strings AMY does:

      gm / gm2   the DECK builds the string (gm.patch_string), so it is parsed
                 live.
      juno6 /    the patch lives in AMY's patches.h and reaches the device as a
      dx7 /      NUMBER, so the deck never sees the string at runtime -- but
      piano      patches.h is in the tree, and tools/gen_patchparams.py distils
                 it through patch_params_from_string() into patchparams.py at
                 build time. Exactly the shape patchfx.py already uses to tell
                 the FX panel what a juno patch's chorus really is.

    Everything else ({} -- drums, an unknown type, a gm2 program the font does
    not cover) stays honestly unknown.
    """
    if not instr:
        return {}
    s = _patch_string(instr)
    if s:
        return patch_params_from_string(s)
    if instr.get('type') in ('juno6', 'dx7', 'piano'):
        try:
            import patchparams
            return patchparams.patch_params(int(instr.get('patch', 0)))
        except Exception as e:
            import decklog
            decklog.dbg("amyparams: patchparams for %s failed: %r"
                        % (instr.get('type'), e))
            return {}
    return {}


def patch_env(instr):
    """The envelopes an instrument's baked patch applies as
    {'amp': {slot: v}, 'filter': {...}}, or {}.

    The bp0/bp1 view of patch_params(), kept in this shape because that is what
    _adsr_string needs: those two are COMPOSITE wire strings, so a touched slot
    has to restate its siblings and must take them from the patch.
    """
    pp = patch_params(instr)
    out = {}
    for name, v in pp.items():
        which, slot = _env_slot(name)
        if which is not None:
            out.setdefault(which, {})[slot] = v
    return out


def _env_slot(name):
    """(env, slot) for an envelope param ('amp','decay'), else (None, None)."""
    d = PARAM_BY_NAME.get(name)
    if d is None:
        return None, None
    ap = d.get('apply') or {}
    if ap.get('kind') != 'env':
        return None, None
    return ap['env'], ap['slot']


def _env_param_name(which, slot):
    for d in PARAMS:
        ap = d['apply']
        if ap['kind'] == 'env' and ap['env'] == which and ap['slot'] == slot:
            return d['name']
    return None


def is_env_param(name):
    return _env_slot(name)[0] is not None


def truth_of(name):
    """Where an untouched param's truth lives: TRUTH_AMY | TRUTH_DECK |
    TRUTH_PATCH (see the constants at the top of this module)."""
    d = PARAM_BY_NAME.get(name)
    return d.get('truth', TRUTH_AMY) if d else TRUTH_AMY


def is_fabricated(d, source):
    """True when the number the editor would print for def `d` is one WE
    invented. Takes the DEF, not a name: the FX defs share names with PARAMS
    entries ('level'), and this must answer for the control being drawn.

    Only TRUTH_PATCH params can be fabricated: their schema default matches
    neither AMY's reset state nor any patch, so with no user value and no patch
    value there is nothing behind it. A TRUTH_AMY param falling through to its
    default is reporting the engine's real state, and a TRUTH_DECK param's
    default is true by construction -- marking either would be noise that
    teaches the user to ignore the marker on the ten params where it means
    something.
    """
    return source == 'default' and d.get('truth', TRUTH_AMY) == TRUTH_PATCH


def param_value_source(params, ppv, name, default=None):
    """(value, source) for one editor control, layered user > patch > schema,
    where source is 'user' | 'patch' | 'default'.

    `ppv` is the instrument's patch values (amyparams.patch_params) -- a flat
    {name: value}. 'default' means neither the user nor the patch supplied one;
    is_fabricated() then says whether that default is a fact or our guess.
    """
    v = (params or {}).get(name)
    if v is not None:
        return v, 'user'
    pv = (ppv or {}).get(name)
    if pv is not None:
        return pv, 'patch'
    if default is None:
        d = PARAM_BY_NAME.get(name)
        default = d['default'] if d else None
    return default, 'default'


def param_value(params, ppv, name, default=None):
    """The effective value one editor control should show (user > patch >
    schema default)."""
    return param_value_source(params, ppv, name, default)[0]


# Patch-number ranges -> synth engine (mirrors instrument.CATS). Used to pick a
# curated editor view (curated.py); 'generic' falls back to the full grouped set.
def engine_of(patch):
    if patch is None:
        return 'generic'
    import catalog
    return catalog.engine_of(patch)   # E-8: catalog owns the boundaries


# --- FX schema (per-DEVICE bus): bus -> list of {name,label,min,max,default,arg}
# `arg` is the amy.<bus>() keyword argument.
FX = {
    'reverb': [
        {'name': 'level', 'label': 'level', 'min': 0, 'max': 1, 'default': 0,
         'arg': 'level', 'unit': '%'},
        # liveness caps at 0.95: at ~1.0 the room self-oscillates -- "notes"
        # that never stop, and (pre-fence firmware) a sound floor that
        # starved every config save
        {'name': 'liveness', 'label': 'liveness', 'min': 0, 'max': 0.95,
         'default': 0.85, 'arg': 'liveness', 'unit': '%'},
        {'name': 'damping', 'label': 'damping', 'min': 0, 'max': 1,
         'default': 0.5, 'arg': 'damping', 'unit': '%'},
    ],
    'chorus': [
        {'name': 'level', 'label': 'level', 'min': 0, 'max': 1, 'default': 0,
         'arg': 'level', 'unit': '%'},
        {'name': 'freq', 'label': 'rate', 'min': 0.1, 'max': 20, 'default': 0.5,
         'arg': 'freq', 'unit': 'Hz'},
        {'name': 'depth', 'label': 'depth', 'min': 0.01, 'max': 1,
         'default': 0.5, 'arg': 'amp', 'unit': '%'},
    ],
    'echo': [
        {'name': 'level', 'label': 'level', 'min': 0, 'max': 2, 'default': 0,
         'arg': 'level'},
        {'name': 'delay_ms', 'label': 'delay', 'min': 0, 'max': 5000,
         'default': 500, 'arg': 'delay_ms', 'unit': 'ms'},
        {'name': 'feedback', 'label': 'feedback', 'min': 0, 'max': 1,
         'default': 0, 'arg': 'feedback', 'unit': '%'},
    ],
    # EQ is per-BUS (per-device). It has no amy.eq() fn -- it's applied via
    # amy.send(synth=<a synth on the bus>, eq="low,mid,high"), so it's kept out
    # of FX_BUSES (the fn-applied set) and handled by fx_eq_string().
    'eq': [
        {'name': 'low', 'label': 'low', 'min': -15, 'max': 15, 'default': 0,
         'unit': 'dB'},
        {'name': 'mid', 'label': 'mid', 'min': -15, 'max': 15, 'default': 0,
         'unit': 'dB'},
        {'name': 'high', 'label': 'high', 'min': -15, 'max': 15, 'default': 0,
         'unit': 'dB'},
    ],
}
FX_BUSES = ('reverb', 'chorus', 'echo')     # applied via amy.<bus>() fns
FX_GROUPS = ('reverb', 'chorus', 'echo', 'eq')


# --- defaults / filtering ---
def default_params():
    return {d['name']: d['default'] for d in PARAMS}


def default_fx():
    return {bus: {p['name']: p['default'] for p in defs}
            for bus, defs in FX.items()}


def params_in_group(group, basic_only=False):
    return [d for d in PARAMS if d['group'] == group
            and (not basic_only or d['tier'] == 'basic')]


def groups():
    seen = []
    for d in PARAMS:
        if d['group'] not in seen:
            seen.append(d['group'])
    return seen


def filter_tier(defs, show_advanced):
    """A view of `defs` for the Basic (advanced hidden) or Advanced UI."""
    if show_advanced:
        return list(defs)
    return [d for d in defs if d.get('tier') == 'basic']


def tabbed_groups(show_advanced):
    """Ordered [(group, defs)] for the left-tabbed Sound editor -- one tab per
    param group, tier-filtered. Groups with no visible params (e.g. Osc B /
    Filter Env in Basic mode) are dropped, so they only appear under Advanced."""
    out = []
    for g in groups():
        defs = [d for d in PARAMS if d['group'] == g
                and (show_advanced or d['tier'] == 'basic')]
        if defs:
            out.append((g, defs))
    return out


def _fx_scale(lo, hi):
    if lo < 0:
        return 1                # signed (EQ dB): whole steps
    if hi <= 2:
        return 100              # 0..1/0..2 levels/depths
    if hi <= 20:
        return 10               # chorus freq
    return 1                    # delay ms, etc.


def fx_defs(bus=None):
    """Flat ParamEditor-style defs for the FX buses (all slider, tier basic).
    Each carries a 'bus' key so an FxEditor writes it to the right device bus."""
    out = []
    for b in FX_GROUPS:
        if bus is not None and b != bus:
            continue
        for p in FX[b]:
            out.append({'name': p['name'], 'bus': b,
                        'group': b[:1].upper() + b[1:],  # str.capitalize() is not in MicroPython
                        'label': p['label'], 'type': 'slider', 'min': p['min'],
                        'max': p['max'], 'default': p['default'],
                        'tier': 'basic', 'scale': _fx_scale(p['min'], p['max']),
                        'unit': p.get('unit', ''),
                        # FX values are always concrete (fx_value layers the
                        # patch's own FX in), so nothing here is ever our
                        # invention. Explicit because these defs share names
                        # with PARAMS entries ('level'), and the honesty marker
                        # keys on the name.
                        'truth': TRUTH_DECK, 'curve': None})
    return out


def fx_tabbed_groups():
    """Ordered [(tab_label, defs)] for the left-tabbed FX editor: one tab per
    bus (Reverb / Chorus / Echo / EQ)."""
    labels = {'reverb': 'Reverb', 'chorus': 'Chorus', 'echo': 'Echo',
              'eq': 'EQ'}
    return [(labels.get(b, b), fx_defs(b)) for b in FX_GROUPS if fx_defs(b)]


def validate():
    """Sanity-check the table (used by tests). Returns True or raises."""
    names = set()
    for d in PARAMS + FM_PARAMS:        # FM controls share the name space
        for k in ('name', 'group', 'type', 'default', 'tier', 'apply'):
            assert k in d, "param missing %s: %r" % (k, d)
        assert d['name'] not in names, "duplicate param %s" % d['name']
        names.add(d['name'])
        assert d['tier'] in ('basic', 'advanced')
        assert d.get('truth') in (TRUTH_AMY, TRUTH_DECK, TRUTH_PATCH), \
            "param %s has no truth classification" % d['name']
        if d['type'] == 'slider':
            assert d['min'] < d['max'], "bad range %s" % d['name']
            if d.get('curve') == 'log':
                # The curve is only invertible -- and therefore only safe to
                # render a stored value onto -- while it is strictly
                # increasing. Assert it here so a future range edit that
                # collapses two positions onto one value fails loudly at
                # import instead of quietly rewriting a user's setting.
                prev = None
                for p in range(curve_steps(d) + 1):
                    v = _log_sval(d, p)
                    assert prev is None or v > prev, \
                        "log curve %s is not strictly increasing at pos %d" % (
                            d['name'], p)
                    prev = v
                assert curve_value(d, 0) == d['min'], d['name']
                assert curve_value(d, curve_steps(d)) == d['max'], d['name']
        if d['type'] == 'dropdown':
            assert len(d['options']) == len(d['option_values'])
    for bus, defs in FX.items():
        for p in defs:
            assert p['min'] < p['max'], "bad fx range %s.%s" % (bus, p['name'])
    return True


# --- pure apply: build call descriptors (executed by forwarder against amy) ---
def _fmt(v):
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v)


def _adsr_string(which, env, penv=None):
    """AMY bp string mirroring the web ADSR encoding: A,1,D,S,R,0.

    bp0/bp1 are ONE composite string, so touching a single slider necessarily
    restates all four slots. Slots the user never set therefore fall back to
    the PATCH's own envelope when it is known (penv), and only then to the
    schema defaults. Filling them from the schema is what made a nudge of
    `attack` silently rewrite a GM patch's baked 60 s decay to 100 ms -- the
    exact hazard _merge_fx already avoids for the FX buses by layering user
    edits over the patch's values instead of over zeros.
    """
    base = dict(ENV_DEFAULTS[which])
    base.update(penv or {})
    base.update(env or {})
    return "%s,1,%s,%s,%s,0" % (_fmt(base['attack']), _fmt(base['decay']),
                                _fmt(base['sustain']), _fmt(base['release']))


def synth_send_calls(params, penv=None):
    """Given an instrument's stored params, return a list of kwargs dicts for
    amy.send(synth=<n>, **kwargs). Deterministic + pure.

    ONLY explicitly-stored params are sent. The schema defaults are display
    fallbacks for the editor -- stamping the whole table over every patch
    rewrote its character (forcing e.g. cutoff 1000 Hz onto A11 made the deck
    sound different from the same patch pre-boot). Unset coef slots emit ''
    which AMY treats as 'leave the patch's value'.

    `penv` is the patch's OWN envelopes (amyparams.patch_env) and is purely a
    fallback for the composite bp0/bp1 strings: an envelope group is emitted
    only when the user has stored at least one of its slots, so penv can never
    turn an untouched instrument into a send. synth_send_calls({}, penv) == []
    for every penv."""
    merged = dict(params or {})

    coefmap = {}                    # (osc, arg) -> {coef: value}
    scalars = []                    # (osc, arg, value)  -- coef is None
    env = {'amp': {}, 'filter': {}}
    bp_env = {}                     # osc -> {pos: value} (FM 4-stage bp0)
    eq = [None, None, None]

    for name, val in merged.items():
        d = PARAM_BY_NAME.get(name)
        if d is None:
            # a stored param this schema no longer knows (config written by
            # a newer deck, or hand-edited) must never kill the router at
            # boot -- the KeyError escaped all the way to "no sound, every
            # boot" (review F-7)
            continue
        ap = d['apply']
        kind = ap['kind']
        if kind in ('bus_send', 'piano_detail', 'piano_sustain'):
            continue        # router-applied kinds, not amy.send(synth=...)
        if kind == 'osc':
            for osc, arg, coef in ap['targets']:
                if coef is None:
                    scalars.append((osc, arg, val))
                else:
                    coefmap.setdefault((osc, arg), {})[coef] = val
        elif kind == 'env':
            env[ap['env']][ap['slot']] = val
        elif kind == 'bp_env':
            bp_env.setdefault(ap['osc'], {})[ap['pos']] = val
        elif kind == 'eq':
            eq[ap['index']] = val

    calls = []
    for (osc, arg), slots in coefmap.items():
        top = max(slots)
        parts = [_fmt(slots[i]) if i in slots else '' for i in range(top + 1)]
        kw = {arg: ",".join(parts)}
        if osc is not None:
            kw['osc'] = osc
        calls.append(kw)
    for osc, arg, val in scalars:
        kw = {arg: val}
        if osc is not None:
            kw['osc'] = osc
        calls.append(kw)
    # NOTE the `if`: an envelope group with no user-stored slot emits NOTHING,
    # whether or not we know the patch's envelope. Seeding the editor's display
    # never reaches this path.
    if env['amp']:
        calls.append({'osc': OSC_CTL,
                      'bp0': _adsr_string('amp', env['amp'],
                                          (penv or {}).get('amp'))})
    if env['filter']:
        calls.append({'osc': OSC_CTL,
                      'bp1': _adsr_string('filter', env['filter'],
                                          (penv or {}).get('filter'))})
    # FM 4-stage breakpoint envelopes: one complete bp0 per osc,
    # "T1,L1,T2,L2,T3,L3,T4,L4", assembled from the fields the user set with the
    # rest filled from the neutral FM_BP_DEFAULTS (operators use their own bp0
    # for the amp env; the ALGO parent's bp0 is the pitch env).
    for osc, fields in bp_env.items():
        vals = list(FM_BP_DEFAULTS.get(osc, [0] * 8))
        for pos, v in fields.items():
            vals[pos] = v
        calls.append({'osc': osc, 'bp0': ",".join(_fmt(v) for v in vals)})
    if any(v is not None for v in eq):
        calls.append({'eq': "%s,%s,%s" % tuple(
            _fmt(v if v is not None else 0) for v in eq)})
    return calls


def patch_fx(patch):
    """FX values the baked patch string itself applies in AMY (chorus/EQ for
    the junos, etc.), from the generated patchfx table. {} = none/unknown."""
    try:
        import patchfx
        return patchfx.patch_fx(patch)
    except Exception as e:
        import decklog
        decklog.dbg("amyparams: patch_fx(%s) failed: %r" % (patch, e))
        return {}


def device_patch_fx(device):
    """The patch-applied FX context for a device's FX panel/router apply:
    the ACTIVE instrument's patch if it lives on this device, else the first
    enabled internal instrument's. (FX buses are global per device, so with
    layered instruments AMY holds the last-loaded patch's values; the active
    instrument is the least surprising stand-in.)"""
    if device != 'internal':
        return {}
    try:
        import deckcfg
        cfg = deckcfg.load()
        cand = [deckcfg.get_instrument(deckcfg.active_instrument())]
        cand += cfg.get('instruments', [])
        for ins in cand:
            if (ins and ins.get('device') == 'internal'
                    and ins.get('enabled', True)
                    and ins.get('type', 'juno6') in ('juno6', 'dx7', 'piano')):
                return patch_fx(ins.get('patch', 0))
    except Exception as e:
        import decklog
        decklog.dbg("amyparams: device_patch_fx(%s) failed: %r" % (device, e))
        pass
    return {}


def _merge_fx(fx, pfx):
    """Layer: defaults < patch-applied values < user overrides. Returns
    (merged, touched-bus-set). Patch strings set their own FX (juno patches
    configure chorus/EQ as part of their sound), so both the editor and the
    apply path must treat the PATCH values -- not zeros -- as the baseline;
    zeroing untouched buses is what dried the boot Juno into an EP."""
    merged = default_fx()
    for bus, vals in (pfx or {}).items():
        if bus in merged and isinstance(vals, dict):
            merged[bus].update(vals)
    touched = set()
    for bus, vals in (fx or {}).items():
        if bus in merged and isinstance(vals, dict) and vals:
            merged[bus].update(vals)
            touched.add(bus)
    return merged, touched


def fx_value(fx, pfx, bus, name):
    """One effective FX value for the editor: user > patch > default."""
    merged, _ = _merge_fx(fx, pfx)
    return merged[bus][name]


def _bus_strings(merged):
    """Wire list-strings for one bus's FX state. Wire slot orders: chorus
    k=[level,max_delay,lfo_freq,depth], reverb h=[level,liveness,damping,
    xover], echo M=[level,delay_ms,max_delay_ms,feedback,filter_coef] --
    slots we don't manage stay empty (AMY keeps its current/default)."""
    ch, rv, ec, eq = (merged['chorus'], merged['reverb'], merged['echo'],
                      merged['eq'])
    return {
        'chorus': "%s,,%s,%s" % (_fmt(ch['level']), _fmt(ch['freq']),
                                 _fmt(ch['depth'])),
        'reverb': "%s,%s,%s" % (_fmt(rv['level']),
                                _fmt(min(0.95, rv['liveness'])),
                                _fmt(rv['damping'])),
        'echo': "%s,%s,,%s" % (_fmt(ec['level']), _fmt(ec['delay_ms']),
                               _fmt(ec['feedback'])),
        'eq': "%s,%s,%s" % (_fmt(eq['low']), _fmt(eq['mid']),
                            _fmt(eq['high'])),
    }


def fx_bus_baseline(pfx):
    """A bus's deterministic FX baseline: defaults merged with the FX its
    instrument's PATCH applies (patchfx table). Sent to every internal bus
    after all patches load, because baked patch strings always land their
    chorus/EQ on bus 0 -- without this, load order decided every bus's FX."""
    merged, _ = _merge_fx(None, pfx)
    return _bus_strings(merged)


def fx_send_strings(fx, pfx=None):
    """User-touched PER-INSTRUMENT FX as {bus_kw: wire-string} for
    amy.send(bus=B, ...), layered over the patch baseline. Reverb is NOT here:
    it is the shared per-device room (AMY_MASTER_REVERB runs one reverb on
    the master mix), sent once by the router without a bus."""
    merged, touched = _merge_fx(fx, pfx)
    s = _bus_strings(merged)
    return {b: s[b] for b in ('chorus', 'echo') if b in touched}


def fx_reverb_string(fx):
    """The device's shared-room reverb as a wire string. ALWAYS a concrete
    value (defaults = room off, overlaid with the user's settings), never
    None: built-in patch strings can carry baked reverb ('h' params) that
    lands on the GLOBAL room at load -- and with every bus feeding the room
    at full send under aux reverb, one such patch used to wet every
    instrument (and a high baked liveness rang forever). The router asserts
    this string after every rebuild so the room is exactly what the user
    chose."""
    merged, _ = _merge_fx(fx, None)
    return _bus_strings(merged)['reverb']


def fx_eq_string(fx, pfx=None):
    """The device's EQ as an amy.send(eq=...) string 'low,mid,high', or None
    when the user never set EQ (leave the patch's own EQ alone). User values
    layer over the patch's own EQ, not over zeros."""
    if not (fx and isinstance(fx.get('eq'), dict) and fx['eq']):
        return None
    merged, _ = _merge_fx(fx, pfx)
    eq = merged['eq']
    return "%s,%s,%s" % (_fmt(eq['low']), _fmt(eq['mid']), _fmt(eq['high']))
