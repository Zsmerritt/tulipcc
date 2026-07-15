# deckcfg.py -- the shared JSON config for the deck apps.
#
# Lives at /user/deck_config.json (in the /user partition, so it survives
# tulip.upgrade()). Settings / Instrument / MPE / Fleet write to it, and boot.py
# calls apply() on startup to restore everything.
#
# INSTRUMENT-FIRST model (deck/PLAN-rework.md Phase 2). The canonical unit is an
# "instrument": a sound bound to a device + MIDI channel. Multiple instruments
# may share a channel (layering / stacking).
#
#   instrument = {id, name, device, channel, patch, num_voices,
#                 mpe:{enabled, members, bend, expression}, enabled}
#     device = 'internal' (the Tulip AMY) or a board index int (USB-MIDI device).
#   active_instrument = the id of the instrument the UI is editing.
#
# BACKWARD COMPATIBILITY: earlier configs stored a device-first `instances` list.
# load() migrates `instances` -> `instruments` (and an even older flat single-
# instrument config) so a live config is never lost. A compat `instances()`
# facade (device-first view over the instruments) is kept so the current UI keeps
# working until Milestones B/C rewire it onto the instrument model.

import json

PATH = '/user/deck_config.json'

# runtime-only state (not persisted). _state['cfg'] caches the parsed config so
# load() stops re-reading + re-parsing the JSON file on every call -- on the
# ESP32-S3 every one of those was an SPI-flash read plus heap churn, and callers
# hit load() dozens of times per panel build. save() keeps it in sync.
_state = {}

# Per-device polyphony budget (voices). A tunable constant; boards + the Tulip
# AMY are treated as having this capacity for the load meter until we probe real
# limits.
DEVICE_CAPACITY = 32

# canonical instrument fields
_INSTRUMENT_KEYS = ('id', 'name', 'device', 'channel', 'patch', 'num_voices',
                    'enabled', 'params', 'type', 'pads', 'kit')


def type_of_patch(patch):
    """Infer an engine type from a patch number (migration + default)."""
    try:
        p = int(patch)
    except (TypeError, ValueError):
        p = 0
    if p < 128:
        return 'juno6'
    if p < 256:
        return 'dx7'
    return 'piano'
_MPE_KEYS = ('enabled', 'members', 'bend', 'expression')


DEFAULTS = {
    'volume': 4,
    'brightness': 5,
    'tfb_font': 0,
    'ui_btn': 60,
    'render_partial': False,  # buffered partial rendering (smoother touch UI)
    'render_vsync': True,     # gate the partial-mode copy to vsync (tear-free)
    'wifi_ssid': '',
    'wifi_pass': '',
    'setup_done': False,
    # screensaver thresholds in seconds (0 = never); see screensaver.py
    'dim_after': 0,
    'sleep_after': 0,
    # global MPE gate: MPE is OFF and hidden until enabled here (Settings)
    'mpe_enabled': False,
    # per-device FX bus overrides: {device_key: {reverb:{...}, chorus:{...},
    # echo:{...}}} where device_key is 'internal' or str(board index). Empty =
    # amyparams defaults (all FX off). See amyparams.FX.
    'fx': {},
    # favorite patch numbers (starred in the patch picker; sorted to the top)
    'favorites': [],
}


def _default_mpe():
    return {'enabled': False, 'members': 15, 'bend': 48, 'expression': True}


def default_instrument(index, device=None, channel=None):
    """A fresh instrument. By index: 0 = internal Tulip on ch1; index k = board
    (k-1) on channel (1+k) -- matching the historical instance layout."""
    if device is None:
        device = 'internal' if index == 0 else index - 1
    if channel is None:
        channel = 1 if index == 0 else 1 + index
    name = 'Tulip' if device == 'internal' else 'Board ' + chr(65 + int(device))
    return {
        'id': index, 'name': name, 'device': device, 'channel': channel,
        'patch': 0, 'num_voices': 10, 'mpe': _default_mpe(), 'enabled': True,
        'type': 'juno6',       # engine: juno6 | dx7 | piano | drums
        'params': {},          # per-synth AMY param overrides (amyparams.PARAMS)
    }


def _merge_instrument(index, d):
    instr = default_instrument(index)
    if isinstance(d, dict):
        for k in _INSTRUMENT_KEYS:
            if k in d:
                instr[k] = d[k]
        m = d.get('mpe')
        if isinstance(m, dict):
            instr['mpe'].update({k: v for k, v in m.items() if k in _MPE_KEYS})
        elif 'mpe' in d:                      # a stray legacy bool
            instr['mpe']['enabled'] = bool(m)
        # Migrate instruments saved before 'type' existed: infer it from the patch.
        if not d.get('type'):
            instr['type'] = type_of_patch(instr.get('patch', 0))
    return instr


