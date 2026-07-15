#!/usr/bin/env python3
"""DrumSynth .ds -> AMY oscillator parameters.

The target is the deck's 2-osc synth-hit voice (tone + chained noise):
AMY io-note-map templates expand to at most 256 wire chars, so every hit
is clamped to two oscillators. Component priority when a patch enables
more: tone-ish = Tone, else Overtones osc1; noise-ish = loudest of Noise /
NoiseBand / NoiseBand2. The Distortion and global Filter sections are not
mapped (flagged in the module docstring's fidelity notes).

Mapping notes (tune-by-ear candidates are marked *):
- Envelope points "t,l ..." are samples@44.1k -> bp0 "ms,0..1" pairs; a
  final short release pair is appended so the envelope self-terminates
  (kit patches run with synth_flags=3 = ignore note-offs).
- Tone pitch: const freq = F2, EG1 coef = log2(F1/F2) octaves, bp1 decay
  time shaped by Droop (*): higher droop = faster fall.
- Noise Slope: negative = LPF, positive = HPF, cutoff scaled by |slope| (*).
- NoiseBand: NOISE through a BPF at F, resonance from dF width (*).
- Levels: section Level/128 = unity-ish gain into the amp coefs const slot.
"""
import math
import re
import sys

SINE, PULSE, SAW_DOWN, SAW_UP, TRIANGLE, NOISE = 0, 1, 2, 3, 4, 5
FILTER_NONE, FILTER_LPF, FILTER_BPF, FILTER_HPF = 0, 1, 2, 3


def parse_ds(path):
    """Parse a .ds file into {section: {key: value}} (values kept as strings)."""
    sections = {}
    cur = None
    for raw in open(path, encoding='latin1'):
        line = raw.strip()
        if not line or line.startswith(';'):
            continue
        if line.startswith('[') and line.endswith(']'):
            cur = line[1:-1]
            sections[cur] = {}
        elif '=' in line and cur is not None:
            k, v = line.split('=', 1)
            sections[cur][k.strip()] = v.strip()
    return sections


def _num(sec, key, default=0.0):
    try:
        return float(sec.get(key, default))
    except (ValueError, AttributeError):
        return default


def _env_points(s):
    """'0,100 968,87 ...' -> [(ms, 0..1), ...]"""
    pts = []
    for tok in s.split():
        try:
            t, l = tok.split(',')
            pts.append((float(t) / 44.1, max(0.0, min(1.0, float(l) / 100.0))))
        except ValueError:
            continue
    return pts


def _env_to_bp(pts, stretch=1.0, max_pts=4):
    """Envelope points -> AMY bp string. Thin to max_pts (first + last kept,
    middles by biggest level steps), end at 0, append a short release pair
    (the kit runs with ignore-note-offs, so the body must self-terminate)."""
    if not pts:
        pts = [(0.0, 1.0), (200.0, 0.0)]
    pts = sorted(pts)
    if pts[0][0] > 0.5:
        pts.insert(0, (0.0, pts[0][1]))
    if len(pts) > max_pts:
        first, last = pts[0], pts[-1]
        mids = pts[1:-1]
        mids.sort(key=lambda p: -abs(p[1]))
        keep = sorted(mids[:max_pts - 2])
        pts = [first] + keep + [last]
    if pts[-1][1] > 0.001:
        pts.append((pts[-1][0] + 30.0, 0.0))
    out = []
    for t, l in pts:
        out.append('%d,%s' % (max(0, round(t * stretch)), _fmt(l)))
    out.append('25,0')       # release pair: quick fade if a note-off arrives
    return ','.join(out)


def _env_duration(pts):
    return pts[-1][0] if pts else 200.0


def _fmt(x):
    if abs(x - round(x)) < 0.005:
        return '%d' % round(x)
    return ('%.2f' % x).rstrip('0').rstrip('.')


def _amp_string(level):
    """Amp coefs: const = component level, velocity coef 1."""
    return '%s,0,1' % _fmt(level)


