# deckcfg.py -- one small JSON config the deck apps share.
#
# Lives at /user/deck_config.json (in the /user partition, so it survives
# tulip.upgrade()). The Settings / Instrument / MPE apps write to it, and
# boot.py calls apply() on startup to restore everything.

import json

PATH = '/user/deck_config.json'

# runtime-only state (not persisted): the channel we last configured
_state = {}

DEFAULTS = {
    'volume': 4,
    'brightness': 5,
    'tfb_font': 0,
    'ui_btn': 60,          # task-bar / menu button height in px (ui_patch)
    'wifi_ssid': '',
    'wifi_pass': '',
    # instrument (a single synth on one MIDI channel)
    'midi_channel': 1,     # 1-16, the channel we listen on / MPE master
    'patch': 0,            # 0-127 Juno, 128-255 DX7, 256 piano
    'num_voices': 10,
    # MPE
    'mpe': False,
    'mpe_members': 15,
    'mpe_bend': 48,
    'mpe_expression': True,
    'setup_done': False,
}


def load():
    try:
        with open(PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    cfg = dict(DEFAULTS)
    cfg.update(data)
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


def apply_instrument(cfg=None):
    """Set MIDI channel 1's synth from config, and (re)apply MPE if enabled."""
    import midi
    import synth as _synth
    import amy
    if cfg is None:
        cfg = load()
    master = cfg.get('midi_channel', 1)
    voices = cfg.get('num_voices', 10)
    # Release whatever channel we set up last time, so changing channels
    # doesn't leave a ghost synth listening on the old one.
    prev = _state.get('channel')
    if prev is not None and prev != master:
        try:
            midi.config.release_synth_for_channel(prev)
        except Exception:
            pass
    _state['channel'] = master
    try:
        midi.config.add_synth(
            _synth.PatchSynth(patch=cfg.get('patch', 0), num_voices=voices),
            channel=master)
    except Exception as e:
        print("deckcfg: instrument setup failed:", e)
        return
    # MPE only exists on firmware built with the MPE changes (Zsmerritt fork).
    # On stock firmware midi.configure_mpe is absent -- skip quietly so we don't
    # spam errors; the saved settings will take effect once you flash MPE firmware.
    if not hasattr(midi, 'configure_mpe'):
        return
    try:
        if cfg.get('mpe'):
            midi.configure_mpe(cfg.get('mpe_members', 15),
                               cfg.get('mpe_bend', 48), master=master)
            if cfg.get('mpe_expression'):
                amy.send(synth=master, amp={'vel': 1, 'ext0': 0.5})
                amy.send(synth=master,
                         filter_freq={'const': 600, 'note': 1, 'ext1': 2},
                         resonance=1.5)
        else:
            midi.configure_mpe(0, master=master)   # actively turn the zone off
    except Exception as e:
        print("deckcfg: MPE setup failed:", e)


def mpe_supported():
    import midi
    return hasattr(midi, 'configure_mpe')


def apply(cfg=None):
    """Apply everything on boot: audio, display, then instrument + MPE."""
    import tulip
    import amy
    if cfg is None:
        cfg = load()
    try:
        amy.volume(cfg.get('volume', 4))
    except Exception:
        pass
    try:
        tulip.brightness(cfg.get('brightness', 5))
    except Exception:
        pass
    try:
        tulip.tfb_font(cfg.get('tfb_font', 0))
    except Exception:
        pass
    apply_instrument(cfg)
