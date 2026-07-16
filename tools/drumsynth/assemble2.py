#!/usr/bin/env python3
"""Kit assembly v2 over make_synthkits' harvest: content-based role
classification (unlocks the community packs whose creative names defeat the
filename classifier), donor roles (a pack missing hats borrows them), variant
kits (packs with 2+ choices per core role yield an A and a B), community
kits dealt from the combined misc pools, and three more by-the-numbers
recipe classics (909, 606, Simmons SDS-V) beside the 808.

Writes the SPLIT deck/synthkits_data/ layout (index.json + one JSON
per pack) -- what the device actually loads. Run from tools/drumsynth/."""
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

def _acid(pitch, sweep_oct=2.0, dur=150, res=6):
    """TB-303-style stab: saw into a resonant lowpass whose cutoff rides EG1
    down from a sweep -- the acid squelch."""
    return {'wave': ds2amy.SAW_DOWN, 'freq': '%g,0' % pitch,
            'bp0': '0,1,%d,0,15,0' % dur, 'amp': '0.9,0,1,1',
            'filter_type': LPF,
            'filter_freq': '%g,0,0,0,%g' % (pitch * 3, sweep_oct),
            'bp1': '0,1,%d,0,1,0' % max(40, dur - 30),
            'resonance': '%g' % res}


TB303 = {   # not a drum machine -- an acid kit: stabs at pad pitches, 606 hats
    'kick':   [_acid(55, 2.5, 260, 7)],
    'snare':  [_acid(110, 3.0, 120, 8)],
    'tomlo':  [_acid(82, 2.0, 200)],
    'tommid': [_acid(110, 2.0, 180)],
    'tomhi':  [_acid(147, 2.0, 160)],
    'congahi': [_acid(220, 1.5, 140)],
    'congalo': [_acid(165, 1.5, 160)],
    'perc':   [_acid(330, 2.5, 100, 8)],
    'chh':    [_noise(35, 0.6, HPF, 8500)],
    'ohh':    [_noise(0, 0.6, HPF, 8000, None, '0,1,80,0.4,350,0,25,0')],
}

TR707 = {   # punchy digital-era Roland: hard kick click, crisp everything
    'kick':   [_tone(58, 1.2, 20, 300, 1.0), _noise(12, 0.6, LPF, 4200)],
    'snare':  [_tone(210, 0.4, 15, 90, 0.55), _noise(190, 1.0, HPF, 1600)],
    'rim':    [_tone(2100, 0, 1, 18, 0.7)],
    'clap':   [_noise(0, 1.0, BPF, 1400, 2.5,
                      '0,1,10,0.5,20,1,30,0.4,42,0.8,260,0,20,0')],
    'chh':    [_noise(40, 0.85, HPF, 8800)],
    'ohh':    [_noise(0, 0.85, HPF, 8200, None, '0,1,100,0.5,430,0,25,0')],
    'tomlo':  [_tone(100, 0.7, 70, 260, 0.9)],
    'tommid': [_tone(150, 0.7, 60, 230, 0.9)],
    'tomhi':  [_tone(200, 0.7, 50, 200, 0.9)],
    'cowbell': [{'wave': P, 'freq': '560,0', 'duty': '0.5',
                 'bp0': '0,1,20,0.4,320,0,20,0', 'amp': '0.55,0,1,1',
                 'filter_type': BPF, 'filter_freq': '2800', 'resonance': '2'},
                {'wave': P, 'freq': '835,0', 'duty': '0.5',
                 'bp0': '0,1,20,0.4,320,0,20,0', 'amp': '0.45,0,1,1',
                 'filter_type': BPF, 'filter_freq': '2800', 'resonance': '2'}],
    'crash':  [_noise(0, 0.8, HPF, 5600, None, '0,1,320,0.4,1400,0,40,0')],
    'ride':   [_noise(0, 0.5, HPF, 8800, None, '0,0.8,220,0.35,1100,0,40,0')],
}

