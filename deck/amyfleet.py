# amyfleet.py -- Tulip-side helpers to enroll and address AMYboards.
#
# Enrollment assigns each board its own MIDI channel. Because the firmware can
# now target one USB device (tulip.midi_out(sysex, device=N)), we send the
# AMYboard control-API SysEx (zP: run Python on the board) to exactly one board:
# it writes the companion sketch's channel file and restarts, so the channel
# sticks across reboots. Needs each board running deck/amyboard_companion/sketch.py.

import tulip

MFR = (0xF0, 0x00, 0x03, 0x45)   # SPSS / AMYboard manufacturer id


def _zp(code):
    # F0 00 03 45  z P <code> Z  F7  -- "run this line of Python on the board"
    return bytes(MFR) + b'zP' + code.encode() + b'Z' + bytes([0xF7])


def enroll(device, channel):
    """Assign `channel` to the AMYboard at USB `device`, durably.

    On firmware with the deck listener, deck_set_channel stores the channel in
    NVS (survives sketch wipes / factory reset) and installs the listener live.
    On older firmware this line no-ops harmlessly; deploy the companion sketch
    (deck/amyboard_companion) as a fallback there.
    """
    tulip.midi_out(_zp("import amyboard; amyboard.deck_set_channel(%d)" % int(channel)), device)


def enroll_from_config():
    """Enroll every amyboard instance to its configured channel/device."""
    import deckcfg
    n = 0
    for inst in deckcfg.instances():
        if inst.get('kind') == 'amyboard' and inst.get('device') is not None:
            enroll(inst['device'], inst.get('channel', 2))
            n += 1
    return n


def set_patch(device, channel, patch):
    """Program Change on the board's channel (companion sketch maps PC -> patch)."""
    tulip.midi_out((0xC0 | ((channel - 1) & 0x0F), patch & 0x7F), device)


def cc(device, channel, control, value):
    """Send a CC to the board (see the companion sketch's CC map)."""
    tulip.midi_out((0xB0 | ((channel - 1) & 0x0F), control & 0x7F, value & 0x7F), device)


def ping(device):
    """AMYboard control-API ping (board replies OK)."""
    tulip.midi_out(bytes(MFR) + b'zIZ' + bytes([0xF7]), device)


def num_devices():
    try:
        return tulip.num_midi_devices()
    except Exception:
        return 0
