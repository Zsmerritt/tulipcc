#!/usr/bin/env python3
"""Kit assembly v2 over make_synthkits' harvest: content-based role
classification (unlocks the community packs whose creative names defeat the
filename classifier), donor roles (a pack missing hats borrows them), variant
kits (packs with 2+ choices per core role yield an A and a B), community
kits dealt from the combined misc pools, and three more by-the-numbers
recipe classics (909, 606, Simmons SDS-V) beside the 808.

Writes deck/synthkits.json. Run from tools/drumsynth/."""
import json
import os
import sys

import make_synthkits as mk
import ds2amy

S, P, N = ds2amy.SINE, ds2amy.PULSE, ds2amy.NOISE
LPF, BPF, HPF = ds2amy.FILTER_LPF, ds2amy.FILTER_BPF, ds2amy.FILTER_HPF

CORE = ('kick', 'snare')
HATS = ('chh', 'ohh')
COMMUNITY = ('misc', 'misc_electro', 'misc_fx', 'misc_perc', 'misc_synth',
             'misc_claps', 'magnetboy', 'ferraro', 'jorgensohn')
NON_DRUM = ('effects', 'instrument', 'misc_bass')


def _hit_features(oscs):
    tone_f, noise_level, noise_filt, noise_ff, dur = None, 0.0, None, 0.0, 0.0
    for o in oscs:
        try:
            bp = str(o.get('bp0', '0,1,200,0')).split(',')
            dur = max(dur, float(bp[-4]))
        except (ValueError, IndexError):
            dur = max(dur, 200.0)
        if o.get('wave') == N:
            try:
                noise_level = float(str(o.get('amp', '1')).split(',')[0])
            except ValueError:
                noise_level = 1.0
            noise_filt = o.get('filter_type')
            try:
                noise_ff = float(o.get('filter_freq', 0))
            except (TypeError, ValueError):
                noise_ff = 0.0
        elif tone_f is None:
            try:
                tone_f = float(str(o.get('freq', '')).split(',')[0])
            except ValueError:
                tone_f = None
    return tone_f, noise_level, noise_filt, noise_ff, dur


def classify_content(oscs):
    """Role from synthesis params: drums live in well-separated regions of
    (tone freq, noisiness, duration). Coarse on purpose -- misassignments
    land in a sibling percussion role, not in a melodic pad."""
    f, nlev, nfilt, nff, dur = _hit_features(oscs)
    noisy = nlev > 0.25
    if f is None and not noisy:
        return None
    if f is None:
        if nfilt == HPF and nff >= 4000:
            if dur < 130:
                return 'chh'
            return 'ohh' if dur < 650 else 'crash'
        if nfilt == BPF:
            return 'clap' if dur < 350 else 'snare'
        return 'shaker' if dur < 150 else 'snare'
    if f < 100:
        return 'kick'
    if f < 250:
        return 'snare' if noisy else 'tommid'
    if f < 900:
        return 'perc' if noisy else 'congahi'
    return 'claves' if dur < 120 else 'perc'


# ---- extra recipe kits (same by-the-numbers spirit as mk.TR808) -------------
def _tone(freq, drop_oct, drop_ms, dur, amp):
    return {'wave': S, 'freq': '%g,0,0,0,%g' % (freq, drop_oct),
            'bp1': '0,1,%d,0,1,0' % max(1, drop_ms),
            'bp0': '0,1,%d,0,20,0' % dur, 'amp': '%g,0,1,1' % amp}


def _noise(dur, amp, filt=None, ff=None, res=None, bp0=None):
    o = {'wave': N, 'bp0': bp0 or '0,1,%d,0,15,0' % dur, 'amp': '%g,0,1,1' % amp}
    if filt is not None:
        o['filter_type'] = filt
        o['filter_freq'] = '%g' % ff
    if res is not None:
        o['resonance'] = '%g' % res
    return o


