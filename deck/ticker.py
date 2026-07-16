# ticker.py -- ONE tick source for the deck's periodic consumers (O-7).
#
# The meter (10Hz), clock, screensaver (3.3Hz) and midimon (6.7Hz) each
# re-armed themselves via tulip.defer: a closure allocation + a shared
# defer slot PER TICK (~20 allocs/s at steady state, competing with panel
# fills and toasts for the pool, and all exposed to the old GC-root bug).
# One lv.timer drives a subscriber table instead; subscribers pay nothing
# per tick beyond their own work.
#
#   key = ticker.every(300, fn)   # fn() about every 300ms (100ms grain)
#   ticker.cancel(key)
#
# A subscriber that raises is TOLERATED for a few consecutive ticks (a
# transient -- e.g. a label touched during a concurrent panel rebuild --
# must not permanently unhook a build-once persistent widget like the
# top-bar clock); only after _MAX_FAILS in a row is it dropped as genuinely
# dead, so it never wedges the shared tick. Panels that want
# teardown-on-delete should still prefer an lv DELETE hook and cancel().

import lvgl as lv

_TICK_MS = 100
_MAX_FAILS = 5          # consecutive raises before a subscriber is dropped
_state = {'timer': None, 'subs': {}, 'n': 0}


def every(period_ms, fn, key=None):
    """Call fn() about every period_ms (quantized to 100ms, min 100).
    Re-registering the same key replaces the old subscriber. Returns key."""
    key = key if key is not None else fn
    _state['subs'][key] = [max(1, round(period_ms / _TICK_MS)), fn, 0]
    _ensure()
    return key


def cancel(key):
    _state['subs'].pop(key, None)


def _fire(_t=None):
    _state['n'] += 1
    n = _state['n']
    for key in list(_state['subs']):
        rec = _state['subs'].get(key)
        if rec is None or n % rec[0]:
            continue
        try:
            rec[1]()
            rec[2] = 0          # a good tick clears the transient-fail run
        except Exception as e:
            rec[2] += 1
            if rec[2] >= _MAX_FAILS:
                cancel(key)     # dead subscriber: drop, never wedge the tick
                try:
                    import decklog
                    decklog.dbg("ticker dropped subscriber %r after %d "
                                "consecutive fails: %r" % (key, rec[2], e))
                except Exception:
                    pass


def _ensure():
    if _state['timer'] is not None:
        return
    try:
        # module-level _fire (not a lambda): the module dict roots it for
        # the GC no matter how the binding stores the callback
        _state['timer'] = lv.timer_create(_fire, _TICK_MS, None)
        return
    except Exception:
        pass
    # no lv.timer on this build: ONE defer chain total (module fn, rooted)
    import tulip

    def _chain(_x):
        _fire()
        try:
            tulip.defer(_chain, 0, _TICK_MS)
        except Exception:
            _state['timer'] = None
    _state['timer'] = 'defer'
    try:
        tulip.defer(_chain, 0, _TICK_MS)
    except Exception:
        _state['timer'] = None
