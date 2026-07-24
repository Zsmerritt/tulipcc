"""E-mu 4MB GM bank runtime table (the deck's 'gm2' instrument font).

The deck's second GM instrument type ('gm2') plays the source-verified E-mu
4MB font ('emu4', block 5 of the reconstructed multi-font bank). AMY serves
these presets at PRESET_BASE+i (wave=PCM) from the `fonts` flash partition,
after the GeneralUser bank (amy/gm.py).

Only the emu4 font is read on-device, so only its data ships here -- stored as
parallel int arrays (each is one MicroPython heap object) instead of a dict of
tuples. Program p is covered iff it appears in _PROGRAMS; the parallel
_PRESETS/_ROOTS/_NZONES carry its (preset, root, nzones) at the same index.

The other reconstructed fonts (merged/emu8/bank4), the DRUMS maps, and the
synth-WAVES collections were provenance/reference data never touched by the
runtime accessors; they now live in tools/gm_big_bank_tables.py (host-only,
not deployed). See sounds/gm/big_bank_map.json for provenance and confidence.
"""

PRESET_BASE = 1024   # must match GM_BIG_PRESET_BASE in src/pcm_gm_big.h

# The deck's second GM instrument type ('gm2') plays the source-verified E-mu
# 4MB font from this bank. Other fonts stay in the host-side tables module
# until their maps pass a listen test.
FONT = 'emu4'

# emu4 coverage as parallel arrays (derived from the emu4 dict; sorted by
# program). _PROGRAMS[i] has preset _PRESETS[i], root _ROOTS[i], _NZONES[i].
_PROGRAMS = [
      0,   2,   4,   5,   6,   7,   8,   9,  10,  11,  12,  13,  14,  15,  16,  17,
     18,  19,  21,  22,  24,  25,  26,  27,  28,  29,  30,  31,  32,  33,  34,  35,
     36,  37,  38,  40,  42,  43,  44,  45,  46,  47,  50,  52,  53,  54,  55,  56,
     57,  58,  59,  60,  61,  62,  64,  65,  66,  67,  68,  69,  70,  71,  72,  73,
     74,  75,  77,  79,  80,  81,  82,  84,  85,  87,  90,  93, 103, 104, 105, 106,
    107, 108, 109, 111, 112, 113, 114, 115, 116, 117, 118, 119,
]

_PRESETS = [
    1903, 1905, 1907, 1909, 1911, 1913, 1915, 1916, 1917, 1918, 1920, 1921, 1922, 1923, 1925, 1927,
    1928, 1930, 1932, 1934, 1936, 1938, 1940, 1942, 1945, 1947, 1948, 1950, 1953, 1954, 1955, 1957,
    1959, 1961, 1963, 1964, 1967, 1970, 1971, 1973, 1975, 1977, 1978, 1980, 1982, 1984, 1986, 1987,
    1989, 1992, 1993, 1995, 1997, 1999, 2001, 2003, 2005, 2007, 2009, 2011, 2013, 2015, 2017, 2018,
    2020, 2021, 2022, 2023, 2024, 2026, 2028, 2029, 2031, 2033, 2038, 2036, 2039, 2041, 2042, 2044,
    2046, 2048, 2049, 2051, 2053, 2054, 2055, 2056, 2057, 2058, 2059, 2060,
]

_ROOTS = [
     74,  61,  66,  65,  60,  62,  91,  94,  89,  71,  68,  79,  86,  73,  68,  78,
     68,  48,  66,  65,  58,  56,  61,  52,  67,  60,  60,  62,  51,  48,  46,  56,
     48,  51,  64,  65,  58,  50,  65,  75,  69,  67,  67,  66,  69,  76,  80,  65,
     65,  60,  68,  65,  66,  64,  65,  62,  63,  60,  71,  67,  64,  64,  90,  77,
     80,  71,  86,  88,  60,  60,  90,  67,  70,  64,  75,  68,  61,  63,  67,  61,
     66,  68,  57,  70,  40,  72,  77,  94,  90,  52,  84,  56,
]

_NZONES = [
    2, 2, 2, 2, 2, 2, 1, 1, 1, 2, 1, 1, 1, 2, 2, 1,
    2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 2,
    2, 2, 2, 3, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 1, 2,
    2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 2,
    1, 1, 1, 1, 2, 2, 1, 2, 2, 3, 1, 2, 2, 1, 2, 2,
    2, 1, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1,
]


def _index(program):
    """Index of program in the sorted _PROGRAMS array, or -1 if absent."""
    lo, hi = 0, len(_PROGRAMS)
    while lo < hi:
        mid = (lo + hi) // 2
        v = _PROGRAMS[mid]
        if v == program:
            return mid
        if v < program:
            lo = mid + 1
        else:
            hi = mid
    return -1


def programs():
    """Sorted GM program numbers this font actually covers."""
    return list(_PROGRAMS)


def has_program(program):
    return _index(program) >= 0


def name(program):
    import gm
    return gm.NAMES[program]


def patch_string(program):
    """AMY patch wire string, same recipe as gm.patch_string: wave=PCM +
    feedback=2 sustain-through-release; the EG release does the fade and the
    engine's loop guard keeps one-shots from machine-gunning."""
    i = _index(program)
    if i < 0:
        raise KeyError(program)
    preset = _PRESETS[i]
    return "v0w7p%db2A5,1,60000,0.85,220,0Z" % preset
