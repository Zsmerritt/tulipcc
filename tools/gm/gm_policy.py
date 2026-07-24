"""Rebake policy for the GeneralUser GM bank.

This module is pure data + pure functions so it can be unit-tested on the host
without a SoundFont, numpy or resampy present (see test_rebake_gm.py).

Background
----------
The shipped bank (amy/sounds/gm/fonts.bin + amy/src/pcm_gm.h) was produced by a
private `sf2_to_amy.py` (credited in pcm_gm.h to Leeman1982/gm2350; the script
itself was never published).  It was reverse-engineered from the shipped table
and GeneralUser GS v1.471; see tools/gm/README.md for the derivation and the
evidence.  The legacy bake is:

    audio  = resampy.resample(src, sample_rate, 22050)          # confirmed
    if the sample has a sustain loop (SF2 sampleModes == 1):
        length = min(full, loopend + 1102, 22747)               # 1102 = 50ms tail
        loop   = (loopstart, loopend)  -- unless the 22747 cap bound, in which
                 case the loop was dropped and the preset became a one-shot.
    else:
        length = min(round(full * 17500 / 22050), 19247)        # (bug: see below)
        loop   = (0, length)                                    # one-shot sentinel

Two defects follow from that:

1. The hard duration caps (22747 frames = 1.0316 s for looped samples, 19247 =
   0.8729 s for un-looped ones).  When the cap bound a looped sample the loop
   was discarded, so e.g. a held Grand Piano note stops dead at 1.03 s.

2. The one-shot length was computed at 17500 Hz while the audio was resampled to
   22050 Hz, so every un-looped sample is truncated to 17500/22050 = 79.365% of
   its real length.  We do NOT fix this globally -- it costs ~0.5 MB across ~35
   percussion presets for a mostly-inaudible decay tail -- but we do fix it for
   the melodic presets where the cap is audible (see TIER_B).

Encoding contract (amy/src/pcm.c)
---------------------------------
`pcm_map_t` is {offset, length, loopstart, loopend, midinote}.  The renderer:

    if (base_index >= sample_length) -> note ends          (sample_length == length)
    else if (feedback > 0 && base_index >= loopend):
        loop_len = loopend - loopstart;
        if (loop_len > 0 && loop_len < length) -> wrap back by loop_len

Two consequences that drive this module:

  * A one-shot is encoded as loopstart=0, loopend=length, so loop_len == length,
    which fails `loop_len < length` and correctly refuses to wrap.  Keep that.

  * For a loop to *ever* engage, loopend must be strictly < length: the
    end-of-sample test runs first, so loopend == length means the note ends
    before the wrap check is reached.  GeneralUser's Grand Piano has
    end_loop == duration exactly, so restoring the full length alone is NOT
    enough -- loopend must be clamped to length-1.  This is why `clamp_loop`
    exists and why it is the single most important function here.
"""

# Frames of tail kept after loopend by the legacy bake (50 ms @ 22050).
TAIL_FRAMES = 1102
SAMPLE_RATE = 22050

# The legacy bake peak-normalized every sample to 0.92 full scale *before*
# truncating it: 146 of the 160 shipped presets peak at exactly
# int(0.92 * 32768) = 30146.  (The other 14 peak lower because their loudest
# moment was in the tail the cap discarded -- e.g. slow-attack pads such as
# "Synth Strings 1-C4", which peaks at 16704.)  Reproducing this is mandatory:
# without it the restored presets would sit up to 3.4 dB off the rest of the
# bank (measured: Applause x1.49, Reverse Cymbal x0.61, Grand Piano x1.07).
PEAK_NORM = 0.92
PEAK_INT16 = 30146  # int(PEAK_NORM * 32768), for tests

# Legacy caps, retained for the presets we deliberately leave alone.
CAP_LOOPED = 22747
CAP_ONESHOT = 19247

# The legacy one-shot length bug: lengths were computed at this rate.
LEGACY_ONESHOT_RATE = 17500

# ---------------------------------------------------------------------------
# Restore policy.
#
# 27 presets are cap-damaged (18 at 22747, 9 at 19247).  Restoring *all* of them
# -- which is the obvious reading of "un-truncate the bank" -- would be wrong:
# 14 of them are cymbals, toms, hi-hats and other percussion.  AMY does not
# apply the SoundFont volume envelope, so transplanting the SF2 loop onto a
# crash cymbal makes it drone forever rather than decay.  We therefore restore
# only where the SF2 says there is a real sustain loop AND the preset is reached
# as a melodic GM program (never as a drum note).

