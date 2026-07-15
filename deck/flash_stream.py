#!/usr/bin/env python3
"""flash_stream.py -- serial firmware flash over a NOISE-IMMUNE framed protocol.

    python deck/flash_stream.py path/to/tulip-firmware-TULIP4_R11.bin [--port COM11]

Both directions are FRAMED and PREFIX-FILTERED, so console chatter (AMY
warnings, decklog, anything) cannot corrupt the transfer in either direction:

  host -> device :  FWD:<seq>:<base64 payload>:<sha256[:8] of payload>\\n
                    FWE:<total sha256>\\n            (end of stream)
  device -> host :  FWA:<seq>:OK | FWA:<seq>:BAD    (per-frame ack, lockstep)
                    FWR:<result lines>              (final verdict)

The device-side receiver (launched once over mpremote, then the port is
handed to a raw pyserial session) ignores every stdin line that doesn't
start with FWD:/FWE:, and the host ignores every serial line that doesn't
start with FWA:/FWR:. Lockstep acks mean a bad frame is resent immediately;
nothing depends on mpremote's REPL protocol during the data phase. After the
stream verifies, the standard OTA write (full partition read-back) runs via
mpremote as usual. The deck's progress modal is driven from the DEVICE side,
one update per ~32 frames.
"""

import base64
import functools
import hashlib
import subprocess
import sys
import time

print = functools.partial(print, flush=True)

FRAME_RAW = 3072          # payload bytes per frame (b64 -> ~4.1KB line)
ACK_TIMEOUT = 10          # seconds to wait for a frame's ack
FRAME_TRIES = 5

RECEIVER = r'''
import sys, binascii, hashlib, os
try:
    import fwprogress
except Exception:
    fwprogress = None
f = open('/user/fw_upgrade.bin', 'wb')
h = hashlib.sha256()
expect = 0
frames = 0
print('FWR:READY')
while True:
    line = sys.stdin.readline()
    if not line:
        continue
    line = line.strip()
    if line.startswith('FWQ:'):
        # handshake ping: answer as often as asked (the one-shot READY print
        # raced the host's port-open and was lost)
        print('FWR:READY')
        continue
    if line.startswith('FWE:'):
        f.close()
        want = line[4:]
        got = binascii.hexlify(h.digest()).decode()
        print('FWR:' + ('DONE' if got == want else 'HASHFAIL:' + got))
        break
    if not line.startswith('FWD:'):
        continue                    # not for us: ignore (chatter, echoes)
    try:
        _, seq_s, payload, check = line.split(':')
        seq = int(seq_s)
        raw = binascii.a2b_base64(payload)
        ok = (binascii.hexlify(hashlib.sha256(raw).digest()).decode()[:8]
              == check) and seq == expect
    except Exception:
        ok = False
        seq = expect
    if ok:
        f.write(raw)
        h.update(raw)
        expect += 1
        frames += 1
        if fwprogress and frames % 32 == 0:
            try:
                fwprogress.update(frames)
            except Exception:
                pass
        print('FWA:%d:OK' % seq)
    else:
        print('FWA:%d:BAD' % seq)
if fwprogress:
    try:
        fwprogress.stage('Verifying and writing to flash...')
    except Exception:
        pass
'''


def sh(port, args, timeout=300):
    cp = subprocess.run(
        [sys.executable, '-m', 'mpremote', 'connect', port, 'resume'] + args,
        capture_output=True, text=True, timeout=timeout)
    return (cp.stdout or '') + (cp.stderr or '')


def read_tagged(ser, tags, deadline):
    """Next serial line starting with one of `tags`; everything else ignored."""
    buf = b''
    while time.time() < deadline:
        b = ser.read(1)
        if not b:
            continue
        if b == b'\n':
            line = buf.decode('utf-8', 'ignore').strip()
            buf = b''
            for t in tags:
                if line.startswith(t):
                    return line
            continue
        buf += b
        if len(buf) > 512:
            buf = b''
    return None


