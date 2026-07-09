# forwarder.py -- routes MIDI across the AMY fleet (Tulip + AMYboards).
#
# Registered with midi.add_callback, it runs alongside Tulip's own synth. Since
# tulip.midi_out() broadcasts and each AMYboard listens on its own channel,
# "sending to a board" = emitting the message on that board's channel.
#
#   Mode 'multi'  (independent): a message on an AMYboard's channel is forwarded
#                 out to it; the internal Tulip channel plays locally as usual.
#   Mode 'stack'  (fan-out): notes on the stack input channel are allocated
#                 across all enabled instances -- round-robin for max polyphony,
#                 or unison (all play, each detuned) when detune is enabled.
#
# A note table maps each held note to the instance(s) playing it, so note-offs
# and pitch bends follow the note-on to the right place.

import tulip
import deckcfg

_state = {
    'on': False,
    'mode': 'multi',
    'boards': [],        # list of amyboard channels (0-based) for routing
    'stack': None,       # stack runtime, see _StackEngine
    'notes': {},         # (in_ch, note) -> list of (target, out_note)
    'rr': 0,             # round-robin cursor
}


def _amyboard_channels(cfg):
    return [i['channel'] for i in cfg['instances']
            if i.get('kind') == 'amyboard' and i.get('enabled', True)]


# --- low level board note helpers (channel is 1-16) ---
def _emit(data, device=None):
    # Send to a specific USB-MIDI device once the firmware supports it; fall
    # back to the single-device broadcast on current firmware.
    if device is not None:
        try:
            tulip.midi_out(bytes(data), device)
            return
        except TypeError:
            pass
    tulip.midi_out(bytes(data))


def _board_note_on(channel, note, vel, device=None):
    c = (channel - 1) & 0x0F
    _emit((0x90 | c, int(note) & 0x7F, int(vel * 127) & 0x7F), device)


def _board_note_off(channel, note, device=None):
    c = (channel - 1) & 0x0F
    _emit((0x80 | c, int(note) & 0x7F, 0), device)


def _board_bend_cents(channel, cents, device=None, semitone_range=2):
    # Static per-board detune via pitch bend (14-bit, center 8192).
    c = (channel - 1) & 0x0F
    frac = max(-1.0, min(1.0, (cents / 100.0) / semitone_range))
    val = int(8192 + frac * 8191)
    _emit((0xE0 | c, val & 0x7F, (val >> 7) & 0x7F), device)


