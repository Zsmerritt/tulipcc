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


def log(msg):
    line = "[%d] %s" % (_ts(), msg)
    try:
        print("DECKLOG " + line)          # serial -> host terminal
    except Exception:
        pass
    try:
        import os
        try:
            if os.stat(_LOGFILE)[6] > _MAX:
                os.remove(_LOGFILE)        # simple roll-over
        except OSError:
            pass
        with open(_LOGFILE, 'a') as f:
            f.write(line + "\n")
    except Exception:
        pass


def log_exc(msg, e=None):
    log(msg + ((": " + repr(e)) if e is not None else ""))
    if e is not None:
        try:
            import sys
            sys.print_exception(e)         # full traceback -> serial
        except Exception:
            pass
