#!/usr/bin/env python3
"""qput.py -- copy a local file onto the deck WITHOUT resetting it.

Same no-reset port discipline as qexec.py (DTR/RTS held LOW before open,
so the CH340 never power-cycles the deck), plus raw-paste mode: plain raw
REPL has NO flow control, and payloads beyond a few KB overran the UART
ring buffer and silently corrupted the written file. Raw-paste's window
handshake makes the device pace the transfer. The write lands on a temp
file, is sha256-verified on-device, and only then renamed over the target
inside the flash fence. Usage:

    python qput.py local_file /user/remote_name
"""
import base64
import hashlib
import os
import sys
import time

import serial

PORT = os.environ.get('DECK_PORT', 'COM11')


def open_port():
    s = serial.Serial()
    s.port = PORT
    s.baudrate = 115200
    s.timeout = 2
    s.dtr = False          # set BEFORE open: no reset pulse
    s.rts = False
    s.open()
    s.dtr = False
    s.rts = False
    return s


def read_until(s, marker, t=15):
    out = b''
    t0 = time.time()
    while time.time() - t0 < t:
        b = s.read(1)
        if b:
            out += b
            if out.endswith(marker):
                return out
    raise RuntimeError('timeout waiting for %r (got ...%r)'
                       % (marker, out[-80:]))


def exec_rawpaste(s, code):
    """Run `code` via raw-paste mode; returns (stdout, stderr) text."""
    s.write(b'\r\x03')
    time.sleep(0.15)
    s.reset_input_buffer()
    s.write(b'\x01')
    read_until(s, b'raw REPL; CTRL-B to exit\r\n>')
    s.write(b'\x05A\x01')
    hdr = read_until(s, b'R\x01', t=5)   # tolerate chatter before the ack
    win = int.from_bytes(s.read(2), 'little')
    if not win:
        raise RuntimeError('bad raw-paste window')
    if isinstance(code, str):
        code = code.encode()
    remaining = win
    i = 0
    while i < len(code):
        while remaining <= 0:
            c = s.read(1)
            if c == b'\x01':
                remaining += win
            elif c == b'\x04':
                s.write(b'\x04')
                raise RuntimeError('device aborted raw-paste input')
            elif c == b'':
                raise RuntimeError('flow-control timeout')
            # anything else is console chatter riding the same UART: skip
        n = min(remaining, len(code) - i)
        s.write(code[i:i + n])
        i += n
        remaining -= n
    s.write(b'\x04')
    read_until(s, b'\x04')               # end-of-data ack
    out = b''
    t0 = time.time()
    while time.time() - t0 < 120:
        chunk = s.read(4096)
        if chunk:
            out += chunk
            if out.endswith(b'\x04>'):
                break
        elif out.endswith(b'\x04>'):
            break
    s.write(b'\x02')                     # back to friendly REPL
    parts = out.split(b'\x04')
    stdout = parts[0].decode('utf-8', 'replace') if parts else ''
    stderr = parts[1].decode('utf-8', 'replace') if len(parts) > 1 else ''
    return stdout, stderr


def main():
    local, remote = sys.argv[1], sys.argv[2]
    if ':' in remote or not remote.startswith('/'):
        sys.exit("remote path %r looks shell-mangled (Git Bash MSYS path "
                 "conversion rewrites /user/... into C:/Program Files/...). "
                 "Run from PowerShell or with MSYS_NO_PATHCONV=1." % remote)
    data = open(local, 'rb').read()
    sha = hashlib.sha256(data).hexdigest()[:12]
    b64 = base64.b64encode(data).decode()
    # temp under /user/var, NOT the destination dir: a root-dir temp file
    # commits to littlefs's unrelocatable superblock pair on every deploy
    # (the exact wear pattern deckcfg E-5 exists to avoid)
    tmp = '/user/var/.qput_tmp'
    lines = ["import binascii, hashlib, os",
             "try:",
             "    os.mkdir('/user/var')",
             "except OSError:",
             "    pass",
             "b=''"]
    lines += ["b+='%s'" % b64[i:i + 512] for i in range(0, len(b64), 512)]
    lines += [
        "print('QPUT:b64len', len(b))",
        "d=binascii.a2b_base64(b)",
        "print('QPUT:decoded', len(d))",
        "import gc, time",
        "del b",
        "gc.collect()",
        "try:",
        "    import tulip",
        "    _fence = (not hasattr(tulip, 'flash_fence_auto')) and hasattr(tulip, 'flash_fence')",
        "except Exception:",
        "    tulip = None; _fence = False",
        "if _fence:",
        "    tulip.flash_fence(1); time.sleep_ms(12)",
        "for _a in range(6):",
        "    try:",
        "        f=open('%s','wb'); f.write(d); f.close()" % tmp,
        "        print('QPUT:written try', _a)",
        "        break",
        "    except OSError as ex:",
        "        print('QPUT:writeerr', _a, repr(ex))",
        "        time.sleep_ms(400)",
        "if _fence:",
        "    tulip.flash_fence(0)",
        "h=hashlib.sha256(open('%s','rb').read()).digest()" % tmp,
        "s=binascii.hexlify(h).decode()[:12]",
        "ok = (s=='%s')" % sha,
        "if ok:",
        "    try:",
        "        import tulip; tulip.flash_fence(1)",
        "    except Exception: pass",
        "    os.rename('%s','%s')" % (tmp, remote),
        "    try:",
        "        import tulip; tulip.flash_fence(0)",
        "    except Exception: pass",
        "print('QPUT:RESULT', s, ok)",
    ]
    s = open_port()
    try:
        stdout, stderr = exec_rawpaste(s, '\n'.join(lines))
    finally:
        s.close()
    for line in stdout.replace('\r', '').splitlines():
        if line.startswith('QPUT:RESULT'):
            if line.endswith('True'):
                print('%s -> %s  sha %s  OK (%d bytes)'
                      % (local, remote, sha, len(data)))
                return
            print('HASH MISMATCH: %s (local %s)' % (line, sha))
            sys.exit(1)
    sys.stderr.write('no QPUT result.\nSTDOUT:%s\nSTDERR:%s\n'
                     % (stdout, stderr))
    sys.exit(1)


if __name__ == '__main__':
    main()