TR727 = {   # the latin sibling: all tuned percussion
    'congahi': [_tone(390, 0.35, 22, 160, 0.9)],
    'congalo': [_tone(230, 0.35, 28, 200, 0.9)],
    'tomlo':  [_tone(150, 0.5, 40, 220, 0.85)],   # timbale-ish
    'tommid': [_tone(200, 0.5, 35, 190, 0.85)],
    'cowbell': [{'wave': P, 'freq': '540,0', 'duty': '0.5',
                 'bp0': '0,1,22,0.4,300,0,20,0', 'amp': '0.55,0,1,1',
                 'filter_type': BPF, 'filter_freq': '2640', 'resonance': '2'}],
    'claves': [_tone(2600, 0, 1, 28, 0.8)],
    'shaker': [_noise(0, 0.6, HPF, 9200, None, '0,0.4,14,1,75,0,15,0')],
    'guiro':  [_noise(0, 0.55, BPF, 2600, 3, '0,0.3,30,1,60,0.3,90,1,140,0,20,0')],
    'tamb':   [_noise(0, 0.6, HPF, 8200, None, '0,1,55,0.3,150,0,20,0')],
    'kick':   [_tone(75, 0.6, 30, 200, 0.8)],     # low conga as kick stand-in
    'snare':  [_tone(240, 0.4, 20, 120, 0.6), _noise(120, 0.7, HPF, 2400)],
    'chh':    [_noise(35, 0.6, HPF, 9000)],
}

LM1 = {     # Linn LM-1 character: warm, fat, famously cymbal-less
    'kick':   [_tone(52, 0.9, 30, 330, 1.0), _noise(15, 0.35, LPF, 3000)],
    'snare':  [_tone(190, 0.5, 25, 130, 0.7), _noise(200, 0.85, LPF, 5200)],
    'rim':    [_tone(1500, 0, 1, 22, 0.65)],
    'clap':   [_noise(0, 0.95, BPF, 1100, 2,
                      '0,1,12,0.5,24,1,38,0.4,52,0.8,300,0,20,0')],
    'chh':    [_noise(45, 0.7, HPF, 7400)],
    'ohh':    [_noise(0, 0.7, HPF, 7000, None, '0,1,110,0.45,430,0,25,0')],
    'tomlo':  [_tone(90, 0.7, 80, 300, 0.9)],
    'tommid': [_tone(130, 0.7, 70, 270, 0.9)],
    'tomhi':  [_tone(175, 0.7, 60, 240, 0.9)],
    'congahi': [_tone(360, 0.3, 25, 170, 0.85)],
    'congalo': [_tone(215, 0.3, 30, 200, 0.85)],
    'shaker': [_noise(0, 0.55, HPF, 8800, None, '0,0.4,15,1,85,0,15,0')],
    'tamb':   [_noise(0, 0.6, HPF, 7800, None, '0,1,60,0.3,160,0,20,0')],
}

DMX = {     # Oberheim DMX: tight, cracking, hip-hop backbone
    'kick':   [_tone(60, 1.0, 22, 260, 1.0), _noise(10, 0.5, LPF, 3800)],
    'snare':  [_tone(220, 0.6, 20, 100, 0.6), _noise(170, 1.0, BPF, 1700, 1.5)],
    'rim':    [_tone(1800, 0, 1, 20, 0.7)],
    'clap':   [_noise(0, 1.0, BPF, 1250, 2,
                      '0,1,11,0.5,22,1,34,0.4,48,0.8,270,0,20,0')],
    'chh':    [_noise(38, 0.8, HPF, 8300)],
    'ohh':    [_noise(0, 0.8, HPF, 7800, None, '0,1,95,0.45,400,0,25,0')],
    'tomlo':  [_tone(95, 0.9, 75, 280, 0.95)],
    'tommid': [_tone(140, 0.9, 65, 250, 0.95)],
    'tomhi':  [_tone(190, 0.9, 55, 220, 0.95)],
    'crash':  [_noise(0, 0.8, HPF, 5400, None, '0,1,330,0.4,1500,0,40,0')],
    'ride':   [_noise(0, 0.5, HPF, 8600, None, '0,0.8,240,0.35,1150,0,40,0')],
    'shaker': [_noise(0, 0.55, HPF, 9000, None, '0,0.4,15,1,80,0,15,0')],
}

