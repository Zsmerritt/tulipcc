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


def _slider(name, group, label, lo, hi, default, tier, apply, scale=1):
    return _p(name, group, label, 'slider', default, tier, apply,
              min=lo, max=hi, scale=scale)


def _wave(name, group, label, default, tier, apply):
    return _p(name, group, label, 'dropdown', default, tier, apply,
              options=WAVE_OPTIONS, option_values=WAVE_VALUES)


# --- the parameter table (per-instrument synth params) ---
PARAMS = [
    # Amp / dynamics
    _slider('level', 'Amp', 'level', 0.001, 7, 1.0, 'basic',
            _osc(OSC_CTL, 'amp', COEF_CONST), scale=100),
    _slider('pan', 'Amp', 'pan', -1.0, 1.0, 0.0, 'advanced',
            _osc(None, 'pan'), scale=100),
    _slider('portamento', 'Amp', 'portamento ms', 0, 1000, 0, 'advanced',
            _osc(None, 'portamento')),

    # Oscillator A
    _wave('oscA_wave', 'Osc A', 'wave', 0, 'basic', _osc(OSC_A, 'wave')),
    _slider('oscA_level', 'Osc A', 'level', 0.001, 1.0, 1.0, 'basic',
            _osc(OSC_A, 'amp', COEF_CONST), scale=100),
    _slider('oscA_freq', 'Osc A', 'freq', 50, 2000, 440, 'advanced',
            _osc(OSC_A, 'freq', COEF_CONST)),
    _slider('oscA_duty', 'Osc A', 'duty', 0.5, 0.99, 0.5, 'advanced',
            _osc(OSC_A, 'duty', COEF_CONST), scale=100),

    # Oscillator B
    _wave('oscB_wave', 'Osc B', 'wave', 0, 'advanced', _osc(OSC_B, 'wave')),
    _slider('oscB_level', 'Osc B', 'level', 0.001, 1.0, 1.0, 'advanced',
            _osc(OSC_B, 'amp', COEF_CONST), scale=100),
    _slider('oscB_freq', 'Osc B', 'freq', 50, 2000, 440, 'advanced',
            _osc(OSC_B, 'freq', COEF_CONST)),
    _slider('oscB_duty', 'Osc B', 'duty', 0.5, 0.99, 0.5, 'advanced',
            _osc(OSC_B, 'duty', COEF_CONST), scale=100),

    # Filter (VCF, on the control osc)
    _slider('filter_freq', 'Filter', 'freq', 20, 8000, 1000, 'basic',
            _osc(OSC_CTL, 'filter_freq', COEF_CONST)),
    _slider('resonance', 'Filter', 'resonance', 0.5, 16, 0.7, 'basic',
            _osc(OSC_CTL, 'resonance'), scale=10),
    _slider('filter_kbd', 'Filter', 'kbd track', 0, 1, 0, 'advanced',
            _osc(OSC_CTL, 'filter_freq', COEF_NOTE), scale=100),
    _slider('filter_env', 'Filter', 'env depth', -10, 10, 0, 'advanced',
            _osc(OSC_CTL, 'filter_freq', COEF_EG1)),

    # Amp envelope (eg0 / bp0)
    _slider('amp_attack', 'Amp Env', 'attack', 0, 1000, 0, 'basic',
            _env('amp', 'attack')),
    _slider('amp_decay', 'Amp Env', 'decay', 0, 2000, 100, 'basic',
            _env('amp', 'decay')),
    _slider('amp_sustain', 'Amp Env', 'sustain', 0, 1, 1.0, 'basic',
            _env('amp', 'sustain'), scale=100),
    _slider('amp_release', 'Amp Env', 'release', 0, 8000, 100, 'basic',
            _env('amp', 'release')),

    # Filter envelope (eg1 / bp1)
    _slider('filt_attack', 'Filter Env', 'attack', 0, 1000, 0, 'advanced',
            _env('filter', 'attack')),
    _slider('filt_decay', 'Filter Env', 'decay', 0, 2000, 100, 'advanced',
            _env('filter', 'decay')),
    _slider('filt_sustain', 'Filter Env', 'sustain', 0, 1, 0, 'advanced',
            _env('filter', 'sustain'), scale=100),
    _slider('filt_release', 'Filter Env', 'release', 0, 8000, 100, 'advanced',
            _env('filter', 'release')),

    # LFO + modulation
    _slider('lfo_freq', 'LFO', 'freq', 0.1, 20, 4, 'basic',
            _osc(OSC_LFO, 'freq', COEF_CONST), scale=10),
    _wave('lfo_wave', 'LFO', 'wave', 0, 'advanced', _osc(OSC_LFO, 'wave')),
    _slider('lfo_pitch', 'LFO', 'to pitch', 0, 4, 0, 'advanced',
            _multi([(OSC_A, 'freq', COEF_MOD), (OSC_B, 'freq', COEF_MOD)]),
            scale=100),
    _slider('lfo_pwm', 'LFO', 'to pwm', 0, 0.49, 0, 'advanced',
            _multi([(OSC_A, 'duty', COEF_MOD), (OSC_B, 'duty', COEF_MOD)]),
            scale=100),
    _slider('lfo_filter', 'LFO', 'to filter', 0, 4, 0, 'advanced',
            _osc(OSC_CTL, 'filter_freq', COEF_MOD), scale=100),
    # NOTE: EQ is per-BUS (per-device), not per-synth (verified on AMY) -- it
    # lives in FX below, not here.
]

