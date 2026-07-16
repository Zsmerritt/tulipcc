#!/usr/bin/env python3
"""Additive resynthesis of the sampled TR-808 metallic hits via AMY's
BYO_PARTIALS: STFT peak-tracking of the baked 808 samples (amy
src/pcm_samples_gamma808.h) -> per-partial frequency + amplitude-envelope
tracks -> AMY patch strings (v0 = BYO_PARTIALS parent, v1..vK = PARTIAL
oscs). This is the "more accurate than oscillator recipes" path for
cymbal/hats/cowbell, whose dense inharmonic spectra a 2-osc hit can't hold.

Output: partials808.json {hit_name: {'oscs_string': patch_string,
'partials': K}} consumed by make_synthkits.py as the 'partials808' pack.
"""
import json
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
AMY = os.path.join(HERE, '..', '..', 'amy')
SR = 22050

# hits worth resynthesizing (metallic/inharmonic); (map name, role, max K)
TARGETS = [
    ('TR-808 Cymbal',       'crash',   12),
    ('TR-808 HiHat Open',   'ohh',     10),
    ('TR-808 HiHat Closed', 'chh',      8),
    ('TR-808 Cowbell',      'cowbell',  6),
]


def load_808():
    """{name: int16 numpy array} from the baked gamma808 headers."""
    hdr = open(os.path.join(AMY, 'src', 'pcm_gamma808.h'), encoding='utf-8').read()
    entries = re.findall(r'\{(\d+), (\d+), \d+, \d+, \d+\}, /\* (.*?) \*/', hdr)
    data = open(os.path.join(AMY, 'src', 'pcm_samples_gamma808.h'),
                encoding='utf-8').read()
    body = data[data.index('{') + 1:data.rindex('}')]
    pcm = np.array([int(v) for v in re.findall(r'-?\d+', body)], dtype=np.int16)
    out = {}
    for off, length, name in entries:
        off, length = int(off), int(length)
        if off + length <= len(pcm):
            out[name.strip()] = pcm[off:off + length]
    return out


def track_partials(x, k, nfft=1024, hop=256):
    """Greedy STFT peak tracking -> [(freq_hz, [(t_ms, amp), ...]), ...]"""
    x = x.astype(np.float64) / 32768.0
    win = np.hanning(nfft)
    frames = []
    for i in range(0, max(1, len(x) - nfft), hop):
        frames.append(np.abs(np.fft.rfft(x[i:i + nfft] * win)))
    if not frames:
        return []
    mags = np.array(frames)                     # [frame, bin]
    freqs = np.fft.rfftfreq(nfft, 1.0 / SR)
    # candidate tracks seeded from the loudest frame's peaks
    seed = int(np.argmax(mags.max(axis=1)))
    row = mags[seed]
    peaks = [b for b in range(2, len(row) - 1)
             if row[b] > row[b - 1] and row[b] >= row[b + 1]]
    peaks.sort(key=lambda b: -row[b])
    tracks = []
    used_bins = set()
    for b in peaks:
        if any(abs(b - u) < 3 for u in used_bins):
            continue                            # too close to a taken partial
        used_bins.add(b)
        # follow this bin (+-2) across all frames
        env = []
        for fi in range(len(mags)):
            lo, hi = max(0, b - 2), min(mags.shape[1], b + 3)
            env.append(float(mags[fi, lo:hi].max()))
        tracks.append((float(freqs[b]), np.array(env)))
        if len(tracks) >= k * 2:
            break
    # rank by energy, keep k
    tracks.sort(key=lambda t: -float((t[1] ** 2).sum()))
    tracks = tracks[:k]
    tracks.sort(key=lambda t: t[0])
    # normalize so the loudest track peaks at 1.0
    peak = max(float(t[1].max()) for t in tracks) or 1.0
    out = []
    for f, env in tracks:
        pts = [(i * hop * 1000.0 / SR, float(v) / peak)
               for i, v in enumerate(env)]
        out.append((f, pts))
    return out


def env_to_bp(pts, max_pts=6):
    """Thin an amp envelope to <=max_pts breakpoints + terminal zero."""
    if not pts:
        return '0,1,100,0,10,0'
    # keep first, peak, then biggest-drop samples; always end at ~0
    pts = pts[:]
    first = pts[0]
    peak = max(pts, key=lambda p: p[1])
    rest = sorted(set([first, peak, pts[-1]]) |
                  set(pts[::max(1, len(pts) // (max_pts * 2))]))
    rest.sort()
    while len(rest) > max_pts:
        # drop the point whose removal changes the shape least
        errs = []
        for i in range(1, len(rest) - 1):
            a, b, c = rest[i - 1], rest[i], rest[i + 1]
            span = (c[0] - a[0]) or 1.0
            interp = a[1] + (b[0] - a[0]) / span * (c[1] - a[1])
            errs.append((abs(interp - b[1]), i))
        rest.pop(min(errs)[1])
    if rest[-1][1] > 0.01:
        rest.append((rest[-1][0] + 40.0, 0.0))
    frags = []
    for t, v in rest:
        vv = ('%.2f' % v).rstrip('0').rstrip('.') or '0'
        frags.append('%d,%s' % (max(0, round(t)), vv))
    frags.append('20,0')
    return ','.join(frags)


def hit_patch_string(tracks):
    """v0 = BYO_PARTIALS parent (num_partials via p), v1..vK = PARTIAL oscs."""
    parts = ['v0w10p%da1,0,1Z' % len(tracks)]
    for i, (f, pts) in enumerate(tracks):
        parts.append('v%dw9f%dA%sZ' % (i + 1, round(f), env_to_bp(pts)))
    return ''.join(parts)


def main():
    samples = load_808()
    out = {}
    for name, role, k in TARGETS:
        if name not in samples:
            print('missing sample:', name)
            continue
        tracks = track_partials(samples[name], k)
        ps = hit_patch_string(tracks)
        out[role] = {'name': name + ' (partials)', 'patch_string': ps,
                     'partials': len(tracks)}
        print('%-22s -> %2d partials, %4d chars, top freqs %s' %
              (name, len(tracks), len(ps),
               [round(f) for f, _ in tracks[:5]]))
    with open(os.path.join(HERE, 'partials808.json'), 'w') as f:
        json.dump(out, f, indent=1)
    print('wrote partials808.json')


if __name__ == '__main__':
    sys.exit(main())
