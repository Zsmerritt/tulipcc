#!/usr/bin/env python3
"""flash_pingpong.py -- safe firmware update via the 80MHz "flasher" slot.

    python deck/flash_pingpong.py path/to/tulip-play-TULIP4_R11.bin
                                  [--port COM11] [--http-port 8787]

Why this exists: this board's 120MHz octal flash corrupts sustained multi-MB
writes under thermal drift. This tool does the big write while the device is
booted into a dedicated 80MHz "flasher" image, where the write is thermally
safe, then hands control back to the 120MHz play image. See deck/PINGPONG.md.

Orchestration (all host-driven over the same no-reset serial transport +
temporary HTTP server that flash_ota.py uses -- shared via deck/flashlib.py):

  1. ARM:      tell the running PLAY firmware to set NVS flash_pending and
               set_boot(flasher), then reset.
  2. WAIT:     poll until the device reboots into the flasher build in flash
               mode and reports its Wi-Fi IP.
  3. SERVE:    start the temp HTTP image server; prove the device can reach it.
  4. WRITE:    drive the SAME write-verify-retry pull as flash_ota, but target
               the PLAY slot BY LABEL (ota_1) -- written at 80MHz.
  5. FINALIZE: clear flash_pending, set_boot(play), reset -> back to 120MHz.
  6. CONFIRM:  poll until the device is back on the play build with the flag
               cleared.

Nothing here touches the partition table or the bootloader. The flasher slot
is the recovery anchor: if any step fails the device stays in (or returns to)
a bootable state -- the play slot is only ever written at 80MHz and is left
intact on failure.

This is the untested-on-hardware host side of the ping-pong scheme; see the
"NEEDS ON-DEVICE VALIDATION" notes in deck/PINGPONG.md.
"""

import hashlib
import sys
import time

import functools
print = functools.partial(print, flush=True)

from flashlib import sh, tagged, build_ota_code, image_server
import flashmode as fm


# The device-side target for the write: the PLAY slot, selected BY LABEL so it
# is correct regardless of which slot we happen to be running from.
PLAY_TARGET = ("Partition.find(Partition.TYPE_APP, label=%r)[0]"
               % fm.PLAY_LABEL)


def _mode_probe(port, timeout=30):
    """Ask the device: which build am I, is an update pending, what's my IP?

    Returns (mode, ip) where mode is 'flash' (flasher build + pending), 'play'
    (not in flash mode) or None (device unreachable / mid-reboot).
    """
    out = sh(port, ['exec', (
        "import flashmode as fm\n"
        "import tulip\n"
        "try:\n"
        "    m = 'flash' if fm.should_enter_flash_mode() else 'play'\n"
        "except Exception:\n"
        "    m = 'play'\n"
        "try:\n"
        "    ip = str(tulip.ip())\n"
        "except Exception:\n"
        "    ip = 'None'\n"
        "print('PP:MODE:' + m)\n"
        "print('PP:IP:' + ip)")], timeout)
    return tagged(out, 'PP:MODE:'), tagged(out, 'PP:IP:')


