# boardxport.py -- framed byte transport for deck <-> AMYboard, transport-swappable.
#
# This sits ONE layer above boardlink.py. boardlink moves a single SysEx
# payload across a link; boardxport turns that into a "send this arbitrary
# 8-bit frame / give me the next 8-bit frame" pipe that a firmware updater
# (deck/boardfw.py) drives without caring whether the wire underneath is
# USB-MIDI today or a raw UART tomorrow:
#
#   xport = boardxport.open(device)                    # USB-MIDI (today, working)
#   xport = boardxport.open(device, transport='uart')  # raw UART (later, skeleton)
#
# Every caller is written against the Transport interface (send_frame /
# recv_frame / flush), so switching the wire is exactly the `transport=`
# argument above -- nothing in boardfw.py or the AMYboard companion changes.
#
# --- why a NEW 8-to-7 packer and not boardlink.encode7 (base64) ------------
# boardlink.encode7/decode7 use base64 (~+33% size) because they carry small
# control-API blobs where a stdlib one-liner beats hand-rolled bit-packing.
# A ~3.2 MB OTA image is a different regime: the ~19 percentage points base64
# wastes is real transfer time over a MIDI-speed link. pack8to7/unpack7to8
# below are the classic MIDI 8-in-7 scheme -- 7 data bytes -> 8 SysEx-safe
# bytes, a fixed +14.3% -- and are pure functions with an exact round trip, so
# they unit-test on the host with no hardware (see deck/test_deck.py).
#
# --- pure logic until UsbMidiTransport/UartTransport below -----------------
# No tulip/machine import at module scope: `import boardxport` must work under
# plain CPython (pytest). The transports import boardlink/tulip/machine LAZILY
# inside their own methods, matching boardlink.py's idiom.

# 0x00-0x7F only in the packed output, so it survives a SysEx data region
# (0xF0/0xF7 stay reserved for framing). 7 in, 8 out.
_GROUP = 7


def pack8to7(data):
    """Arbitrary bytes -> 7-bit-safe bytes (MIDI 8-in-7). Exact round trip.

    For each group of up to 7 input bytes we emit one "high-bits" byte (bit j
    set == input byte j had its 0x80 bit set) followed by each input byte
    masked to 7 bits. Overhead is a fixed +1 byte per 7 (~+14.3%).
    """
    data = bytes(data)
    out = bytearray()
    n = len(data)
    i = 0
    while i < n:
        group = data[i:i + _GROUP]
        i += _GROUP
        msb = 0
        for j in range(len(group)):
            if group[j] & 0x80:
                msb |= (1 << j)
        out.append(msb)
        for b in group:
            out.append(b & 0x7F)
    return bytes(out)


def unpack7to8(packed):
    """Inverse of pack8to7. Exact round trip for anything pack8to7 produced.

    Robust to a truncated final group (the last high-bits byte may be followed
    by fewer than 7 data bytes) -- it consumes whatever data bytes remain.
    """
    packed = bytes(packed)
    out = bytearray()
    n = len(packed)
    i = 0
    while i < n:
        msb = packed[i]
        i += 1
        k = n - i
        if k > _GROUP:
            k = _GROUP
        for j in range(k):
            b = packed[i + j] & 0x7F
            if msb & (1 << j):
                b |= 0x80
            out.append(b)
        i += k
    return bytes(out)


# --- Transport interface ---------------------------------------------------

class Transport:
    """A framed 8-bit pipe to one board. Subclasses implement the wire.

    send_frame(frame)  -- transmit one arbitrary 8-bit frame (bytes).
    recv_frame(timeout_ms) -- next received 8-bit frame, or None on timeout.
    flush()            -- drop any buffered/pending inbound frames.

    Framing and 7-bit safety are the transport's job; a frame handed to
    send_frame comes back byte-for-byte from the peer's recv_frame.
    """

    def __init__(self, device):
        self.device = device

    def send_frame(self, frame):
        raise NotImplementedError

    def recv_frame(self, timeout_ms=200):
        raise NotImplementedError

    def flush(self):
        raise NotImplementedError


# --- USB-MIDI transport (working) ------------------------------------------

class UsbMidiTransport(Transport):
    """WORKING transport: an 8-bit frame is pack8to7()'d to SysEx-safe bytes
    and carried inside boardlink's AMYboard SysEx envelope (F0 00 03 45 .. F7).

    Reuses boardlink.UsbMidiLink VERBATIM for the actual bytes on/off the wire
    (tulip.midi_out / tulip.sysex_in, and the single-global-sysex-buffer
    caveats documented there) -- boardxport adds only the 8<->7 packing so the
    layer above can speak raw binary. No boardlink changes are needed for this
    transport.
    """

    def __init__(self, device, link=None):
        Transport.__init__(self, device)
        self._link = link          # injectable for host tests

    def _get_link(self):
        if self._link is None:
            import boardlink
            self._link = boardlink.UsbMidiLink(self.device)
        return self._link

    def send_frame(self, frame):
        self._get_link().send(pack8to7(frame))

    def recv_frame(self, timeout_ms=200):
        payload = self._get_link().recv(timeout_ms=timeout_ms)
        if payload is None:
            return None
        return unpack7to8(payload)

    def flush(self):
        # Drain any already-buffered inbound SysEx frame(s) with a zero-wait
        # poll, so a stale reply can't be mistaken for the next one.
        link = self._get_link()
        while link.recv(timeout_ms=0) is not None:
            pass


