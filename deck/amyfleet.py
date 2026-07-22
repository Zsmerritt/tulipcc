# amyfleet.py -- Tulip-side helpers to enroll and address AMYboards.
#
# Enrollment assigns each board the MIDI channel(s) it should listen on. Because
# the firmware can target one USB device (tulip.midi_out(sysex, device=N)), we
# send the AMYboard control-API SysEx (zP: run Python on the board) to exactly
# one board: it writes the durable channel store and (re)installs the listener,
# so the assignment sticks across reboots. Needs each board running the deck
# listener (baked into firmware) or deck/amyboard_companion/sketch.py.
#
# Multi-channel: the deck can host several instruments on one board, each on its
# own channel. Enrollment used to assign a single channel per board, so a second
# instrument moved to a board on a DIFFERENT channel could never sound. We now
# enroll a board on its FULL list of channels at once.

import tulip

MFR = (0xF0, 0x00, 0x03, 0x45)   # SPSS / AMYboard manufacturer id


def _zp(code):
    # F0 00 03 45  z P <code> Z  F7  -- "run this line of Python on the board"
    return bytes(MFR) + b'zP' + code.encode() + b'Z' + bytes([0xF7])


def enroll_channels(device, chs):
    """Assign a LIST of MIDI channels to the AMYboard at USB `device`, durably.

    The deck can host several instruments on one board, each on its own channel;
    the old single-channel enroll meant a second instrument moved to a board on
    a different channel could never sound. This enrolls every channel at once.

    On firmware with the multi-channel deck listener, deck_set_channels stores
    the list in NVS (survives sketch wipes / factory reset) and installs the
    listener live. On firmware that predates it, we degrade gracefully to the
    single-channel deck_set_channel with the first channel (better one
    instrument sounds than none); on firmware without either the line no-ops
    (deploy the companion sketch as a fallback there).

    An EMPTY list is a MEANINGFUL frame, not a no-op: when the last instrument
    moves OFF a board (instmove.move_instrument), enroll_channels(dev, []) makes
    the board's listener release the vacated synth so it stops sounding. Skip it
    and the orphaned synth keeps playing forever. The degrade lambda guards
    l[0] so an empty list on old firmware is a safe no-op rather than IndexError."""
    chs = sorted({int(c) for c in chs if 1 <= int(c) <= 16})
    # getattr-degrade keeps this one statement working on BOTH firmwares; the
    # list literal (%r of ints -> e.g. [2, 5]) stays well under the ~200-char
    # zP line budget for any realistic channel set.
    code = ("import amyboard; getattr(amyboard,'deck_set_channels',"
            "lambda l: amyboard.deck_set_channel(l[0]) if l else None)(%r)"
            % (chs,))
    tulip.midi_out(_zp(code), device)


def enroll(device, channel):
    """Back-compat single-channel enroll -> enroll_channels(device, [channel])."""
    enroll_channels(device, [int(channel)])


def enroll_from_config():
    """Enroll every board-backed instrument to its channel, grouped by device.

    A board can now host several instruments, each on its own channel, so we
    collect each device's FULL channel list and enroll them all in one frame --
    lifting the old one-channel limit where only the first instrument per device
    was enrolled and a second on a different channel could never sound. Internal
    instruments have no board to enroll and are ignored. Returns the number of
    devices enrolled."""
    import deckcfg
    by_dev = {}   # device -> set of channels routed to it
    for instr in deckcfg.instruments():
        dev = instr.get('device')
        if not isinstance(dev, int):
            continue
        by_dev.setdefault(dev, set()).add(int(instr.get('channel', 2)))
    for dev, chs in by_dev.items():
        enroll_channels(dev, sorted(chs))
    return len(by_dev)


def set_patch(device, channel, patch):
    """Program Change on the board's channel (companion sketch maps PC -> patch)."""
    tulip.midi_out((0xC0 | ((channel - 1) & 0x0F), patch & 0x7F), device)


