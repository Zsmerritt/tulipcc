#!/usr/bin/env python3
"""flash_ota.py -- OTA-flash a fork firmware over the local network.

    python deck/flash_ota.py path/to/tulip-firmware-TULIP4_R11.bin
                             [--port COM11] [--http-port 8787]

If this machine and the Tulip can see each other on the LAN, this script:
  1. asks the device for its IP over the serial command channel (nothing in
     the device UI is involved -- deliberately host-driven);
  2. starts a TEMPORARY http server on this machine hosting the image;
  3. proves the DEVICE can reach the server (a ping fetch -- catches firewalls
     before any flashing starts);
  4. drives the device to stream the image straight into the inactive OTA
     partition: sha256 over the download, sha256 read back out of flash, and
     set_boot() only if both match the host's hash;
  5. reboots the device into the new firmware and tears the server down.

WiFi + TCP makes the transfer itself corruption-proof and ~100x faster than
the serial path (seconds vs ~20 minutes) -- serial carries only the small
command scripts. The on-device progress modal (fwprogress) tracks the
download. If there's no network path, it says so and points at flash_fw.py
(the chunked serial fallback).
"""

import hashlib
import http.server
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

import functools
print = functools.partial(print, flush=True)
from functools import partial

# Device-side updater. Placeholders (__BASE__/__SHA__/__SIZE__) are replaced
# textually -- no %-format, so the device code can use % freely.
DEVICE_OTA = """
import hashlib, binascii
from esp32 import Partition
import tuliprequests as ur
try:
    import fwprogress
    fwprogress.show(100, title='Firmware update (OTA)')
except Exception:
    fwprogress = None
SEC = 4096
SIZE = __SIZE__
ota = Partition(Partition.RUNNING).get_next_update()
assert SIZE <= ota.info()[3], 'image larger than partition'
r = ur.get('__BASE__/fw.bin')
h = hashlib.sha256()
block = 0
total = (SIZE + SEC - 1) // SEC
for chunk in r.generate(chunk_size=SEC):
    h.update(chunk)
    if len(chunk) < SEC:
        chunk = chunk + bytes(b'\\xff' * (SEC - len(chunk)))
    ota.writeblocks(block, chunk)
    block += 1
    if fwprogress and block % 32 == 0:
        try:
            fwprogress.update(int(96 * block / total))
        except Exception:
            pass
r.close()
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
assert binascii.hexlify(rh.digest()).decode() == '__SHA__', 'partition mismatch'
print('OTA:OK')
ota.set_boot()
print('OTA:BOOTSET')
if fwprogress:
    try:
        fwprogress.done()
    except Exception:
        pass
"""


def sh(port, args, timeout=300):
    cp = subprocess.run(
        [sys.executable, '-m', 'mpremote', 'connect', port, 'resume'] + args,
        capture_output=True, text=True, timeout=timeout)
    return (cp.stdout or '') + (cp.stderr or '')


def tagged(out, tag):
    """Last line starting with `tag` -- immune to console chatter."""
    val = None
    for line in (out or '').replace('\r', '').splitlines():
        if line.startswith(tag):
            val = line[len(tag):]
    return val


def main():
    args = sys.argv[1:]
    port, http_port = 'COM11', 8787
    if '--port' in args:
        i = args.index('--port')
        port = args[i + 1]
        del args[i:i + 2]
    if '--http-port' in args:
        i = args.index('--http-port')
        http_port = int(args[i + 1])
        del args[i:i + 2]
    if len(args) != 1:
        print(__doc__)
        return 2
    image = args[0]

    data = open(image, 'rb').read()
    sha = hashlib.sha256(data).hexdigest()
    print('image: %d bytes, sha256 %s' % (len(data), sha))

    # 1. can the device tell us its IP? Put the update modal up FIRST --
    # the prep phase (wifi check, ping) used to be invisible, so the player
    # had no idea an update was in motion and kept using the deck.
    out = sh(port, ['exec', (
        "import tulip\n"
        "try:\n"
        "    import fwprogress\n"
        "    fwprogress.show(100, title='Firmware update (OTA)')\n"
        "    fwprogress.stage('Preparing... do not touch the deck.')\n"
        "except Exception:\n"
        "    pass\n"
        "print('IP:' + str(tulip.ip()))")], 60)
    dev_ip = tagged(out, 'IP:')
    if not dev_ip or dev_ip == 'None':
        # drop the modal again -- nothing is going to happen
        sh(port, ['exec', ("try:\n"
                           "    import fwprogress\n"
                           "    fwprogress.fail('No Wi-Fi -- update not started.')\n"
                           "except Exception:\n"
                           "    pass")], 30)
        print('Device is not on Wi-Fi (tulip.ip() = %r).' % dev_ip)
        print('Connect it in Settings > Wi-Fi, or use the serial fallback:')
        print('  python deck/flash_fw.py %s --port %s' % (image, port))
        return 3
    print('device ip: %s' % dev_ip)

    # 2. our IP on the network route that reaches the device
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect((dev_ip, 1))
    host_ip = s.getsockname()[0]
    s.close()
    base = 'http://%s:%d' % (host_ip, http_port)
    print('serving from: %s' % base)

    # 3. temp server hosting the image
    tmp = tempfile.mkdtemp(prefix='tulip_ota_')
    try:
        shutil.copyfile(image, os.path.join(tmp, 'fw.bin'))
        with open(os.path.join(tmp, 'ping.txt'), 'w') as f:
            f.write('pong')
        handler = partial(http.server.SimpleHTTPRequestHandler, directory=tmp)
        httpd = http.server.ThreadingHTTPServer(('0.0.0.0', http_port), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        # 4. prove the DEVICE can reach us before flashing anything
        out = sh(port, ['exec', (
            "import tuliprequests as ur\n"
            "try:\n"
            "    r = ur.get('%s/ping.txt')\n"
            "    print('PING:' + r.text.strip())\n"
            "    r.close()\n"
            "except Exception as e:\n"
            "    print('PING:FAIL ' + repr(e))" % base)], 90)
        ping = tagged(out, 'PING:')
        if ping != 'pong':
            print('Device cannot reach this machine (%r).' % ping)
            print('Likely a firewall: allow python.exe inbound on port %d,'
                  % http_port)
            print('or check both ends are on the same network. Serial fallback:')
            print('  python deck/flash_fw.py %s --port %s' % (image, port))
            return 4
        print('device can reach the server; starting OTA pull')

        # 5. device pulls, writes, verifies, sets boot
        code = (DEVICE_OTA.replace('__BASE__', base)
                          .replace('__SHA__', sha)
                          .replace('__SIZE__', str(len(data))))
        out = sh(port, ['exec', code], timeout=900)
        if 'OTA:OK' not in out or 'OTA:BOOTSET' not in out:
            print('OTA FAILED:\n' + out)
            # a failed update must be VISIBLE on the device, not a quiet
            # return to Home (tap the notice to dismiss)
            sh(port, ['exec', 'import fwprogress; fwprogress.fail()'], 60)
            return 5
        print('flashed + verified (download sha and partition read-back'
              ' both match)')

        # 6. reboot into it
        sh(port, ['exec', '--no-follow', 'import machine; machine.reset()'], 30)
        print('DONE -- device rebooting into the new firmware')
        time.sleep(1)
        httpd.shutdown()
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
