# AMYboard Sketch -- Tulip Deck companion
# Top-level code runs once at boot. loop() is called every 32nd note.
# DESCRIPTION: listen on the assigned MIDI channel(s); Program Change -> patch,
# a documented CC set -> AMY params. Channels persist across reboots and are set
# by the Tulip during enrollment (see "Enrollment" below), so each board in the
# fleet can be addressed and fully controlled independently.
#
# Multi-channel: the deck can host several instruments on one board, each on its
# own channel. This sketch used to listen on exactly ONE channel, so a second
# instrument moved here on a different channel could never sound. It now keeps a
# LIST of channels and per-channel state, adding one synth per channel.
#
# Deploy this as the board's sketch (AMYboard Online "write to sketch", or the
# control API zT to /user/current/sketch.py). One copy per board; the channels
# make them distinct.
#
# Enrollment: the Tulip assigns this board its channels by sending, to THIS USB
# device only (tulip.midi_out(sysex, device=N)), a control-API zP that writes
# the channel file and restarts the sketch -- see deck/amyfleet.py. On boot we
# read that file, so the assignment sticks.
#
# CC map (received on any of our channels):
#   PC (program change) -> patch (0-127 Juno, 128-255 DX7, 256 piano)
#   CC 74 -> filter cutoff / brightness
#   CC 71 -> resonance
#   CC 70 -> detune (best-effort; ignored if this AMY build lacks it)
#   CC 73 -> amp attack (best-effort)
#   CC 72 -> amp release (best-effort)
#   CC 75 -> polyphony (1-16 voices)

import amy, midi, synth

CH_FILE = '/user/deck_channels'        # csv of channels; was a single int
LEGACY_CH_FILE = '/user/deck_channel'  # pre-multi-channel single int (migration)
DEFAULT_CHANNELS = [1]
DEFAULT_PATCH = 0
DEFAULT_VOICES = 8

# Per-channel synth state, keyed by MIDI channel. The board can host several
# deck instruments, each on its own channel -- the single global `state` the
# one-channel sketch kept could only ever serve one.
channels = {}   # ch -> {'patch':, 'voices':, 'reson':}


def _parse_channels(s):
    # Normalize a csv (or a bare legacy int) into a sorted, deduped 1..16 list.
    out = []
    for part in str(s).replace(' ', '').split(','):
        if not part:
            continue
        try:
            c = int(part)
        except Exception:
            continue
        if 1 <= c <= 16 and c not in out:
            out.append(c)
    out.sort()
    return out


def _new_state():
    return {'patch': DEFAULT_PATCH, 'voices': DEFAULT_VOICES, 'reson': 1.0}


def _load_channels():
    for path in (CH_FILE, LEGACY_CH_FILE):   # new csv first, then legacy int
        try:
            chs = _parse_channels(open(path).read())
            if chs:
                return chs
        except Exception:
            pass
    return list(DEFAULT_CHANNELS)


def _save_channels(chs):
    try:
        open(CH_FILE, 'w').write(','.join(str(c) for c in chs))
    except Exception:
        pass


def _apply_channel(ch):
    # Put a synth on `ch` so the default MIDI handler plays note on/off for it;
    # our callback below adds patch + CC control per channel.
    st = channels.get(ch)
    if st is None:
        return
    try:
        midi.config.release_synth_for_channel(ch)
    except Exception:
        pass
    midi.config.add_synth(
        synth.PatchSynth(patch=st['patch'], num_voices=st['voices']),
        channel=ch)


def _apply():
    for ch in channels:
        _apply_channel(ch)


def set_channels(chs):
    chs = _parse_channels(','.join(str(c) for c in chs)) or list(DEFAULT_CHANNELS)
    # Drop synths for channels we're no longer enrolled on, so they go silent.
    for ch in list(channels):
        if ch not in chs:
            try:
                midi.config.release_synth_for_channel(ch)
            except Exception:
                pass
            channels.pop(ch, None)
    for ch in chs:
        if ch not in channels:
            channels[ch] = _new_state()
    _save_channels(chs)
    _apply()


def set_channel(ch):
    # Back-compat: enroll on a single channel.
    set_channels([ch])


def set_patch(ch, p):
    st = channels.get(ch)
    if st is None:
        return
    st['patch'] = int(p)
    _apply_channel(ch)


def set_voices(ch, n):
    st = channels.get(ch)
    if st is None:
        return
    st['voices'] = max(1, min(16, int(n)))
    _apply_channel(ch)


def _try_send(ch, **kw):
    # amy.send with best-effort params: never let an unsupported kwarg on this
    # firmware break MIDI handling.
    try:
        amy.send(synth=ch, **kw)
    except Exception:
        pass