def _instrument_from_instance(index, inst):
    """Migrate one old device-first `instance` dict into an instrument."""
    if inst.get('kind') == 'internal':
        device = 'internal'
    else:
        device = inst.get('device')
        if device is None:
            device = max(0, index - 1)
    instr = default_instrument(index, device=device, channel=inst.get('channel'))
    if inst.get('name'):
        instr['name'] = inst['name']
    instr['patch'] = inst.get('patch', 0)
    instr['num_voices'] = inst.get('num_voices', 10)
    instr['enabled'] = inst.get('enabled', True)
    instr['mpe'] = {
        'enabled': inst.get('mpe', False),
        'members': inst.get('mpe_members', 15),
        'bend': inst.get('mpe_bend', 48),
        'expression': inst.get('mpe_expression', True),
    }
    return instr


def _instrument_from_flat(data):
    """Migrate the oldest single-instrument flat config into one instrument."""
    instr = default_instrument(0)
    instr['patch'] = data.get('patch', 0)
    instr['num_voices'] = data.get('num_voices', 10)
    instr['channel'] = data.get('midi_channel', 1)
    instr['mpe'] = {
        'enabled': data.get('mpe', False),
        'members': data.get('mpe_members', 15),
        'bend': data.get('mpe_bend', 48),
        'expression': data.get('mpe_expression', True),
    }
    return instr


def load():
    cached = _state.get('cfg')
    if cached is not None:
        return cached
    try:
        with open(PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in data.items()
                if k not in ('instances', 'instruments')})

    if isinstance(data.get('instruments'), list) and data['instruments']:
        instruments = [_merge_instrument(i, d)
                       for i, d in enumerate(data['instruments'])]
    elif isinstance(data.get('instances'), list) and data['instances']:
        instruments = [_instrument_from_instance(i, d)
                       for i, d in enumerate(data['instances'])]
    elif any(k in data for k in ('patch', 'midi_channel', 'num_voices', 'mpe')):
        instruments = [_instrument_from_flat(data)]
    else:
        instruments = [default_instrument(0)]
    cfg['instruments'] = instruments

    ids = [i['id'] for i in instruments]
    if 'active_instrument' in data:
        act = data['active_instrument']
    elif isinstance(data.get('active_instance'), int):
        # migrate the old active index into the corresponding instrument id
        idx = data['active_instance']
        act = ids[idx] if 0 <= idx < len(ids) else ids[0]
    else:
        act = ids[0]
    if act not in ids:
        act = ids[0]
    cfg['active_instrument'] = act
    _state['cfg'] = cfg
    return cfg


def _write(cfg):
    # Drop the legacy `instances` key if an old config still carries it.
    data = {k: v for k, v in cfg.items() if k != 'instances'}
    try:
        with open(PATH, 'w') as f:
            json.dump(data, f)
    except OSError as e:
        print("deckcfg: could not save:", e)


def save(cfg):
    _state['cfg'] = cfg
    _write(cfg)


def flush():
    """Write the cached config to flash now. Pairs with the setters' flush=False
    (drag-time updates): the UI updates the cache per slider tick and flushes
    once on release, so a drag costs one flash write instead of dozens."""
    cfg = _state.get('cfg')
    if cfg is not None:
        _write(cfg)


def invalidate():
    """Drop the cache so the next load() re-reads the file (tests / external
    edits to the config file)."""
    _state.pop('cfg', None)


def set_value(key, value):
    cfg = load()
    cfg[key] = value
    save(cfg)
    return cfg


def get(key, default=None):
    return load().get(key, default)


# --- patch favorites (list of patch numbers, newest last) ---
def favorites(cfg=None):
    return list((cfg or load()).get('favorites', []))


def is_favorite(patch, cfg=None):
    try:
        return int(patch) in favorites(cfg)
    except (TypeError, ValueError):
        return False


def toggle_favorite(patch):
    cfg = load()
    favs = list(cfg.get('favorites', []))
    p = int(patch)
    if p in favs:
        favs.remove(p)
    else:
        favs.append(p)
    cfg['favorites'] = favs
    save(cfg)
    return p in favs


# --- instrument accessors (canonical) ---
def instruments(cfg=None):
    return (cfg or load())['instruments']


def get_instrument(iid, cfg=None):
    for i in instruments(cfg):
        if i['id'] == iid:
            return i
    return None


