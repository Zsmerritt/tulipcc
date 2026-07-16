#!/usr/bin/env python3
"""Generate deck/synthkits.json (hit corpus + kit role maps) from the
harvested .ds patches plus the Synth-Secrets TR-808 baseline recipes.

Run from tools/drumsynth/. Output: ../../deck/synthkits.json consumed by
deck/synthkits.py (thin API). Every hit is <=2 AMY oscs (tone + noise) --
see ds2amy.py for the mapping and its fidelity notes."""
import json
import os
import re
import sys

import ds2amy

HERE = os.path.dirname(os.path.abspath(__file__))
PATCHES = os.path.join(HERE, 'patches')
OUT = os.path.join(HERE, '..', '..', 'deck', 'synthkits.json')

# GM drum note per role + patterns over the space-normalized filename.
# ORDER MATTERS: specific before generic (hat o before the hihat catch-all,
# bongo l before bongo, tom lo/hi before the bare-tom fallback).
ROLES = [
    ('kick',    36, r'kick|kck|\bbd\b|bass ?drum|bdrum|\bkd\b|\bk\b'),
    ('snare',   38, r'snare|snr|\bsd\b'),
    ('rim',     37, r'rim|stick|\brs\b'),
    ('clap',    39, r'clap|\bcp\b'),
    ('phh',     44, r'pedal|phh'),
    ('ohh',     46, r'hat ?o|\boh\b|open|hho|ophat'),
    ('chh',     42, r'hat ?c|\bch\b|closed|clsd|chh|hhc|clhat|hi ?hat|\bhh\b|hat\b'),
    ('tomlo',   45, r'tom ?(l\b|lo|low|1)|lowtom|\blt\b'),
    ('tomhi',   50, r'tom ?(h\b|hi|3)|hitom|\bht\b'),
    ('tommid',  47, r'tom'),
    ('crash',   49, r'crash|cymbal|\bcym\b'),
    ('ride',    51, r'ride|\brd\b'),
    ('cowbell', 56, r'cowbell|cow|agogo|\bcb\b'),
    ('tamb',    54, r'tamb'),
    ('shaker',  70, r'shaker|maraca|cabasa|\bshk\b|\bmrc\b|\bcab\b'),
    ('congalo', 64, r'(bongo|conga) ?l\b|tumba'),
    ('congahi', 63, r'(bongo|conga) ?h?\b|quinto|cnga|\bcga\b'),
    ('claves',  75, r'clave|woodblock|\bblock\b|\bclv\b'),
    ('guiro',   73, r'guiro|\bcui\b'),
    ('perc',    60, r'perc|\bprc\b'),
]


def classify(name):
    n = re.sub(r'[_\-.]+', ' ', name.lower())
    n = re.sub(r'\b[0-9a-f]{6}\b', '', n)     # strip dedupe hash suffixes
    for role, note, pat in ROLES:
        if re.search(pat, n):
            return role, note
    return None, None


def harvest_hits():
    hits = {}          # hit_key -> {'name', 'pack', 'oscs'}
    packs = {}         # pack -> {role: [hit_key, ...]} + ['all']
    bad = 0
    for pack in sorted(os.listdir(PATCHES)):
        pdir = os.path.join(PATCHES, pack)
        if not os.path.isdir(pdir):
            continue
        packs[pack] = {}
        for fn in sorted(os.listdir(pdir)):
            if not fn.lower().endswith('.ds'):
                continue
            base = os.path.splitext(fn)[0]
            key = '%s/%s' % (pack, base)
            try:
                hit = ds2amy.convert(ds2amy.parse_ds(os.path.join(pdir, fn)), base)
            except Exception:
                bad += 1
                continue
            hits[key] = {'name': base, 'pack': pack, 'oscs': hit['oscs']}
            role, note = classify(base)
            if role:
                packs[pack].setdefault(role, []).append(key)
            packs[pack].setdefault('_all', []).append(key)
    return hits, packs, bad


# ---- Synth-Secrets TR-808 baseline (tuned by the published numbers) ---------
S, P, N = ds2amy.SINE, ds2amy.PULSE, ds2amy.NOISE
LPF, BPF, HPF = ds2amy.FILTER_LPF, ds2amy.FILTER_BPF, ds2amy.FILTER_HPF

