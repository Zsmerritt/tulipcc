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


def _p(name, group, label, ptype, default, tier, apply, **kw):
    d = {'name': name, 'group': group, 'label': label, 'type': ptype,
         'default': default, 'tier': tier, 'apply': apply, 'scale': 1}
    d.update(kw)
    return d


def _slider(name, group, label, lo, hi, default, tier, apply, scale=1, unit=''):
    return _p(name, group, label, 'slider', default, tier, apply,
              min=lo, max=hi, scale=scale, unit=unit)


def _wave(name, group, label, default, tier, apply):
    return _p(name, group, label, 'dropdown', default, tier, apply,
              options=WAVE_OPTIONS, option_values=WAVE_VALUES)


# --- the parameter table (per-instrument synth params) ---
PARAMS = [
    # Amp / dynamics
    _slider('level', 'Amp', 'level', 0.001, 7, 1.0, 'basic',
            _osc(OSC_CTL, 'amp', COEF_CONST), scale=100),
    _slider('pan', 'Amp', 'pan', -1.0, 1.0, 0.0, 'basic',
            _osc(None, 'pan'), scale=100),
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
    _slider('filter_freq', 'Filter', 'cutoff', 20, 8000, 1000, 'basic',
            _osc(OSC_CTL, 'filter_freq', COEF_CONST), unit='Hz'),
    _slider('resonance', 'Filter', 'resonance', 0.5, 16, 0.7, 'basic',
            _osc(OSC_CTL, 'resonance'), scale=10),
    _slider('filter_kbd', 'Filter', 'kbd track', 0, 1, 0, 'advanced',
            _osc(OSC_CTL, 'filter_freq', COEF_NOTE), scale=100),
    _slider('filter_env', 'Filter', 'env depth', -10, 10, 0, 'basic',
            _osc(OSC_CTL, 'filter_freq', COEF_EG1)),

    # Amp envelope (eg0 / bp0)
    _slider('amp_attack', 'Amp Env', 'attack', 0, 1000, 0, 'basic',
            _env('amp', 'attack'), unit='ms'),
    _slider('amp_decay', 'Amp Env', 'decay', 0, 2000, 100, 'basic',
            _env('amp', 'decay'), unit='ms'),
    _slider('amp_sustain', 'Amp Env', 'sustain', 0, 1, 1.0, 'basic',
            _env('amp', 'sustain'), scale=100, unit='%'),
    _slider('amp_release', 'Amp Env', 'release', 0, 8000, 100, 'basic',
            _env('amp', 'release'), unit='ms'),

    # Filter envelope (eg1 / bp1)
    _slider('filt_attack', 'Filter Env', 'attack', 0, 1000, 0, 'advanced',
            _env('filter', 'attack'), unit='ms'),
    _slider('filt_decay', 'Filter Env', 'decay', 0, 2000, 100, 'advanced',
            _env('filter', 'decay'), unit='ms'),
    _slider('filt_sustain', 'Filter Env', 'sustain', 0, 1, 0, 'advanced',
            _env('filter', 'sustain'), scale=100, unit='%'),
    _slider('filt_release', 'Filter Env', 'release', 0, 8000, 100, 'advanced',
            _env('filter', 'release'), unit='ms'),

    # LFO + modulation
    _slider('lfo_freq', 'LFO', 'rate', 0.1, 20, 4, 'basic',
            _osc(OSC_LFO, 'freq', COEF_CONST), scale=10, unit='Hz'),
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
            {'kind': 'bus_send'}, scale=100, unit='%'),

    # Piano partial detail (OPT-8): a sustained piano voice renders ~24
    # partial oscillators (~14% of a core per held note). This caps the
    # harmonic index the interp-partials engine uses -- lower trades subtle
    # top-end air for real polyphony headroom. Device-global (the C engine
    # has one limit); forwarder applies kind 'piano_quality' via
    # tulip.piano_partials(); synth_send_calls skips it.
    _slider('piano_quality', 'FX', 'partial detail', 8, 40, 40, 'advanced',
            {'kind': 'piano_quality'}),
]