# --- Raw UART transport (skeleton, not wired) ------------------------------

class UartTransport(Transport):
    """SAME interface as UsbMidiTransport, over a raw 8-bit UART -- NOT wired to
    real hardware yet.

    A UART carries 8-bit bytes natively, so there is NO 7-bit packing here:
    frames go out length-prefixed like deck/flash_stream.py's FWD:/FWA: line
    protocol (a start byte, a 2-byte big-endian length, the raw frame, then a
    1-byte XOR checkbyte), and recv resyncs on the start byte. That framing is
    written below; only the machine.UART byte I/O is a TODO stub until a
    UART-connected AMYboard exists to validate against.

    Switching a caller from USB-MIDI to this is exactly:
        xport = boardxport.open(device, transport='uart')
    Nothing else about the caller changes.
    """

    # TODO(hardware): confirm id/pins/baud once a UART-connected AMYboard
    # exists. 460800 is a placeholder -- an OTA image over 31250 baud MIDI is
    # slow, and a dedicated UART can run far faster; the real value comes from
    # AMYboard's UART bring-up.
    DEFAULT_UART_ID = 1
    DEFAULT_BAUD = 460800
    DEFAULT_TX_PIN = None
    DEFAULT_RX_PIN = None
    START = 0x7E                    # frame start byte (resync anchor)
    MAX_FRAME = 0xFFFF

    def __init__(self, device, uart_id=None, baudrate=None, tx=None, rx=None):
        Transport.__init__(self, device)
        self.uart_id = self.DEFAULT_UART_ID if uart_id is None else uart_id
        self.baudrate = self.DEFAULT_BAUD if baudrate is None else baudrate
        self.tx = self.DEFAULT_TX_PIN if tx is None else tx
        self.rx = self.DEFAULT_RX_PIN if rx is None else rx
        self._uart = None

    # -- pure framing helpers (host-testable; no hardware) ------------------

    @staticmethod
    def _checkbyte(frame):
        c = 0
        for b in frame:
            c ^= b
        return c & 0xFF

    @classmethod
    def build_wire(cls, frame):
        """One frame -> on-wire bytes: START | len(2 BE) | frame | xor."""
        frame = bytes(frame)
        if len(frame) > cls.MAX_FRAME:
            raise ValueError('frame too large for UART framing')
        n = len(frame)
        return (bytes([cls.START, (n >> 8) & 0xFF, n & 0xFF])
                + frame + bytes([cls._checkbyte(frame)]))

    @classmethod
    def parse_wire(cls, buf):
        """Inverse of build_wire on a complete buffer.

        Returns (frame, consumed) if a full valid frame is present at/after the
        next START byte, else (None, consumed) after skipping resync garbage.
        Lets recv_frame's byte loop stay a thin wrapper over this pure logic.
        """
        buf = bytes(buf)
        i = 0
        n = len(buf)
        # resync to START
        while i < n and buf[i] != cls.START:
            i += 1
        if i >= n or n - i < 4:
            return (None, i)
        length = (buf[i + 1] << 8) | buf[i + 2]
        end = i + 3 + length
        if end >= n:
            return (None, i)       # frame not fully arrived yet
        frame = buf[i + 3:end]
        chk = buf[end]
        if cls._checkbyte(frame) != chk:
            # bad checkbyte: skip this START and let caller resync past it
            return (None, i + 1)
        return (frame, end + 1)

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

    def send_frame(self, frame):
        # TODO(hardware): self._open().write(self.build_wire(frame))
        raise NotImplementedError(
            'UartTransport.send_frame: TODO -- wire machine.UART.write() '
            '(framing via build_wire is ready) once a UART AMYboard exists')

    def recv_frame(self, timeout_ms=200):
        # TODO(hardware): accumulate bytes from self._open().read() into a
        # buffer, feeding parse_wire() until it returns a frame or timeout_ms
        # elapses; drop resync garbage per parse_wire's `consumed`.
        raise NotImplementedError(
            'UartTransport.recv_frame: TODO -- wire machine.UART.read() '
            '(framing via parse_wire is ready) once a UART AMYboard exists')

    def flush(self):
        # TODO(hardware): self._open().read()  # discard pending bytes
        raise NotImplementedError(
            'UartTransport.flush: TODO -- wire machine.UART pending drain')


# --- factory ---------------------------------------------------------------

_TRANSPORTS = {
    'usbmidi': UsbMidiTransport,
    'uart': UartTransport,
}


def open(device, transport='usbmidi'):
    """Return a Transport to `device` over `transport` ('usbmidi' or 'uart').

    THE one-line swap: boardfw.py and the AMYboard companion are written
    against the Transport interface, so changing the wire is exactly this
    call's `transport` argument -- nothing else changes.

    Shadows the builtin `open` inside this module only; call as
    boardxport.open(...) (matches boardlink.open's factory convention).
    """
    try:
        cls = _TRANSPORTS[transport]
    except KeyError:
        raise ValueError('unknown transport %r (want %s)' %
                          (transport, ' or '.join(sorted(_TRANSPORTS))))
    return cls(device)