TR909 = {   # punchier and brighter than the 808 across the board
    'kick':   [_tone(48, 1.6, 35, 380, 1.0), _noise(20, 0.45, LPF, 3500)],
    'snare':  [_tone(240, 0.5, 25, 110, 0.6), _noise(220, 1.0, HPF, 1200)],
    'rim':    [_tone(1900, 0, 1, 20, 0.7)],
    'clap':   [_noise(0, 1.0, BPF, 1300, 2,
                      '0,1,12,0.5,24,1,36,0.4,50,0.85,300,0,20,0')],
    'chh':    [_noise(55, 0.8, HPF, 7800)],
    'ohh':    [_noise(0, 0.8, HPF, 7200, None, '0,1,120,0.5,500,0,25,0')],
    'tomlo':  [_tone(95, 0.8, 90, 300, 0.9), _noise(25, 0.2, LPF, 1500)],
    'tommid': [_tone(140, 0.8, 75, 260, 0.9), _noise(25, 0.2, LPF, 1900)],
    'tomhi':  [_tone(190, 0.8, 60, 220, 0.9), _noise(25, 0.2, LPF, 2400)],
    'crash':  [_noise(0, 0.85, HPF, 5200, None, '0,1,350,0.4,1600,0,40,0'),
               _tone(6800, 0, 1, 1000, 0.12)],
    'ride':   [_noise(0, 0.5, HPF, 8600, None, '0,0.8,250,0.35,1200,0,40,0'),
               _tone(6000, 0, 1, 800, 0.18)],
}

TR606 = {    # the thin buzzy little brother
    'kick':   [_tone(62, 1.2, 25, 240, 1.0)],
    'snare':  [_tone(200, 0.4, 18, 80, 0.5), _noise(150, 0.9, HPF, 2200)],
    'chh':    [_noise(35, 0.7, HPF, 8500)],
    'ohh':    [_noise(0, 0.7, HPF, 8000, None, '0,1,80,0.4,350,0,25,0')],
    'tomlo':  [_tone(90, 0.6, 70, 260, 0.85)],
    'tomhi':  [_tone(150, 0.6, 55, 210, 0.85)],
    'crash':  [_noise(0, 0.7, HPF, 6000, None, '0,1,250,0.4,1200,0,40,0')],
}

SIMMONS = {  # SDS-V: the 80s hexagons -- huge pitch sweeps + noise crack
    'kick':   [_tone(55, 2.0, 120, 450, 1.0), _noise(30, 0.5, LPF, 2800)],
    'snare':  [_tone(180, 1.2, 80, 220, 0.8), _noise(260, 0.9, HPF, 900)],
    'tomlo':  [_tone(70, 1.5, 200, 500, 0.95), _noise(40, 0.3, LPF, 1400)],
    'tommid': [_tone(110, 1.5, 170, 420, 0.95), _noise(40, 0.3, LPF, 1800)],
    'tomhi':  [_tone(160, 1.5, 140, 360, 0.95), _noise(40, 0.3, LPF, 2300)],
    'chh':    [_noise(45, 0.65, HPF, 8200)],
    'ohh':    [_noise(0, 0.65, HPF, 7600, None, '0,1,100,0.4,420,0,25,0')],
    'clap':   [_noise(0, 0.9, BPF, 1200, 2,
                      '0,1,11,0.4,22,0.9,33,0.35,46,0.8,280,0,20,0')],
}

RECIPES = [('tr808syn', 'TR-808', mk.TR808), ('tr909syn', 'TR-909', TR909),
           ('tr606syn', 'TR-606', TR606), ('simmons', 'SDS-V', SIMMONS)]

KIT_NAMES = {'cr78': 'CR-78', 'cr8000': 'CR-8000', 'tr606': 'TR-606 mda',
             'tr77': 'TR-77', 'tr808': 'TR-808 mda', 'tr909': 'TR-909 mda',
             'linn': 'LinnDrum', 'farfisa': 'Farfisa', 'latin': 'Latin',
             'acoustic': 'Acoustic', 'electro': 'Electro', 'hats': 'Hats',
             'misc_hats': 'Hats B', 'r_n_b': 'R&B', 'rnb': 'R&B 2',
             'tr808syn': 'TR-808 recipe', 'tr909syn': 'TR-909 recipe',
             'tr606syn': 'TR-606 recipe', 'simmons': 'Simmons SDS-V', 'partials808': 'TR-808 partials'}


