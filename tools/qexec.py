#!/usr/bin/env python3
"""qexec.py -- exec MicroPython on the deck WITHOUT resetting it.

mpremote's port-open pulses DTR/RTS, which power-cycles the CH340-wired
deck and races its ~35s boot (the all-night plague). This opens COM11 with
DTR/RTS held LOW from the start (no pulse), enters raw REPL, runs the
script, returns output. Usage:

    python qexec.py script.py           # run a file
    python qexec.py - "print(1+1)"      # run an inline snippet
    python qexec.py script.py TAG:      # only lines starting TAG: reach
                                        # stdout; the rest (chatter, boot
                                        # noise) goes to stderr

The optional TAG filter is the serial-protocol convention (see
deck/SERIAL-PROTOCOL.md): scripts print their meaningful results with a
unique prepended type, callers listen only for that type, and console
chatter can never corrupt a decision.
"""
import os
import sys
import time

import serial

PORT = os.environ.get('DECK_PORT', 'COM11')


def open_port():
    s = serial.Serial()
    s.port = PORT
    s.baudrate = 115200
    s.timeout = 0.5
    s.dtr = False          # set BEFORE open: no reset pulse
    s.rts = False
    s.open()
    s.dtr = False
    s.rts = False
    return s


def exec_code(s, code):
    # TWO interrupts, spaced: a busy MP task (mid-GC, mid-panel-build) can
    # eat the first ^C -- mpremote does the same for the same reason
    s.write(b'\r\x03')
    time.sleep(0.1)
    s.write(b'\x03')
    time.sleep(0.15)
    s.reset_input_buffer()
    s.write(b'\x01')              # raw REPL
    time.sleep(0.25)
    s.reset_input_buffer()
    if isinstance(code, str):
        code = code.encode()
    # chunked write so the UART fifo keeps up
    for i in range(0, len(code), 256):
        s.write(code[i:i + 256])
        time.sleep(0.01)
    s.write(b'\x04')              # execute
    out = b''
    t0 = time.time()
    while time.time() - t0 < 180:
        chunk = s.read(4096)
        if chunk:
            out += chunk
            if out.endswith(b'\x04>'):
                break
        elif out.endswith(b'\x04>'):
            break
    s.write(b'\x02')              # back to friendly REPL for the UI's sake
    return out


def main():
    if sys.argv[1] == '-':
        code = sys.argv[2]
        tag = sys.argv[3] if len(sys.argv) > 3 else None
    else:
        code = open(sys.argv[1], 'rb').read()
        tag = sys.argv[2] if len(sys.argv) > 2 else None
    s = open_port()
    try:
        out = exec_code(s, code)
    finally:
        s.close()
    # raw-repl framing: b'OK' + stdout + \x04 + stderr + \x04 + '>'
    body = out
    if b'OK' in body[:10]:
        body = body.split(b'OK', 1)[1]
    parts = body.split(b'\x04')
    stdout = parts[0] if parts else b''
    stderr = parts[1] if len(parts) > 1 else b''
    text = stdout.decode('utf-8', 'replace')
    if tag:
        # tagged-listener mode: only exactly-typed lines are results;
        # everything else on the shared console is chatter -> stderr
        for line in text.replace('\r', '').splitlines():
            if line.startswith(tag):
                sys.stdout.write(line + '\n')
            elif line.strip():
                sys.stderr.write(line + '\n')
    else:
        sys.stdout.write(text)
    if stderr.strip():
        sys.stderr.write('\n[device stderr] '
                         + stderr.decode('utf-8', 'replace'))
        sys.exit(1)


if __name__ == '__main__':
    main()
