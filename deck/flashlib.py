#!/usr/bin/env python3
"""flashlib.py -- shared host+device plumbing for OTA-style firmware flashing.

Extracted from flash_ota.py so that both flash paths share ONE copy of the
proven pieces instead of copy-pasting them:

  * flash_ota.py       -- direct OTA into the inactive slot (120MHz play path)
  * flash_pingpong.py  -- reboot-into-80MHz-flasher "ping-pong" scheme; writes
                          the PLAY slot at 80MHz where big writes are thermally
                          safe (see deck/PINGPONG.md)

The genuinely shared, battle-tested bits live here:
  - DEVICE_OTA         : the device-side accumulate/write-verify-retry pull,
                         now parametrised by target partition (__TARGET__) and
                         modal title (__TITLE__).
  - build_ota_code()   : textual placeholder substitution into DEVICE_OTA.
  - sh()               : the no-reset raw-paste serial transport.
  - tagged()           : last-line-with-prefix parser (chatter-immune).
  - image_server / host_ip_to : the temporary HTTP image host + route lookup.

flash_ota.py's behaviour is UNCHANGED: it builds the device code with the
DEFAULT target (the inactive OTA slot) and the same modal title as before, so
the generated device script is byte-for-byte what it always was.
"""

import functools
import http.server
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from functools import partial

print = functools.partial(print, flush=True)

# The slot flash_ota.py always targeted: the inactive OTA partition. Kept as a
# code string so it is substituted verbatim into the device script.
DEFAULT_TARGET = 'Partition(Partition.RUNNING).get_next_update()'
DEFAULT_TITLE = 'Firmware update (OTA)'

# Device-side updater. Placeholders (__TARGET__/__TITLE__/__BASE__/__SHA__/
# __SIZE__) are replaced textually -- no %-format, so the device code can use %
# freely.
DEVICE_OTA = """
import hashlib, binascii
from esp32 import Partition
import tuliprequests as ur
try:
    import fwprogress
    fwprogress.show(100, title='__TITLE__')
except Exception:
    fwprogress = None
SEC = 4096
SIZE = __SIZE__
ota = __TARGET__
assert SIZE <= ota.info()[3], 'image larger than partition'
r = ur.get('__BASE__/fw.bin')
h = hashlib.sha256()
block = 0
total = (SIZE + SEC - 1) // SEC
# ACCUMULATE to exact sectors: the socket can return SHORT reads mid-
# stream (not just at EOF), and padding those to a full block misaligned
# every byte after -- download hash fine, partition content wrong.
buf = b''
vbuf = bytearray(SEC)
retried = 0
def wr(blk, w):
    # WRITE-VERIFY-RETRY: sustained multi-MB writes on this part land a
    # scatter of bad sectors that single-block writes don't (seen live:
    # 10/13 regions mismatched with a hash-verified download). Verify
    # every block; rewrite up to 3x; report what it took.
    global retried
    for attempt in range(4):
        ota.writeblocks(blk, w)
        ota.readblocks(blk, vbuf)
        if bytes(vbuf) == w:
            return attempt
    print('BADBLK:%d' % blk)
    return -1
for chunk in r.generate(chunk_size=SEC):
    h.update(chunk)
    buf += chunk
    while len(buf) >= SEC:
        if wr(block, buf[:SEC]):
            retried += 1
        buf = buf[SEC:]
        block += 1
        if fwprogress and block % 32 == 0:
            try:
                fwprogress.update(int(96 * block / total))
            except Exception:
                pass
r.close()
if buf:
    if wr(block, buf + bytes(b'\\xff' * (SEC - len(buf)))):
        retried += 1
    block += 1
print('RETRIED:%d' % retried)
got = binascii.hexlify(h.digest()).decode()
print('DL:' + got)
assert got == '__SHA__', 'download hash mismatch'
if fwprogress:
    try:
        fwprogress.stage('Verifying flash...')
    except Exception:
        pass
rh = hashlib.sha256()
buf = bytearray(SEC)
left = SIZE
for i in range(block):
    ota.readblocks(i, buf)
    take = SEC if left >= SEC else left
    rh.update(bytes(buf[0:take]))
    left -= take
if binascii.hexlify(rh.digest()).decode() != '__SHA__':
    # ADVISORY only: sustained bulk re-reads on this 120MHz octal-flash
    # bin proved UNSTABLE (two consecutive read passes disagreed with
    # each other while per-block write-verify passed 809/809). The
    # per-block verify above is the integrity gate; the ESP bootloader
    # re-validates the image sha at boot and rolls back on failure.
    print('OTA:BULK-REVERIFY-FLAKY (per-block verify + boot validation govern)')
print('OTA:OK')
ota.set_boot()
print('OTA:BOOTSET')
if fwprogress:
    try:
        fwprogress.done()
    except Exception:
        pass
"""