TR505 = {   # budget digital: thin, clicky, charming
    'kick':   [_tone(68, 1.0, 18, 200, 0.95)],
    'snare':  [_tone(230, 0.4, 15, 80, 0.5), _noise(140, 0.9, HPF, 2100)],
    'rim':    [_tone(2000, 0, 1, 16, 0.65)],
    'clap':   [_noise(0, 0.9, BPF, 1350, 2,
                      '0,1,10,0.5,20,0.9,32,0.4,44,0.75,230,0,20,0')],
    'chh':    [_noise(32, 0.75, HPF, 9200)],
    'ohh':    [_noise(0, 0.75, HPF, 8600, None, '0,1,85,0.4,340,0,25,0')],
    'tomlo':  [_tone(105, 0.6, 60, 220, 0.85)],
    'tomhi':  [_tone(185, 0.6, 45, 180, 0.85)],
    'cowbell': [{'wave': P, 'freq': '555,0', 'duty': '0.5',
                 'bp0': '0,1,18,0.4,280,0,20,0', 'amp': '0.5,0,1,1',
                 'filter_type': BPF, 'filter_freq': '2700', 'resonance': '2'}],
    'congahi': [_tone(370, 0.3, 20, 150, 0.8)],
    'congalo': [_tone(225, 0.3, 25, 180, 0.8)],
    'crash':  [_noise(0, 0.7, HPF, 6000, None, '0,1,280,0.4,1250,0,40,0')],
}

KR55 = {    # Korg preset box: soft, polite, ticky
    'kick':   [_tone(64, 0.7, 35, 240, 0.9)],
    'snare':  [_tone(200, 0.4, 22, 110, 0.5), _noise(160, 0.7, HPF, 2600)],
    'chh':    [_noise(28, 0.6, HPF, 9400)],
    'ohh':    [_noise(0, 0.6, HPF, 8800, None, '0,1,75,0.4,320,0,25,0')],
    'tomlo':  [_tone(98, 0.5, 55, 230, 0.8)],
    'tomhi':  [_tone(170, 0.5, 45, 190, 0.8)],
    'cowbell': [{'wave': P, 'freq': '530,0', 'duty': '0.5',
                 'bp0': '0,1,20,0.35,260,0,20,0', 'amp': '0.45,0,1,1',
                 'filter_type': BPF, 'filter_freq': '2500', 'resonance': '2'}],
    'claves': [_tone(2450, 0, 1, 26, 0.7)],
    'crash':  [_noise(0, 0.6, HPF, 6200, None, '0,1,240,0.4,1100,0,40,0')],
}

DR110 = {   # Boss Dr. Rhythm: the tssss machine
    'kick':   [_tone(66, 1.1, 20, 220, 0.95)],
    'snare':  [_tone(215, 0.5, 16, 85, 0.5), _noise(150, 0.95, HPF, 2300)],
    'clap':   [_noise(0, 0.9, BPF, 1300, 2,
                      '0,1,10,0.5,20,0.9,30,0.4,42,0.75,240,0,20,0')],
    'chh':    [_noise(30, 0.8, HPF, 9600)],
    'ohh':    [_noise(0, 0.8, HPF, 9000, None, '0,1,90,0.45,380,0,25,0')],
    'crash':  [_noise(0, 0.75, HPF, 5800, None, '0,1,300,0.4,1350,0,40,0')],
}

