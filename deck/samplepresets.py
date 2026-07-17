# samplepresets.py -- user SAMPLE presets (Phase 2): load a 16-bit mono WAV off
# the SD card into a resident PCM preset and play it as a drum one-shot from a
# synth-kit pad.
#
# This is the SAMPLE-BYTES half of the preset feature (Phase 1 = param overlay).
# It is intentionally split so the pure, host-testable logic (WAV validation,
# preset-number allocation, the one-shot patch string, the typed-swap shape)
# lives here and is unit-tested, while the parts that MUST be validated on real
# hardware are isolated in clearly-marked device functions below.
#
# ===========================================================================
# ON-DEVICE VALIDATION REQUIRED (coordinator: verify on hardware before merge)
# ---------------------------------------------------------------------------
#  1. QUIESCE-BEFORE-UNLOAD TIMING. amy.load_sample() -> pcm_load() first calls
#     pcm_unload_preset(), which free()s a linked-list node on the MP core while
#     render_pcm() walks that same list on the AUDIO core -- with NO lock, and
#     the flash fence does NOT cover it. So a preset that has a LIVE VOICE must
#     be quiesced (num_voices=0 on the owning synth) BEFORE any load/unload.
#     load_sample_into()/unload_sample() take quiesce_synth for exactly this;
#     the timing (does num_voices=0 actually retire the voice before the free?)
#     needs to be confirmed by ear/scope on device. See SynthKit.retweak.
#  2. PSRAM BUDGET. One baked GM bank already spills to PSRAM, so headroom is
#     contested. MAX_SAMPLE_FRAMES below is a PLACEHOLDER, not a measured limit,
#     and there is deliberately NO eviction yet (a pad swapped AWAY from a
#     sample leaves it resident until the kit rebuilds). Do NOT design eviction
#     blind -- measure free PSRAM with N user samples loaded first, then set the
#     cap and decide the eviction policy. TODO(device): measured numbers.
#  3. PER-NOTE BEHAVIOUR. feedback=0 must actually make pcm_load play the sample
#     ONCE (no loop); the one-shot EG release must not choke a short hit; and a
#     sample loaded at midinote=ROOT_NOTE must play at natural pitch when the
#     pad fires note 60. Confirm on device. The pad's tune/decay/level/snap
#     sliders are NOT yet wired to sample pads (PCM pitch/decay want on-device
#     tuning) -- retweak leaves them unapplied on purpose.
# ===========================================================================

# Preset-number window for USER samples. Baked banks must not be shadowed:
# gamma9001 = 256..391, GM = 512..671, gm_big above that (see amy/src/pcm.c
# ~88-99). 700+ sits clear of all of them. The ceiling is a soft cap here --
# the REAL ceiling is a PSRAM budget that must be measured on device (see #2).
USER_PRESET_BASE = 700
USER_PRESET_LIMIT = 799            # TODO(device): raise/lower to the measured
                                   # PSRAM budget, not this placeholder.

# AMY caps a filename at 127 chars (MAX_FILENAME_LEN, amy api). load_sample
# streams the file into RAM, but the path still rides the wire -- enforce it.
MAX_FILENAME_LEN = 127

# The SD card mount point (deck/boot.py mounts it here). Samples live on SD
# because a corpus of WAVs is too big for the /user littlefs partition.
SD_ROOT = '/sd'

PCM_WAVE = 7                       # amy.PCM: the PCM wavetable oscillator
ROOT_NOTE = 60                     # synth-kit pads fire their hit at note 60

# Placeholder length cap (~5 s @ 44.1 kHz mono 16-bit). NOT a measured PSRAM
# limit -- see validation note #2. A short drum one-shot is far under this.
MAX_SAMPLE_FRAMES = 220500

_HEADER_READ = 4096                # bytes read to locate fmt + data chunks


# --------------------------------------------------------------------------
# Pure logic (host-testable)
# --------------------------------------------------------------------------
def parse_wav_header(buf):
    """Parse a RIFF/WAVE header from `buf` (bytes). Returns an info dict
    {channels, rate, bits[, data_bytes, frames]} on success. Raises ValueError
    for anything that is not a 16-bit mono PCM WAV. `frames` is present only if
    the data chunk header fell within the bytes provided."""
    if len(buf) < 12 or buf[0:4] != b'RIFF' or buf[8:12] != b'WAVE':
        raise ValueError("not a WAV file")
    pos = 12
    n = len(buf)
    fmt = None
    data_bytes = None
    while pos + 8 <= n:
        cid = buf[pos:pos + 4]
        csize = int.from_bytes(buf[pos + 4:pos + 8], 'little')
        body = pos + 8
        if cid == b'fmt ' and body + 16 <= n:
            audio_fmt = int.from_bytes(buf[body:body + 2], 'little')
            channels = int.from_bytes(buf[body + 2:body + 4], 'little')
            rate = int.from_bytes(buf[body + 4:body + 8], 'little')
            bits = int.from_bytes(buf[body + 14:body + 16], 'little')
            fmt = (audio_fmt, channels, rate, bits)
        elif cid == b'data':
            data_bytes = csize
            break
        pos = body + csize + (csize & 1)   # RIFF chunks are word-aligned
    if fmt is None:
        raise ValueError("no fmt chunk")
    audio_fmt, channels, rate, bits = fmt
    if audio_fmt != 1:
        raise ValueError("not PCM (format tag %d)" % audio_fmt)
    if channels != 1:
        raise ValueError("not mono (%d channels)" % channels)
    if bits != 16:
        raise ValueError("not 16-bit (%d-bit)" % bits)
    info = {'channels': channels, 'rate': rate, 'bits': bits}
    if data_bytes is not None:
        info['data_bytes'] = data_bytes
        info['frames'] = data_bytes // 2       # 2 bytes/frame (16-bit mono)
    return info