PARAM_BY_NAME = {d['name']: d for d in PARAMS}


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


def _wire_bp(patch_string, letter):
    """The osc-0 value of wire field `letter` in an AMY patch string, else None.

    A patch string is letter-prefixed numeric fields grouped into 'v<n>' osc
    blocks ("v0w7p512b2A5,1,60000,0.85,220,0Z"). The deck's envelope sliders
    write bp0/bp1 on OSC_CTL, so only that osc's block is relevant.
    """
    s = str(patch_string or '')
    osc = None
    found = None
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
            elif c == letter and osc == OSC_CTL:
                found = val
            i = j
        else:
            i += 1
    return found


def patch_env_from_string(patch_string):
    """{'amp': {...}, 'filter': {...}} for the envelopes readable off an AMY
    patch string. A key is present ONLY when that envelope parsed."""
    if not patch_string:
        return {}
    out = {}
    for which, letter in (('amp', 'A'), ('filter', 'B')):
        e = parse_bp(_wire_bp(patch_string, letter))
        if e:
            out[which] = e
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
    except Exception:
        return None        # e.g. a program the gm2 font does not cover
    return None


def patch_env(instr):
    """The envelopes an instrument's baked patch ACTUALLY applies, or {}.

    KNOWN for the GM types ('gm'/'gm2'), whose patch strings the DECK itself
    builds (gm.patch_string / gmbig.patch_string) -- so the real bp0 is right
    there to read.

    UNKNOWN ({}) for juno6/dx7/piano: those patches live in AMY's own
    patches.h and reach the device as a patch NUMBER, never as a string the
    deck can inspect. Rather than fall back to invented numbers, the editor
    renders the unknown case as "patch default". (patchfx.py solves the same
    problem for FX with a table generated from patches.h; an envelope table
    could close this gap the same way -- see the report.)
    """
    return patch_env_from_string(_patch_string(instr))


def _env_slot(name):
    """(env, slot) for an envelope param ('amp','decay'), else (None, None)."""
    d = PARAM_BY_NAME.get(name)
    if d is None:
        return None, None
    ap = d.get('apply') or {}
    if ap.get('kind') != 'env':
        return None, None
    return ap['env'], ap['slot']


def is_env_param(name):
    return _env_slot(name)[0] is not None


def param_value_source(params, penv, name, default=None):
    """(value, source) for one editor control, layered user > patch > schema,
    where source is 'user' | 'patch' | 'default'.

    'default' on an ENVELOPE param means the patch's envelope is unknown: the
    value is a schema fallback the patch never promised, and the editor must
    label it rather than draw it as fact.
    """
    v = (params or {}).get(name)
    if v is not None:
        return v, 'user'
    which, slot = _env_slot(name)
    if which is not None:
        pe = (penv or {}).get(which) or {}
        if slot in pe:
            return pe[slot], 'patch'
    if default is None:
        d = PARAM_BY_NAME.get(name)
        default = d['default'] if d else None
    return default, 'default'


def param_value(params, penv, name, default=None):
    """The effective value one editor control should show (user > patch >
    schema default)."""
    return param_value_source(params, penv, name, default)[0]


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
                        'unit': p.get('unit', '')})
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
    for d in PARAMS:
        for k in ('name', 'group', 'type', 'default', 'tier', 'apply'):
            assert k in d, "param missing %s: %r" % (k, d)
        assert d['name'] not in names, "duplicate param %s" % d['name']
        names.add(d['name'])
        assert d['tier'] in ('basic', 'advanced')
        if d['type'] == 'slider':
            assert d['min'] < d['max'], "bad range %s" % d['name']
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
        if kind in ('bus_send', 'piano_quality'):
            continue        # router-applied kinds, not amy.send(synth=...)
        if kind == 'osc':
            for osc, arg, coef in ap['targets']:
                if coef is None:
                    scalars.append((osc, arg, val))
                else:
                    coefmap.setdefault((osc, arg), {})[coef] = val
        elif kind == 'env':
            env[ap['env']][ap['slot']] = val
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
    except Exception:
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
    except Exception:
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
