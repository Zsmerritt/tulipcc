# curated.py -- engine-native curated editor views (D4).
#
# A curated view is a thin data spec over the SAME amyparams.PARAMS: it picks a
# subset, groups it into engine-native tabs, and relabels controls with terms a
# player of that engine expects (a Juno-6 owner wants "DCO / VCF / VCA", not
# "Osc A / Filter / Amp"). The generic grouped editor (amyparams.tabbed_groups)
# stays the fallback for any engine without a curated view.
#
# Views are DATA -- adding one never needs a bespoke editor class. rack.sound_panel
# picks the view for the active instrument's engine (amyparams.engine_of).
#
# NOTE: the deck's param table is a subtractive/analog set. Juno-6 maps onto it
# natively; the DX7 (FM) and Piano (PCM) views expose the subset that still
# applies (level, brightness=filter, envelope, mod) with engine-appropriate
# names -- a familiar simplified surface until engine-specific params are added.

import amyparams
from parameditor import ParamEditor


# Each view: ordered tabs of (tab_label, [param names]) + engine-native labels.
_VIEWS = {
    'juno6': {
        'name': 'Juno-6',
        'tabs': [
            ('DCO', ['oscA_wave', 'oscA_duty', 'oscB_wave', 'oscB_duty',
                     'oscB_freq']),
            ('VCF', ['filter_freq', 'resonance', 'filter_env', 'filter_kbd']),
            ('ENV', ['amp_attack', 'amp_decay', 'amp_sustain', 'amp_release']),
            ('LFO', ['lfo_freq', 'lfo_pitch', 'lfo_pwm', 'lfo_filter']),
            ('VCA', ['level', 'pan', 'reverb_send']),
        ],
        'labels': {
            'oscA_wave': 'DCO wave', 'oscA_duty': 'DCO pulse width',
            'oscB_wave': 'sub wave', 'oscB_duty': 'sub width',
            'oscB_freq': 'sub tune', 'filter_freq': 'cutoff',
            'resonance': 'resonance', 'filter_env': 'env mod',
            'filter_kbd': 'kybd follow', 'lfo_freq': 'LFO rate',
            'lfo_pitch': 'LFO to DCO', 'lfo_pwm': 'LFO to PWM',
            'lfo_filter': 'LFO to VCF', 'level': 'volume', 'pan': 'pan',
            'amp_attack': 'attack', 'amp_decay': 'decay',
            'amp_sustain': 'sustain', 'amp_release': 'release',
            'reverb_send': 'reverb send',
        },
    },
    # DX7 is an FM voice, not the four-osc analog layout: osc 0 is the ALGO
    # parent (algorithm/feedback/pitch-LFO), osc 1 the LFO, oscs 2..7 the six
    # operators. The view mirrors that -- a Voice page for the global FM
    # controls, a Tone page for the post-filter, then ONE PAGE PER OPERATOR
    # (OP 1..OP 6), each with that operator's ratio / output / decay. Page-per-
    # operator is the deck's own paging idiom (the pad editor's per-pad pages,
    # build_tabbed's left tab bar) -- 6*3 controls can't share a screen, and a
    # grid would need a bespoke control; tabs reuse the existing machinery and
    # keep each operator's three sliders full-width and legible. See
    # amyparams.FM_PARAMS for the osc addressing and the "patch default" honesty.
    'dx7': {
        'name': 'DX7-FM',
        'tabs': [
            ('Voice', ['fm_algorithm', 'fm_feedback', 'lfo_freq',
                       'fm_lfo_pitch', 'level', 'pan', 'reverb_send']),
            ('Tone', ['filter_freq', 'resonance']),
            ('OP 1', ['fm_op1_ratio', 'fm_op1_level', 'fm_op1_decay']),
            ('OP 2', ['fm_op2_ratio', 'fm_op2_level', 'fm_op2_decay']),
            ('OP 3', ['fm_op3_ratio', 'fm_op3_level', 'fm_op3_decay']),
            ('OP 4', ['fm_op4_ratio', 'fm_op4_level', 'fm_op4_decay']),
            ('OP 5', ['fm_op5_ratio', 'fm_op5_level', 'fm_op5_decay']),
            ('OP 6', ['fm_op6_ratio', 'fm_op6_level', 'fm_op6_decay']),
        ],
        'labels': {
            'fm_algorithm': 'algorithm', 'fm_feedback': 'feedback',
            'lfo_freq': 'LFO speed', 'fm_lfo_pitch': 'LFO to pitch',
            'level': 'output level', 'pan': 'pan',
            'reverb_send': 'reverb send',
            'filter_freq': 'brightness', 'resonance': 'edge',
            'fm_op1_ratio': 'ratio', 'fm_op1_level': 'output',
            'fm_op1_decay': 'decay',
            'fm_op2_ratio': 'ratio', 'fm_op2_level': 'output',
            'fm_op2_decay': 'decay',
            'fm_op3_ratio': 'ratio', 'fm_op3_level': 'output',
            'fm_op3_decay': 'decay',
            'fm_op4_ratio': 'ratio', 'fm_op4_level': 'output',
            'fm_op4_decay': 'decay',
            'fm_op5_ratio': 'ratio', 'fm_op5_level': 'output',
            'fm_op5_decay': 'decay',
            'fm_op6_ratio': 'ratio', 'fm_op6_level': 'output',
            'fm_op6_decay': 'decay',
        },
    },
    'piano': {
        'name': 'Piano',
        'tabs': [
            ('Tone', ['level', 'filter_freq', 'resonance', 'pan',
                      'reverb_send', 'piano_quality']),
            ('Dynamics', ['amp_attack', 'amp_decay', 'amp_sustain',
                          'amp_release']),
        ],
        'labels': {
            'level': 'volume', 'filter_freq': 'brightness',
            'resonance': 'hardness', 'pan': 'pan',
            'piano_quality': 'partial detail',
            'amp_attack': 'strike', 'amp_decay': 'body',
            'amp_sustain': 'sustain', 'amp_release': 'release',
        },
    },
}


class CuratedEditor(ParamEditor):
    """A ParamEditor whose labels come from a per-engine map (falls back to the
    param's own label)."""

    def __init__(self, iid, defs=None, labels=None, **kw):
        super().__init__(iid, defs=defs, **kw)
        self._labels = labels or {}

    def label_for(self, d):
        return self._labels.get(d['name'], d.get('label', d['name']))


def has_view(engine):
    return engine in _VIEWS


def view_name(engine):
    return _VIEWS.get(engine, {}).get('name')


def labels(engine):
    return _VIEWS.get(engine, {}).get('labels', {})


def tabbed(engine, show_advanced):
    """[(tab_label, defs)] for a curated engine view, tier-filtered. Unknown
    param names are skipped; empty tabs (all-advanced hidden in Basic) drop out."""
    spec = _VIEWS.get(engine)
    if spec is None:
        return amyparams.tabbed_groups(show_advanced)
    out = []
    for label, names in spec['tabs']:
        defs = []
        for n in names:
            d = amyparams.PARAM_BY_NAME.get(n)
            if d is None:
                continue
            if show_advanced or d.get('tier') == 'basic':
                defs.append(d)
        if defs:
            out.append((label, defs))
    return out
