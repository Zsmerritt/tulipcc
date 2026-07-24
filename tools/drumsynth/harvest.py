#!/usr/bin/env python3
"""Collect DrumSynth .ds patches from sparse-cloned source repos into
patches/<pack>/, validating the [Tone]+Envelope signature and deduping by
content hash. Usage: python harvest.py <dir-with-clones>"""
import hashlib
import os
import shutil
import sys

SOURCES = [
    (('lmms', 'data', 'samples', 'drumsynth'), 'lmms'),
    (('neil', 'presets', 'mdaDrumLibrary'), 'neil'),
]


def main(clone_root):
    here = os.path.dirname(os.path.abspath(__file__))
    seen = set()
    kept = dropped = dup = 0
    for parts, tag in SOURCES:
        root_dir = os.path.join(clone_root, *parts)
        if not os.path.isdir(root_dir):
            print('missing source:', root_dir)
            continue
        for dirpath, _, files in os.walk(root_dir):
            pack = os.path.relpath(dirpath, root_dir).replace(os.sep, '/').lower()
            if pack == '.':
                pack = 'root'
            for fn in files:
                if not fn.lower().endswith('.ds'):
                    continue
                data = open(os.path.join(dirpath, fn), 'rb').read()
                txt = data.decode('latin1')
                if '[Tone]' not in txt or 'Envelope' not in txt:
                    dropped += 1
                    continue
                h = hashlib.sha1(data).hexdigest()
                if h in seen:
                    dup += 1
                    continue
                seen.add(h)
                out = os.path.join(here, 'patches', pack)
                os.makedirs(out, exist_ok=True)
                dest = os.path.join(out, fn.lower().replace(' ', '_'))
                if os.path.exists(dest):
                    dest = dest[:-3] + '_' + h[:6] + '.ds'
                shutil.copyfile(os.path.join(dirpath, fn), dest)
                kept += 1
    print('kept %d unique, %d duplicates skipped, %d invalid dropped'
          % (kept, dup, dropped))
    packs = sorted(os.listdir(os.path.join(here, 'patches')))
    print(len(packs), 'packs:', ', '.join(packs))


if __name__ == '__main__':
    main(sys.argv[1])
