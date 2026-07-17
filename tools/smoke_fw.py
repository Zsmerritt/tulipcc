#!/usr/bin/env python3
"""smoke_fw.py -- flash a candidate firmware image and prove the deck boots.

CI only ever COMPILES the ESP firmware; nothing runs it. A CI-green image
bricked the deck (MicroPython never started: teal screen, no REPL, no
panic) and nothing caught it, because "it linked" is not "it booted". This
is the missing gate: flash the candidate into the OTA slot the deck is NOT
running from, point otadata at it, reset, and read the boot log back. If
the deck does not reach a live `>>>`, otadata is pointed back at the known
good slot and the deck is re-booted and re-checked, so a bad image can
never leave the bench dead.

    python smoke_fw.py --app build/tulip.bin
    python smoke_fw.py --app tulip.bin --sys tulip-sys.bin --timeout 40
    python smoke_fw.py --app tulip.bin --keep      # leave a failure flashed

Exits 0 on PASS, 1 on FAIL.
"""
import argparse
import os
import struct
import subprocess
import sys
import tempfile
import time
import zlib

import serial

PORT = os.environ.get('DECK_PORT', 'COM11')

OTADATA_OFF = 0xd000
OTADATA_SIZE = 0x2000          # two 4K sectors: 0xd000 and 0xe000
SECTOR = 0x1000
OTA_OFF = (0x10000, 0x3a0000)  # ota_0, ota_1
OTA_SIZE = 0x390000
SYS_OFF = 0x730000
SYS_SIZE = 0x300000

ESP_OTA_IMG_VALID = 2
SEQ_INVALID = 0xffffffff

BANNER = 'TulipCC with ESP32S3'   # NOT bare "MicroPython": the C log line
PROMPT = '>>>'                    # "Starting MicroPython on core 1" prints
FATAL = ('Guru Meditation', 'FATAL:', 'alloc failed')  # on healthy boots too


# ---------------------------------------------------------------- esptool

class Esp:
    """esptool driver that tracks whether the deck is parked in the ROM
    bootloader. --before no_reset ONLY works if it already is; from a
    running deck it dies with "No serial data received"."""

    def __init__(self, port, baud):
        self.port = port
        self.baud = baud
        self.in_bootloader = False

    def run(self, args, leave_in_bootloader=True, expect_hash=False):
        before = 'no_reset' if self.in_bootloader else 'default_reset'
        after = 'no_reset' if leave_in_bootloader else 'hard_reset'
        cmd = [sys.executable, '-m', 'esptool', '--chip', 'esp32s3',
               '--port', self.port, '--baud', str(self.baud),
               '--before', before, '--after', after] + args
        print('[esptool] ' + ' '.join(cmd[2:]))
        self.in_bootloader = False   # pessimistic until the call succeeds
        p = subprocess.run(cmd, capture_output=True, text=True)
        out = (p.stdout or '') + (p.stderr or '')
        if p.returncode != 0:
            sys.stdout.write(out)
            raise RuntimeError('esptool failed (%s): %s'
                               % (p.returncode, ' '.join(args)))
        if expect_hash and 'Hash of data verified' not in out:
            sys.stdout.write(out)
            raise RuntimeError('esptool did not report "Hash of data '
                               'verified" for: %s' % ' '.join(args))
        self.in_bootloader = leave_in_bootloader
        return out

    def read_flash(self, off, size, path):
        self.run(['read_flash', hex(off), hex(size), path])
        return open(path, 'rb').read()

    def write_flash(self, off, path):
        self.run(['write_flash', hex(off), path], expect_hash=True)


# ---------------------------------------------------------------- otadata

def entry_crc(seq):
    return zlib.crc32(struct.pack('<I', seq), 0xffffffff) & 0xffffffff


def decode_otadata(blob):
    """-> [(seq, state, valid), (seq, state, valid)] for sectors 0 and 1."""
    out = []
    for i in range(2):
        seq, _pad, state, crc = struct.unpack('<I20sII',
                                              blob[i * SECTOR:i * SECTOR + 32])
        valid = seq != SEQ_INVALID and crc == entry_crc(seq)
        out.append((seq, state, valid))
    return out


def encode_otadata_sector(seq):
    body = (struct.pack('<I', seq) + b'\xff' * 20
            + struct.pack('<II', ESP_OTA_IMG_VALID, entry_crc(seq)))
    return body + b'\xff' * (SECTOR - len(body))


def read_state(esp, tmpdir):
    blob = esp.read_flash(OTADATA_OFF, OTADATA_SIZE,
                          os.path.join(tmpdir, 'otadata.bin'))
    entries = decode_otadata(blob)
    max_idx, max_seq = None, 0
    for i, (seq, _state, valid) in enumerate(entries):
        if valid and seq > max_seq:
            max_idx, max_seq = i, seq
    if max_idx is None:
        # nothing valid: the bootloader falls back to ota_0, and we may
        # claim either sector as the "loser" to write into
        return entries, None, 0, 0
    return entries, max_idx, max_seq, (max_seq - 1) % 2


