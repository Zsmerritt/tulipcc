# boardlink.py -- deck <-> AMYboard transport, one link per board.
#
# Everything a firmware updater or an instrument-state push needs sits on
# top of this file: BoardLink.send()/recv() move bytes; the physical
# transport underneath is swappable with a ONE-LINE change at the call site:
#
#   link = boardlink.open(device)                      # USB-MIDI (today, working)
#   link = boardlink.open(device, transport='uart')     # hardware UART (later, skeleton)
#
# Callers never see UsbMidiLink or UartLink directly -- both implement the
# same BoardLink interface, so nothing above open() has to change when the
# transport does.
#
# Framing recap (see docs/amyboard/control_api.md and deck/amyfleet.py, which
# this reuses verbatim):
#   F0 00 03 45 <7-bit payload> F7     -- same envelope both directions.
#   00 03 45 is AMYboard's manufacturer id ("SPSS"); the board ignores any
#   SysEx that doesn't start with it, and always replies inside the same
#   envelope (AK / OK / V<version> / X<base64 traceback> / 0|C|E<base64 chunk>).
#
# --- 7-bit codec -------------------------------------------------------
# SysEx data bytes must be 0x00-0x7F (0xF0/0xF7 are reserved framing bytes);
# firmware images and raw AMY state are arbitrary 8-bit binary. encode7/
# decode7 make an exact round trip between the two.
#
# Base64 (via binascii.b2a_base64/a2b_base64) was picked over 8-in-7-bit
# packing:
#   - ~33% size overhead vs ~14% for a hand-rolled packer -- worse, but not
#     dramatically so (a 3.3 MB OTA image becomes ~4.4 MB either way it has
#     to cross a MIDI-speed link).
#   - binascii is real stdlib on BOTH MicroPython and CPython, so this stays
#     a one-line call with no custom bit-packing code to get wrong -- and it
#     is EXACTLY what the rest of this codebase already uses for the same
#     job (tulip/shared/amyboard-py/amyboard.py's X-frame tracebacks,
#     deck/flash_stream.py's transfer protocol, tuliprequests.py).
# If the extra ~19 percentage points of OTA transfer time ever matter, only
# these two functions need to change -- nothing above them (BoardLink, the
# frame header, either transport) depends on the encoding used.
#
# --- pure logic only until UsbMidiLink/UartLink below ------------------
# No tulip/machine import at module scope: `import boardlink` must work
# under plain CPython (pytest) with no mocking at all. tulip/machine are
# imported LAZILY inside the transport classes' own methods -- the same
# lazy-import idiom used throughout deck/*.py (amyfleet.enroll_from_config,
# midimon.panel's `import midi`/`import forwarder`, etc).

import binascii

MFR = (0xF0, 0x00, 0x03, 0x45)   # SPSS / AMYboard manufacturer id (amyfleet.py)


def encode7(data):
    """bytes -> 7-bit-safe bytes (base64, no trailing newline/whitespace)."""
    return binascii.b2a_base64(bytes(data)).rstrip()


def decode7(data):
    """Inverse of encode7. Exact round trip for anything encode7 produced."""
    return bytes(binascii.a2b_base64(data))


def build_envelope(payload):
    """Wrap `payload` bytes in the AMYboard SysEx envelope: F0 00 03 45 ... F7."""
    return bytes(MFR) + bytes(payload) + bytes([0xF7])


def parse_envelope(raw):
    """Inverse of build_envelope.

    Returns the payload bytes (manufacturer id and F0/F7 stripped), or None
    if `raw` is empty/too short or isn't an AMYboard-manufacturer SysEx frame
    -- e.g. some OTHER device's SysEx crossed the same global receive buffer.
    """
    if not raw or len(raw) < 5:
        return None
    if raw[0] != 0xF0 or raw[-1] != 0xF7:
        return None
    if bytes(raw[1:4]) != bytes(MFR[1:4]):
        return None
    return bytes(raw[4:-1])