def cc(device, channel, control, value):
    """Send a CC to the board (see the companion sketch's CC map)."""
    tulip.midi_out((0xB0 | ((channel - 1) & 0x0F), control & 0x7F, value & 0x7F), device)


def ping(device):
    """AMYboard control-API ping (board replies OK)."""
    tulip.midi_out(bytes(MFR) + b'zIZ' + bytes([0xF7]), device)


# --- push an instrument's FULL sound state to a board ------------------------
#
# A board used to receive only a raw Program Change with `patch & 0x7F`. That
# masked every DX7 patch (128..255) and the piano (256) down onto a WRONG Juno
# (130 & 0x7F == 2), and carried neither the sound-design params nor the voice
# count. These push the real state instead, by running Python ON the board via
# the zP control-API (never amy.send -- see push_instrument's docstring).
#
# Board synth-number mapping (verified against tulip/shared/py/midi.py +
# synth.py): midi.config.add_synth(synth, channel=ch) calls synth.set_channel(ch),
# which sets synth.synth = ch. So the AMY synth registered for a channel is
# addressable as `amy.send(synth=ch, ...)` -- synth number == channel.

_MELODIC = ('juno6', 'dx7', 'piano')


def _fmt_val(v):
    """A Python literal for a board-side amy.send kwarg value, or None when it
    will not serialize cleanly (skip that kwarg rather than push broken code)."""
    if isinstance(v, bool):            # before int: bool is an int subclass
        return str(v)
    if isinstance(v, str):
        return repr(v)                 # AMY coef/bp/eq wire strings
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    return None


def _send_line(ch, kw):
    """One board-side `amy.send(synth=ch, ...)` line from a synth_send_calls
    kwargs dict, or None if any value will not serialize (drop the whole call --
    a half-serialized amy.send would misconfigure the voice)."""
    parts = []
    for k, v in kw.items():
        s = _fmt_val(v)
        if s is None:
            return None
        parts.append("%s=%s" % (k, s))
    return "import amy; amy.send(synth=%d,%s)" % (ch, ",".join(parts))


def _push_param_lines(device, instr, ch):
    """Serialize instr's stored params into board-side amy.send lines (one zP
    message each -- several short messages beat one long one) and push them.
    Returns the number of lines actually sent."""
    try:
        import amyparams
        calls = amyparams.synth_send_calls(instr.get('params', {}),
                                           amyparams.patch_env(instr))
    except Exception:
        return 0
    n = 0
    for kw in calls:
        line = _send_line(ch, kw)
        if line is None:
            continue                   # a value that would not serialize
        try:
            tulip.midi_out(_zp(line), device)
            n += 1
        except Exception:
            pass
    return n


def push_instrument(device, instr):
    """Push an instrument's FULL sound state to the AMYboard at USB `device`.

    Replaces the old bare Program Change (patch & 0x7F) that aliased DX7/piano
    patches onto the wrong Juno and dropped params + voice count. Runs Python
    ON the board (zP control-API) to register a PatchSynth with the FULL patch
    number (0..256, NOT & 0x7F) and this instrument's num_voices on its channel
    -- exactly what the firmware deck listener does at boot (amyboard.py:765) --
    then applies the stored params.

    Melodic engines only (juno6/dx7/piano; a missing type is juno6). gm/gm2/
    drums have no deck-side patch to push and return False -- belt-and-braces
    behind instmove, which already blocks moving those to a board.

    Everything goes through tulip.midi_out (via _zp), NEVER amy.send, so it is
    unaffected by the _AmyBatch context that overrides amy.send in the caller.
    Returns True when a push was made."""
    t = instr.get('type') or 'juno6'
    if t not in _MELODIC:
        return False
    ch = int(instr.get('channel', 2))
    patch = int(instr.get('patch', 0))
    nv = int(instr.get('num_voices', 10))
    # add_synth releases any synth already on this channel first, then binds the
    # new one on that channel (mirrors amyboard.py:765). Full patch number here
    # is the whole point -- & 0x7F is what aliased 130 -> Juno 2.
    line = ("import midi,synth; midi.config.add_synth("
            "synth.PatchSynth(patch=%d,num_voices=%d),channel=%d)"
            % (patch, nv, ch))
    tulip.midi_out(_zp(line), device)
    _push_param_lines(device, instr, ch)
    return True