# Tier A: full length + sustain loop restored.
#   Criteria: currently cap-damaged, SF2 sampleModes == 1, and reachable via
#   gm.PROGRAM_PRESET (melodic) rather than only gm.DRUM_PRESET.
TIER_A = {
    0:   "Grand Piano-C4    - the headline defect; held notes died at 1.03s",
    11:  "Tubular Bells-C4  - melodic bell, real sustain loop",
    36:  "Violin-B3         - sustained bowed string",
    40:  "StrLoop - A2      - literally a loop sample",
    43:  "Timpani Soft      - melodic timpani (program 47), rolls need the loop",
    44:  "StrLoop - C3      - literally a loop sample",
    56:  "Brass Section-C4  - sustained brass",
    70:  "Bottle Blow       - sustained wind",
    71:  "Shakuhachi        - sustained wind",
    103: "Applause          - looping ambience (program 126)",
}

# Tier B: full length restored, but stays a one-shot (SF2 has no usable loop).
#   These are melodic programs where the 0.87s cap is plainly audible.
TIER_B = {
    82: "Sitar-G3          - melodic, no SF2 loop; cap cut the ring to 0.87s",
}

# Tier C: deliberately left byte-identical to the shipped bank.
#   Documented so the exclusions are reviewable rather than silent.
TIER_C_EXCLUDED = {
    100: "Birds             - shipped sample is NOT GeneralUser v1.471's 'Birds' "
         "(audio cross-correlation -0.007 against it, vs +1.000 for every other "
         "restore candidate). It is one of the 'et al.' samples whose source "
         "font we do not have, so rebaking it would silently swap the sound. "
         "Stays capped at 22747 frames.",
    96:  "Reverse Cymbal    - shape matches v1.471 (ncc +1.000) but the shipped "
         "sample carries ~1.56x the gain our normalization produces, so a "
         "rebake would make the already-audible first 0.87s ~3.9 dB quieter. "
         "The legacy gain is unexplained (its implied full-scale peak would "
         "clip), so we leave it alone rather than trade a cap for a level "
         "regression.",
    156: "Chimes            - drum-note-only (note 84); SF2 loop would drone",
    94:  "Standard Tom 3    - melodic tom (program 117); a tom must not sustain",
    119: "Standard Tom 5    - drum note",
    122: "Standard Tom 4    - drum note",
    123: "Hi-Hat Open       - drum note",
    124: "Standard Tom 3    - drum note",
    126: "Crash Cymbal      - drum note, SF2 sampleModes == 0",
    128: "Ride Cymbal       - drum note, SF2 sampleModes == 0",
    130: "Ride Bell         - drum note, SF2 sampleModes == 0",
    132: "Splash Cymbal     - drum note, SF2 sampleModes == 0",
    134: "Crash Cymbal      - drum note, SF2 sampleModes == 0",
    135: "Vibra Slap        - drum note, no SF2 loop",
    159: "Surdo Open        - drum note, no SF2 loop",
    73:  "Ocarina-C#5       - not present in GeneralUser GS v1.471 (see README)",
}


def clamp_loop(length, loopstart, loopend):
    """Return (loopstart, loopend) that the AMY renderer will actually loop.

    Enforces the pcm.c contract:
      * loopend < length              (else the end-of-sample test fires first)
      * loop_len > 0
      * loop_len < length             (else it is read as a one-shot)

    Returns None if no legal sustain loop can be represented, in which case the
    caller must emit the one-shot encoding via `oneshot_loop`.

    Degenerate input (loopstart >= loopend) is rejected rather than nudged into
    a legal-looking loop: manufacturing a 1-frame loop out of a bad SoundFont
    entry would buzz at 22 kHz, which is far worse than falling back to a
    one-shot.
    """
    if length <= 1:
        return None
    loopend = min(loopend, length - 1)
    if loopstart < 0 or loopstart >= loopend:
        return None
    loop_len = loopend - loopstart
    if loop_len <= 0 or loop_len >= length:
        return None
    return (loopstart, loopend)


def oneshot_loop(length):
    """The bank's one-shot sentinel: loop_len == length, which pcm.c refuses."""
    return (0, length)


def is_oneshot(length, loopstart, loopend):
    """True if this entry encodes a one-shot under the pcm.c rules."""
    loop_len = loopend - loopstart
    return not (loop_len > 0 and loop_len < length)


def legacy_oneshot_length(full_len):
    """Reproduce the legacy 17500/22050 one-shot length (for --mode=full)."""
    return round(full_len * LEGACY_ONESHOT_RATE / SAMPLE_RATE)


def plan_entry(index, full_len, rs_loopstart, rs_loopend, sf2_looped):
    """Decide (length, loopstart, loopend) for one preset in surgical mode.

    Returns None for "not ours -- copy the shipped bytes verbatim".
    """
    if index in TIER_A:
        if not sf2_looped:
            raise ValueError("preset %d is Tier A but SF2 reports no loop" % index)
        length = min(full_len, rs_loopend + TAIL_FRAMES)
        loop = clamp_loop(length, rs_loopstart, rs_loopend)
        if loop is None:
            raise ValueError("preset %d: no representable loop after clamping" % index)
        return (length, loop[0], loop[1])
    if index in TIER_B:
        length = full_len
        return (length,) + oneshot_loop(length)
    return None
