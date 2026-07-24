#!/usr/bin/env python3
"""loudness_flags.py -- host-side / CI loudness FLAG report for the synthkits
drum corpus. Reporting only: it reads the committed synthkits_data (and an
optional render-metrics CSV) and never regenerates or mutates kit data.

Why a flag list and not a gate
------------------------------
Commit fb0eead2 added a static loudness FLOOR to assemble2.py: it normalizes
amplitude-quiet hits up into a [0.5, 1.0] design peak BEFORE the deck's
KIT_GAIN, so that gain is a trim rather than a rescue. A follow-up measurement
rendered all 1164 hits through AMY and found that turning [0.5, 1.0] into a
HARD peak gate would reject ~62% of a perfectly good corpus -- two-oscillator
summing legitimately drives realized peaks past 1.0, and plenty of usable hits
sit below 0.5 on the pre-gain design scale. A hard gate is the wrong tool. A
FLAG list a human or CI can scan is the right one.

Three flag categories
---------------------
  dead_silent   Hits that render to absolute silence. Detected STATICALLY from
                the patch params -- no render needed: every oscillator has a
                const-0 amp OR a bp0 envelope whose level slots are all zero.
                This reproduces exactly the 11 hits the render found silent
                (9 amp-const-0, plus the tr808/clave pair whose envelopes are
                all zeros despite a non-zero amp).

  low_tail      Hits whose REALIZED peak -- rendered, back-projected onto the
                design amplitude scale (rendered_peak / CAL) -- is below 0.25.
                Present but very quiet. Needs render metrics.

  rms_outlier   Hits whose realized RMS sits more than 15 dB from the median
                RMS of the kit they appear in. A per-kit balance flag. Only
                ~2.5% of in-kit hits trip it, which is exactly what makes it a
                useful flag instead of a gate. Needs render metrics.

Render metrics
--------------
The peak/RMS flags read a CSV produced by the amy_render_cli harness, one line
per hit: ``id,peak,rms_active,dur_ms,residual_peak`` (a leading ``#`` comment
line carrying the render settings is ignored). Point ``--render`` at that CSV.
Without it you still get the render-free dead_silent flags.
"""

import argparse
import json
import math
import os
import statistics
import sys


# Measured design->rendered amplitude scale: rendered_peak = design_amp * CAL,
# linear up to the engine's hard clip (from the corpus render calibration).
CAL = 0.072542

LOW_TAIL_PEAK = 0.25      # realized (design-scale) peak below this -> flag
RMS_DB_WINDOW = 15.0      # |RMS - kit median RMS| beyond this (dB) -> flag
RMS_MIN_KIT = 3           # kits with fewer rendered hits than this are skipped


# --------------------------------------------------------------------------
# corpus loading (read-only)
# --------------------------------------------------------------------------
def default_data_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, '..', '..', 'deck',
                                          'synthkits_data'))


def load_corpus(data_dir):
    """Return (hits, kit_notes):
      hits      {hit_key: params dict} for every hit in every pack.
      kit_notes {kit_key: [hit_key, ...]} -- the distinct hits each kit maps.
    """
    with open(os.path.join(data_dir, 'index.json')) as f:
        idx = json.load(f)
    hits = {}
    for pack in idx.get('packs', {}):
        try:
            with open(os.path.join(data_dir, pack + '.json')) as f:
                hits.update(json.load(f))
        except (OSError, ValueError):
            pass
    kit_notes = {}
    for kk, kv in idx.get('kits', {}).items():
        kit_notes[kk] = sorted(set(kv.get('notes', {}).values()))
    return hits, kit_notes


# --------------------------------------------------------------------------
# dead-silent detection (static -- no render)
# --------------------------------------------------------------------------
def _const(val):
    try:
        return float(str(val).split(',')[0])
    except (ValueError, TypeError):
        return None


def _osc_silent(osc):
    """An oscillator contributes nothing if its const amp is 0, or its bp0
    envelope opens to zero at every level slot (never rises above silence)."""
    amp = _const(osc.get('amp', '0'))
    if amp is not None and amp == 0.0:
        return True
    if 'bp0' in osc:
        levels = []
        for x in str(osc['bp0']).split(',')[1::2]:   # odd slots == levels
            try:
                levels.append(float(x))
            except ValueError:
                pass
        if levels and all(v == 0.0 for v in levels):
            return True
    return False