PARAM_BY_NAME = {d['name']: d for d in PARAMS}


# --- FX schema (per-DEVICE bus): bus -> list of {name,label,min,max,default,arg}
# `arg` is the amy.<bus>() keyword argument.
FX = {
    'reverb': [
        {'name': 'level', 'label': 'level', 'min': 0, 'max': 1, 'default': 0,
         'arg': 'level'},
        {'name': 'liveness', 'label': 'live', 'min': 0, 'max': 1,
         'default': 0.85, 'arg': 'liveness'},
        {'name': 'damping', 'label': 'damp', 'min': 0, 'max': 1,
         'default': 0.5, 'arg': 'damping'},
    ],
    'chorus': [
        {'name': 'level', 'label': 'level', 'min': 0, 'max': 1, 'default': 0,
         'arg': 'level'},
        {'name': 'freq', 'label': 'freq', 'min': 0.1, 'max': 20, 'default': 0.5,
         'arg': 'freq'},
        {'name': 'depth', 'label': 'depth', 'min': 0.01, 'max': 1,
         'default': 0.5, 'arg': 'amp'},
    ],
    'echo': [
        {'name': 'level', 'label': 'level', 'min': 0, 'max': 2, 'default': 0,
         'arg': 'level'},
        {'name': 'delay_ms', 'label': 'delay', 'min': 0, 'max': 5000,
         'default': 500, 'arg': 'delay_ms'},
        {'name': 'feedback', 'label': 'feedback', 'min': 0, 'max': 1,
         'default': 0, 'arg': 'feedback'},
    ],
    # EQ is per-BUS (per-device). It has no amy.eq() fn -- it's applied via
    # amy.send(synth=<a synth on the bus>, eq="low,mid,high"), so it's kept out
    # of FX_BUSES (the fn-applied set) and handled by fx_eq_string().
    'eq': [
        {'name': 'low', 'label': 'low', 'min': -15, 'max': 15, 'default': 0},
        {'name': 'mid', 'label': 'mid', 'min': -15, 'max': 15, 'default': 0},
        {'name': 'high', 'label': 'high', 'min': -15, 'max': 15, 'default': 0},
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
                        'tier': 'basic', 'scale': _fx_scale(p['min'], p['max'])})
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


def _adsr_string(env):
    # AMY bp string mirroring the web ADSR encoding: A,1,D,S,R,0
    return "%s,1,%s,%s,%s,0" % (_fmt(env.get('attack', 0)),
                                _fmt(env.get('decay', 100)),
                                _fmt(env.get('sustain', 1)),
                                _fmt(env.get('release', 100)))


def synth_send_calls(params):
    """Given an instrument's stored params (merged over defaults), return a list
    of kwargs dicts for amy.send(synth=<n>, **kwargs). Deterministic + pure."""
    merged = default_params()
    merged.update(params or {})

    coefmap = {}                    # (osc, arg) -> {coef: value}
    scalars = []                    # (osc, arg, value)  -- coef is None
    env = {'amp': {}, 'filter': {}}
    eq = [None, None, None]

    for name, val in merged.items():
        ap = PARAM_BY_NAME[name]['apply']
        kind = ap['kind']
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
    if env['amp']:
        calls.append({'osc': OSC_CTL, 'bp0': _adsr_string(env['amp'])})
    if env['filter']:
        calls.append({'osc': OSC_CTL, 'bp1': _adsr_string(env['filter'])})
    if any(v is not None for v in eq):
        calls.append({'eq': "%s,%s,%s" % tuple(
            _fmt(v if v is not None else 0) for v in eq)})
    return calls


def fx_calls(fx):
    """Given a device's stored FX overrides, return [(bus, kwargs)] for the
    fn-applied buses amy.reverb()/chorus()/echo(). (EQ is separate --
    fx_eq_string.) Missing values fall back to defaults."""
    merged = default_fx()
    for bus, vals in (fx or {}).items():
        if bus in merged and isinstance(vals, dict):
            merged[bus].update(vals)
    out = []
    for bus in FX_BUSES:
        kw = {p['arg']: merged[bus][p['name']] for p in FX[bus]}
        out.append((bus, kw))
    return out


def fx_eq_string(fx):
    """The device's EQ as an amy.send(eq=...) string 'low,mid,high'."""
    eq = default_fx()['eq']
    if fx and isinstance(fx.get('eq'), dict):
        eq.update(fx['eq'])
    return "%s,%s,%s" % (_fmt(eq['low']), _fmt(eq['mid']), _fmt(eq['high']))