def _wait_for(port, want_mode, need_ip, deadline_s, settle_s=3):
    """Poll _mode_probe until the device reports want_mode (and an IP if asked).

    The device is rebooting (~35s for the flasher), so failures to connect are
    expected and simply retried until deadline_s elapses.
    """
    t0 = time.time()
    last = None
    while time.time() - t0 < deadline_s:
        try:
            mode, ip = _mode_probe(port)
        except Exception as e:
            mode, ip = None, None
            last = repr(e)
        if mode == want_mode and (not need_ip or (ip and ip != 'None')):
            return ip
        time.sleep(settle_s)
    print('  (last probe: mode=%r ip=%r err=%r)' % (mode if 'mode' in dir()
          else None, ip if 'ip' in dir() else None, last))
    return None


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
    print('play image: %d bytes, sha256 %s' % (len(data), sha))

    # 1. ARM: set flash_pending + set_boot(flasher) on the running play image,
    # then reset. request_update() returns True only if both stuck.
    print('arming ping-pong update (set flash_pending, set_boot flasher)...')
    out = sh(port, ['exec', (
        "import flashmode as fm\n"
        "print('PP:ARMED:' + ('1' if fm.request_update() else '0'))")], 60)
    if tagged(out, 'PP:ARMED:') != '1':
        print('could not arm the update on the device:\n' + out)
        print('The device was NOT rebooted; it is still on the play image.')
        return 3
    print('armed; rebooting into the flasher slot')
    sh(port, ['exec', '--no-follow', 'import machine; machine.reset()'], 30)

    # 2. WAIT for the flasher to boot into flash mode with Wi-Fi up.
    print('waiting for the flasher to boot into flash mode (up to ~120s)...')
    time.sleep(8)                      # let the reset take + boot start
    dev_ip = _wait_for(port, 'flash', need_ip=True, deadline_s=120)
    if not dev_ip:
        print('flasher did not reach flash mode with Wi-Fi in time.')
        print('The play slot was NOT written. Recover by rebooting the device;')
        print('flash mode auto-recovers to play after its idle timeout.')
        return 4
    print('flasher is up in flash mode; device ip: %s' % dev_ip)

    # 3. SERVE the image; prove the device can reach us.
    with image_server(image, http_port) as srv:
        base = srv.base_for(dev_ip)
        print('serving from: %s' % base)
        out = sh(port, ['exec', (
            "import tuliprequests as ur\n"
            "try:\n"
            "    r = ur.get('%s/ping.txt')\n"
            "    print('PP:PING:' + r.text.strip())\n"
            "    r.close()\n"
            "except Exception as e:\n"
            "    print('PP:PING:FAIL ' + repr(e))" % base)], 90)
        if tagged(out, 'PP:PING:') != 'pong':
            print('device (flash mode) cannot reach this machine.')
            print('Allow python.exe inbound on port %d, or check the network.'
                  % http_port)
            print('The play slot was NOT written; reboot to recover to play.')
            return 5
        print('device can reach the server; writing the PLAY slot at 80MHz')

        # 4. WRITE: same write-verify-retry pull as flash_ota, but the target
        # is the PLAY slot selected BY LABEL (so it is correct even though we
        # are booted from the flasher slot).
        code = build_ota_code(base, sha, len(data), target=PLAY_TARGET,
                              title='Safe update (80MHz)')
        out = sh(port, ['exec', code], timeout=1800)
        if tagged(out, 'OTA:') != 'BOOTSET' or 'OTA:OK' not in out:
            # NB: the DEVICE_OTA script also calls ota.set_boot() on the PLAY
            # partition object on success (OTA:BOOTSET). We still explicitly
            # finalize below to clear the NVS flag; set_boot(play) is idempotent.
            print('WRITE FAILED:\n' + out)
            print('The play slot may be partially written, but flash_pending is'
                  ' still set, so the device stays in the flasher (recovery'
                  ' anchor). Re-run this tool to retry.')
            sh(port, ['exec', 'import fwprogress; fwprogress.fail()'], 60)
            return 6
        print('play slot flashed + verified at 80MHz')

    # 5. FINALIZE: clear flash_pending, set_boot(play), reset.
    print('finalizing: clearing flash_pending, set_boot(play), reboot...')
    sh(port, ['exec', '--no-follow',
              'import flashmode as fm; fm.finalize_to_play()'], 30)

    # 6. CONFIRM the device came back on the play build with the flag cleared.
    print('waiting for the device to return to 120MHz play...')
    time.sleep(8)
    if _wait_for(port, 'play', need_ip=False, deadline_s=120):
        print('DONE -- device is back on the play image (flash_pending cleared)')
        return 0
    print('device did not confirm return to play within the timeout.')
    print('It should auto-recover to play on its own; re-probe with:')
    print("  python tools/qexec.py - \"import flashmode as fm;"
          " print(fm.should_enter_flash_mode())\"")
    return 7


if __name__ == '__main__':
    sys.exit(main())