# --- Stack engine: internal AMY + boards playing one shared profile ---
class _StackEngine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.detune = cfg.get('detune', {})
        self.instances = [i for i in cfg['instances'] if i.get('enabled', True)]
        # a managed internal synth (not bound to the input channel, so Tulip's
        # default handler won't double-play it)
        self.unison = max(1, self.detune.get('unison_voices', 3))
        # Voice priority: when boards are present and prioritize_boards is set,
        # round-robin allocates to the AMYboards first (their own SoCs do the
        # synthesis, offloading the Tulip and freeing display bandwidth). The
        # internal Tulip AMY is used only if there are no boards.
        self.prio_boards = cfg.get('prioritize_boards', True)
        self.internal = None
        for i in self.instances:
            if i.get('kind') == 'internal':
                try:
                    import synth as _synth
                    # give the internal AMY enough polyphony for unison chords
                    nv = i.get('num_voices', 10)
                    if self.detune.get('enabled'):
                        nv = min(64, max(nv, self.unison * 4))
                    self.internal = _synth.PatchSynth(patch=i.get('patch', 0),
                                                      num_voices=nv)
                except Exception as e:
                    print("forwarder: internal synth failed:", e)
        # apply static per-board detune offsets if unison detune is on
        if self.detune.get('enabled'):
            boards = self._boards()
            for idx, i in enumerate(boards):
                _board_bend_cents(i['channel'], self._spread(idx, len(boards)),
                                  i.get('device'))

    def _boards(self):
        return [i for i in self.instances if i.get('kind') == 'amyboard']

    def _spread(self, k, total):
        # symmetric cents offset for voice k of total (k=0..total-1)
        if total <= 1:
            return 0.0
        s = self.detune.get('spread_cents', 8)
        return -s + (2.0 * s) * (k / (total - 1))

    def note_on(self, note, vel):
        # returns targets: list of (kind, played_note, channel, device)
        targets = []
        if self.detune.get('enabled'):
            # unison: `unison_voices` detuned voices on the internal AMY (float
            # notes), plus one detuned layer per enabled board.
            boards = self._boards()
            for inst in boards:
                _board_note_on(inst['channel'], note, vel, inst.get('device'))
                targets.append(('board', note, inst['channel'], inst.get('device')))
            if self.internal is not None:
                for k in range(self.unison):
                    pn = note + self._spread(k, self.unison) / 100.0
                    self.internal.note_on(pn, vel)
                    targets.append(('internal', pn, None, None))
        else:
            # round-robin: one instance takes this note (max polyphony).
            boards = self._boards()
            alloc = boards if (self.prio_boards and boards) else self.instances
            inst = alloc[_state['rr'] % len(alloc)]
            _state['rr'] += 1
            if inst.get('kind') == 'internal':
                if self.internal is not None:
                    self.internal.note_on(note, vel)
                targets.append(('internal', note, None, None))
            else:
                _board_note_on(inst['channel'], note, vel, inst.get('device'))
                targets.append(('board', note, inst['channel'], inst.get('device')))
        return targets

    def note_off(self, targets):
        for kind, played_note, channel, device in targets:
            if kind == 'internal':
                if self.internal is not None:
                    self.internal.note_off(played_note)
            else:
                _board_note_off(channel, played_note, device)

    def release(self):
        if self.internal is not None:
            try:
                self.internal.release()
            except Exception:
                pass
            self.internal = None


def _route(m):
    if not _state['on'] or not m:
        return
    status = m[0] & 0xF0
    ch = (m[0] & 0x0F) + 1

    if _state['mode'] == 'multi':
        # forward messages destined for an AMYboard channel to that board's device
        if ch in _state['boards']:
            _emit(m, _state['board_dev'].get(ch))
        return

    # stack mode
    st = _state['stack']
    if st is None:
        return
    if ch != _state['stack_in']:
        return
    if status == 0x90 and len(m) > 2 and m[2] > 0:
        tg = st.note_on(m[1], m[2] / 127.0)
        _state['notes'][(ch, m[1])] = tg
    elif status == 0x80 or (status == 0x90 and len(m) > 2 and m[2] == 0):
        tg = _state['notes'].pop((ch, m[1]), None)
        if tg:
            st.note_off(tg)


def start():
    """(Re)start routing from current config. Safe to call repeatedly."""
    import midi
    cfg = deckcfg.load()
    _state['mode'] = cfg.get('mode', 'multi')
    _state['boards'] = _amyboard_channels(cfg)
    _state['board_dev'] = {i['channel']: i.get('device')
                           for i in cfg['instances']
                           if i.get('kind') == 'amyboard' and i.get('enabled', True)}
    _state['notes'] = {}
    _state['rr'] = 0
    # the channel the player uses in stack mode = the internal instance channel
    internal = cfg['instances'][0]
    for i in cfg['instances']:
        if i.get('kind') == 'internal':
            internal = i
            break
    _state['stack_in'] = internal.get('channel', 1)
    # release a previous stack engine's synth so switching modes / restarting
    # doesn't leak voices and exhaust AMY's oscillators.
    if _state.get('stack') is not None:
        _state['stack'].release()
    _state['stack'] = _StackEngine(cfg) if _state['mode'] == 'stack' else None

    if not _state.get('registered'):
        try:
            midi.add_callback(_route)
            _state['registered'] = True
        except Exception as e:
            print("forwarder: add_callback failed:", e)
    _state['on'] = True


def stop():
    _state['on'] = False


def status():
    return {'on': _state['on'], 'mode': _state['mode'],
            'boards': _state['boards'], 'held': len(_state['notes'])}
