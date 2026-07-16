# screensaver.py -- idle screen dimming + sleep, with wake on touch or MIDI.
#
# Single idle source: LVGL's built-in input-inactivity timer,
#   lv.display_get_default().get_inactive_time()  -> ms since the last touch.
# Touch resets it automatically. For MIDI we call display.trigger_activity()
# from a midi callback, so incoming notes also reset it -- so one timer covers
# both wake sources.
#
# Two thresholds come from deckcfg (seconds; 0 = never): 'dim_after' and
# 'sleep_after'. idle < dim -> full brightness (the user's 'brightness' setting,
# left untouched so the Settings slider still works); past dim -> DIM_LEVEL; past
# sleep -> SLEEP_LEVEL.
#
# NOTE: tulip.brightness clamps to [1,9] (esp32s3_display.c), so SLEEP_LEVEL=1 is
# near-off, not a hard backlight cut. A true off needs a firmware backlight-off.
#
# start() from boot.py (after deckcfg.apply()); call reload() when the Settings
# dim/sleep values change so it picks them up immediately.

import tulip
import lvgl as lv

DIM_LEVEL = 2       # brightness (1..9) while dimmed
SLEEP_LEVEL = 0     # brightness while asleep: 0 = backlight fully off (needs the
                    # display_brightness(0) firmware change; clamps to ~1/near-off
                    # on older firmware)
TICK_MS = 300       # idle poll period -- small so touch wake feels immediate

# phase: None | 'full' | 'dim' | 'sleep'
_state = {'on': False, 'phase': None, 'full': 5,
          'dim_after': 0, 'sleep_after': 0}


def reload():
    """Re-read brightness + the dim/sleep thresholds from deckcfg."""
    try:
        import deckcfg
        c = deckcfg.load()
        _state['full'] = c.get('brightness', 5)
        _state['dim_after'] = c.get('dim_after', 0) or 0
        _state['sleep_after'] = c.get('sleep_after', 0) or 0
    except Exception:
        pass


def _idle_ms():
    try:
        return lv.display_get_default().get_inactive_time()
    except Exception:
        return 0


def _apply_phase(phase):
    # Only touch brightness when the phase actually changes -- while 'full', we
    # leave brightness alone so the Settings slider stays in control.
    if phase == _state['phase']:
        return
    _state['phase'] = phase
    level = {'full': _state['full'], 'dim': DIM_LEVEL, 'sleep': SLEEP_LEVEL}[phase]
    try:
        tulip.brightness(level)
    except Exception:
        pass


def note_activity(*a):
    """Wake on MIDI. Hot path: this runs per MIDI message, so it does ONE
    dict store; the lv trigger_activity() round-trip (~20-50us/msg through
    the MP binding -- ~1-2% of the MP core under an MPE stream) happens
    once per 300ms tick instead (O-3). Dim/sleep still restores instantly
    on the first message after idle (that branch is rare by definition)."""
    _state['midi_seen'] = True
    if _state['phase'] in ('dim', 'sleep'):
        _apply_phase('full')


def _tick(x):
    # No periodic config re-read here: Settings calls reload() whenever
    # brightness or the dim/sleep thresholds change, so polling the config file
    # from flash every few seconds bought nothing.
    if not _state['on']:
        return
    if _state.get('midi_seen'):
        _state['midi_seen'] = False
        try:
            lv.display_get_default().trigger_activity()
        except Exception:
            pass
    idle = _idle_ms()
    sa = _state['sleep_after']
    da = _state['dim_after']
    if sa and idle >= sa * 1000:
        _apply_phase('sleep')
    elif da and idle >= da * 1000:
        _apply_phase('dim')
    else:
        _apply_phase('full')
    try:
        tulip.defer(_tick, 0, TICK_MS)
    except Exception:
        _state['on'] = False


def start():
    if _state['on']:
        return
    reload()
    _state['on'] = True
    _state['phase'] = None
    try:
        import midi
        midi.add_callback(note_activity)   # wake on incoming MIDI
    except Exception:
        pass
    try:
        tulip.defer(_tick, 0, TICK_MS)
    except Exception:
        _state['on'] = False


def stop():
    """Stop dimming and restore the user's full brightness."""
    _state['on'] = False
    _state['phase'] = None
    try:
        tulip.brightness(_state['full'])
    except Exception:
        pass