# --- frame header: opcode byte + payload, for multiplexing over one link ---
#
# This is OUR OWN header, layered INSIDE an AMYboard SysEx payload -- separate
# from AMYboard's own z<cmd> grammar. It lets a higher layer (a firmware
# updater, an instrument-state push) tag what a blob of bytes IS before it
# goes through encode7() and build_envelope(). The opcode is a single 7-bit
# byte (0-0x7F) so it stays SysEx-safe unencoded.
#
# Nothing in this file sends a pack_frame()'d message over the wire today --
# BoardLink.ping()/version() go straight through AMYboard's existing zI/zP
# commands. This is opcode-space + pack/unpack plumbing ONLY, for future
# layers to build on; no OTA or state-push logic is implemented here.
OP_PING = 0x00          # reserved (BoardLink.ping already uses AMYboard's zI)
OP_VERSION = 0x01       # reserved (BoardLink.version already uses AMYboard's zP)
OP_OTA_BEGIN = 0x10     # reserved for a future firmware updater
OP_OTA_DATA = 0x11
OP_OTA_END = 0x12
OP_STATE_PUSH = 0x20    # reserved for a future instrument-state push
OP_STATE_ACK = 0x21
# 0x30-0x7F: unassigned.


def pack_frame(opcode, payload=b''):
    """opcode (0-0x7F) + payload -> one frame body for a multiplexed higher layer."""
    if opcode < 0 or opcode > 0x7F:
        raise ValueError('opcode must be 0-0x7F')
    return bytes([opcode]) + bytes(payload)


def unpack_frame(frame):
    """Inverse of pack_frame. Returns (opcode, payload), or None if frame is empty."""
    if not frame:
        return None
    return (frame[0], bytes(frame[1:]))


# --- BoardLink interface -----------------------------------------------

class BoardLink:
    """One board, one transport. Subclasses implement send()/recv()."""

    def __init__(self, device):
        self.device = device

    def send(self, payload):
        """Frame `payload` (bytes) into the AMYboard SysEx envelope and transmit."""
        raise NotImplementedError

    def recv(self, timeout_ms=200):
        """Next decoded reply payload (bytes, envelope stripped), or None on timeout."""
        raise NotImplementedError

    def ping(self, timeout_ms=1000):
        """AMYboard control-API ping (zI). True if 'OK' comes back in time."""
        self.send(b'zIZ')
        deadline = _now_ms() + timeout_ms
        while True:
            remaining = deadline - _now_ms()
            if remaining <= 0:
                return False
            reply = self.recv(timeout_ms=remaining)
            if reply == b'OK':
                return True

    def version(self, timeout_ms=1000):
        """Ask the board for its firmware version (the 'V' reply). None on timeout.

        Sent as zP (run Python): amyboard.report_version() pushes a 'V' frame
        back asynchronously. An 'AK' (the zP command's own ack) may arrive
        first -- it's simply not a 'V' frame, so the loop below keeps polling.
        """
        self.send(b'zPimport amyboard; amyboard.report_version()Z')
        deadline = _now_ms() + timeout_ms
        while True:
            remaining = deadline - _now_ms()
            if remaining <= 0:
                return None
            reply = self.recv(timeout_ms=remaining)
            if reply and reply[0:1] == b'V':
                return bytes(reply[1:])


def _now_ms():
    # Lazy import (see module header): only reached from ping()/version(),
    # never at `import boardlink` time.
    import tulip
    return tulip.amy_ticks_ms()


# --- USB-MIDI transport (working) ---------------------------------------

class UsbMidiLink(BoardLink):
    """WORKING transport: tulip.midi_out() to send, tulip.sysex_in() to receive.

    Incoming SysEx path (see tulip/shared/py/midi.py's c_fired_midi_event and
    tulip/shared/modtulip.c's tulip_sysex_in): the firmware fills ONE GLOBAL
    sysex_buffer/sysex_len -- there is no per-device tag, a SysEx frame is a
    SysEx frame regardless of which USB port it arrived on -- and schedules
    the Python midi callback. midi.py's own dispatcher only drains that
    buffer into `midi.sysex_callback` if that single slot has been claimed;
    recv() below calls tulip.sysex_in() DIRECTLY instead, so it works whether
    or not anything has claimed that slot, and it consumes each frame at most
    once (tulip.sysex_in() clears sysex_len on read).

    VERIFY ON HARDWARE: with more than one AMYboard attached, or another
    subsystem also polling tulip.sysex_in() / claiming midi.sysex_callback at
    the same time, a reply can be read by whichever caller happens to poll
    first -- there is no per-device receive queue in the firmware today, only
    one global buffer for all incoming SysEx. Fine for driving one board at a
    time (which is all amyfleet.py/the current deck UI ever does); a fleet
    talking to several boards' control APIs concurrently needs either a
    single serialized owner of all SysEx traffic or a firmware-side
    per-device queue -- out of scope here.
    """

    def send(self, payload):
        import tulip
        tulip.midi_out(build_envelope(payload), self.device)

    def recv(self, timeout_ms=200):
        import tulip
        from time import sleep_ms
        deadline = tulip.amy_ticks_ms() + timeout_ms
        while True:
            raw = tulip.sysex_in()
            if raw:
                payload = parse_envelope(raw)
                if payload is not None:
                    return payload
                # Some other device's SysEx (wrong/missing manufacturer id):
                # not ours, drop it and keep polling for the rest of timeout.
            if tulip.amy_ticks_ms() >= deadline:
                return None
            sleep_ms(5)