def main():
    import serial                      # pyserial (mpremote dependency)
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
    data = open(image, 'rb').read()
    total_sha = hashlib.sha256(data).hexdigest()
    frames = [data[i:i + FRAME_RAW] for i in range(0, len(data), FRAME_RAW)]
    print('image: %d bytes, %d frames, sha256 %s'
          % (len(data), len(frames), total_sha))

    # progress modal sized in frames; then launch the receiver and release
    # the port for the raw serial session
    sh(port, ['exec',
              "import fwprogress; fwprogress.show(%d)" % len(frames)], 60)
    sh(port, ['exec', '--no-follow', RECEIVER], 60)
    time.sleep(1)

    # dtr/rts LOW before open: default-asserted lines can reset ESP boards
    # wired for auto-reset (would kill the receiver we just launched)
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = 115200
    ser.timeout = 0.2
    ser.dtr = False
    ser.rts = False
    ser.open()
    try:
        line = None
        for _ in range(10):                  # ping until the receiver answers
            ser.write(b'FWQ:\n')
            line = read_tagged(ser, ('FWR:',), time.time() + 2)
            if line == 'FWR:READY':
                break
        if line != 'FWR:READY':
            print('receiver did not come up (%r)' % line)
            return 3
        print('receiver ready; streaming')
        t0 = time.time()
        for seq, raw in enumerate(frames):
            payload = base64.b64encode(raw).decode()
            check = hashlib.sha256(raw).hexdigest()[:8]
            frame = ('FWD:%d:%s:%s\n' % (seq, payload, check)).encode()
            sent = False
            for attempt in range(1, FRAME_TRIES + 1):
                ser.write(frame)
                ack = read_tagged(ser, ('FWA:',), time.time() + ACK_TIMEOUT)
                if ack == 'FWA:%d:OK' % seq:
                    sent = True
                    break
                print('frame %d attempt %d: %r' % (seq, attempt, ack))
            if not sent:
                print('FAILED: frame %d never acked' % seq)
                ser.close()
                sh(port, ['exec', 'import fwprogress; fwprogress.fail()'], 60)
                return 4
            if seq and seq % 128 == 0:
                rate = (seq * FRAME_RAW) / max(1, time.time() - t0) / 1024
                print('%d/%d frames (%.1f KB/s)' % (seq, len(frames), rate))
        ser.write(('FWE:%s\n' % total_sha).encode())
        verdict = read_tagged(ser, ('FWR:',), time.time() + 30)
        print('stream verdict: %r (%.1f min)'
              % (verdict, (time.time() - t0) / 60))
        # hand the REPL back cleanly WITHIN this session (exit raw REPL) so
        # the next mpremote attach works; re-opening the port to do it can
        # pulse DTR/RTS and power-cycle the board
        time.sleep(0.3)
        ser.write(b'\x02')
        time.sleep(0.3)
        if verdict != 'FWR:DONE':
            sh(port, ['exec', 'import fwprogress; fwprogress.fail()'], 60)
            return 5
    finally:
        try:
            ser.close()
        except Exception:
            pass

    # standard OTA write phase (tag-verified, full partition read-back)
    print('writing to OTA partition (full read-back verify)...')
    out = sh(port, ['exec', """
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
    print('OTA:BOOTSET')"""], timeout=600)
    if 'OTA:OK' not in out or 'OTA:BOOTSET' not in out:
        print('FAILED: OTA write/verify\n' + out)
        sh(port, ['exec', 'import fwprogress; fwprogress.fail()'], 60)
        return 6
    print('flashed + verified; rebooting device')
    sh(port, ['exec', 'import fwprogress; fwprogress.done()'], 60)
    sh(port, ['exec', '--no-follow', 'import machine; machine.reset()'], 30)
    print('DONE -- device rebooting into the new firmware')
    return 0


if __name__ == '__main__':
    sys.exit(main())