def path_ok(path):
    """True when a path fits AMY's MAX_FILENAME_LEN."""
    return bool(path) and len(path) <= MAX_FILENAME_LEN


def wav_info(path):
    """Read + validate a WAV file's header. Returns the info dict or raises
    OSError / ValueError."""
    with open(path, 'rb') as f:
        buf = f.read(_HEADER_READ)
    return parse_wav_header(buf)


def validate(path):
    """(ok, info_or_reason). Checks path length, WAV format (16-bit mono PCM),
    and the placeholder length cap. Never raises."""
    if not path_ok(path):
        return False, "path too long (max %d chars)" % MAX_FILENAME_LEN
    try:
        info = wav_info(path)
    except OSError:
        return False, "cannot read file"
    except ValueError as e:
        return False, str(e)
    frames = info.get('frames')
    if frames is not None and frames > MAX_SAMPLE_FRAMES:
        return False, "sample too long (%d frames > %d)" % (frames,
                                                            MAX_SAMPLE_FRAMES)
    return True, info


def one_shot_patch_string(preset):
    """AMY patch wire string that plays PCM `preset` as a ONE-SHOT.

    w7 = PCM oscillator; b0 = feedback 0 -> play the sample ONCE with NO loop
    (pcm_load otherwise loops the whole sample). The one-shot EG lets a long
    sample ring to its natural end and keeps note-off from choking it -- the
    same recipe gm.patch_string uses for its one-shot presets."""
    return "v0w%dp%db0A0,1,30000,1,4000,0Z" % (PCM_WAVE, preset)


# --- typed per-pad swaps ---------------------------------------------------
# A synth-kit pad's hit_swaps value is normally a bare corpus hit key (a str).
# A SAMPLE swap is a dict instead, so drums_kit can tell the two apart and load
# the WAV rather than build a corpus mini-synth.
def make_sample_swap(path, preset):
    return {'kind': 'sample', 'path': path, 'preset': preset}


def is_sample_swap(v):
    return isinstance(v, dict) and v.get('kind') == 'sample'


def swap_path(v):
    return v.get('path') if isinstance(v, dict) else None


def swap_preset(v):
    return v.get('preset') if isinstance(v, dict) else None


def used_presets(instruments):
    """The set of user preset numbers currently claimed by sample swaps across
    every instrument -- so allocation never collides with a resident sample."""
    used = set()
    for instr in instruments or []:
        for v in (instr.get('hit_swaps') or {}).values():
            if is_sample_swap(v):
                p = swap_preset(v)
                if isinstance(p, int):
                    used.add(p)
    return used


def alloc_preset(used):
    """Lowest free preset number in the user window not in `used`. Raises
    ValueError if the (placeholder) window is exhausted."""
    used = set(used or ())
    p = USER_PRESET_BASE
    while p in used:
        p += 1
    if p > USER_PRESET_LIMIT:
        raise ValueError("no free user preset slots (window %d..%d full)"
                         % (USER_PRESET_BASE, USER_PRESET_LIMIT))
    return p


def preset_for(instruments, note_swap):
    """The preset number a pad should use: reuse the pad's existing sample-swap
    preset (so re-picking a WAV doesn't leak a number), else allocate a fresh
    one clear of every other instrument's samples."""
    if is_sample_swap(note_swap):
        p = swap_preset(note_swap)
        if isinstance(p, int):
            return p
    return alloc_preset(used_presets(instruments))


def list_wavs(directory):
    """(dirs, wavs) under `directory`: subdirectory names and *.wav filenames,
    each sorted. Missing dir -> ([], [])."""
    import os
    dirs, wavs = [], []
    try:
        for ent in os.ilistdir(directory):
            name, typ = ent[0], ent[1]
            if name.startswith('.'):
                continue
            if typ & 0x4000:
                dirs.append(name)
            elif name.lower().endswith('.wav'):
                wavs.append(name)
    except (OSError, AttributeError):
        try:
            for name in os.listdir(directory):
                full = directory.rstrip('/') + '/' + name
                if name.startswith('.'):
                    continue
                if _is_dir(full):
                    dirs.append(name)
                elif name.lower().endswith('.wav'):
                    wavs.append(name)
        except OSError:
            return [], []
    dirs.sort()
    wavs.sort()
    return dirs, wavs


def _is_dir(path):
    import os
    try:
        return os.stat(path)[0] & 0x4000 != 0
    except OSError:
        return False


# --------------------------------------------------------------------------
# Device I/O (NOT host-testable -- see the validation banner at the top)
# --------------------------------------------------------------------------
def load_sample_into(preset, path, midinote=ROOT_NOTE, quiesce_synth=None):
    """DEVICE: load a WAV into resident PCM `preset`.

    CONCURRENCY (load-bearing): amy.load_sample -> pcm_load first unloads
    `preset`, free()ing a list node the audio core may be walking. If a synth
    can currently render this preset, pass quiesce_synth=<its synth number> so
    we kill its voices (num_voices=0) BEFORE the load. Mirrors the num_voices=0
    / reload / num_voices=1 dance in SynthKit.retweak. Uses load_sample
    (RESIDENT PSRAM), never disk_sample (which reads the filesystem on the
    render path -- unsafe for short drum one-shots)."""
    import amy
    if quiesce_synth is not None:
        amy.send(synth=quiesce_synth, num_voices=0)
    amy.load_sample(path, preset=preset, midinote=midinote)


def unload_sample(preset, quiesce_synth=None):
    """DEVICE: free a resident PCM preset. Same quiesce rule as load."""
    import amy
    if quiesce_synth is not None:
        amy.send(synth=quiesce_synth, num_voices=0)
    try:
        amy.unload_sample(preset)
    except Exception:
        pass
