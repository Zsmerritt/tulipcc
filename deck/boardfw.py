# boardfw.py -- Tulip drives a firmware update onto an attached AMYboard.
#
# The Tulip streams an OTA app image from its own filesystem (/user or /sd) to
# a connected AMYboard, which writes it to its INACTIVE OTA slot, verifies it,
# flips the boot pointer and reboots into the new firmware. Same ESP32-S3 OTA
# mechanism the Tulip uses on itself (deck/flash_stream.py, deck/flashlib.py);
# this is the board-to-board version, over a boardxport.Transport.
#
# LAYERING
#   boardlink.py   -- one SysEx payload on/off the wire (+ opcode registry)
#   boardxport.py  -- framed 8-bit pipe, transport-swappable (USB-MIDI | UART)
#   boardfw.py     -- THIS: the OTA message codec + the update flow, both ends
#
# Both ends live here on purpose: OtaSender runs on the Tulip, OtaReceiver runs
# on the AMYboard companion (deck/amyboard_companion/sketch.py imports it), and
# they MUST agree on the frame codec byte-for-byte. Keeping one copy is why the
# codec is shared rather than duplicated. Nothing here imports tulip/machine at
# module scope, so it loads under CPython (pytest) and MicroPython alike;
# hashlib/binascii/struct are real stdlib on both.
#
# WIRE PROTOCOL (each item is one boardxport frame; transport handles 7-bit
# safety + enveloping, so these are raw 8-bit):
#   host -> dev  OP_OTA_QUERY   : (empty)            "who are you?"
#   dev  -> host OP_OTA_INFO    : capacity(4) version(utf-8)
#   host -> dev  OP_OTA_BEGIN   : size(4) sha256(32) version(utf-8)
#   dev  -> host OP_OTA_ACK     : seq(2)=0xFFFF status(1)   (begin accepted?)
#   host -> dev  OP_OTA_DATA    : seq(2) check(4)=sha256(chunk)[:4] chunk
#   dev  -> host OP_OTA_ACK     : seq(2) status(1)          (per chunk, lockstep)
#   host -> dev  OP_OTA_END     : (empty)             "image sent -- verify+commit"
#   dev  -> host OP_OTA_RESULT  : status(1) message(utf-8)
#
# Lockstep acks with resend-on-BAD are lifted straight from flash_stream.py: a
# corrupted or out-of-order chunk is NAK'd and resent immediately, so console
# chatter / a dropped SysEx frame can't silently corrupt the image. Integrity
# is checked twice -- a fast per-chunk sha prefix on the wire, and the full
# image sha256 at commit -- matching the design's "integrity stays" policy.
#
# MEMORY: the Tulip streams the image from a file, never loading it into RAM;
# the AMYboard buffers at most one chunk + one 4 KB flash sector at a time.
# Internal SRAM is critically full on both devices -- neither side ever holds
# the whole 3.2 MB image.

import binascii
import hashlib
import struct

import boardlink

# status bytes
ST_OK = 1
ST_BAD = 0
ST_FAIL = 0

# The ACK seq that answers OP_OTA_BEGIN (no real chunk owns 0xFFFF).
BEGIN_SEQ = 0xFFFF

# Default image chunk (raw bytes per OP_OTA_DATA frame, before 8->7 packing).
# Small enough to sit comfortably inside a SysEx frame and the board's
# one-chunk buffer; pack8to7 grows it ~+14%.
DEFAULT_CHUNK = 1024

SECTOR = 4096          # ESP32 flash erase/write block


# --- frame codec (shared by both ends) -------------------------------------

def _sha(data):
    return hashlib.sha256(data).digest()


def enc_query():
    return boardlink.pack_frame(boardlink.OP_OTA_QUERY)


def enc_info(capacity, version):
    return boardlink.pack_frame(
        boardlink.OP_OTA_INFO,
        struct.pack('>I', capacity) + _to_bytes(version))


def dec_info(frame):
    """(capacity, version_bytes) from an OP_OTA_INFO frame, or None."""
    op, payload = boardlink.unpack_frame(frame)
    if op != boardlink.OP_OTA_INFO or len(payload) < 4:
        return None
    return (struct.unpack('>I', payload[:4])[0], payload[4:])


def enc_begin(size, sha32, version):
    if len(sha32) != 32:
        raise ValueError('sha32 must be 32 bytes')
    return boardlink.pack_frame(
        boardlink.OP_OTA_BEGIN,
        struct.pack('>I', size) + bytes(sha32) + _to_bytes(version))