def active_instrument(cfg=None):
    return (cfg or load())['active_instrument']


def set_active_instrument(iid):
    cfg = load()
    if any(i['id'] == iid for i in cfg['instruments']):
        cfg['active_instrument'] = iid
        save(cfg)
    return cfg


def _next_id(insts):
    return (max(i['id'] for i in insts) + 1) if insts else 0


def _unique_name(insts, base):
    """A name not already used by another instrument (so rows are tellable apart):
    'Tulip', then 'Tulip 2', 'Tulip 3', ..."""
    names = {i.get('name') for i in insts}
    if base not in names:
        return base
    n = 2
    while ('%s %d' % (base, n)) in names:
        n += 1
    return '%s %d' % (base, n)


def next_free_channel(device, cfg=None, exclude_iid=None):
    """The lowest MIDI channel (1-16) not used by another instrument on `device`
    (so a new/moved instrument lands on a free channel, not always ch 1)."""
    cfg = cfg or load()
    used = {i.get('channel') for i in cfg['instruments']
            if i.get('device') == device and i.get('id') != exclude_iid}
    for ch in range(1, 17):
        if ch not in used:
            return ch
    return 1


def add_instrument(device='internal', channel=1, **kw):
    cfg = load()
    insts = cfg['instruments']
    iid = _next_id(insts)
    instr = default_instrument(iid, device=device, channel=channel)
    instr['id'] = iid
    for k, v in kw.items():
        if k in _INSTRUMENT_KEYS:
            instr[k] = v
    if 'name' not in kw:                       # distinct default name
        instr['name'] = _unique_name(insts, instr.get('name', 'Instrument'))
    insts.append(instr)
    save(cfg)
    return instr


def remove_instrument(iid):
    cfg = load()
    cfg['instruments'] = [i for i in cfg['instruments'] if i['id'] != iid]
    if not cfg['instruments']:
        cfg['instruments'] = [default_instrument(0)]
    ids = [i['id'] for i in cfg['instruments']]
    if cfg['active_instrument'] not in ids:
        cfg['active_instrument'] = ids[0]
    save(cfg)
    return cfg


def set_instrument(iid, key, val, flush=True):
    """Set one instrument field. flush=False updates only the in-RAM cache
    (typing-time updates, e.g. rename per keystroke); call flush() to commit."""
    cfg = load()
    for i in cfg['instruments']:
        if i['id'] == iid:
            if key == 'mpe' and isinstance(val, dict):
                i.setdefault('mpe', _default_mpe()).update(val)
            else:
                i[key] = val
            break
    if flush:
        save(cfg)
    else:
        _state['cfg'] = cfg
    return cfg


def set_instrument_mpe(iid, subkey, val):
    cfg = load()
    for i in cfg['instruments']:
        if i['id'] == iid:
            i.setdefault('mpe', _default_mpe())[subkey] = val
            break
    save(cfg)
    return cfg


def get_instrument_param(iid, name, default=None):
    """A stored per-synth param value, else the caller default, else the
    amyparams schema default."""
    instr = get_instrument(iid)
    if instr is not None:
        val = instr.get('params', {}).get(name)
        if val is not None:
            return val
    if default is not None:
        return default
    try:
        import amyparams
        return amyparams.PARAM_BY_NAME[name]['default']
    except Exception:
        return default


def set_instrument_param(iid, name, value, flush=True):
    """Set one stored param. flush=False updates only the in-RAM cache (for
    slider drags -- live audition reads the cache); call flush() on release."""
    cfg = load()
    for i in cfg['instruments']:
        if i['id'] == iid:
            i.setdefault('params', {})[name] = value
            break
    if flush:
        save(cfg)
    else:
        _state['cfg'] = cfg
    return cfg


def _device_key(device):
    return 'internal' if device == 'internal' else str(device)


def device_fx(device, cfg=None):
    """The stored FX-bus overrides for a device ({} = all defaults)."""
    return (cfg or load()).get('fx', {}).get(_device_key(device), {})


def set_device_fx(device, bus, name, value, flush=True):
    """Set one FX-bus value. flush=False updates only the in-RAM cache (slider
    drags); call flush() on release."""
    cfg = load()
    fx = cfg.setdefault('fx', {})
    fx.setdefault(_device_key(device), {}).setdefault(bus, {})[name] = value
    if flush:
        save(cfg)
    else:
        _state['cfg'] = cfg
    return cfg


def instruments_on_channel(ch, cfg=None):
    return [i for i in instruments(cfg)
            if i.get('enabled', True) and i.get('channel') == ch]


