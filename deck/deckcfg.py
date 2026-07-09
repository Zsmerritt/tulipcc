# deckcfg.py -- the shared JSON config for the deck apps.
#
# Lives at /user/deck_config.json (in the /user partition, so it survives
# tulip.upgrade()). Settings / Instrument / MPE / Fleet write to it, and boot.py
# calls apply() on startup to restore everything.
#
# The instrument is modelled as a list of "instances": instance 0 is always the
# internal Tulip AMY; further instances are attached AMYboards. Each instance has
# the same shape (channel, patch, voices, MPE ...) so one UI can edit any of them.
# 'mode' is 'multi' (independent per-channel instruments) or 'stack' (one profile
# fanned out across all instances -- see forwarder.py).

import json

PATH = '/user/deck_config.json'

# runtime-only state (not persisted)
_state = {}

# --- an instance is one AMY: the internal Tulip synth, or an AMYboard ---
_INSTANCE_KEYS = ('name', 'kind', 'id', 'enabled', 'channel', 'device', 'patch',
                  'num_voices', 'mpe', 'mpe_members', 'mpe_bend', 'mpe_expression')


def default_instance(index):
    if index == 0:
        name, kind, ch = 'Tulip', 'internal', 1
    else:
        name, kind, ch = 'Board ' + chr(64 + index), 'amyboard', 1 + index
    return {
        'name': name, 'kind': kind, 'id': None, 'enabled': True,
        # channel = MIDI channel; device = USB-MIDI device index (once the
        # firmware supports per-device output; 0/None = the single claimed device)
        'channel': ch, 'device': None, 'patch': 0, 'num_voices': 10,
        'mpe': False, 'mpe_members': 15, 'mpe_bend': 48, 'mpe_expression': True,
    }


DEFAULTS = {
    'volume': 4,
    'brightness': 5,
    'tfb_font': 0,
    'ui_btn': 60,
    'wifi_ssid': '',
    'wifi_pass': '',
    'setup_done': False,
    # fleet
    'mode': 'multi',           # 'multi' | 'stack'
    'active_instance': 0,
    'detune': {'enabled': False, 'spread_cents': 8, 'unison_voices': 3},
}


def _merge_instance(index, data):
    inst = default_instance(index)
    if isinstance(data, dict):
        inst.update({k: v for k, v in data.items() if k in _INSTANCE_KEYS})
    return inst