def dec_begin(frame):
    """(size, sha32, version_bytes) from an OP_OTA_BEGIN frame, or None."""
    op, payload = boardlink.unpack_frame(frame)
    if op != boardlink.OP_OTA_BEGIN or len(payload) < 36:
        return None
    size = struct.unpack('>I', payload[:4])[0]
    return (size, payload[4:36], payload[36:])


def enc_data(seq, chunk):
    return boardlink.pack_frame(
        boardlink.OP_OTA_DATA,
        struct.pack('>H', seq) + _sha(chunk)[:4] + bytes(chunk))


def dec_data(frame):
    """(seq, chunk) from an OP_OTA_DATA frame if its check matches, else None.

    A mismatched check (corruption on the wire) returns None so the receiver
    NAKs it -- exactly flash_stream.py's per-frame sha[:8] guard.
    """
    op, payload = boardlink.unpack_frame(frame)
    if op != boardlink.OP_OTA_DATA or len(payload) < 6:
        return None
    seq = struct.unpack('>H', payload[:2])[0]
    check = payload[2:6]
    chunk = payload[6:]
    if _sha(chunk)[:4] != check:
        return None
    return (seq, chunk)


def enc_end():
    return boardlink.pack_frame(boardlink.OP_OTA_END)


def enc_ack(seq, status):
    return boardlink.pack_frame(
        boardlink.OP_OTA_ACK, struct.pack('>H', seq) + bytes([status]))


def dec_ack(frame):
    """(seq, status) from an OP_OTA_ACK frame, or None."""
    op, payload = boardlink.unpack_frame(frame)
    if op != boardlink.OP_OTA_ACK or len(payload) != 3:
        return None
    return (struct.unpack('>H', payload[:2])[0], payload[2])


def enc_result(status, message=''):
    return boardlink.pack_frame(
        boardlink.OP_OTA_RESULT, bytes([status]) + _to_bytes(message))


def dec_result(frame):
    """(status, message_bytes) from an OP_OTA_RESULT frame, or None."""
    op, payload = boardlink.unpack_frame(frame)
    if op != boardlink.OP_OTA_RESULT or len(payload) < 1:
        return None
    return (payload[0], payload[1:])


def _to_bytes(s):
    if isinstance(s, (bytes, bytearray)):
        return bytes(s)
    return s.encode('utf-8')


def _opcode(frame):
    up = boardlink.unpack_frame(frame)
    return None if up is None else up[0]


# --- Tulip side: drive the update -----------------------------------------

