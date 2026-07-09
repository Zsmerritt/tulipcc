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
def _board_note_on(channel, note, vel):
    c = (channel - 1) & 0x0F
    tulip.midi_out((0x90 | c, int(note) & 0x7F, int(vel * 127) & 0x7F))


def _board_note_off(channel, note):
    c = (channel - 1) & 0x0F
    tulip.midi_out((0x80 | c, int(note) & 0x7F, 0))


def _board_bend_cents(channel, cents, semitone_range=2):
    # Static per-board detune via pitch bend (14-bit, center 8192).
    c = (channel - 1) & 0x0F
    frac = max(-1.0, min(1.0, (cents / 100.0) / semitone_range))
    val = int(8192 + frac * 8191)
    tulip.midi_out((0xE0 | c, val & 0x7F, (val >> 7) & 0x7F))


# --- Stack engine: internal AMY + boards playing one shared profile ---
class _StackEngine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.detune = cfg.get('detune', {})
        self.instances = [i for i in cfg['instances'] if i.get('enabled', True)]
        # a managed internal synth (not bound to the input channel, so Tulip's
        # default handler won't double-play it)
        self.internal = None
        for i in self.instances:
            if i.get('kind') == 'internal':
                try:
                    import synth as _synth
                    self.internal = _synth.PatchSynth(
                        patch=i.get('patch', 0),
                        num_voices=i.get('num_voices', 10))
                except Exception as e:
                    print("forwarder: internal synth failed:", e)
        # apply static per-board detune offsets if unison detune is on
        if self.detune.get('enabled'):
            for idx, i in enumerate(self._boards()):
                _board_bend_cents(i['channel'], self._offset(idx))

    def _boards(self):
        return [i for i in self.instances if i.get('kind') == 'amyboard']

    def _offset(self, idx):
        # symmetric cents spread across N sources
        d = self.detune
        per = d.get('per_instance')
        if per and idx < len(per):
            return per[idx]
        spread = d.get('spread_cents', 8)
        n = max(1, len(self.instances))
        # -spread .. +spread evenly
        return -spread + (2 * spread) * (idx / (n - 1)) if n > 1 else 0

    def note_on(self, note, vel):
        targets = []
        if self.detune.get('enabled'):
            # unison: every instance plays, each detuned
            for idx, inst in enumerate(self.instances):
                off = self._offset(idx)
                if inst.get('kind') == 'internal':
                    if self.internal is not None:
                        self.internal.note_on(note + off / 100.0, vel)
                    targets.append(('internal', note))
                else:
                    _board_note_on(inst['channel'], note, vel)  # board pre-bent
                    targets.append(('board:%d' % inst['channel'], note))
        else:
            # round-robin: one instance takes this note
            inst = self.instances[_state['rr'] % len(self.instances)]
            _state['rr'] += 1
            if inst.get('kind') == 'internal':
                if self.internal is not None:
                    self.internal.note_on(note, vel)
                targets.append(('internal', note))
            else:
                _board_note_on(inst['channel'], note, vel)
                targets.append(('board:%d' % inst['channel'], note))
        return targets

    def note_off(self, targets):
        for kind, out_note in targets:
            if kind == 'internal':
                if self.internal is not None:
                    self.internal.note_off(out_note)
            elif kind.startswith('board:'):
                _board_note_off(int(kind.split(':')[1]), out_note)


def _route(m):
    if not _state['on'] or not m:
        return
    status = m[0] & 0xF0
    ch = (m[0] & 0x0F) + 1

    if _state['mode'] == 'multi':
        # forward messages destined for an AMYboard channel
        if ch in _state['boards']:
            tulip.midi_out(bytes(m))
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
    _state['notes'] = {}
    _state['rr'] = 0
    # the channel the player uses in stack mode = the internal instance channel
    internal = cfg['instances'][0]
    for i in cfg['instances']:
        if i.get('kind') == 'internal':
            internal = i
            break
    _state['stack_in'] = internal.get('channel', 1)
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
