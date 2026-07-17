# Rebaking the GeneralUser GM bank

Regenerates `amy/sounds/gm/fonts.bin` + `amy/src/pcm_gm.h` (160 presets, 22050 Hz
mono) from GeneralUser GS v1.471, restoring the real length and sustain loops to
the melodic presets that the original bake truncated.

## TL;DR

```sh
pip install sf2utils resampy numpy audioop-lts   # audioop-lts only on py3.13+
python tools/gm/rebake_gm.py --sf2 /path/to/"GeneralUser GS v1.471.sf2" --verify
python -m pytest tools/gm/test_rebake_gm.py -q
```

Writes `amy/sounds/gm/fonts.bin`, `amy/src/pcm_gm.h`, and patches `PRESET_LOOPED`
in both `deck/gm.py` and `amy/amy/gm.py`. `--dry-run` reports without writing.

## The SoundFont (do not commit it)

GeneralUser GS **v1.471**, sha256
`f45b6b4a68b6bf3d792fcbb6d7de24dc701a0f89c5900a21ef3aaece993b839a` (29.8 MB).
The script warns if the digest does not match.

Mirror it locally; **do not hotlink** S. Christian Collins' files -- that is the
one thing his licence asks. It is not committed here (30 MB binary). Canonical
home: <https://schristiancollins.com/generaluser.php>. v1.471 specifically is no
longer offered upstream (`github.com/mrbumpy409/GeneralUser-GS` has no tags or
releases and its `main` is v2.x with a different preset set); it was obtained
from a preservation mirror (`ROCKNIX/generaluser-gs`).

**Licence / provenance.** GeneralUser GS is free and permissively licensed: "You
may use GeneralUser GS without restriction... feel free to use it in your
software projects, and to modify the SoundFont bank." No attribution required;
the only ask is not to hotlink. The bank is embedded here as resampled PCM, which
the licence explicitly permits.

## What the original bake did (reverse-engineered)

The private `sf2_to_amy.py` credited in `pcm_gm.h` (Leeman1982/gm2350) was never
published -- that repo contains an AMY fork dump and `medusa_gm.h`, but not the
script. The bake was recovered from the shipped artefacts instead:

| evidence | conclusion |
|---|---|
| 148/160 preset names match the SF2 `shdr` chunk | source is GeneralUser GS v1.471 |
| 128/148 name-matched presets cross-correlate at **ncc > 0.99** against v1.471 resampled with `resampy` | same font *and* same resampling pipeline; no pitch shift |
| 146/160 presets peak at exactly **30146** = `int(0.92 * 32768)` | each sample was peak-normalized to 0.92 full scale |
| the 14 that peak lower are slow-attack pads (e.g. `Synth Strings 1-C4` at 16704) | normalization ran on the **full** sample, *then* it was truncated |
| looped presets have `length - loopend` <= 1102 (26 of them exactly 1102), never more | looped samples were cut at `loopend + 50 ms` |
| un-looped presets are all exactly **79.365%** of their full length | one-shot lengths were computed at 17500 Hz while the audio was resampled to 22050 -- a bug |
| 18 damaged presets sit at 22747 frames, 9 at 19247; the split tracks SF2 `sampleModes` exactly (9/9 un-looped -> 19247) | two hard duration caps: 22747 (1.0316 s) for looped, 19247 (0.8729 s) for one-shots |

So the legacy bake was:

```
audio = normalize(resampy.resample(src, sample_rate, 22050), peak=0.92)
if sampleModes == 1:  length = min(full, loopend + 1102, 22747)   # loop dropped if the cap bound
else:                 length = min(round(full * 17500/22050), 19247)
```

**The damage**: when the 22747 cap bound a looped sample, the loop was discarded
and the preset was re-encoded as a one-shot. A held Grand Piano note stops dead
at 1.03 s. 27 presets are affected.

## What this rebake does, and what it deliberately does not

Default mode is **surgical**: every preset not being fixed is copied
byte-for-byte out of the shipped blob, so 149/160 presets are *bit-identical*
rather than "close modulo resampler version". Only the presets in
`gm_policy.TIER_A` / `TIER_B` are re-derived from the SoundFont.