class OtaSender:
    """Streams a firmware image from the Tulip's filesystem to a board.

    Parameters
      transport    : a boardxport.Transport (open() picks USB-MIDI vs UART)
      open_stream  : callable -> a FRESH readable binary file each call. The
                     image is never loaded whole into RAM; it is read in
                     `chunk`-sized pieces (once to hash, once to stream).
      size         : image size in bytes
      version      : version string advertised in OP_OTA_BEGIN (optional)
      chunk        : bytes per OP_OTA_DATA frame
      tries        : resend attempts per chunk before giving up
      ack_timeout_ms : how long to wait for each ack
      progress     : optional callback(sent_bytes, total_bytes, phase)
      should_continue : optional callable -> False to abort mid-stream

    run() returns a result dict: {'ok': bool, 'phase': str, 'sha': hex,
    'message': str, 'sent': int, 'frames': int}.
    """

    def __init__(self, transport, open_stream, size, version='',
                 chunk=DEFAULT_CHUNK, tries=5, ack_timeout_ms=4000,
                 begin_timeout_ms=8000, result_timeout_ms=30000,
                 progress=None, should_continue=None):
        self.transport = transport
        self.open_stream = open_stream
        self.size = size
        self.version = version
        self.chunk = chunk
        self.tries = tries
        self.ack_timeout_ms = ack_timeout_ms
        self.begin_timeout_ms = begin_timeout_ms
        self.result_timeout_ms = result_timeout_ms
        self.progress = progress
        self.should_continue = should_continue

    def _emit(self, sent, phase):
        if self.progress:
            try:
                self.progress(sent, self.size, phase)
            except Exception:
                pass

    def _aborted(self):
        return self.should_continue is not None and not self.should_continue()

    def _hash_image(self):
        """Stream the file once to get the whole-image sha256 (never buffered
        entire in RAM)."""
        h = hashlib.sha256()
        f = self.open_stream()
        try:
            while True:
                b = f.read(self.chunk)
                if not b:
                    break
                h.update(b)
        finally:
            f.close()
        return h.digest()

    def _recv_matching(self, decoder, timeout_ms):
        """Poll for a frame the decoder accepts; ignore anything else (stray
        acks, chatter) until timeout."""
        import tulip
        deadline = tulip.amy_ticks_ms() + timeout_ms
        while True:
            remaining = deadline - tulip.amy_ticks_ms()
            if remaining <= 0:
                return None
            frame = self.transport.recv_frame(timeout_ms=remaining)
            if frame is None:
                continue
            got = decoder(frame)
            if got is not None:
                return got

    def probe(self, timeout_ms=4000):
        """Ask the board its identity/version (OP_OTA_QUERY -> OP_OTA_INFO).

        Returns (capacity, version_str) or None on timeout. Used to confirm a
        board is present and updatable before, and its NEW version after, an
        update. (boardlink.BoardLink.version() is the pre-transport equivalent
        over the control API; this one rides the same OTA transport so it works
        identically once the wire is UART.)"""
        self.transport.flush()
        self.transport.send_frame(enc_query())
        got = self._recv_matching(dec_info, timeout_ms)
        if got is None:
            return None
        capacity, ver = got
        return (capacity, ver.decode('utf-8', 'replace'))

    def run(self):
        sha32 = self._hash_image()
        sha_hex = binascii.hexlify(sha32).decode()
        result = {'ok': False, 'phase': 'begin', 'sha': sha_hex,
                  'message': '', 'sent': 0, 'frames': 0}

        # BEGIN handshake. The board answers with either an ACK (accepted) or a
        # RESULT (refused, e.g. image larger than the slot); accept both.
        def _begin_reply(frame):
            a = dec_ack(frame)
            if a is not None:
                return ('ack', a)
            r = dec_result(frame)
            if r is not None:
                return ('result', r)
            return None

        self.transport.flush()
        self._emit(0, 'begin')
        self.transport.send_frame(enc_begin(self.size, sha32, self.version))
        got = self._recv_matching(_begin_reply, self.begin_timeout_ms)
        if got is None:
            result['message'] = 'no BEGIN ack (board not responding)'
            return result
        kind, val = got
        if kind == 'result':
            result['message'] = ('board refused BEGIN: '
                                 + val[1].decode('utf-8', 'replace'))
            return result
        if val[0] != BEGIN_SEQ or val[1] != ST_OK:
            result['message'] = 'board refused BEGIN (image too large?)'
            return result

        # DATA stream, lockstep
        result['phase'] = 'stream'
        f = self.open_stream()
        seq = 0
        sent = 0
        try:
            while True:
                if self._aborted():
                    result['phase'] = 'aborted'
                    result['message'] = 'aborted by caller'
                    return result
                chunk = f.read(self.chunk)
                if not chunk:
                    break
                frame = enc_data(seq, chunk)
                if not self._send_acked(frame, seq):
                    result['phase'] = 'stream'
                    result['message'] = 'chunk %d never acked' % seq
                    result['sent'] = sent
                    result['frames'] = seq
                    return result
                sent += len(chunk)
                seq = (seq + 1) & 0xFFFF
                result['sent'] = sent
                result['frames'] = seq
                self._emit(sent, 'stream')
        finally:
            f.close()

        # END -> verify + commit on the board
        result['phase'] = 'commit'
        self._emit(sent, 'commit')
        self.transport.send_frame(enc_end())
        res = self._recv_matching(dec_result, self.result_timeout_ms)
        if res is None:
            result['message'] = 'no commit result from board'
            return result
        status, msg = res
        result['message'] = msg.decode('utf-8', 'replace')
        result['ok'] = (status == ST_OK)
        result['phase'] = 'done' if result['ok'] else 'commit'
        return result

    def _send_acked(self, frame, seq):
        """Send one frame, wait for its OK ack, resend on BAD/timeout."""
        for _ in range(self.tries):
            self.transport.send_frame(frame)
            ack = self._recv_matching(dec_ack, self.ack_timeout_ms)
            if ack is not None and ack[0] == seq and ack[1] == ST_OK:
                return True
            # BAD, wrong-seq, or timeout -> resend the same frame
        return False


# --- AMYboard side: receive + write OTA -----------------------------------

