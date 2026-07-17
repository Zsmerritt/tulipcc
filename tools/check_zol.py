#!/usr/bin/env python3
"""Fail the build if any Xtensa zero-overhead loop (ZOL) is nested.

Why: amy/src/amy_blockops.h hand-writes `loopnez` (which occupies the single
LBEG/LEND/LCOUNT register set) inside inline asm. If the compiler ever formed
its own hardware `loop`/`loopgtz`/`loopnez` AROUND code containing one of
those asm blocks, the inner loopnez would corrupt the outer loop's registers
-- a silent, hardware-only failure (rare audio glitches up to a core-0
runaway). Current esp GCC (14.2/15.1) provably refuses to do this
(gcc/hw-doloop.cc sets loop->has_asm for any inline asm and the xtensa
hwloop_optimize rejects has_asm/has_call/non-innermost bodies), which is what
lets amy.c build at -O3 -funroll-loops. This check exists so a future
toolchain that changes that policy fails CI instead of shipping.

Usage:
    check_zol.py <build-dir-or-obj> [more paths...]

For each directory argument, scans every object built from amy/src/*.c.
For each .obj/.o argument, scans that object. An object's disassembly is
searched for ZOL instructions; the span of each (its address .. its end
target) must not contain another ZOL instruction. Sections are independent
(-ffunction-sections), so spans are checked per section.

Exits nonzero on: a nested ZOL, no objects found, objdump failure, or zero
ZOL instructions found across all scanned objects (vacuous pass guard --
amy.c always contains the blockops' own loopnez).
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

OBJDUMP_CANDIDATES = ("xtensa-esp32s3-elf-objdump", "xtensa-esp-elf-objdump")

SEC_RE = re.compile(r"^[0-9a-f]+ <(.*)>:$")
ZOL_RE = re.compile(
    r"^\s*([0-9a-f]+):\s+[0-9a-f]+\s+(loopnez|loopgtz|loop)\t\S+,\s*([0-9a-f]+)"
)


def find_objdump():
    for name in OBJDUMP_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    sys.exit("check_zol: no xtensa objdump on PATH (looked for %s)"
             % ", ".join(OBJDUMP_CANDIDATES))


def collect_objects(args):
    objs = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            hits = list(p.rglob("*.obj")) + list(p.rglob("*.c.o"))
            objs += [q for q in hits
                     if "amy" in str(q).replace("\\", "/").lower()
                     and "/src/" in str(q).replace("\\", "/")]
        elif p.suffix in (".obj", ".o"):
            objs.append(p)
        else:
            sys.exit(f"check_zol: not a directory or object file: {a}")
    return sorted(set(objs))


def check_object(objdump, obj):
    """Return (n_zols, n_nested) for one object file."""
    out = subprocess.run([objdump, "-d", str(obj)],
                         capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"check_zol: objdump failed on {obj}:\n{out.stderr}")
    sec, per_sec = None, {}
    for line in out.stdout.splitlines():
        m = SEC_RE.match(line)
        if m:
            sec = m.group(1)
            continue
        m = ZOL_RE.match(line)
        if m and sec is not None:
            per_sec.setdefault(sec, []).append(
                (int(m.group(1), 16), m.group(2), int(m.group(3), 16)))
    n_zols = sum(len(v) for v in per_sec.values())
    n_nested = 0
    for s, zols in per_sec.items():
        for a, mnem, end in zols:
            for a2, mnem2, _ in zols:
                if a2 != a and a < a2 < end:
                    n_nested += 1
                    print(f"check_zol: NESTED ZOL in {obj} <{s}>: "
                          f"{mnem}@0x{a:x}..0x{end:x} contains {mnem2}@0x{a2:x}",
                          file=sys.stderr)
    return n_zols, n_nested


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    objdump = find_objdump()
    objs = collect_objects(sys.argv[1:])
    if not objs:
        sys.exit("check_zol: no amy/src object files found under the given paths")
    total = nested = 0
    for obj in objs:
        n, bad = check_object(objdump, obj)
        total += n
        nested += bad
    if nested:
        sys.exit(f"check_zol: FAIL -- {nested} nested zero-overhead loop(s) "
                 f"across {len(objs)} object(s)")
    if total == 0:
        sys.exit("check_zol: FAIL -- zero ZOL instructions found; the "
                 "blockops loopnez should always be present (vacuous pass?)")
    print(f"check_zol: OK -- {total} ZOL instructions in {len(objs)} "
          f"object(s), none nested")


if __name__ == "__main__":
    main()
