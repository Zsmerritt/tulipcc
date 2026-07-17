"""Host tests for the GM rebake policy and the emitted table.

Pure Python: no SoundFont, numpy or resampy required. Run with:

    python -m pytest tools/gm/test_rebake_gm.py -q

The table tests read the checked-in amy/src/pcm_gm.h, so they guard the bank we
actually ship against the encoding rules in amy/src/pcm.c.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gm_policy as P
from rebake_gm import parse_header

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
HEADER = os.path.join(REPO, "amy", "src", "pcm_gm.h")
BLOB = os.path.join(REPO, "amy", "sounds", "gm", "fonts.bin")


# --------------------------------------------------------------------------
# clamp_loop: the rule that makes a restored loop actually engage.
# --------------------------------------------------------------------------

def test_clamp_loop_forces_loopend_below_length():
    # The Grand Piano case: SF2 end_loop == duration, so a naive transplant
    # yields loopend == length, which pcm.c's end-of-sample test kills first.
    ls, le = P.clamp_loop(44474, 21957, 44474)
    assert le == 44473
    assert le < 44474
    assert not P.is_oneshot(44474, ls, le)


def test_clamp_loop_never_collides_with_the_oneshot_sentinel():
    # A sample whose SF2 loop spans the whole sample is legitimate -- Birds
    # loops [7, 51513] of 51520 frames -- so it must stay representable. What
    # must never happen is emitting loop_len >= length, which pcm.c reads as a
    # one-shot and refuses to wrap.
    ls, le = P.clamp_loop(1000, 0, 1000)
    assert (ls, le) == (0, 999)
    assert not P.is_oneshot(1000, ls, le)
    # The one-shot sentinel stays distinct: loopend == length, not length-1.
    assert P.oneshot_loop(1000) == (0, 1000)
    assert P.is_oneshot(1000, *P.oneshot_loop(1000))


def test_clamp_loop_rejects_degenerate():
    assert P.clamp_loop(1000, 500, 500) is None
    assert P.clamp_loop(1000, 900, 800) is None
    assert P.clamp_loop(1, 0, 1) is None
    assert P.clamp_loop(0, 0, 0) is None


def test_clamp_loop_preserves_a_normal_loop():
    assert P.clamp_loop(1000, 400, 900) == (400, 900)


def test_oneshot_sentinel_is_refused_by_the_pcm_c_rule():
    ls, le = P.oneshot_loop(5000)
    assert (ls, le) == (0, 5000)
    assert P.is_oneshot(5000, ls, le)  # loop_len == length -> no wrap


def test_is_oneshot_matches_pcm_c_guard():
    # pcm.c: wrap iff loop_len > 0 && loop_len < length
    for length, ls, le in [(100, 0, 100), (100, 0, 0), (100, 50, 50), (100, 60, 40)]:
        assert P.is_oneshot(length, ls, le)
    for length, ls, le in [(100, 10, 90), (100, 1, 99), (100, 50, 99)]:
        assert not P.is_oneshot(length, ls, le)


def test_legacy_oneshot_length_reproduces_the_17500_bug():
    # The shipped bank truncated un-looped samples to 17500/22050 of their
    # length. Spot-check against real shipped values (Bongo Tone, Castanets).
    assert P.legacy_oneshot_length(1457) == 1156
    assert P.legacy_oneshot_length(821) == 652


# --------------------------------------------------------------------------
# plan_entry: the restore policy.
# --------------------------------------------------------------------------

def test_peak_norm_constant_matches_the_shipped_bank():
    # 146/160 shipped presets peak at exactly this value; it is the fingerprint
    # of the legacy bake's normalization and the restored presets must match it
    # or they will sit at the wrong level relative to the rest of the bank.
    assert int(P.PEAK_NORM * 32768) == P.PEAK_INT16 == 30146


def test_restored_presets_are_peak_normalized(table):
    # Tier A/B presets are re-derived from the SF2, so they must land on the
    # same 0.92 full-scale ceiling as the presets copied from the old blob.
    import struct
    blob = open(BLOB, "rb").read()
    for idx in sorted(set(P.TIER_A) | set(P.TIER_B)):
        r = table[idx]
        raw = blob[r.offset * 2:(r.offset + r.length) * 2]
        vals = struct.unpack("<%dh" % r.length, raw)
        peak = max(abs(v) for v in vals)
        # Peak may be below the ceiling if the loudest moment fell outside the
        # kept region, but it must never exceed it.
        assert peak <= P.PEAK_INT16, "%s peaks at %d" % (r.name, peak)


def test_plan_entry_tier_a_restores_a_loop():
    length, ls, le = P.plan_entry(0, 44474, 21957, 44474, sf2_looped=True)
    assert length == 44474
    assert not P.is_oneshot(length, ls, le)


def test_plan_entry_tier_a_keeps_the_50ms_tail_rule():
    # length is capped at loopend + 1102 so we do not pay for a long unused tail.
    length, ls, le = P.plan_entry(0, 99999, 10000, 20000, sf2_looped=True)
    assert length == 20000 + P.TAIL_FRAMES


def test_plan_entry_tier_a_without_sf2_loop_is_an_error():
    with pytest.raises(ValueError):
        P.plan_entry(0, 44474, 0, 0, sf2_looped=False)


def test_plan_entry_tier_b_stays_a_oneshot():
    length, ls, le = P.plan_entry(82, 44309, 100, 200, sf2_looped=False)
    assert length == 44309
    assert P.is_oneshot(length, ls, le)


def test_plan_entry_returns_none_for_untouched_presets():
    assert P.plan_entry(5, 1000, 100, 900, sf2_looped=True) is None


def test_policy_tiers_are_disjoint():
    assert not (set(P.TIER_A) & set(P.TIER_B))
    assert not (set(P.TIER_A) & set(P.TIER_C_EXCLUDED))
    assert not (set(P.TIER_B) & set(P.TIER_C_EXCLUDED))


# --------------------------------------------------------------------------
# The shipped table must obey the pcm.c contract.
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def table():
    rows, base = parse_header(HEADER)
    return rows


def test_table_round_trips_through_the_emitter(tmp_path):
    from rebake_gm import write_header
    rows, base = parse_header(HEADER)
    out = str(tmp_path / "pcm_gm.h")
    total = rows[-1].offset + rows[-1].length
    write_header(out, rows, base, total, P.SAMPLE_RATE)
    again, base2 = parse_header(out)
    assert base2 == base
    assert again == rows


def test_no_preset_has_loopend_past_length(table):
    bad = [r.name for r in table if r.loopend > r.length]
    assert bad == []


def test_no_preset_has_loopstart_past_length(table):
    bad = [r.name for r in table if r.loopstart > r.length]
    assert bad == []


def test_offsets_are_contiguous_and_start_at_zero(table):
    assert table[0].offset == 0
    for prev, cur in zip(table, table[1:]):
        assert cur.offset == prev.offset + prev.length, cur.name


def test_table_matches_blob_size(table):
    total = table[-1].offset + table[-1].length
    assert os.path.getsize(BLOB) == total * 2


def test_declared_frames_match_the_table(table):
    total = table[-1].offset + table[-1].length
    src = open(HEADER).read()
    assert "#define GM_BIN_FRAMES %d\n" % total in src
    assert "#define GM_NUM_SAMPLES %d\n" % len(table) in src


def test_bank_fits_the_generaluser_slice(table):
    # The fonts partition gives GeneralUser 0..0x4B0000; the big bank starts there.
    total = table[-1].offset + table[-1].length
    assert total * 2 <= 0x4B0000


def test_tier_a_presets_actually_loop(table):
    # The whole point of the rebake: these must not encode as one-shots.
    for idx in P.TIER_A:
        r = table[idx]
        assert not P.is_oneshot(r.length, r.loopstart, r.loopend), r.name


def test_grand_piano_is_restored(table):
    r = table[0]
    assert r.name.startswith("Grand Piano")
    assert r.length == 44474, "expected the real 2.02s length, got %d" % r.length
    assert (r.loopstart, r.loopend) == (21957, 44473)
    assert not P.is_oneshot(r.length, r.loopstart, r.loopend)


def test_drum_presets_stay_oneshots(table):
    # Looping these would drone: AMY does not apply the SF2 volume envelope.
    for idx in (119, 122, 123, 124, 126, 128, 130, 132, 134, 156):
        r = table[idx]
        assert P.is_oneshot(r.length, r.loopstart, r.loopend), r.name