TR808 = {
    'kick': [   # 55Hz sine, pitch drop from ~2x, click transient
        {'wave': S, 'freq': '55,0,0,0,1', 'bp1': '0,1,60,0,1,0',
         'bp0': '0,1,450,0,25,0', 'amp': '1,0,1,1'},
        {'wave': N, 'bp0': '0,1,15,0,10,0', 'amp': '0.25,0,1,1',
         'filter_type': LPF, 'filter_freq': '2500'}],
    'snare': [  # ~180Hz body + snappy highpassed noise
        {'wave': S, 'freq': '180,0,0,0,0.6', 'bp1': '0,1,40,0,1,0',
         'bp0': '0,1,120,0,20,0', 'amp': '0.8,0,1,1'},
        {'wave': N, 'bp0': '0,1,180,0,20,0', 'amp': '0.9,0,1,1',
         'filter_type': HPF, 'filter_freq': '1800'}],
    'rim': [
        {'wave': S, 'freq': '1700,0', 'bp0': '0,1,25,0,6,0', 'amp': '0.7,0,1,1'},
        {'wave': N, 'bp0': '0,1,12,0,6,0', 'amp': '0.4,0,1,1',
         'filter_type': BPF, 'filter_freq': '3400', 'resonance': '3'}],
    'clap': [   # noise with the 808's retrigger burst then decay
        {'wave': N, 'bp0': '0,1,10,0.4,20,0.9,30,0.35,42,0.8,320,0,20,0',
         'amp': '1,0,1,1', 'filter_type': BPF, 'filter_freq': '1100',
         'resonance': '2'}],
    'chh': [
        {'wave': N, 'bp0': '0,1,40,0,8,0', 'amp': '0.7,0,1,1',
         'filter_type': HPF, 'filter_freq': '7000'}],
    'phh': [
        {'wave': N, 'bp0': '0,1,25,0,8,0', 'amp': '0.55,0,1,1',
         'filter_type': HPF, 'filter_freq': '7500'}],
    'ohh': [
        {'wave': N, 'bp0': '0,1,90,0.4,420,0,30,0', 'amp': '0.7,0,1,1',
         'filter_type': HPF, 'filter_freq': '6500'}],
    'tomlo': [
        {'wave': S, 'freq': '80,0,0,0,0.7', 'bp1': '0,1,110,0,1,0',
         'bp0': '0,1,330,0,25,0', 'amp': '0.9,0,1,1'},
        {'wave': N, 'bp0': '0,1,20,0,8,0', 'amp': '0.15,0,1,1',
         'filter_type': LPF, 'filter_freq': '1200'}],
    'tommid': [
        {'wave': S, 'freq': '120,0,0,0,0.7', 'bp1': '0,1,90,0,1,0',
         'bp0': '0,1,280,0,25,0', 'amp': '0.9,0,1,1'},
        {'wave': N, 'bp0': '0,1,20,0,8,0', 'amp': '0.15,0,1,1',
         'filter_type': LPF, 'filter_freq': '1600'}],
    'tomhi': [
        {'wave': S, 'freq': '165,0,0,0,0.7', 'bp1': '0,1,70,0,1,0',
         'bp0': '0,1,240,0,25,0', 'amp': '0.9,0,1,1'},
        {'wave': N, 'bp0': '0,1,20,0,8,0', 'amp': '0.15,0,1,1',
         'filter_type': LPF, 'filter_freq': '2000'}],
    'congahi': [
        {'wave': S, 'freq': '370,0,0,0,0.35', 'bp1': '0,1,25,0,1,0',
         'bp0': '0,1,170,0,20,0', 'amp': '0.85,0,1,1'}],
    'congalo': [
        {'wave': S, 'freq': '220,0,0,0,0.35', 'bp1': '0,1,30,0,1,0',
         'bp0': '0,1,210,0,20,0', 'amp': '0.85,0,1,1'}],
    'claves': [
        {'wave': S, 'freq': '2500,0', 'bp0': '0,1,30,0,6,0', 'amp': '0.8,0,1,1'}],
    'cowbell': [  # the famous 540 + 800 Hz pulse pair through a bandpass
        {'wave': P, 'freq': '540,0', 'duty': '0.5',
         'bp0': '0,1,25,0.4,380,0,20,0', 'amp': '0.6,0,1,1',
         'filter_type': BPF, 'filter_freq': '2640', 'resonance': '2'},
        {'wave': P, 'freq': '800,0', 'duty': '0.5',
         'bp0': '0,1,25,0.4,380,0,20,0', 'amp': '0.5,0,1,1',
         'filter_type': BPF, 'filter_freq': '2640', 'resonance': '2'}],
    'crash': [  # honest approximation; cymbals are the sampled exception
        {'wave': N, 'bp0': '0,1,300,0.5,1500,0,40,0', 'amp': '0.8,0,1,1',
         'filter_type': HPF, 'filter_freq': '5000'},
        {'wave': S, 'freq': '6300,0', 'bp0': '0,1,900,0,40,0',
         'amp': '0.15,0,1,1'}],
    'ride': [
        {'wave': N, 'bp0': '0,0.8,200,0.4,1100,0,40,0', 'amp': '0.5,0,1,1',
         'filter_type': HPF, 'filter_freq': '8000'},
        {'wave': S, 'freq': '5600,0', 'bp0': '0,0.6,700,0,40,0',
         'amp': '0.2,0,1,1'}],
    'shaker': [
        {'wave': N, 'bp0': '0,0.4,15,1,80,0,15,0', 'amp': '0.6,0,1,1',
         'filter_type': HPF, 'filter_freq': '9000'}],
    'tamb': [
        {'wave': N, 'bp0': '0,1,60,0.3,160,0,20,0', 'amp': '0.6,0,1,1',
         'filter_type': HPF, 'filter_freq': '8000'},
        {'wave': S, 'freq': '7600,0', 'bp0': '0,0.5,120,0,20,0',
         'amp': '0.2,0,1,1'}],
}


