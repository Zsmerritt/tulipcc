#!/usr/bin/env python3
"""Generate deck/patchparams.py: the synth-param values AMY's built-in patches
actually bake, distilled from amy/src/patches.h.

WHY THIS EXISTS. The deck loads juno6/dx7/piano patches by NUMBER
(amy.send(synth=n, patch=11)), so at runtime it never sees the patch string and
cannot read back what the patch set. The Sound editor therefore used to draw
its schema defaults for every one of them -- showing "cutoff 1000 Hz" for Juno
A11, whose baked cutoff is 179.93 Hz, and doing it in the authoritative voice of
a number on a knob. All 128 juno patches diverge on cutoff; 86 on resonance;
120 on kbd track; 108 on level; every one of them on the amp envelope.

patches.h is right here in the tree, so the values are not unknowable -- they
are merely not available at RUNTIME. This distils them at build time, exactly
as patchfx.py already does for the FX a patch applies.

The extraction itself is amyparams.patch_params_from_string(), the SAME parser
the deck runs against the GM patch strings it builds live. There is deliberately
no second implementation to drift: if the reader is wrong, it is wrong in one
place and both paths show it.

Usage (from the repo root):
    python tools/gen_patchparams.py            # rewrite deck/patchparams.py
    python tools/gen_patchparams.py --check    # verify it is up to date (CI)
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DECK = os.path.join(ROOT, 'deck')
PATCHES_H = os.path.join(ROOT, 'amy', 'src', 'patches.h')
OUT = os.path.join(DECK, 'patchparams.py')

sys.path.insert(0, DECK)
import amyparams                                        # noqa: E402

# /* 11: Juno A22 Organ II */ "v1w4a1,,0,1Z..." -- the first definition of a
# number wins: patches.h #ifdefs alternate ROM variants of the drum patches
# (258+), which carry no synth params we model anyway.
_ENTRY = re.compile(r'/\*\s*(\d+):\s*(.*?)\s*\*/\s*"((?:[^"\\]|\\.)*)"', re.S)

HEADER = '''"""Synth-param values baked into AMY's built-in patch strings (generated
from amy/src/patches.h by tools/gen_patchparams.py -- do not hand-edit;
rerun the generator when patches.h changes).

The deck loads these patches by NUMBER, so it cannot read them back at
runtime; without this table the Sound editor showed its own schema
defaults instead and called them the patch's values (Juno A11's cutoff is
179.93 Hz, not the 1000 Hz the editor drew). Same idea as patchfx.py,
which does this for the FX a patch applies.

Only params the deck's schema can faithfully represent are here, and only
where the patch really sets them -- a name that is absent is a patch that
said nothing, which the editor renders as "patch default" rather than
inventing a number. Oscs 1..3 are read only for patches using the deck's
four-osc layout (a SILENT control osc); on the DX7 bank those oscs are FM
operators, and calling operator 2 "Osc A" would be a category error.
"""

'''


def parse_patches_h(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        src = f.read()
    out = {}
    for num, name, body in _ENTRY.findall(src):
        out.setdefault(int(num), (name.strip(), body))
    return out


def _informative(name, value):
    """Is this baked value worth a row in the table?

    A patch value that EQUALS the schema default tells the editor nothing it
    would not already show -- with or without the entry it draws the same
    number -- so carrying it is pure weight. This module is parsed on a
    MicroPython device where a big literal is not free: the full table runs
    ~60 KB, and this codebase has already taken a watchdog reset compiling an
    oversized data file (see synthkits.py's split-data note).

    The exception is the TRUTH_PATCH params, which must ALWAYS be kept even
    when they match. There, "the patch is silent" and "the patch happens to
    agree with our default" are different facts with different renderings: 28
    junos really do bake a 0 ms attack, and dropping that row would make the
    editor claim it does not know an attack it knows exactly. Keeping them is
    what stops this optimisation from becoming the very bug it serves.
    """
    if amyparams.truth_of(name) == amyparams.TRUTH_PATCH:
        return True
    d = amyparams.PARAM_BY_NAME.get(name)
    return d is None or float(value) != float(d['default'])


def build_table(entries):
    table = {}
    for num, (name, body) in sorted(entries.items()):
        vals = amyparams.patch_params_from_string(body)
        vals = {k: v for k, v in vals.items() if _informative(k, v)}
        if vals:
            table[num] = (name, vals)
    return table


def _fmt(v):
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return repr(round(v, 6)) if isinstance(v, float) else repr(v)


def render(table):
    lines = [HEADER, 'PARAMS = {\n']
    for num, (name, vals) in sorted(table.items()):
        body = ', '.join("%r: %s" % (k, _fmt(vals[k])) for k in sorted(vals))
        lines.append('    %d: {%s},  # %s\n' % (num, body, name))
    lines.append('}\n\n\n')
    lines.append('def patch_params(patch):\n')
    lines.append('    """{param_name: value} a built-in patch bakes '
                 '({} = none/unknown)."""\n')
    lines.append('    return PARAMS.get(patch, {})\n')
    return ''.join(lines)


def main():
    entries = parse_patches_h(PATCHES_H)
    if not entries:
        sys.exit('no patches parsed from %s' % PATCHES_H)
    text = render(build_table(entries))
    if '--check' in sys.argv:
        try:
            with open(OUT, encoding='utf-8') as f:
                cur = f.read()
        except OSError:
            cur = None
        if cur != text:
            sys.exit('deck/patchparams.py is stale: rerun '
                     'tools/gen_patchparams.py')
        print('deck/patchparams.py is up to date')
        return
    with open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    print('wrote %s (%d patches)' % (OUT, text.count('\n    ')))


if __name__ == '__main__':
    main()