RZ1 = {     # Casio RZ-1: crunchy lo-fi digital
    'kick':   [_tone(62, 1.3, 16, 210, 1.0), _noise(8, 0.55, LPF, 4500)],
    'snare':  [_tone(235, 0.5, 14, 75, 0.55), _noise(130, 1.0, HPF, 1900)],
    'rim':    [_tone(2200, 0, 1, 14, 0.7)],
    'clap':   [_noise(0, 1.0, BPF, 1450, 2.5,
                      '0,1,9,0.5,18,1,28,0.4,40,0.8,220,0,20,0')],
    'chh':    [_noise(26, 0.85, HPF, 9800)],
    'ohh':    [_noise(0, 0.85, HPF, 9200, None, '0,1,80,0.4,330,0,25,0')],
    'tomlo':  [_tone(100, 0.8, 55, 230, 0.9)],
    'tomhi':  [_tone(180, 0.8, 42, 185, 0.9)],
    'cowbell': [{'wave': P, 'freq': '575,0', 'duty': '0.5',
                 'bp0': '0,1,16,0.4,260,0,20,0', 'amp': '0.5,0,1,1',
                 'filter_type': BPF, 'filter_freq': '2900', 'resonance': '2'}],
    'crash':  [_noise(0, 0.75, HPF, 5500, None, '0,1,290,0.4,1300,0,40,0')],
}

TR626 = {   # brighter 505 successor with more voices
    'kick':   [_tone(64, 1.1, 20, 240, 1.0)],
    'snare':  [_tone(225, 0.4, 16, 90, 0.55), _noise(160, 0.95, HPF, 2000)],
    'rim':    [_tone(2050, 0, 1, 18, 0.65)],
    'clap':   [_noise(0, 0.95, BPF, 1380, 2,
                      '0,1,10,0.5,20,0.95,31,0.4,44,0.8,250,0,20,0')],
    'chh':    [_noise(34, 0.8, HPF, 9000)],
    'ohh':    [_noise(0, 0.8, HPF, 8400, None, '0,1,90,0.4,360,0,25,0')],
    'tomlo':  [_tone(100, 0.7, 62, 240, 0.88)],
    'tommid': [_tone(145, 0.7, 55, 215, 0.88)],
    'tomhi':  [_tone(195, 0.7, 48, 190, 0.88)],
    'ride':   [_noise(0, 0.5, HPF, 8800, None, '0,0.8,230,0.35,1100,0,40,0')],
    'crash':  [_noise(0, 0.75, HPF, 5700, None, '0,1,310,0.4,1400,0,40,0')],
    'shaker': [_noise(0, 0.55, HPF, 9200, None, '0,0.4,14,1,78,0,15,0')],
    'congahi': [_tone(375, 0.3, 22, 155, 0.82)],
    'congalo': [_tone(228, 0.3, 26, 185, 0.82)],
}

RECIPES = [('tr808syn', 'TR-808', mk.TR808), ('tr909syn', 'TR-909', TR909),
           ('tr606syn', 'TR-606', TR606), ('simmons', 'SDS-V', SIMMONS),
           ('tb303', 'TB-303 Acid', TB303), ('tr707', 'TR-707', TR707),
           ('tr727', 'TR-727', TR727), ('lm1', 'LinnDrum LM-1', LM1),
           ('dmx', 'Oberheim DMX', DMX), ('tr505', 'TR-505', TR505),
           ('kr55', 'KR-55', KR55), ('dr110', 'DR-110', DR110),
           ('rz1', 'RZ-1', RZ1), ('tr626', 'TR-626', TR626)]

