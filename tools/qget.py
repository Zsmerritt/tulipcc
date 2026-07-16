#!/usr/bin/env python3
"""qget.py -- fetch a file from the deck WITHOUT resetting it (see qexec.py).

Usage: python qget.py /user/shot.png C:/local/shot.png

Serial-protocol hardening (see deck/SERIAL-PROTOCOL.md): every payload line
the device sends is tagged 'B64:<nonce>:' with a per-invocation nonce, and
ONLY exactly-tagged lines are decoded -- console chatter (AMY warnings,
decklog echoes, stray output from a previous attempt) can share the stream
freely without corrupting the fetch. The final 'B64END:<nonce>:<sha8>' line
carries a checksum over the raw bytes; the fetch fails loudly on any
mismatch instead of writing a corrupt file.
"""
import base64
import hashlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    dev, local = sys.argv[1], sys.argv[2]
    nonce = os.urandom(4).hex()   # random beats time-derived for uniqueness
    code = (
        "import binascii, hashlib\n"
        "h = hashlib.sha256()\n"
        "with open(%r, 'rb') as f:\n"
        "    while True:\n"
        "        b = f.read(3072)\n"
        "        if not b:\n"
        "            break\n"
        "        h.update(b)\n"
        "        print('B64:%s:' + binascii.b2a_base64(b).decode().strip())\n"
        "print('B64END:%s:' + binascii.hexlify(h.digest())[:8].decode())\n"
        % (dev, nonce, nonce))
    cp = subprocess.run(
        [sys.executable, os.path.join(HERE, 'qexec.py'), '-', code],
        capture_output=True, text=True, timeout=600)
    if cp.returncode != 0:
        sys.stderr.write(cp.stdout + cp.stderr)
        sys.exit(1)
    tag = 'B64:%s:' % nonce
    end = 'B64END:%s:' % nonce
    data = b''
    want = None
    for line in cp.stdout.replace('\r', '').splitlines():
        if line.startswith(tag):
            data += base64.b64decode(line[len(tag):])
        elif line.startswith(end):
            want = line[len(end):].strip()
    got = hashlib.sha256(data).hexdigest()[:8]
    if want is None or got != want:
        sys.stderr.write('qget: checksum mismatch (%s != %s) -- corrupt or '
                         'incomplete stream, nothing written\n'
                         % (got, want))
        sys.exit(2)
    with open(local, 'wb') as f:
        f.write(data)
    print('%s -> %s (%d bytes, sha8 %s)' % (dev, local, len(data), got))


if __name__ == '__main__':
    main()