class OtaReceiver:
    """Consumes OTA frames on the AMYboard and writes the image to its inactive
    OTA slot. Pure state machine: feed(frame) -> a reply frame (bytes) or None.

    The companion sketch owns the wire; it hands each decoded frame to feed()
    and transmits whatever feed() returns. That keeps ALL protocol/verify logic
    host-testable (deck/test_deck.py drives it with a fake partition), with no
    tulip/machine dependency in this class.

    Parameters
      partition : the target OTA Partition object. Needs writeblocks(block,
                  buf) / readblocks(block, buf) / info() (info()[3] == byte
                  capacity, like esp32.Partition) / set_boot(). Injected so a
                  fake bytearray-backed partition tests it on the host.
      version   : this board's firmware version, reported to OP_OTA_QUERY.
      sector    : flash block size (4096).
      write_tries : per-block write-verify-retry attempts (flashlib pattern).

    Memory: one 4 KB sector buffer + the current chunk. Never the whole image.
    """

    def __init__(self, partition, version='', sector=SECTOR, write_tries=4):
        self.partition = partition
        self.version = version
        self.sector = sector
        self.write_tries = write_tries
        self._vbuf = bytearray(sector)
        self._reset()

    def _reset(self):
        self.active = False
        self.size = 0
        self.want_sha = b''
        self.expect_seq = 0
        self.block = 0
        self.received = 0
        self._buf = bytearray()
        self._h = None
        self.bad_block = False

    def _capacity(self):
        try:
            return self.partition.info()[3]
        except Exception:
            return 0

    def feed(self, frame):
        op = _opcode(frame)
        if op == boardlink.OP_OTA_QUERY:
            return enc_info(self._capacity(), self.version)
        if op == boardlink.OP_OTA_BEGIN:
            return self._on_begin(frame)
        if op == boardlink.OP_OTA_DATA:
            return self._on_data(frame)
        if op == boardlink.OP_OTA_END:
            return self._on_end()
        return None            # unknown/foreign frame: ignore

    def _on_begin(self, frame):
        dec = dec_begin(frame)
        if dec is None:
            return enc_result(ST_FAIL, 'bad BEGIN')
        size, sha32, version = dec
        self._reset()          # a fresh BEGIN restarts from scratch (no resume)
        cap = self._capacity()
        if cap and size > cap:
            return enc_result(ST_FAIL, 'image larger than slot')
        self.active = True
        self.size = size
        self.want_sha = binascii.hexlify(sha32).decode()
        self._h = hashlib.sha256()
        return enc_ack(BEGIN_SEQ, ST_OK)

    def _on_data(self, frame):
        if not self.active:
            return enc_ack(0, ST_BAD)
        dec = dec_data(frame)
        if dec is None:
            # corrupt check -> NAK; host resends. seq unknown, echo expect.
            return enc_ack(self.expect_seq & 0xFFFF, ST_BAD)
        seq, chunk = dec
        if self.expect_seq > 0 and seq == ((self.expect_seq - 1) & 0xFFFF):
            # Duplicate of the frame we last accepted: our OK ack was lost and
            # the host resent. Re-ack idempotently WITHOUT rewriting -- this is
            # what makes the lockstep survive a dropped ack instead of
            # deadlocking (host resends seq N while we already want N+1).
            return enc_ack(seq, ST_OK)
        if seq != (self.expect_seq & 0xFFFF):
            # genuinely out of order / unexpected -> NAK.
            return enc_ack(seq, ST_BAD)
        self._buf.extend(chunk)
        self._h.update(chunk)
        self.received += len(chunk)
        self._flush_full_sectors()
        self.expect_seq += 1
        return enc_ack(seq, ST_OK)

    def _flush_full_sectors(self):
        while len(self._buf) >= self.sector:
            self._write_block(self.block, bytes(self._buf[:self.sector]))
            del self._buf[:self.sector]
            self.block += 1

    def _write_block(self, block, data):
        # WRITE-VERIFY-RETRY: sustained multi-MB writes on this flash scatter
        # bad sectors that single writes don't (flashlib.DEVICE_OTA). Verify
        # each block and rewrite up to write_tries; a persistent failure is
        # remembered and fails the commit.
        for _ in range(self.write_tries):
            self.partition.writeblocks(block, data)
            self.partition.readblocks(block, self._vbuf)
            if bytes(self._vbuf) == data:
                return
        self.bad_block = True

    def _on_end(self):
        if not self.active:
            return enc_result(ST_FAIL, 'no active transfer')
        # flush the final partial sector, padded with 0xFF (erased-flash value)
        if self._buf:
            pad = self.sector - len(self._buf)
            self._write_block(self.block, bytes(self._buf) + b'\xff' * pad)
            self.block += 1
            self._buf = bytearray()
        got = self._h.hexdigest() if hasattr(self._h, 'hexdigest') else \
            binascii.hexlify(self._h.digest()).decode()
        if self.bad_block:
            self.active = False
            return enc_result(ST_FAIL, 'flash write-verify failed')
        if got != self.want_sha:
            self.active = False
            return enc_result(ST_FAIL, 'image sha mismatch')
        if self.received != self.size:
            self.active = False
            return enc_result(ST_FAIL, 'size mismatch %d/%d'
                              % (self.received, self.size))
        try:
            self.partition.set_boot()
        except Exception as e:
            self.active = False
            return enc_result(ST_FAIL, 'set_boot failed: %s' % e)
        self.active = False
        return enc_result(ST_OK, 'ok')