def push_params(device, instr):
    """Push ONLY the params overlay to the board (live Sound-editor edits),
    without recreating the synth. Addresses the per-channel synth as AMY synth
    number == channel (verified in midi.py/synth.py).

    Graceful by design: a missing amyparams, a bad device, or a param that will
    not serialize cleanly is skipped rather than raised -- a parameditor slider
    drag must never take down the router. Returns the number of lines sent."""
    try:
        ch = int(instr.get('channel', 2))
        return _push_param_lines(device, instr, ch)
    except Exception:
        return 0


# --- push a board's device-level FX overrides to it --------------------------
#
# The Devices>FX editor happily stores per-device FX for a BOARD too, but the
# router only ever transmitted the INTERNAL device's FX -- a board's stored FX
# was placebo (edited, saved, never heard). This pushes it, running Python ON
# the board via the zP control-API (never amy.send in the caller, so it is
# unaffected by the router's _AmyBatch context that overrides amy.send).

def push_fx(device, fx, channels=()):
    """Push a board's stored device-FX overrides to it (chorus/echo/reverb/eq).
    Returns number of zP lines sent.

    A board's per-channel synths all render into AMY bus 0, so:
      * chorus/echo -> a bus-less amy.send (bus 0). Only the buses the USER
        actually set are sent (fx_send_strings returns only touched buses):
        patch FX is baked by the board's OWN patch load, so we push only the
        user's overrides on top -- not a full baseline that would fight it.
      * reverb -> the shared master room, but ONLY when the user configured it
        (mirrors forwarder._room_string's user_set check). The auto-room is an
        internal-only convenience; never push it to a board.
      * eq -> per enrolled synth (synth number == channel, verified in
        midi.py/synth.py), one amy.send(synth=ch, eq=...) for each channel in
        `channels`. Skipped when the user set no EQ or no channels are given.

    Graceful per line (like push_params): a bad device or a serialization slip
    is skipped, never raised -- an FX edit must not take down the router.
    Returns 0 immediately when `fx` is empty/falsy."""
    if not fx:
        return 0
    try:
        import amyparams
    except Exception:
        return 0
    n = 0

    def _emit(code):
        try:
            tulip.midi_out(_zp(code), device)
            return 1
        except Exception:
            return 0

    # chorus/echo: user-touched buses only (empty patch-fx baseline -- the board
    # bakes its patch FX itself; we layer just what the user configured).
    try:
        sends = amyparams.fx_send_strings(fx, {})
    except Exception:
        sends = {}
    for bus_kw, wire in sends.items():
        n += _emit("import amy; amy.send(%s=%r)" % (bus_kw, wire))

    # reverb: the shared room, only when the USER set it (never the auto-room).
    if isinstance(fx.get('reverb'), dict) and fx['reverb']:
        try:
            rv = amyparams.fx_reverb_string(fx)
        except Exception:
            rv = None
        if rv is not None:
            n += _emit("import amy; amy.send(reverb=%r)" % rv)

    # eq: per enrolled synth (synth number == channel); None = user never set EQ.
    try:
        eq = amyparams.fx_eq_string(fx, {})
    except Exception:
        eq = None
    if eq is not None:
        for ch in channels:
            n += _emit("import amy; amy.send(synth=%d,eq=%r)" % (int(ch), eq))

    return n


def num_devices():
    try:
        return tulip.num_midi_devices()
    except Exception:
        return 0
