# decklog.py -- persistent deck logging, for diagnosing the "drops back to Home"
# glitch (and anything else intermittent).
#
# Every line is BOTH printed to the serial console (the connected computer's
# terminal captures it live, so it survives a device reboot on the host side) AND
# appended to /user/deck.log on the device (survives reboots on the device -- read
# it back with `mpremote fs cat :/user/deck.log`). The return-to-Home paths and
# panel-builder exceptions log here with a traceback, so the next occurrence is
# captured either way.

import tulip

_LOGFILE = '/user/deck.log'
_MAX = 40000        # cap the on-device log (bytes) so it can't fill flash


def _ts():
    try:
        return tulip.amy_ticks_ms()
    except Exception:
        return 0


_PENDING = []       # lines waiting for a quiet moment to hit flash


def log(msg):
    line = "[%d] %s" % (_ts(), msg)
    try:
        print("DECKLOG " + line)          # serial -> host terminal
    except Exception:
        pass
    # A flash write while AMY renders PCM from the mmap'd banks crashes the
    # S3 (see deckcfg.quiet_now). Queue lines while sound may be rendering;
    # flush the queue on the next quiet log call (the serial copy above
    # always lands immediately, so nothing is lost for live debugging).
    _PENDING.append(line)
    try:
        import deckcfg
        if not deckcfg.quiet_now():
            if len(_PENDING) > 200:
                del _PENDING[:-200]
            return
    except Exception:
        pass
    try:
        import os
        try:
            if os.stat(_LOGFILE)[6] > _MAX:
                # Roll over keeping ONE previous generation -- deleting the log
                # outright could throw away the lead-up to the very event being
                # diagnosed.
                try:
                    os.remove(_LOGFILE + '.old')
                except OSError:
                    pass
                os.rename(_LOGFILE, _LOGFILE + '.old')
        except OSError:
            pass
        with open(_LOGFILE, 'a') as f:
            f.write("\n".join(_PENDING) + "\n")
        del _PENDING[:]
    except Exception:
        pass


_DBG = None    # tri-state: None = read config lazily on first dbg()


def debug_on():
    global _DBG
    if _DBG is None:
        try:
            import deckcfg
            _DBG = bool(deckcfg.load().get('debug'))
        except Exception:
            _DBG = False
    return _DBG


def set_debug(on):
    """Settings toggle hook -- flips verbose logging without a reboot."""
    global _DBG
    _DBG = bool(on)
    log("debug mode %s" % ("ON" if _DBG else "off"))


def dbg(msg):
    """Verbose line: only logged while debug mode (Settings) is on."""
    if debug_on():
        log(msg)


def log_exc(msg, e=None):
    log(msg + ((": " + repr(e)) if e is not None else ""))
    if e is not None:
        try:
            import sys
            sys.print_exception(e)         # full traceback -> serial
        except Exception:
            pass