**We do not restore all 27.** Fourteen of them are cymbals, toms, hi-hats and
other percussion. AMY does not apply the SoundFont volume envelope, so
transplanting the SF2 loop onto a crash cymbal makes it drone forever instead of
decaying -- the SF2 relies on a 20-40 s envelope decay that AMY never reads. The
policy therefore restores a loop only where the SF2 reports a real sustain loop
**and** the preset is reachable as a melodic GM program (`gm.PROGRAM_PRESET`)
rather than only as a drum note (`gm.DRUM_PRESET`).

- **Tier A** (11): full length + sustain loop restored. Grand Piano, Tubular
  Bells, Violin, StrLoop A2/C3, Timpani Soft, Brass Section, Bottle Blow,
  Shakuhachi, Applause.
- **Tier B** (1): full length restored, stays a one-shot -- Sitar (melodic, but
  the SF2 gives it no loop; the cap cut its ring to 0.87 s).
- **Tier C**: left byte-identical, each with a recorded reason in `gm_policy.py`.
  Two are worth knowing about:
  - **Birds** -- the shipped sample is **not** GeneralUser v1.471's `Birds`
    (ncc **-0.007** against it, versus +1.000 for every other candidate). It is
    one of the 12 "et al." samples whose source font we do not have, so rebaking
    it would silently swap the sound.
  - **Reverse Cymbal** -- shape matches (ncc +1.000) but the shipped sample
    carries ~1.56x the gain our normalization produces, so a rebake would make
    the already-audible first 0.87 s about 3.9 dB quieter. Unexplained; left alone.

The 17500 Hz one-shot length bug is **not** fixed globally: it would add ~0.5 MB
across ~35 percussion presets for a mostly inaudible decay tail.

## The subtle part: `loopend` must be < `length`

`amy/src/pcm.c` ends a note when `base_index >= sample_length` **before** it ever
reaches the loop-wrap check. So a preset with `loopend == length` never loops --
the note just ends.

GeneralUser's Grand Piano has `end_loop == duration` exactly (62526 of 62526), so
restoring the full length **alone would not have fixed it**: it would still cut
off, just at 2.02 s instead of 1.03 s. `gm_policy.clamp_loop()` therefore clamps
`loopend` to `length - 1`.

The one-shot sentinel is the *other* side of the same coin: one-shots are encoded
`loopstart=0, loopend=length`, giving `loop_len == length`, which fails pcm.c's
`loop_len < length` test and correctly refuses to wrap. Do not "fix" that guard.

## The 12 presets that are not in v1.471

148/160 names match. The other 12 are the "et al." in `pcm_gm.h`'s header and are
copied verbatim; we have no source for them:

| preset | note |
|---|---|
| `Harpsi_1-F4` | v1.471 has `Harpsichord-F4` (different naming) |
| `Accordion-A#2`, `Accordion-F#2` | v1.471 spells it `Accordian` (sic) |
| `Viola-60` | v1.471 uses note names (`Viola-E4`) |
| `French Horn-D#3` | v1.471 has `French Horn-E#3` |
| `Ocarina-C#5` | v1.471 has `Ocarina-C5` |
| `Synth Bell-1a` | v1.471 has `Synth Bell-1` |
| `Standard Snare 1_v1` | v1.471 has `Standard Snare 1` |
| `Wave_Saw-057`, `Wave_Ramp-057`, `Wave_Pulse3750-057`, `Wave_Pulse5000-057` | no `Wave_*` samples exist in v1.471 at all |

The `_1` / `-60` / `_v1` / `-1a` suffixes suggest the original baker merged and
de-duplicated several fonts. Name matching alone would have mis-assigned some of
these, which is why every rebaked preset is additionally checked by audio
cross-correlation (that is how Birds was caught).

## Lockstep

The bank's size drives the flash layout. These must agree or the bank plays
garbage:

- `tulip/esp32s3/boards/N32R8/tulip-partitions-32MB.csv` -- `fonts` geometry
  (shared by all N32R8 boards, incl. TULIP4_R11_DEBUG/FLASHER)
- `tulip/shared/amy_connector.c` -- `GM_BIG_BYTE_OFFSET`
- `tulip/fs_create.py` -- `GM_BIG_OFFSET`
- `deck/gm.py` **and** `amy/amy/gm.py` -- `PRESET_LOOPED` (different files; they
  only share this table). The deck reads it to choose `feedback`/EG, so a stale
  entry means a restored preset silently refuses to sustain.

`fs_create.py` hard-fails if `fonts.bin` overruns `GM_BIG_OFFSET`, so a
half-applied change breaks the build rather than the sound.
