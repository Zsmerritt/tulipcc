#!/usr/bin/env python3
"""qget.py -- fetch a file from the deck WITHOUT resetting it (see qexec.py).
Usage: python qget.py /user/shot.png C:/local/shot.png"""
import base64
import subprocess
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
dev, local = sys.argv[1], sys.argv[2]
code = (
    "import binascii\n"
    "with open(%r, 'rb') as f:\n"
    "    while True:\n"
    "        b = f.read(3072)\n"
    "        if not b:\n"
    "            break\n"
    "        print(binascii.b2a_base64(b).decode().strip())\n" % dev)
cp = subprocess.run([sys.executable, os.path.join(HERE, 'qexec.py'), '-', code],
                    capture_output=True, text=True, timeout=600)
if cp.returncode != 0:
    sys.stderr.write(cp.stdout + cp.stderr)
    sys.exit(1)
data = b''.join(base64.b64decode(line) for line in cp.stdout.split() if line)
with open(local, 'wb') as f:
    f.write(data)
print('%s -> %s (%d bytes)' % (dev, local, len(data)))
