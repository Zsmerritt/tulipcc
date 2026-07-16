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
    'dx7': {
        'name': 'DX7',
        'tabs': [
            ('Level', ['level', 'pan', 'reverb_send']),
            ('Tone', ['filter_freq', 'resonance']),
            ('EG', ['amp_attack', 'amp_decay', 'amp_sustain', 'amp_release']),
            ('Mod', ['lfo_freq', 'lfo_pitch']),
        ],
        'labels': {
            'level': 'output level', 'pan': 'pan',
            'filter_freq': 'brightness', 'resonance': 'edge',
            'amp_attack': 'EG rate 1 (attack)', 'amp_decay': 'EG rate 2 (decay)',
            'amp_sustain': 'EG level (sustain)', 'amp_release': 'EG release',
            'lfo_freq': 'LFO speed', 'lfo_pitch': 'LFO pitch mod',
        },
    },
    'piano': {
        'name': 'Piano',
        'tabs': [
            ('Tone', ['level', 'filter_freq', 'resonance', 'pan',
                      'reverb_send']),
            ('Dynamics', ['amp_attack', 'amp_decay', 'amp_sustain',
                          'amp_release']),
        ],
        'labels': {
            'level': 'volume', 'filter_freq': 'brightness',
            'resonance': 'hardness', 'pan': 'pan',
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