def device_load(device, cfg=None):
    """Sum of num_voices for enabled instruments assigned to `device`."""
    return sum(i.get('num_voices', 0) for i in instruments(cfg)
               if i.get('enabled', True) and i.get('device') == device)


def device_list(cfg=None):
    """The internal Tulip AMY plus each USB-MIDI board, with per-device load."""
    cfg = cfg or load()
    try:
        import tulip
        n = tulip.num_midi_devices()
    except Exception:
        n = 0
    devs = [{'device': 'internal', 'name': 'Tulip', 'kind': 'internal',
             'connected': True, 'capacity': DEVICE_CAPACITY,
             'load': device_load('internal', cfg)}]
    for d in range(n):
        devs.append({'device': d, 'name': 'Board ' + chr(65 + d),
                     'kind': 'amyboard', 'connected': True,
                     'capacity': DEVICE_CAPACITY, 'load': device_load(d, cfg)})
    # configured-but-not-currently-connected boards still show (disconnected)
    used = {i.get('device') for i in cfg['instruments']
            if isinstance(i.get('device'), int)}
    for d in sorted(x for x in used if x >= n):
        devs.append({'device': d, 'name': 'Board ' + chr(65 + d),
                     'kind': 'amyboard', 'connected': False,
                     'capacity': DEVICE_CAPACITY, 'load': device_load(d, cfg)})
    return devs


# --- applying config to hardware ---
def mpe_supported():
    import midi
    return hasattr(midi, 'configure_mpe')


def mpe_enabled(cfg=None):
    """The global MPE gate. When False, no MPE UI shows and the router applies
    no MPE, regardless of any instrument's own mpe.enabled."""
    return bool((cfg or load()).get('mpe_enabled', False))


def apply_all(cfg=None):
    """The router (forwarder) owns all instrument sound generation now, so
    applying config = (re)start it. It rebuilds the internal synths and pushes
    board patches from the current instruments."""
    try:
        import forwarder
        forwarder.start()
    except Exception as e:
        print("deckcfg: router start failed:", e)


def apply_instance(i=None, cfg=None):
    apply_all(cfg)


def apply_instrument(iid=None, cfg=None):
    apply_all(cfg)


def sync_time():
    """NTP + LOCALIZE: set the RTC from NTP (UTC), then shift it to local time
    using the UTC offset of the network's public IP (geo-IP), so the top-bar
    clock reads wall-clock time. The offset is cached in config, so later
    NTP-only syncs still localize even if the geo lookup fails."""
    import tulip
    if tulip.ip() is None:
        return False
    try:
        import ntptime
        ntptime.settime()               # RTC := UTC
    except Exception:
        return False
    off = None
    try:
        # ip-api.com: plain HTTP, tiny JSON, offset in seconds.
        # (worldtimeapi.org trips tuliprequests with a BadStatusLine.)
        import tuliprequests as urequests
        r = urequests.get('http://ip-api.com/json/?fields=status,offset')
        j = r.json()
        r.close()
        if j.get('status') == 'success':
            off = int(j.get('offset', 0))
            set_value('tz_offset_s', off)
    except Exception:
        pass
    if off is None:
        off = get('tz_offset_s')        # cached from a previous success
    if off:
        try:
            import machine
            import time
            tm = time.localtime(time.time() + off)
            machine.RTC().datetime((tm[0], tm[1], tm[2], tm[6] + 1,
                                    tm[3], tm[4], tm[5], 0))
        except Exception:
            pass
    return True


def apply(cfg=None):
    """Apply device settings on boot: audio + display. The MIDI router is started
    separately by boot.py (forwarder.start())."""
    import tulip
    import amy
    if cfg is None:
        cfg = load()

    def _volume():
        # Current amy has no .volume() -- only amy.send(volume=). The old
        # amy.volume call here failed silently inside the try, so the saved
        # volume was never actually restored at boot (UX-REVIEW-6 C1 fallout).
        vol = getattr(amy, 'volume', None)
        if vol is not None:
            vol(cfg.get('volume', 4))
        else:
            amy.send(volume=cfg.get('volume', 4))
    for fn in (_volume,
               lambda: tulip.brightness(cfg.get('brightness', 5)),
               lambda: tulip.tfb_font(cfg.get('tfb_font', 0)),
               lambda: tulip.display_vsync(1 if cfg.get('render_vsync', True) else 0),
               lambda: tulip.display_partial(1 if cfg.get('render_partial', False) else 0)):
        try:
            fn()
        except Exception:
            pass
