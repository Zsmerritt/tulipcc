# decklog.py -- persistent deck logging, for diagnosing the "drops back to Home"
# glitch (and anything else intermittent).
#
# Every line is BOTH printed to the serial console (the connected computer's
# terminal captures it live, so it survives a device reboot on the host side) AND
# appended to /user/var/deck.log on the device (survives reboots on the device --
# read it back with tools/qget.py). The return-to-Home paths and
# panel-builder exceptions log here with a traceback, so the next occurrence is
# captured either way.

import tulip

_MAX = 40000        # cap the on-device log (bytes) so it can't fill flash


def _logfile():
    """Prefer /sd (its own peripheral: writes never race the flash cache or
    need the fence) when a card is mounted; else internal flash. On flash
    the log lives under /user/var: littlefs commits land on the parent
    directory's metadata pair, and for root files that is the UNRELOCATABLE
    superblock pair -- concentrating every log append there is what wore it
    into the 'Corrupted dir pair {0x1,0x0}' state (E-5)."""
    import os
    try:
        os.stat('/sd')
        return '/sd/deck.log'
    except OSError:
        pass
    try:
        os.mkdir('/user/var')
    except OSError:
        pass
    return '/user/var/deck.log'


_LOGFILE = _logfile()


def _ts():
    try:
        return tulip.amy_ticks_ms()
    except Exception:
        return 0


_PENDING = []       # buffered lines (serial copy always lands immediately)
_state = {'size': None,     # on-flash log size, tracked in RAM (no per-call
                            # os.stat -- that was a littlefs metadata walk
                            # PER LOG LINE, O-6)
          'pend': 0,        # buffered bytes
          'armed': False}   # a deferred flush is scheduled
_FLUSH_BYTES = 2048         # flush when this much is buffered...
_FLUSH_MS = 3000            # ...or this long after the first buffered line


def _write_pending():
    """One append + at most one rollover per FLUSH, not per line: batching
    turns N metadata-pair commits into 1 (O-6)."""
    import os
    if not _PENDING:
        return
    data = "\n".join(_PENDING) + "\n"
    del _PENDING[:]
    _state['pend'] = 0
    if _state['size'] is None:
        try:
            _state['size'] = os.stat(_LOGFILE)[6]
        except OSError:
            _state['size'] = 0
    if _state['size'] + len(data) > _MAX:
        # Roll over keeping ONE previous generation -- deleting the log
        # outright could throw away the lead-up to the very event being
        # diagnosed.
        try:
            os.remove(_LOGFILE + '.old')
        except OSError:
            pass
        try:
            os.rename(_LOGFILE, _LOGFILE + '.old')
        except OSError:
            pass
        _state['size'] = 0
    with open(_LOGFILE, 'a') as f:
        f.write(data)
    _state['size'] += len(data)


def flush(_=None):
    """Flush buffered lines to storage now. Deferred-timer target; also
    call before an intentional reboot so the tail isn't lost."""
    _state['armed'] = False
    try:
        if _LOGFILE.startswith('/sd'):
            _write_pending()  # SD is its own peripheral: no fence needed
        else:
            # A flash write while AMY renders PCM from the mmap'd banks
            # crashed the S3 (see deckcfg.fenced_write). Fence firmware
            # writes immediately; older firmware waits for quiet.
            import deckcfg
            if not deckcfg.fenced_write(_write_pending) \
                    and len(_PENDING) > 200:
                del _PENDING[:-200]
    except Exception:
        pass


def log(msg):
    line = "[%d] %s" % (_ts(), msg)
    try:
        print("DECKLOG " + line)          # serial -> host terminal
    except Exception:
        pass
    _PENDING.append(line)
    _state['pend'] += len(line) + 1
    if _state['pend'] >= _FLUSH_BYTES:
        flush()
    elif not _state['armed']:
        _state['armed'] = True
        try:
            tulip.defer(flush, 0, _FLUSH_MS)
        except Exception:
            flush()                        # no defer slot: flush inline


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
