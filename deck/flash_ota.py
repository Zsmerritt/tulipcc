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

The serial transport, device-side write-verify-retry pull and HTTP image
server are shared with flash_pingpong.py via deck/flashlib.py.
"""

import hashlib
import sys
import time

import functools
print = functools.partial(print, flush=True)

from flashlib import sh, tagged, build_ota_code, image_server


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

    # 2 + 3. temp server hosting the image; our IP is the route to the device
    with image_server(image, http_port) as srv:
        base = srv.base_for(dev_ip)
        print('serving from: %s' % base)

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

        # 5. device pulls, writes, verifies, sets boot (default target: the
        # inactive OTA slot -- exactly as this tool always did)
        code = build_ota_code(base, sha, len(data))
        out = sh(port, ['exec', code], timeout=900)
        # tagged-prefix parse like every other decision point: chatter can
        # interleave with (or split) plain substrings; the LAST 'OTA:'-tagged
        # line is the device script's own final word. Fail-closed either way
        # -- success markers only originate from the OTA script after BOTH
        # sha checks pass.
        if tagged(out, 'OTA:') != 'BOOTSET' or 'OTA:OK' not in out:
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
        return 0


if __name__ == '__main__':
    sys.exit(main())