def main():
    hits, packs, bad = harvest_hits()
    # partials-resynthesized 808 metallic hits (partials808.py): stored as
    # ready patch strings, not osc dicts -- synthkits passes them through
    try:
        pj = json.load(open(os.path.join(HERE, 'partials808.json')))
        packs['partials808'] = {}
        for role, h in pj.items():
            key = 'partials808/%s' % role
            hits[key] = {'name': h['name'], 'pack': 'partials808',
                         'patch_string': h['patch_string']}
            packs['partials808'][role] = [key]
            packs['partials808'].setdefault('_all', []).append(key)
    except (OSError, ValueError):
        pass
    # baseline kit: its hits join the corpus under the 'tr808syn' pack
    packs['tr808syn'] = {}
    for role, oscs in TR808.items():
        key = 'tr808syn/%s' % role
        hits[key] = {'name': '808 %s (synth)' % role, 'pack': 'tr808syn',
                     'oscs': oscs}
        packs['tr808syn'][role] = [key]
        packs['tr808syn'].setdefault('_all', []).append(key)

    # kits: any pack with a kick + snare + one hat makes a kit; roles pick
    # their first classified hit, remaining hits stay reachable via the corpus
    kits = {}
    for pack, roles in sorted(packs.items()):
        have = set(roles) - {'_all'}
        if not {'kick', 'snare'} <= have or not ({'chh', 'ohh'} & have):
            continue
        note_map = {}
        for role, note, _ in ROLES:
            if role in roles:
                note_map[str(note)] = roles[role][0]
        kits[pack] = {'name': pack.replace('_', ' '), 'notes': note_map}

    data = {'version': 1, 'hits': hits, 'kits': kits,
            'packs': {p: r.get('_all', []) for p, r in packs.items()}}
    with open(OUT, 'w', newline='\n') as f:
        json.dump(data, f, separators=(',', ':'))
    print('hits: %d (%d unparsable dropped)' % (len(hits), bad))
    print('kits: %d -> %s' % (len(kits), ', '.join(sorted(kits))))
    print('json: %d KB' % (os.path.getsize(OUT) // 1024))


if __name__ == '__main__':
    sys.exit(main())
