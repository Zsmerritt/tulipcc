# presets.py -- user sound-design PRESETS for the deck's instruments.
#
# A "preset" is the SOUND-DESIGN OVERLAY on a patch -- NOT the sample bytes.
# The capture set is exactly what the rack editor's "Reset patch" button clears
# (params / reverb_send / hits / hit_swaps) PLUS the identity of the sound the
# overlay sits on (type / patch, and for drums the kit). Those fields already
# round-trip through deckcfg._INSTRUMENT_KEYS, so RECALL is nothing more than
# writing the captured fields back onto an instrument and re-applying it --
# there is no new apply path (see recall()).
#
# FX/EQ ARE DELIBERATELY EXCLUDED. FX live on the shared DEVICE bus
# (deckcfg.device_fx / cfg['fx']), not on the instrument, so restoring them
# from a preset would stomp every OTHER instrument on that device. reverb_send
# IS included -- it is a per-instrument sound parameter (forwarder _apply_params),
# not a device-bus setting.
#
# STORAGE: one JSON file per preset under /user/var/presets/<slug>.json. Living
# under /user/var (not inside deck_config.json) means:
#   * a config reset -- which rewrites/clears deck_config.json -- leaves the
#     preset library intact, and
#   * the library survives tulip.upgrade() (the /user partition is preserved).
# Writes go through deckcfg.fenced_write() -- the SAME PCM-safe flash fence the
# config uses -- so a preset save can never race AMY's mmap'd-sample rendering
# into the dual-core watchdog crash. We do NOT hand-roll a second fence.
#
# This module is pure Python (json + os + deckcfg) and is host-testable; it does
# NOT import tulip/lvgl at module scope. The UI lives in rack.py.

import os
import json

import deckcfg

# One file per preset here. Overridable in tests (host tmp dir), read live on
# every call so a test override takes effect without reimporting.
PRESETS_DIR = '/user/var/presets'

RECORD_VERSION = 1

# The fields that make up a preset, mirroring the rack "Reset patch" clear-set
# (params/reverb_send/hits/hit_swaps) plus the sound identity (type/patch, and
# kit for drums). Captured with explicit defaults so RECALL is deterministic:
# recalling a preset restores the overlay to EXACTLY the saved state, clearing
# anything the target instrument had that the preset did not (the same "reset
# then set" behaviour a user expects from loading a preset).
_MAX_NAME = 40
_MAX_SLUG = 48


def _now():
    """A monotonic-ish creation stamp. ticks_ms on device, wall-clock on host;
    used only for display ordering / info, never for identity."""
    try:
        from time import ticks_ms
        return ticks_ms()
    except Exception:
        try:
            import time
            return int(time.time())
        except Exception:
            return 0


def slug(name):
    """A filesystem-safe slug for a preset name: lowercase alnum runs joined by
    single hyphens. Empty / all-punctuation names fall back to 'preset'."""
    out = []
    prev_dash = False
    for ch in (name or '').strip().lower():
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif not prev_dash:
            out.append('-')
            prev_dash = True
    s = ''.join(out).strip('-')
    if len(s) > _MAX_SLUG:
        s = s[:_MAX_SLUG].strip('-')
    return s or 'preset'


def _clean_name(name):
    n = (name or '').strip()
    if len(n) > _MAX_NAME:
        n = n[:_MAX_NAME].strip()
    return n or 'Preset'


def _path(sl):
    return PRESETS_DIR.rstrip('/') + '/' + sl + '.json'


def capture(instr, name=None):
    """Build a versioned preset record from an instrument dict.

    Captures the full sound-design overlay with explicit defaults (so recall is
    deterministic). 'kit' is only meaningful for drums, so it is captured only
    for a drums instrument."""
    instr = instr or {}
    rec = {
        'v': RECORD_VERSION,
        'name': _clean_name(name if name is not None else instr.get('name')),
        'type': instr.get('type', 'juno6'),
        'patch': instr.get('patch', 0),
        'params': dict(instr.get('params') or {}),
        'reverb_send': instr.get('reverb_send', 0.0),
        'hits': dict(instr.get('hits') or {}),
        'hit_swaps': dict(instr.get('hit_swaps') or {}),
        'created': _now(),
    }
    if instr.get('type') == 'drums':
        rec['kit'] = instr.get('kit', 384)
    return rec


# Which record fields get written back onto an instrument on recall. 'name' /
# 'v' / 'created' are metadata, not instrument fields, so they are excluded.
_RECALL_KEYS = ('type', 'patch', 'params', 'reverb_send', 'hits', 'hit_swaps',
                'kit')