def select_slot(esp, tmpdir, slot):
    """Point otadata at `slot`. The bootloader takes the sector with the
    HIGHEST valid seq and boots slot (seq-1)%2 -- so the seq must both beat
    the incumbent AND carry the right parity, and it goes in the sector NOT
    holding the current max so the max always wins cleanly."""
    _entries, max_idx, max_seq, _booted = read_state(esp, tmpdir)
    seq = max_seq + 1
    while (seq - 1) % 2 != slot:
        seq += 1
    target = 0 if max_idx is None else 1 - max_idx
    path = os.path.join(tmpdir, 'otadata_new.bin')
    with open(path, 'wb') as f:
        f.write(encode_otadata_sector(seq))
    print('  otadata: seq=%d state=VALID -> sector %d (0x%x), selects ota_%d'
          % (seq, target, OTADATA_OFF + target * SECTOR, slot))
    esp.write_flash(OTADATA_OFF + target * SECTOR, path)
    _e, _mi, back_seq, back_slot = read_state(esp, tmpdir)
    if back_seq != seq or back_slot != slot:
        raise RuntimeError('otadata readback disagrees: seq=%s slot=%s'
                           % (back_seq, back_slot))
    return seq


# ---------------------------------------------------------------- image

def check_app(path):
    size = os.path.getsize(path)
    with open(path, 'rb') as f:
        hdr = f.read(16)
    if len(hdr) < 16 or hdr[0] != 0xe9:
        sys.exit('%s: not an ESP image (magic 0x%02x, want 0xe9)'
                 % (path, hdr[0] if hdr else 0))
    chip = struct.unpack('<H', hdr[12:14])[0]
    if chip != 0x0009:
        sys.exit('%s: chip id 0x%04x is not ESP32-S3 (0x0009)' % (path, chip))
    if size > OTA_SIZE:
        sys.exit('%s: %d bytes exceeds the OTA slot (0x%x)'
                 % (path, size, OTA_SIZE))
    print('  app image OK: %d bytes, chip 0x%04x, %.1f%% of slot'
          % (size, chip, 100.0 * size / OTA_SIZE))


# ---------------------------------------------------------------- serial

def capture_boot(port, timeout):
    s = serial.Serial()
    s.port = port
    s.baudrate = 115200
    s.timeout = 0.2
    s.dtr = False          # set BEFORE open: a plain open pulses the CH340
    s.rts = False          # and power-cycles the deck under us
    s.open()
    s.dtr = False
    s.rts = False
    s.reset_input_buffer()
    # reset-to-run: DTR low holds IO0 high (normal boot, not download),
    # the RTS pulse drives EN
    s.setDTR(False)
    s.setRTS(True)
    time.sleep(0.2)
    s.setRTS(False)
    out = b''
    t0 = time.time()
    while time.time() - t0 < timeout:
        chunk = s.read(4096)
        if chunk:
            out += chunk
            text = out.decode('utf-8', 'replace')
            if BANNER in text and PROMPT in text.split(BANNER, 1)[1]:
                break
    s.close()
    return out.decode('utf-8', 'replace')


def verdict(log):
    """(ok, reasons, warnings). Three independent things must all hold: the
    interpreter started (banner), it survived boot.py and came back to the
    prompt (>>> AFTER the banner), and the deck app itself ran (DECKLOG)."""
    reasons = []
    for f in FATAL:
        if f in log:
            reasons.append('found %r' % f)
    if BANNER not in log:
        reasons.append('no %r banner' % BANNER)
        prompt = PROMPT in log
    else:
        prompt = PROMPT in log.split(BANNER, 1)[1]
    if not prompt:
        reasons.append('no %r prompt' % PROMPT)
    # DECKLOG is a GATE, not a warning. A prompt only proves MicroPython is
    # alive; boot.py wraps every step in try/except, so the deck app can fail
    # outright and still hand back a perfectly usable >>>. An image that boots
    # to a bare REPL with no deck is a BAD image -- rejecting it is the whole
    # point -- and it must not pass merely because the interpreter survived.
    if 'DECKLOG' not in log:
        reasons.append('no DECKLOG: boot.py never ran (bare REPL, deck did not come up)')
    return not reasons, reasons, []