# --- Hardware UART transport (skeleton) ---------------------------------

class UartLink(BoardLink):
    """SAME interface as UsbMidiLink, over machine.UART -- NOT wired to real
    hardware yet. Framing is ready (build_envelope/parse_envelope, reused
    unchanged from UsbMidiLink -- AMYboard's F0...F7 control-API framing is
    documented as transport-agnostic); the raw byte I/O is TODO stubs until
    a UART-connected AMYboard exists to validate against.

    Switching a caller from USB-MIDI to this is exactly:
        link = boardlink.open(device, transport='uart')
    Nothing else about the caller changes.
    """

    # TODO(hardware): confirm id/pins/baud once a UART-connected AMYboard
    # exists. 31250 is the MIDI-standard baud rate (this link carries the
    # same MIDI/SysEx byte stream, just not over USB); AMYboard's actual
    # UART bring-up may specify something else.
    DEFAULT_UART_ID = 1
    DEFAULT_BAUD = 31250
    DEFAULT_TX_PIN = None
    DEFAULT_RX_PIN = None

    def __init__(self, device, uart_id=None, baudrate=None, tx=None, rx=None):
        BoardLink.__init__(self, device)
        self.uart_id = self.DEFAULT_UART_ID if uart_id is None else uart_id
        self.baudrate = self.DEFAULT_BAUD if baudrate is None else baudrate
        self.tx = self.DEFAULT_TX_PIN if tx is None else tx
        self.rx = self.DEFAULT_RX_PIN if rx is None else rx
        self._uart = None

    def _open(self):
        if self._uart is None:
            import machine
            kwargs = {'baudrate': self.baudrate}
            if self.tx is not None:
                kwargs['tx'] = self.tx
            if self.rx is not None:
                kwargs['rx'] = self.rx
            self._uart = machine.UART(self.uart_id, **kwargs)
        return self._uart

    def send(self, payload):
        # TODO: self._open().write(build_envelope(payload))
        raise NotImplementedError(
            'UartLink.send: TODO -- wire machine.UART.write() once a '
            'UART-connected AMYboard exists to validate against')

    def recv(self, timeout_ms=200):
        # TODO: byte-at-a-time read loop that resyncs on 0xF0 and buffers
        # until 0xF7 (or timeout_ms elapses / a max-length guard trips), then
        # parse_envelope() the result. Unlike USB-MIDI there is no firmware-
        # side sysex_in() equivalent to do this framing for us over a raw
        # UART byte stream -- it has to be built here.
        raise NotImplementedError(
            'UartLink.recv: TODO -- wire machine.UART read + resync framing')


# --- factory -------------------------------------------------------------

_TRANSPORTS = {
    'usbmidi': UsbMidiLink,
    'uart': UartLink,
}


def open(device, transport='usbmidi'):
    """Return a BoardLink to `device` over `transport` ('usbmidi' or 'uart').

    THE one-line swap: everything else in this file and every caller (ping/
    version, a future updater, a future instrument-state push) is written
    against the BoardLink interface, so switching transports is exactly this
    call's `transport` argument -- nothing else changes.

    NOTE: shadows the builtin `open`. Only within this module's namespace
    (call as `boardlink.open(...)`); matches the factory name this file's
    callers are written against.
    """
    try:
        cls = _TRANSPORTS[transport]
    except KeyError:
        raise ValueError('unknown transport %r (want %s)' %
                          (transport, ' or '.join(sorted(_TRANSPORTS))))
    return cls(device)