def main():
    hits, packs, bad = mk.harvest_hits()
    # content-classification fallback
    reclassified = 0
    for key, h in hits.items():
        pack = h['pack']
        if pack in NON_DRUM:
            continue
        in_roles = any(key in v for r, v in packs[pack].items() if r != '_all')
        if in_roles:
            continue
        role = classify_content(h['oscs'])
        if role:
            packs[pack].setdefault(role, []).append(key)
            reclassified += 1

    # partials-resynthesized hits (ready patch strings)
    try:
        pj = json.load(open(os.path.join(mk.HERE, 'partials808.json')))
        packs['partials808'] = {}
        for role, h in pj.items():
            key = 'partials808/%s' % role
            hits[key] = {'name': h['name'], 'pack': 'partials808',
                         'patch_string': h['patch_string']}
            packs['partials808'][role] = [key]
            packs['partials808'].setdefault('_all', []).append(key)
    except (OSError, ValueError):
        pass

    # recipe kits join the corpus as packs
    for pkey, disp, table in RECIPES:
        packs[pkey] = {}
        for role, oscs in table.items():
            key = '%s/%s' % (pkey, role)
            hits[key] = {'name': '%s %s' % (disp, role), 'pack': pkey,
                         'oscs': oscs}
            packs[pkey][role] = [key]
            packs[pkey].setdefault('_all', []).append(key)

    # donor pools for missing roles
    donors = {}
    for role, note, _ in mk.ROLES:
        for src_pack in (('hats',) if role in HATS else ()) + (
                'tr808syn', 'tr909syn', 'misc_perc', 'misc'):
            pool = packs.get(src_pack, {}).get(role)
            if pool:
                donors[role] = pool[0]
                break

    def build_kit(roles_map, variant=0):
        note_map, own = {}, 0
        for role, note, _ in mk.ROLES:
            pool = roles_map.get(role)
            if pool:
                note_map[str(note)] = pool[variant % len(pool)]
                own += 1
            elif role in CORE + HATS and role in donors:
                note_map[str(note)] = donors[role]
        return note_map, own

    kits = {}
    for pack, roles in sorted(packs.items()):
        if pack in NON_DRUM or pack in COMMUNITY:
            continue
        have = set(roles) - {'_all'}
        if len(have) < 3:
            continue
        nm, own = build_kit(roles)
        if own < 3:
            continue
        disp = KIT_NAMES.get(pack, pack.replace('_', ' '))
        kits[pack] = {'name': disp, 'notes': nm}
        if all(len(roles.get(r, ())) >= 2 for r in CORE):
            nm2, _ = build_kit(roles, variant=1)
            kits[pack + '_b'] = {'name': disp + ' B', 'notes': nm2}

    # community kits: deal complete kits from the combined misc pools
    pool = {}
    for pack in COMMUNITY:
        for role, keys in packs.get(pack, {}).items():
            if role != '_all':
                pool.setdefault(role, []).extend(keys)
    n_com = min(6, min((len(pool.get(r, ())) for r in CORE), default=0))
    for i in range(n_com):
        nm, own = build_kit(pool, variant=i)
        if own >= 4:
            kits['community_%c' % (97 + i)] = {
                'name': 'Community %c' % (65 + i), 'notes': nm}

    # SPLIT OUTPUT: a single 279KB on-device JSON parse starved the UI task
    # (watchdog reset during kit builds). index.json stays small; each pack's
    # hits load on demand.
    outdir = os.path.join(os.path.dirname(mk.OUT), 'synthkits_data')
    os.makedirs(outdir, exist_ok=True)
    for fn in os.listdir(outdir):
        os.remove(os.path.join(outdir, fn))
    by_pack = {}
    for key, h in hits.items():
        by_pack.setdefault(h['pack'], {})[key] = h
    for pack, ph in by_pack.items():
        with open(os.path.join(outdir, pack + '.json'), 'w', newline='\n') as f:
            json.dump(ph, f, separators=(',', ':'))
    index = {'version': 2, 'kits': kits,
             'packs': {p: r.get('_all', []) for p, r in packs.items()},
             'names': {k: h['name'] for k, h in hits.items()}}
    with open(os.path.join(outdir, 'index.json'), 'w', newline='\n') as f:
        json.dump(index, f, separators=(',', ':'))
    try:
        os.remove(mk.OUT)
    except OSError:
        pass
    print('hits: %d (%d unparsable, %d content-reclassified)'
          % (len(hits), bad, reclassified))
    print('kits: %d' % len(kits))
    total = sum(os.path.getsize(os.path.join(outdir, fn))
                for fn in os.listdir(outdir))
    print('split: %d files, %d KB total, index %d KB'
          % (len(os.listdir(outdir)), total // 1024,
             os.path.getsize(os.path.join(outdir, 'index.json')) // 1024))


if __name__ == '__main__':
    sys.exit(main())