def dead_silent_flags(hits):
    """Sorted hit keys that render to silence, detected purely from params.
    Partials (patch_string) hits are never flagged here -- none are silent and
    their gain lives in the parent carrier, not a simple const amp."""
    out = []
    for key, h in hits.items():
        if 'patch_string' in h:
            continue
        oscs = h.get('oscs') or []
        if oscs and all(_osc_silent(o) for o in oscs):
            out.append(key)
    return sorted(out)


# --------------------------------------------------------------------------
# render-metric flags
# --------------------------------------------------------------------------
def load_render_csv(path):
    """Parse an amy_render_cli CSV -> {hit_key: (peak, rms, dur_ms, residual)}.
    Hit keys can contain commas, so split from the RIGHT for the 4 metrics."""
    out = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, p, r, d, res = line.rsplit(',', 4)
            out[key] = (float(p), float(r), float(d), float(res))
    return out


def _db(x):
    return 20.0 * math.log10(x) if x > 0 else -120.0


def low_tail_flags(render, thresh=LOW_TAIL_PEAK):
    """[(hit_key, design_peak)] for hits whose realized peak (design scale) is
    non-zero but below `thresh`. Sorted quietest first."""
    out = []
    for key, (peak, _rms, _d, _res) in render.items():
        dp = peak / CAL
        if 0.0 < dp < thresh:
            out.append((key, dp))
    return sorted(out, key=lambda t: t[1])


def rms_outlier_flags(render, kit_notes, db_window=RMS_DB_WINDOW):
    """[(kit_key, hit_key, delta_db)] for hits whose realized RMS is more than
    `db_window` dB from their kit's median RMS. Loudest deviation first."""
    out = []
    for kk, keys in kit_notes.items():
        rmss = [(h, render[h][1]) for h in keys
                if h in render and render[h][1] > 0.0]
        if len(rmss) < RMS_MIN_KIT:
            continue
        med = statistics.median(r for _, r in rmss)
        for h, r in rmss:
            delta = _db(r) - _db(med)
            if abs(delta) > db_window:
                out.append((kk, h, delta))
    return sorted(out, key=lambda t: -abs(t[2]))


# --------------------------------------------------------------------------
# top-level report
# --------------------------------------------------------------------------
def compute_flags(data_dir=None, render_path=None):
    data_dir = data_dir or default_data_dir()
    hits, kit_notes = load_corpus(data_dir)
    report = {
        'data_dir': data_dir,
        'n_hits': len(hits),
        'n_kits': len(kit_notes),
        'dead_silent': dead_silent_flags(hits),
        'low_tail': [],
        'rms_outlier': [],
        'rendered': False,
    }
    if render_path:
        render = load_render_csv(render_path)
        report['rendered'] = True
        report['n_rendered'] = len(render)
        report['low_tail'] = low_tail_flags(render)
        report['rms_outlier'] = rms_outlier_flags(render, kit_notes)
    return report


def print_report(report):
    print('== synthkits loudness flags ==')
    print('corpus: %d hits, %d kits (%s)'
          % (report['n_hits'], report['n_kits'], report['data_dir']))

    ds = report['dead_silent']
    print('\n[dead_silent] %d hit(s) render to silence (static detection):'
          % len(ds))
    for k in ds:
        print('  %s' % k)

    if not report['rendered']:
        print('\n[low_tail] / [rms_outlier]: skipped -- pass --render <csv> '
              '(amy_render_cli output) to compute peak/RMS flags.')
        return

    lt = report['low_tail']
    print('\n[low_tail] %d hit(s) with realized peak < %.2f (design scale):'
          % (len(lt), LOW_TAIL_PEAK))
    for k, dp in lt:
        print('  %-40s peak=%.3f' % (k, dp))

    ro = report['rms_outlier']
    print('\n[rms_outlier] %d hit-in-kit entr(y/ies) > %.0f dB from kit median '
          'RMS:' % (len(ro), RMS_DB_WINDOW))
    for kk, h, d in ro:
        print('  %-14s %-40s %+6.1f dB' % (kk, h, d))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data', default=None,
                    help='synthkits_data dir (default: deck/synthkits_data)')
    ap.add_argument('--render', default=None,
                    help='amy_render_cli CSV for peak/RMS flags (optional)')
    ap.add_argument('--json', default=None,
                    help='also write the flag report to this JSON path')
    args = ap.parse_args(argv)

    report = compute_flags(args.data, args.render)
    print_report(report)
    if args.json:
        with open(args.json, 'w') as f:
            json.dump(report, f, indent=1)
        print('\nwrote %s' % args.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