def load():
    try:
        with open(PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    cfg = dict(DEFAULTS)
    cfg['detune'] = dict(DEFAULTS['detune'])
    cfg.update({k: v for k, v in data.items()
                if k not in ('instances', 'detune')})

    if isinstance(data.get('instances'), list) and data['instances']:
        cfg['instances'] = [_merge_instance(i, d)
                            for i, d in enumerate(data['instances'])]
    else:
        # migrate an older single-instrument config into instance 0
        inst0 = default_instance(0)
        for old, new in (('patch', 'patch'), ('num_voices', 'num_voices'),
                         ('midi_channel', 'channel'), ('mpe', 'mpe'),
                         ('mpe_members', 'mpe_members'), ('mpe_bend', 'mpe_bend'),
                         ('mpe_expression', 'mpe_expression')):
            if old in data:
                inst0[new] = data[old]
        cfg['instances'] = [inst0]

    if isinstance(data.get('detune'), dict):
        cfg['detune'].update(data['detune'])

    if cfg['active_instance'] >= len(cfg['instances']):
        cfg['active_instance'] = 0
    return cfg


def save(cfg):
    try:
        with open(PATH, 'w') as f:
            json.dump(cfg, f)
    except OSError as e:
        print("deckcfg: could not save:", e)


def set(key, value):
    cfg = load()
    cfg[key] = value
    save(cfg)
    return cfg


def get(key, default=None):
    return load().get(key, default)


# --- instance accessors ---
def instances(cfg=None):
    return (cfg or load())['instances']


def num_instances():
    return len(load()['instances'])


def get_instance(i, cfg=None):
    insts = instances(cfg)
    return insts[i] if 0 <= i < len(insts) else None


def active_index():
    return load()['active_instance']


def set_active(i):
    set('active_instance', i)


def set_instance(i, key, value):
    cfg = load()
    if 0 <= i < len(cfg['instances']):
        cfg['instances'][i][key] = value
        save(cfg)
    return cfg


def set_mode(mode):
    set('mode', mode)


def set_detune(key, value):
    cfg = load()
    cfg['detune'][key] = value
    save(cfg)
    return cfg


def ensure_count(n):
    """Grow/shrink the instance list to n (min 1). Instance 0 stays internal."""
    n = max(1, n)
    cfg = load()
    insts = cfg['instances']
    while len(insts) < n:
        insts.append(default_instance(len(insts)))
    if len(insts) > n:
        del insts[n:]
    if cfg['active_instance'] >= n:
        cfg['active_instance'] = 0
    save(cfg)
    return cfg


# --- applying config to hardware ---
def mpe_supported():
    import midi
    return hasattr(midi, 'configure_mpe')


def _apply_internal(inst):
    import midi
    import synth as _synth
    import amy
    master = inst.get('channel', 1)
    prev = _state.get('internal_channel')
    if prev is not None and prev != master:
        try:
            midi.config.release_synth_for_channel(prev)
        except Exception:
            pass
    _state['internal_channel'] = master
    try:
        midi.config.add_synth(
            _synth.PatchSynth(patch=inst.get('patch', 0),
                              num_voices=inst.get('num_voices', 10)),
            channel=master)
    except Exception as e:
        print("deckcfg: instrument setup failed:", e)
        return
    if not hasattr(midi, 'configure_mpe'):
        return
    try:
        if inst.get('mpe'):
            midi.configure_mpe(inst.get('mpe_members', 15),
                               inst.get('mpe_bend', 48), master=master)
            if inst.get('mpe_expression'):
                amy.send(synth=master, amp={'vel': 1, 'ext0': 0.5})
                amy.send(synth=master,
                         filter_freq={'const': 600, 'note': 1, 'ext1': 2},
                         resonance=1.5)
        else:
            midi.configure_mpe(0, master=master)
    except Exception as e:
        print("deckcfg: MPE setup failed:", e)


def _apply_amyboard(inst):
    # Phase 5: push full params to the board over its channel (companion sketch).
    # For now we send the patch as a Program Change so a stock board follows.
    import tulip
    ch = inst.get('channel', 2) - 1
    patch = inst.get('patch', 0)
    try:
        tulip.midi_out((0xC0 | (ch & 0x0F), patch & 0x7F))
    except Exception:
        pass


def apply_instance(i, cfg=None):
    inst = get_instance(i, cfg)
    if inst is None or not inst.get('enabled', True):
        return
    if inst.get('kind') == 'internal':
        _apply_internal(inst)
    else:
        _apply_amyboard(inst)


def apply_all(cfg=None):
    if cfg is None:
        cfg = load()
    for i in range(len(cfg['instances'])):
        apply_instance(i, cfg)


# Backwards-compatible alias used by the current Instrument/MPE apps: apply the
# active instance.
def apply_instrument(cfg=None):
    if cfg is None:
        cfg = load()
    apply_instance(cfg['active_instance'], cfg)


def apply(cfg=None):
    """Apply everything on boot: audio, display, then every instance."""
    import tulip
    import amy
    if cfg is None:
        cfg = load()
    for fn in (lambda: amy.volume(cfg.get('volume', 4)),
               lambda: tulip.brightness(cfg.get('brightness', 5)),
               lambda: tulip.tfb_font(cfg.get('tfb_font', 0))):
        try:
            fn()
        except Exception:
            pass
    apply_all(cfg)