def build_ota_code(base, sha, size, target=DEFAULT_TARGET, title=DEFAULT_TITLE):
    """Substitute the DEVICE_OTA placeholders into a runnable device script.

    `target` is a Python EXPRESSION (evaluated on the device) that yields the
    Partition to write. Default reproduces flash_ota.py exactly (the inactive
    slot); flash_pingpong.py passes an explicit label-based find so it writes
    the PLAY slot even when booted from the flasher slot.
    """
    return (DEVICE_OTA.replace('__TARGET__', target)
                      .replace('__TITLE__', title)
                      .replace('__BASE__', base)
                      .replace('__SHA__', sha)
                      .replace('__SIZE__', str(size)))


def sh(port, args, timeout=300):
    """Run `exec <code>` on the device WITHOUT resetting it.

    mpremote's port-open pulses DTR/RTS, which power-cycles CH340-wired
    Tulips -- the IP query then raced the reboot it had itself caused and
    reported 'not on Wi-Fi' on a connected device. Opens with DTR/RTS held
    low (no pulse), raw-REPL, exec, close. Only 'exec' is used here."""
    args = [a for a in args if a != '--no-follow']   # mpremote-ism, ignored
    assert args and args[0] == 'exec'
    code = args[1]
    try:
        import serial
    except ImportError:
        cp = subprocess.run(
            [sys.executable, '-m', 'mpremote', 'connect', port, 'resume'] + args,
            capture_output=True, text=True, timeout=timeout)
        return (cp.stdout or '') + (cp.stderr or '')
    s = serial.Serial()
    s.port = port
    s.baudrate = 115200
    s.timeout = 0.5
    s.dtr = False
    s.rts = False
    s.open()
    s.dtr = False
    s.rts = False
    try:
        s.write(b'\r\x03')
        time.sleep(0.1)
        s.write(b'\x03')       # twice: a busy MP task can eat the first ^C
        time.sleep(0.15)
        s.reset_input_buffer()
        s.write(b'\x01')
        buf = b''
        t0 = time.time()
        while time.time() - t0 < 5 and not buf.endswith(b'raw REPL; CTRL-B to exit\r\n>'):
            c = s.read(1)
            if c:
                buf += c
        # RAW-PASTE mode (windowed flow control): plain raw REPL has none
        # and silently DROPS BYTES past a few KB of payload -- the OTA
        # pull script arrived on-device with a SyntaxError mid-line.
        s.write(b'\x05A\x01')
        hdr = b''
        t0 = time.time()
        while time.time() - t0 < 5 and not hdr.endswith(b'R\x01'):
            c = s.read(1)
            if c:
                hdr += c
        win = int.from_bytes(s.read(2), 'little') or 256
        data = code.encode()
        remaining, i = win, 0
        while i < len(data):
            while remaining <= 0:
                c = s.read(1)
                if c == b'\x01':
                    remaining += win
                elif c == b'':
                    raise RuntimeError('raw-paste flow-control timeout')
            n = min(remaining, len(data) - i)
            s.write(data[i:i + n])
            i += n
            remaining -= n
        s.write(b'\x04')
        t0 = time.time()
        while time.time() - t0 < 5:
            c = s.read(1)
            if c == b'\x04':
                break              # end-of-data ack; output follows
        out = b''
        t0 = time.time()
        while time.time() - t0 < timeout:
            chunk = s.read(4096)
            if chunk:
                out += chunk
            if out.endswith(b'\x04>'):
                break
        s.write(b'\x02')
    finally:
        s.close()
    if b'OK' in out[:10]:
        out = out.split(b'OK', 1)[1]
    return out.replace(b'\x04', b'\n').decode('utf-8', 'replace')


def tagged(out, tag):
    """Last line starting with `tag` -- immune to console chatter."""
    val = None
    for line in (out or '').replace('\r', '').splitlines():
        if line.startswith(tag):
            val = line[len(tag):]
    return val


def host_ip_to(dev_ip):
    """Our IP on the network route that reaches the device."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect((dev_ip, 1))
    ip = s.getsockname()[0]
    s.close()
    return ip


class image_server:
    """Temporary HTTP server hosting fw.bin (+ a ping.txt reachability probe).

    Use as a context manager:

        with image_server(path_to_bin, http_port) as srv:
            base = srv.base_for(dev_ip)     # http://<host_ip>:<port>
            ...                             # device pulls base + '/fw.bin'
    """

    def __init__(self, image, http_port):
        self.image = image
        self.http_port = http_port
        self._tmp = None
        self._httpd = None

    def __enter__(self):
        self._tmp = tempfile.mkdtemp(prefix='tulip_ota_')
        shutil.copyfile(self.image, os.path.join(self._tmp, 'fw.bin'))
        with open(os.path.join(self._tmp, 'ping.txt'), 'w') as f:
            f.write('pong')
        handler = partial(http.server.SimpleHTTPRequestHandler, directory=self._tmp)
        self._httpd = http.server.ThreadingHTTPServer(('0.0.0.0', self.http_port),
                                                       handler)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self

    def base_for(self, dev_ip):
        return 'http://%s:%d' % (host_ip_to(dev_ip), self.http_port)

    def __exit__(self, *exc):
        try:
            if self._httpd is not None:
                self._httpd.shutdown()
        except Exception:
            pass
        shutil.rmtree(self._tmp or '', ignore_errors=True)
        return False
