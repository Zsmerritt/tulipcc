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
    # 'dx7' is built after this dict by _dx7_view() -- it has 9 tabs of ~11
    # controls each, too much to spell out as a literal.
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


# The controls each operator page hosts, in order: level/ratio/amp-LFO then the
# 4-stage amp env (T1,L1..T4,L4). These name the fm_op<N>_* params in
# amyparams.FM_PARAMS.
_FM_OP_FIELDS = ['ratio', 'level', 'amplfo',
                 't1', 'l1', 't2', 'l2', 't3', 'l3', 't4', 'l4']


def _dx7_view():
    """The full AMYboard-parity DX7/FM surface. DX7 is an FM voice, not the
    four-osc analog layout: osc 0 is the ALGO parent (algorithm/feedback/
    pitch-LFO + the pitch env on its bp0), osc 1 the LFO, oscs 2..7 the six
    operators (level/ratio/amp-LFO + a 4-stage amp env on each osc's bp0).

    Layout mirrors that engine, using the deck's own per-page paging idiom (the
    pad editor's per-pad pages / build_tabbed's left tab bar): a Voice page for
    the FM block, a Pitch page (the parent's pitch env), a VCF page (the post-
    filter + its ADSR), then ONE PAGE PER OPERATOR (OP 1..OP 6). 6x11 controls
    can't share a screen and a bp-grid would need a bespoke control; a page per
    operator keeps every slider full-width and reuses the existing machinery.
    The 4-stage envelope uses the deck's own envelope idiom -- one slider per
    stage value (as the ADSR sliders already are), assembled into a bp0 string
    by amyparams.synth_send_calls."""
    tabs = [
        ('Voice', ['fm_algorithm', 'fm_feedback', 'lfo_wave', 'lfo_freq',
                   'fm_lfo_pitch', 'level', 'pan', 'reverb_send']),
        ('Pitch', ['fm_pitch_t1', 'fm_pitch_l1', 'fm_pitch_t2', 'fm_pitch_l2',
                   'fm_pitch_t3', 'fm_pitch_l3', 'fm_pitch_t4', 'fm_pitch_l4']),
        ('VCF', ['filter_freq', 'resonance', 'filter_kbd', 'filter_env',
                 'filt_attack', 'filt_decay', 'filt_sustain', 'filt_release']),
    ]
    for op in range(1, 7):
        tabs.append(('OP %d' % op,
                     ['fm_op%d_%s' % (op, f) for f in _FM_OP_FIELDS]))
    # Only the shared/voice controls need engine-native labels; the per-op and
    # pitch-env controls keep their own def labels (ratio / output / amp LFO /
    # T1 attack..L4 end, T1..L4), which read correctly per page.
    labels = {
        'fm_algorithm': 'algorithm', 'fm_feedback': 'feedback',
        'lfo_wave': 'LFO wave', 'lfo_freq': 'LFO speed',
        'fm_lfo_pitch': 'LFO to pitch', 'level': 'output level',
        'pan': 'pan', 'reverb_send': 'reverb send',
        'filter_freq': 'cutoff', 'resonance': 'resonance',
        'filter_kbd': 'kbd track', 'filter_env': 'env depth',
        'filt_attack': 'VCF attack', 'filt_decay': 'VCF decay',
        'filt_sustain': 'VCF sustain', 'filt_release': 'VCF release',
    }
    return {'name': 'DX7-FM', 'tabs': tabs, 'labels': labels}


_VIEWS['dx7'] = _dx7_view()


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