def tail(log, n=40):
    lines = log.replace('\r', '').splitlines()
    return '\n'.join(lines[-n:])


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--app', required=True, help='candidate app .bin')
    ap.add_argument('--sys', dest='sysbin', help='optional /sys image')
    ap.add_argument('--port', default=PORT)
    ap.add_argument('--baud', type=int, default=460800)
    ap.add_argument('--timeout', type=int, default=30,
                    help='seconds to capture the boot log (default 30)')
    ap.add_argument('--keep', action='store_true',
                    help='do NOT auto-revert on failure (leaves it flashed '
                         'for debugging -- the deck may stay dead)')
    args = ap.parse_args()

    check_app(args.app)
    if args.sysbin and not os.path.exists(args.sysbin):
        sys.exit('%s: no such file' % args.sysbin)

    tmpdir = tempfile.mkdtemp(prefix='smoke_fw_')
    esp = Esp(args.port, args.baud)
    sys_backup = None
    orig_slot = None
    selected = False       # candidate is live in otadata -> must be undone
    print('\n== inspect ==')
    try:
        entries, max_idx, max_seq, orig_slot = read_state(esp, tmpdir)
        for i, (seq, state, valid) in enumerate(entries):
            print('  sector %d @0x%x: seq=%s state=0x%x %s'
                  % (i, OTADATA_OFF + i * SECTOR,
                     'ERASED' if seq == SEQ_INVALID else seq, state,
                     'valid' if valid else 'INVALID'))
        if max_idx is None:
            print('  no valid otadata entry; assuming the deck runs ota_0')
        cand = 1 - orig_slot
        print('  running ota_%d (seq=%d) -> candidate is ota_%d @0x%x'
              % (orig_slot, max_seq, cand, OTA_OFF[cand]))

        if args.sysbin:
            # /sys is ONE partition shared by both slots: flashing it puts
            # the fallback image at risk too, so it must be restorable
            print('\n== back up /sys ==')
            sys_backup = os.path.join(tmpdir, 'sys_backup.bin')
            esp.read_flash(SYS_OFF, SYS_SIZE, sys_backup)
            print('  saved %s' % sys_backup)

        print('\n== flash candidate ==')
        esp.write_flash(OTA_OFF[cand], args.app)
        if args.sysbin:
            esp.write_flash(SYS_OFF, args.sysbin)
        select_slot(esp, tmpdir, cand)
        selected = True

        print('\n== boot (%ds) ==' % args.timeout)
        log = capture_boot(args.port, args.timeout)
        ok, reasons, warnings = verdict(log)
    except Exception as e:
        print('\nVERDICT: FAIL -- %s' % e)
        # anything we changed has to come back, even on an aborted run:
        # /sys is shared, and a half-done run must not leave the deck
        # pointed at an untested image
        if (selected or sys_backup) and not args.keep:
            print('\n== restore ==')
            try:
                if sys_backup:
                    esp.write_flash(SYS_OFF, sys_backup)
                if selected:
                    select_slot(esp, tmpdir, orig_slot)
                    back = capture_boot(args.port, args.timeout)
                    if verdict(back)[0]:
                        print('  fallback ota_%d is healthy again.' % orig_slot)
                    else:
                        print('  FALLBACK ota_%d ALSO UNHEALTHY -- recover by '
                              'hand.' % orig_slot)
                        print(tail(back))
            except Exception as e2:
                print('RESTORE ALSO FAILED: %s' % e2)
                print('THE DECK MAY BE DEAD -- recover by hand.')
        sys.exit(1)

    print('\n' + '=' * 60)
    if ok:
        print('VERDICT: PASS -- ota_%d booted to a live prompt' % cand)
        print('=' * 60)
        print('  banner + >>> seen; candidate left selected.')
        for w in warnings:
            print('  warning: %s' % w)
        sys.exit(0)

    print('VERDICT: FAIL -- ota_%d did not come up' % cand)
    print('=' * 60)
    for r in reasons:
        print('  - %s' % r)
    print('\n---- boot log tail ----\n%s\n-----------------------' % tail(log))

    if args.keep:
        print('\n--keep: candidate LEFT SELECTED. The deck is likely dead; '
              'run again without --keep, or re-select ota_%d by hand.'
              % orig_slot)
        sys.exit(1)

    print('\n== auto-revert to ota_%d ==' % orig_slot)
    try:
        if sys_backup:
            esp.write_flash(SYS_OFF, sys_backup)
        select_slot(esp, tmpdir, orig_slot)
        back = capture_boot(args.port, args.timeout)
        back_ok, back_reasons, _bw = verdict(back)
    except Exception as e:
        print('REVERT FAILED: %s' % e)
        print('THE DECK MAY BE DEAD -- recover by hand.')
        sys.exit(1)

    if back_ok:
        print('  fallback ota_%d is healthy again. Deck recovered.'
              % orig_slot)
    else:
        print('  FALLBACK ota_%d ALSO UNHEALTHY: %s' % (orig_slot,
                                                        ', '.join(back_reasons)))
        print('---- fallback log tail ----\n%s' % tail(back))
        print('THE DECK MAY BE DEAD -- recover by hand.')
    sys.exit(1)


if __name__ == '__main__':
    main()
