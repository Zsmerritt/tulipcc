# logtail.py -- pure tail-reading logic behind the Debug > Logs screen.
#
# Split out from logs.py (the LVGL panel) the same way shellmodel.py is
# split from homeshell.py: this module only touches `os` + plain file I/O
# (identical surface on MicroPython and CPython), so it unit-tests under
# plain CPython with a real temp file -- no lvgl/tulip mocking needed.

DEFAULT_TAIL_BYTES = 8192   # generous for ~40 lines of decklog's
                            # "[ticks] message" format; still far short of
                            # decklog._MAX (40000), so a refresh tick never
                            # reads the whole on-device log into RAM.


def read_tail_bytes(path, max_bytes=DEFAULT_TAIL_BYTES, open_fn=open):
    """Read up to the last `max_bytes` of `path` WITHOUT loading the whole
    file: seek near the end first. Returns (data, truncated) -- data is b''
    if the file doesn't exist (a fresh deck with no log yet is not an
    error); `truncated` tells tail_lines() whether the read started
    mid-line (so it knows to drop the leading fragment)."""
    import os
    try:
        size = os.stat(path)[6]
    except OSError:
        return b'', False
    truncated = size > max_bytes
    try:
        with open_fn(path, 'rb') as f:
            if truncated:
                f.seek(size - max_bytes)
            return f.read(), truncated
    except OSError:
        return b'', False


def _decode(data):
    """utf-8, falling back to a byte-for-byte ASCII-safe substitution --
    MicroPython's bytes.decode() doesn't accept an errors= argument, so this
    can't rely on decode('utf-8', 'replace')."""
    try:
        return data.decode('utf-8')
    except Exception:
        return ''.join(chr(b) if b < 128 else '?' for b in data)


def tail_lines(data, n=40, truncated=True):
    """(data, truncated) from read_tail_bytes() -> up to the last `n`
    complete lines, oldest first / newest last (decklog.py appends
    chronologically, so this needs no reordering). When `truncated` is
    True the read started mid-line (we seeked past the start of the file),
    so the leading fragment is dropped rather than shown as a cut-off
    line; when False (short file, read from byte 0) every line is kept."""
    if not data:
        return []
    text = _decode(data) if isinstance(data, bytes) else data
    lines = text.split('\n')
    if truncated and len(lines) > 1:
        lines = lines[1:]            # drop the partial leading fragment
    lines = [l for l in lines if l]  # drop blank lines (trailing newline)
    return lines[-n:]