def _cc(ch, control, value):
    v = value / 127.0
    st = channels.get(ch)
    if control == 74:      # filter cutoff / brightness
        _try_send(ch, filter_freq=100.0 + v * 6000.0)
    elif control == 71:    # resonance
        r = 0.1 + v * 8.0
        if st is not None:
            st['reson'] = r
        _try_send(ch, resonance=r)
    elif control == 70:    # detune (best-effort)
        _try_send(ch, detune=v * 0.5)
    elif control == 73:    # amp attack (best-effort)
        _try_send(ch, bp0='%d,1.0,%d,0.0' % (int(v * 2000), 200))
    elif control == 72:    # amp release (best-effort)
        _try_send(ch, bp0='0,1.0,%d,0.0' % int(50 + v * 3000))
    elif control == 75:    # polyphony
        set_voices(ch, 1 + int(v * 15))


def midi_cb(m):
    if not m:
        return
    status = m[0] & 0xF0
    ch = (m[0] & 0x0F) + 1
    if ch not in channels:
        return  # not one of this board's enrolled channels
    if status == 0xC0:                     # Program Change -> patch
        set_patch(ch, m[1])
    elif status == 0xB0 and len(m) > 2:    # Control Change -> params
        _cc(ch, m[1], m[2])


# --- firmware over-the-wire update (Tulip-driven) ---------------------------
# The Tulip streams a new AMYboard firmware image to THIS board over USB-MIDI
# SysEx and we write it to our inactive OTA slot, verify, set_boot and reboot.
# All the protocol/verify logic lives in deck/boardfw.OtaReceiver (host-tested);
# this glue only moves bytes on/off the wire and owns the real Partition.
#
# DEPLOYMENT: copy deck/boardlink.py, deck/boardxport.py and deck/boardfw.py to
# this board alongside sketch.py (AMYboard Online "add file", or mpremote cp).
# They import nothing at module scope that a non-Tulip board lacks.
#
# WIRING NOTE (verify on hardware): we claim midi.sysex_callback -- the single
# global SysEx slot (see deck/boardlink.py's hazard note). Our receiver only
# acts on OTA opcodes and returns None for anything else, so ordinary control-
# API SysEx is undisturbed. On-device we still need to confirm the firmware's
# C control-API path forwards (rather than swallows) these non-'z' SPSS frames
# to the Python sysex_callback; that is the remaining hardware bring-up step.

_ota = {'rx': None}


def _fw_version():
    try:
        import tulip
        return tulip.version()
    except Exception:
        return '?'


def _ota_target():
    # our INACTIVE OTA slot -- same as deck/flash_stream.py writes on the Tulip
    from esp32 import Partition
    return Partition(Partition.RUNNING).get_next_update()


def _ota_send(reply):
    if not reply:
        return
    try:
        import tulip
        import boardxport
        tulip.midi_out(bytes([0xF0, 0x00, 0x03, 0x45])
                       + boardxport.pack8to7(reply) + bytes([0xF7]))
    except Exception:
        pass


def ota_sysex(raw):
    # Fed every incoming SysEx frame. Ignore non-AMYboard / non-OTA traffic.
    try:
        import boardlink
        import boardxport
        import boardfw
    except Exception:
        return
    payload = boardlink.parse_envelope(raw)
    if payload is None:
        return
    frame = boardxport.unpack7to8(payload)
    up = boardlink.unpack_frame(frame)
    if up is None:
        return
    op = up[0]
    # Only spin up the receiver (and its 4 KB sector buffer) for OUR opcodes,
    # so a stray SysEx never allocates on a memory-tight board.
    if op not in (boardlink.OP_OTA_QUERY, boardlink.OP_OTA_BEGIN,
                  boardlink.OP_OTA_DATA, boardlink.OP_OTA_END):
        return
    rx = _ota['rx']
    if rx is None:
        rx = boardfw.OtaReceiver(_ota_target(), version=_fw_version())
        _ota['rx'] = rx
    reply = rx.feed(frame)
    _ota_send(reply)
    # A final RESULT ends the session: free the buffer, and on success reboot
    # into the freshly-committed firmware (after the ack has gone out).
    res = boardfw.dec_result(reply) if reply else None
    if res is not None:
        _ota['rx'] = None
        if res[0] == boardfw.ST_OK:
            try:
                import machine
                from time import sleep_ms
                sleep_ms(200)         # let the RESULT frame flush to the Tulip
                machine.reset()
            except Exception:
                pass


# --- boot ---
for _ch in _load_channels():
    channels[_ch] = _new_state()
_apply()
midi.add_callback(midi_cb)
try:
    midi.sysex_callback = ota_sysex   # receive Tulip-driven firmware updates
except Exception:
    pass


def loop():
    pass

# Do not edit. Set automatically by the knobs on AMYboard Online.
_auto_generated_knobs = """
"""