KIT_NAMES = {'cr78': 'CR-78', 'cr8000': 'CR-8000', 'tr606': 'TR-606 mda',
             'tr77': 'TR-77', 'tr808': 'TR-808 mda', 'tr909': 'TR-909 mda',
             'linn': 'LinnDrum', 'farfisa': 'Farfisa', 'latin': 'Latin',
             'acoustic': 'Acoustic', 'electro': 'Electro', 'hats': 'Hats',
             'misc_hats': 'Hats B', 'r_n_b': 'R&B', 'rnb': 'R&B 2',
             'tr808syn': 'TR-808 recipe', 'tr909syn': 'TR-909 recipe',
             'tr606syn': 'TR-606 recipe', 'simmons': 'Simmons SDS-V',
             'partials808': 'TR-808 partials', 'tb303': 'TB-303 Acid',
             'tr707': 'TR-707', 'tr727': 'TR-727', 'lm1': 'LinnDrum LM-1',
             'dmx': 'Oberheim DMX', 'tr505': 'TR-505', 'kr55': 'KR-55',
             'dr110': 'DR-110', 'rz1': 'RZ-1', 'tr626': 'TR-626'}


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

    # ---- content-hash dedupe + loudness normalization (review mystery 4) --
    # 1) Pool DEPTH drives variant fan-out, but the harvest keeps byte-
    #    identical .ds files under name_<hash> aliases -- tr909's "D" kit
    #    existed only because duplicates padded its pools, and its hit deal
    #    was an arbitrary re-index with no loudness relationship to A.
    #    Dedupe pools by CONTENT before anything counts them.
    seen_sig = {}
    dropped = 0
    for pack, roles in packs.items():
        for role, keys in list(roles.items()):
            if role == '_all':
                continue
            kept = []
            for k in keys:
                h = hits.get(k) or {}
                sig = json.dumps(h.get('oscs') or h.get('patch_string'),
                                 sort_keys=True)
                if seen_sig.setdefault((pack, role, sig), k) == k:
                    kept.append(k)
                else:
                    dropped += 1
            roles[role] = kept
    # 2) Normalize amplitude-quiet hits into [0.5, 1.0] peak BEFORE the
    #    deck's KIT_GAIN, so that gain is a policy trim, not a rescue.
    #    (Quietness from collapsed envelopes was the ds2amy E-13 sort bug,
    #    fixed there; this catches the level-shaped remainder.)
    boosted = 0
    for key, h in hits.items():
        oscs = h.get('oscs')
        if not oscs:
            continue
        score = 0.0
        for o in oscs:
            try:
                const = float(str(o.get('amp', '0')).split(',')[0])
            except ValueError:
                continue
            lv = []
            for x in str(o.get('bp0', '')).split(',')[1::2]:
                try:
                    lv.append(float(x))
                except ValueError:
                    pass
            env_pk = max(lv[:-1] or lv or [1.0])   # ignore the release pair
            score = max(score, const * env_pk)
        if 0 < score < 0.5:
            f = min(8.0, 0.8 / score)
            for o in oscs:
                if 'amp' in o:
                    p = str(o['amp']).split(',')
                    p[0] = ('%.3f' % (float(p[0]) * f)).rstrip('0').rstrip('.')
                    o['amp'] = ','.join(p)
            boosted += 1
    print('dedupe: %d duplicate pool entries dropped; %d quiet hits normalized'
          % (dropped, boosted))

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
        # variant fan-out: as many kits as the shallowest CORE pool supports
        # (capped at 4) -- each variant indexes one deeper into every pool
        depth = min(4, min(len(roles.get(r, ())) for r in CORE))
        for v in range(1, depth):
            nmv, _ = build_kit(roles, variant=v)
            if nmv != nm:
                kits['%s_%c' % (pack, 97 + v)] = {
                    'name': '%s %c' % (disp, 65 + v), 'notes': nmv}

    # community kits: deal complete kits from the combined misc pools
    pool = {}
    for pack in COMMUNITY:
        for role, keys in packs.get(pack, {}).items():
            if role != '_all':
                pool.setdefault(role, []).extend(keys)
    n_com = min(16, min((len(pool.get(r, ())) for r in CORE), default=0))
    for i in range(n_com):
        nm, own = build_kit(pool, variant=i)
        if own >= 4:
            kits['community_%c' % (97 + i)] = {
                'name': 'Community %c' % (65 + i), 'notes': nm}

    # hybrid kits: deal from EVERYTHING at once (named packs + community);
    # unequal pool lengths make each variant offset land on a fresh cross-
    # pack combination
    hybrid = {}
    for pack, roles in packs.items():
        if pack in NON_DRUM:
            continue
        for role, keys in roles.items():
            if role != '_all':
                hybrid.setdefault(role, []).extend(keys)
    for i in range(10):
        nm, own = build_kit(hybrid, variant=i * 7 + 3)   # stride past the
        if own >= 6:                                     # community deals
            kits['hybrid_%c' % (97 + i)] = {
                'name': 'Hybrid %c' % (65 + i), 'notes': nm}

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
