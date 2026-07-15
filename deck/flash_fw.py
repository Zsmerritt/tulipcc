#!/usr/bin/env python3
"""flash_fw.py -- flash a fork-built firmware image over the serial link.

    python deck/flash_fw.py path/to/tulip-firmware-TULIP4_R11.bin [--port COM11]

Everything this pipeline learned the hard way, consolidated:
  * the image is sent in 100 KB chunks, each sha256-verified ON-DEVICE against
    the host hash (tagged replies -- 'HASH:<hex>' -- because async device
    printf can pollute the exec output), with retries per chunk;
  * assembly on-device is verified (size + full hash) BEFORE the chunks are
    deleted;
  * the OTA write reads every byte back out of the flash partition and
    compares hashes before set_boot();
  * the deck shows a progress screen (fwprogress.py) with a bar + percentage
    while it runs.

Wants the device running the deck (fwprogress deployed). ~20 minutes for a
3.2 MB image at this link's throughput.
"""

import hashlib
import os
import subprocess
import sys
import time

import functools
print = functools.partial(print, flush=True)

CHUNK = 102400
TRIES = 5


def sh(dev_args, timeout=300):
    cp = subprocess.run([sys.executable, '-m', 'mpremote'] + dev_args,
                        capture_output=True, text=True, timeout=timeout)
    return cp.returncode, (cp.stdout or '') + (cp.stderr or '')


def main():
    args = sys.argv[1:]
    port = 'COM11'
    if '--port' in args:
        i = args.index('--port')
        port = args[i + 1]
        del args[i:i + 2]
    if len(args) != 1:
        print(__doc__)
        return 2
    image = args[0]
    base = ['connect', port, 'resume']

    data = open(image, 'rb').read()
    total_hash = hashlib.sha256(data).hexdigest()
    chunks = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)]
    n = len(chunks)
    print('image: %d bytes, %d chunks, sha256 %s' % (len(data), n, total_hash))

    def dev(code, timeout=300):
        rc, out = sh(base + ['exec', code], timeout)
        return out

    def dev_hash(name):
        out = dev("""
import hashlib, binascii
try:
    h = hashlib.sha256()
    f = open('/user/%s','rb')
    while True:
        b = f.read(16384)
        if not b: break
        h.update(b)
    f.close()
    print('HASH:' + binascii.hexlify(h.digest()).decode())
except OSError:
    print('HASH:missing')""" % name)
        for line in out.replace('\r', '').splitlines():
            if line.startswith('HASH:'):
                return line[5:]
        return 'noreply'

    tmp = '_fwchunk.bin'
    dev("import fwprogress; fwprogress.show(%d)" % n, 60)
    dev("""
import os
for f in os.listdir('/user'):
    if f.startswith('fwc_') or f == 'fw_upgrade.bin':
        os.remove('/user/' + f)
print('cleaned')""")

    for i, blob in enumerate(chunks):
        name = 'fwc_%03d' % i
        want = hashlib.sha256(blob).hexdigest()
        if dev_hash(name) == want:            # resume support
            print('%s already ok' % name)
        else:
            ok = False
            for attempt in range(1, TRIES + 1):
                open(tmp, 'wb').write(blob)
                sh(base + ['fs', 'cp', tmp, ':/user/' + name])
                time.sleep(1)
                if dev_hash(name) == want:
                    print('%s ok (attempt %d)' % (name, attempt))
                    ok = True
                    break
                print('%s mismatch (attempt %d)' % (name, attempt))
                time.sleep(2)
            if not ok:
                print('FAILED: %s did not verify' % name)
                dev("import fwprogress; fwprogress.fail()", 60)
                return 3
        dev("import fwprogress; fwprogress.update(%d, %d)" % (i + 1, n), 60)
    try:
        os.remove(tmp)
    except OSError:
        pass

    print('assembling...')
    dev("import fwprogress; fwprogress.stage('Verifying assembled image...')", 60)
    dev("""
import os
names = sorted(f for f in os.listdir('/user') if f.startswith('fwc_'))
out = open('/user/fw_upgrade.bin', 'wb')
for nme in names:
    f = open('/user/' + nme, 'rb')
    while True:
        b = f.read(16384)
        if not b: break
        out.write(b)
    f.close()
out.close()
print('assembled', os.stat('/user/fw_upgrade.bin')[6])""")
    got = dev_hash('fw_upgrade.bin')
    if got != total_hash:
        print('FAILED: assembled hash %s != %s' % (got, total_hash))
        dev("import fwprogress; fwprogress.fail()", 60)
        return 4
    print('assembled image verified')
    dev("""
import os
for f in os.listdir('/user'):
    if f.startswith('fwc_'):
        os.remove('/user/' + f)
print('chunks cleaned')""")

    print('writing to OTA partition (full read-back verify)...')
    dev("import fwprogress; fwprogress.stage('Writing to flash (do not power off)...')", 60)
    out = dev("""
import os, hashlib, binascii
from esp32 import Partition
SRC = '/user/fw_upgrade.bin'
SEC = 4096
ota = Partition(Partition.RUNNING).get_next_update()
size = os.stat(SRC)[6]
assert size <= ota.info()[3], 'image larger than partition'
fh = hashlib.sha256()
f = open(SRC, 'rb')
block = 0
while True:
    chunk = f.read(SEC)
    if not chunk: break
    fh.update(chunk)
    if len(chunk) < SEC:
        chunk = chunk + bytes(b'\\xff' * (SEC - len(chunk)))
    ota.writeblocks(block, chunk)
    block += 1
f.close()
rh = hashlib.sha256()
buf = bytearray(SEC)
left = size
for i in range(block):
    ota.readblocks(i, buf)
    take = SEC if left >= SEC else left
    rh.update(bytes(buf[0:take]))
    left -= take
a = binascii.hexlify(fh.digest()).decode()
b = binascii.hexlify(rh.digest()).decode()
print('OTA:' + ('OK' if a == b else 'MISMATCH'))
if a == b:
    ota.set_boot()
    os.remove(SRC)
    print('OTA:BOOTSET')""", timeout=600)
    if 'OTA:OK' not in out or 'OTA:BOOTSET' not in out:
        print('FAILED: OTA write/verify\n' + out)
        dev("import fwprogress; fwprogress.fail()", 60)
        return 5

    print('flashed + verified; rebooting device')
    dev("import fwprogress; fwprogress.done()", 60)
    time.sleep(1)
    sh(base + ['exec', '--no-follow', 'import machine; machine.reset()'], 30)
    print('DONE -- device rebooting into the new firmware')
    return 0


if __name__ == '__main__':
    sys.exit(main())