def convert(sections, name=''):
    """-> {'name': ..., 'oscs': [ {AMY kwargs}, ... ]} (1-2 oscs)."""
    gen = sections.get('General', {})
    stretch = _num(gen, 'Stretch', 100.0) / 100.0 or 1.0
    tuning = 2.0 ** (_num(gen, 'Tuning', 0.0) / 12.0)
    gain_db = max(-24.0, min(24.0, _num(gen, 'Level', 0.0)))
    # DrumSynth v1 files use Level=0 for unity as well; only treat clearly
    # negative values as attenuation.
    gain = 10.0 ** (min(0.0, gain_db) / 20.0)

    oscs = []

    # ---- tone-ish component ----
    tone = sections.get('Tone', {})
    over = sections.get('Overtones', {})
    if int(_num(tone, 'On', 0)):
        pts = _env_points(tone.get('Envelope', ''))
        f1 = max(10.0, _num(tone, 'F1', 200.0)) * tuning
        f2 = max(10.0, _num(tone, 'F2', 50.0)) * tuning
        droop = _num(tone, 'Droop', 50.0)
        dur = _env_duration(pts) * stretch
        # * pitch-fall time: droop 0 = slow glide over most of the hit,
        #   droop 100 = nearly instant drop
        pitch_ms = max(5.0, min(400.0, dur * (110.0 - droop) / 400.0))
        osc = {
            'wave': SINE,
            'freq': '%s,0,0,0,%s' % (_fmt(f2), _fmt(math.log2(f1 / f2))),
            'bp1': '0,1,%d,0,1,0' % round(pitch_ms),
            'bp0': _env_to_bp(pts, stretch),
            'amp': _amp_string(gain * _num(tone, 'Level', 128.0) / 128.0),
        }
        phase = _num(tone, 'Phase', 0.0) / 360.0
        if phase > 0.01:
            osc['phase'] = _fmt(phase)
        oscs.append(osc)
    elif int(_num(over, 'On', 0)):
        pts = _env_points(over.get('Envelope1', ''))
        f1 = max(20.0, _num(over, 'F1', 200.0)) * tuning
        wave = SINE if int(_num(over, 'Wave1', 0)) == 0 else PULSE
        oscs.append({
            'wave': wave,
            'freq': '%s,0' % _fmt(f1),
            'bp0': _env_to_bp(pts, stretch),
            'amp': _amp_string(gain * _num(over, 'Level', 128.0) / 128.0),
        })

    # ---- noise-ish component: loudest enabled of Noise/NoiseBand/NoiseBand2
    cands = []
    noise = sections.get('Noise', {})
    if int(_num(noise, 'On', 0)):
        cands.append(('noise', _num(noise, 'Level', 128.0), noise))
    for sec_name in ('NoiseBand', 'NoiseBand2'):
        nb = sections.get(sec_name, {})
        if int(_num(nb, 'On', 0)):
            cands.append(('band', _num(nb, 'Level', 128.0), nb))
    if cands:
        kind, level, sec = max(cands, key=lambda c: c[1])
        osc = {
            'wave': NOISE,
            'bp0': _env_to_bp(_env_points(sec.get('Envelope', '')), stretch),
            'amp': _amp_string(gain * level / 128.0),
        }
        if kind == 'noise':
            slope = _num(sec, 'Slope', 0.0)
            if slope < -5:      # * darker
                osc['filter_type'] = FILTER_LPF
                osc['filter_freq'] = _fmt(max(200.0, 12000.0 * 2.0 ** (slope / 25.0)))
            elif slope > 5:     # * brighter
                osc['filter_type'] = FILTER_HPF
                osc['filter_freq'] = _fmt(min(10000.0, 200.0 * 2.0 ** (slope / 18.0)))
        else:
            f = max(80.0, _num(sec, 'F', 1000.0)) * tuning
            df = max(2.0, _num(sec, 'dF', 50.0))
            osc['filter_type'] = FILTER_BPF
            osc['filter_freq'] = _fmt(f)
            osc['resonance'] = _fmt(max(0.7, min(8.0, 4.0 * 50.0 / df)))
        oscs.append(osc)

    if not oscs:            # degenerate patch: keep it audible
        oscs.append({'wave': SINE, 'freq': '200,0',
                     'bp0': '0,1,120,0,25,0', 'amp': _amp_string(gain)})
    return {'name': name, 'oscs': oscs[:2]}


def hit_wire_events(hit):
    """Debug/preview: per-osc wire fragments (v0 = tone, v1 = noise)."""
    frags = []
    for i, osc in enumerate(hit['oscs']):
        parts = ['v%d' % i, 'w%d' % osc['wave']]
        if 'freq' in osc:
            parts.append('f' + osc['freq'])
        if 'phase' in osc:
            parts.append('P' + osc['phase'])
        if 'bp1' in osc:
            parts.append('B' + osc['bp1'])
        parts.append('A' + osc['bp0'])
        parts.append('a' + osc['amp'])
        if 'filter_type' in osc:
            parts.append('G%d' % osc['filter_type'])
            parts.append('F' + osc['filter_freq'])
        if 'resonance' in osc:
            parts.append('R' + osc['resonance'])
        frags.append(''.join(parts))
    return frags


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if a != '--preview']
    hit = convert(parse_ds(args[0]), args[0])
    if '--preview' in sys.argv:
        for frag in hit_wire_events(hit):
            print(frag)
    else:
        print(hit)
