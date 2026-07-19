# AMYboard Sketch -- Tulip Deck companion
# Top-level code runs once at boot. loop() is called every 32nd note.
# DESCRIPTION: listen on an assigned MIDI channel; Program Change -> patch,
# a documented CC set -> AMY params. Channel persists across reboots and is set
# by the Tulip during enrollment (see "Enrollment" below), so each board in the
# fleet can be addressed and fully controlled independently.
#
# Deploy this as the board's sketch (AMYboard Online "write to sketch", or the
# control API zT to /user/current/sketch.py). One copy per board; the channel
# makes them distinct.
#
# Enrollment: the Tulip assigns this board a channel by sending, to THIS USB
# device only (tulip.midi_out(sysex, device=N)), a control-API zP that writes
# the channel file and restarts the sketch -- see deck/amyfleet.py. On boot we
# read that file, so the assignment sticks.
#
# CC map (received on our channel):
#   PC (program change) -> patch (0-127 Juno, 128-255 DX7, 256 piano)
#   CC 74 -> filter cutoff / brightness
#   CC 71 -> resonance
#   CC 70 -> detune (best-effort; ignored if this AMY build lacks it)
#   CC 73 -> amp attack (best-effort)
#   CC 72 -> amp release (best-effort)
#   CC 75 -> polyphony (1-16 voices)

import amy, midi, synth

CH_FILE = '/user/deck_channel'
DEFAULT_CHANNEL = 1
DEFAULT_PATCH = 0
DEFAULT_VOICES = 8

state = {
    'channel': DEFAULT_CHANNEL,
    'patch': DEFAULT_PATCH,
    'voices': DEFAULT_VOICES,
    'reson': 1.0,
}


def _load_channel():
    try:
        return max(1, min(16, int(open(CH_FILE).read().strip())))
    except Exception:
        return DEFAULT_CHANNEL


def _save_channel(ch):
    try:
        open(CH_FILE, 'w').write(str(ch))
    except Exception:
        pass


def _apply():
    # Put our synth on the assigned channel so the default MIDI handler plays
    # note on/off for us; our callback below adds patch + CC control.
    try:
        midi.config.release_synth_for_channel(state['channel'])
    except Exception:
        pass
    midi.config.add_synth(
        synth.PatchSynth(patch=state['patch'], num_voices=state['voices']),
        channel=state['channel'])


def set_channel(ch):
    ch = max(1, min(16, int(ch)))
    try:
        midi.config.release_synth_for_channel(state['channel'])
    except Exception:
        pass
    state['channel'] = ch
    _save_channel(ch)
    _apply()


def set_patch(p):
    state['patch'] = int(p)
    _apply()


def set_voices(n):
    state['voices'] = max(1, min(16, int(n)))
    _apply()


def _try_send(**kw):
    # amy.send with best-effort params: never let an unsupported kwarg on this
    # firmware break MIDI handling.
    try:
        amy.send(synth=state['channel'], **kw)
    except Exception:
        pass


def _cc(control, value):
    v = value / 127.0
    if control == 74:      # filter cutoff / brightness
        _try_send(filter_freq=100.0 + v * 6000.0)
    elif control == 71:    # resonance
        state['reson'] = 0.1 + v * 8.0
        _try_send(resonance=state['reson'])
    elif control == 70:    # detune (best-effort)
        _try_send(detune=v * 0.5)
    elif control == 73:    # amp attack (best-effort)
        _try_send(bp0='%d,1.0,%d,0.0' % (int(v * 2000), 200))
    elif control == 72:    # amp release (best-effort)
        _try_send(bp0='0,1.0,%d,0.0' % int(50 + v * 3000))
    elif control == 75:    # polyphony
        set_voices(1 + int(v * 15))


def midi_cb(m):
    if not m:
        return
    status = m[0] & 0xF0
    ch = (m[0] & 0x0F) + 1
    if ch != state['channel']:
        return  # not addressed to this board
    if status == 0xC0:                     # Program Change -> patch
        set_patch(m[1])
    elif status == 0xB0 and len(m) > 2:    # Control Change -> params
        _cc(m[1], m[2])


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
state['channel'] = _load_channel()
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