def _atomic_write(path, text):
    """Write beside + rename over (littlefs-atomic). Mirrors deckcfg._write's
    crash-safe pattern so a preset file is never left torn."""
    try:
        os.mkdir(PRESETS_DIR)
    except OSError:
        pass
    with open(path + '.new', 'w') as f:
        f.write(text)
    try:
        os.rename(path + '.new', path)          # clobbers on littlefs
    except OSError:
        # Windows host (tests): rename won't clobber an existing file
        try:
            os.remove(path)
        except OSError:
            pass
        os.rename(path + '.new', path)


def _write_file(path, text, _retry=0):
    """PCM-safe write of one preset file. Reuses deckcfg.fenced_write() -- the
    config's flash fence -- and, on the oldest (manual-quiet) firmware where a
    write must wait for silence, defers and retries from the UI queue instead
    of writing into a live PCM render. Never re-implements the fence itself."""
    def do():
        _atomic_write(path, text)
    if deckcfg.fenced_write(do):
        return True
    if _retry < 40:
        try:
            import tulip
            tulip.defer(lambda x: _write_file(path, text, _retry + 1), 0, 250)
            return False
        except Exception:
            pass
    do()        # retry cap reached (or no defer available): accept the risk
    return True


def exists(name):
    """True when a preset with this name's slug already exists on disk."""
    try:
        return _path(slug(name)) and slug(name) + '.json' in _listdir()
    except Exception:
        return False


def _listdir():
    try:
        return [n for n in os.listdir(PRESETS_DIR) if n.endswith('.json')]
    except OSError:
        return []


def unique_name(name):
    """A preset name whose slug does not collide with an existing preset:
    'Bass', then 'Bass 2', 'Bass 3', ... Reuses deckcfg._unique_name over the
    existing preset names so the auto-suffix rule matches the instrument one."""
    existing = [{'name': p['name']} for p in list_presets()]
    base = _clean_name(name)
    candidate = deckcfg._unique_name(existing, base)
    # guard the (pathological) case where suffixing still slug-collides
    n = 2
    while slug(candidate) + '.json' in _listdir():
        candidate = '%s %d' % (base, n)
        n += 1
    return candidate


def save(name, instr):
    """Capture `instr`'s sound overlay under `name` and write it (overwriting a
    same-slug preset). Returns the stored record (with its 'slug')."""
    name = _clean_name(name)
    rec = capture(instr, name=name)
    sl = slug(name)
    _write_file(_path(sl), json.dumps(rec))
    rec = dict(rec)
    rec['slug'] = sl
    return rec


def load(sl):
    """Read one preset record by slug (with 'slug' attached), or None."""
    try:
        with open(_path(sl)) as f:
            rec = json.load(f)
        if isinstance(rec, dict):
            rec['slug'] = sl
            return rec
    except (OSError, ValueError):
        pass
    return None


def list_presets():
    """All stored presets as records (each with 'slug'), sorted by name."""
    out = []
    for fn in _listdir():
        sl = fn[:-5]                     # strip '.json'
        rec = load(sl)
        if rec is not None:
            out.append(rec)
    out.sort(key=lambda r: (r.get('name') or '').lower())
    return out


def delete(sl):
    """Remove a preset file by slug. Missing file is a no-op."""
    try:
        os.remove(_path(sl))
        return True
    except OSError:
        return False


def rename(sl, new_name):
    """Rename a preset. Writes the record under the new name's slug and removes
    the old file (unless the slug is unchanged). Returns the new record, or None
    if the source is gone."""
    rec = load(sl)
    if rec is None:
        return None
    name = _clean_name(new_name)
    new_sl = slug(name)
    if new_sl != sl and (new_sl + '.json') in _listdir():
        name = unique_name(name)
        new_sl = slug(name)
    rec['name'] = name
    _write_file(_path(new_sl), json.dumps(
        {k: v for k, v in rec.items() if k != 'slug'}))
    if new_sl != sl:
        delete(sl)
    rec['slug'] = new_sl
    return rec


def recall(iid, record):
    """Apply a preset onto an instrument -- this IS recall. Writes each captured
    field onto the instrument via the proven deckcfg setters (flush=False so the
    whole overlay lands in ONE flash write), commits once, then rebuilds just
    that instrument (apply_instrument -> forwarder.rebuild_one).

    Recall may change the instrument's TYPE (a drums preset recalled onto a
    melodic instrument, or vice versa) -- the record is a complete sound
    identity, not a partial tweak."""
    if not record:
        return False
    for k in _RECALL_KEYS:
        if k in record:
            deckcfg.set_instrument(iid, k, record[k], flush=False)
    deckcfg.flush()                      # one write for the whole overlay
    deckcfg.apply_instrument(iid)
    return True
