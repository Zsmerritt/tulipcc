# forwarder.py -- per-instrument MIDI router (Tulip AMY + AMYboards).
#
# Registered with midi.add_callback, it is the SOLE owner of instrument sound
# generation (deck/PLAN-rework.md Phase 2). Layering is enabled: an incoming MIDI
# message on channel C is dispatched to EVERY enabled instrument whose channel is
# C. Each instrument routes to its device:
#
#   * internal instrument -> its own AMY synth, owned here (not registered with
#     midi.config, so Tulip's default per-channel handler never double-plays it).
#     On start() we also release any midi.config synth on that channel.
#   * board instrument    -> tulip.midi_out(msg, device=index): the raw message
#     is forwarded to that USB-MIDI device, which plays it on channel C.
#
# Same patch on the same channel across devices = a stack (all play in unison);
# different patches on one channel = a layer (all play). (Optional round-robin
# voice spreading for same-patch stacks is a later enhancement -- today every
# instrument on the channel plays every note.)
#
# A note table maps each held (channel, note) to the internal synth(s) playing
# it, so note-offs release the right voices. Board notes are forwarded raw (the
# board tracks its own), so only internal voices need explicit release -- and
# start() releases all owned synths first, preventing the voice leak.

import tulip
import deckcfg

_state = {
    'on': False,
    'synths': {},        # instrument id -> PatchSynth (internal instruments)
    'routes': {},        # channel (1-16) -> list of route entries
    'notes': {},         # (channel, note) -> list of internal instrument ids
    'mpe_members': set(),  # member channels of active MPE zones (AMY C handles)
    'registered': False,
}


def _emit(data, device=None):
    # Send to a specific USB-MIDI device when the firmware supports it; fall back
    # to the single-device broadcast on older firmware.
    if device is not None:
        try:
            tulip.midi_out(bytes(data), device)
            return
        except TypeError:
            pass
    tulip.midi_out(bytes(data))


def _boards_on(entries):
    return [e for e in entries if e['kind'] == 'board']


def _route(m):
    if not _state['on'] or not m:
        return
    status = m[0] & 0xF0
    ch = (m[0] & 0x0F) + 1
    if ch in _state['mpe_members']:
        # Member channel of an active MPE zone: AMY's C layer routes the note +
        # per-note expression to the zone synth (== the zone master channel).
        # The forwarder -- like midi.midi_event_cb -- must not also handle it.
        return
    entries = _state['routes'].get(ch)
    if not entries:
        return

    if status == 0x90 and len(m) > 2 and m[2] > 0:
        note = m[1]
        vel = m[2] / 127.0
        played = []
        for e in entries:
            if e['kind'] == 'internal':
                syn = _state['synths'].get(e['iid'])
                if syn is not None:
                    try:
                        syn.note_on(note, vel)
                    except Exception:
                        pass
                    played.append(e['iid'])
            else:
                _emit(m, e['device'])          # raw forward to the board
        if played:
            _state['notes'][(ch, note)] = played
    elif status == 0x80 or (status == 0x90 and len(m) > 2 and m[2] == 0):
        note = m[1]
        for iid in _state['notes'].pop((ch, note), []):
            syn = _state['synths'].get(iid)
            if syn is not None:
                try:
                    syn.note_off(note)
                except Exception:
                    pass
        for e in _boards_on(entries):
            _emit(m, e['device'])              # forward the note-off raw
    else:
        # CC / pitch bend / aftertouch: forward to boards on this channel (MPE +
        # expression reach the board). Internal CC handling lands in a later
        # milestone.
        for e in _boards_on(entries):
            _emit(m, e['device'])


def _release_synths():
    for syn in _state['synths'].values():
        try:
            syn.release()
        except Exception:
            pass
    _state['synths'] = {}


def _apply_params(syn, params):
    """Push an internal instrument's stored AMY params to its owned synth via
    amy.send(synth=<n>, ...). Also assigns the synth to the device's FX bus
    (internal device = bus 0) so per-bus FX (reverb/chorus/echo/EQ) apply."""
    sn = getattr(syn, 'synth', None)
    if sn is None:
        return
    try:
        import amy
        import amyparams
    except Exception:
        return
    try:
        amy.send(synth=sn, bus=0)     # internal device FX bus
    except Exception:
        pass
    for kw in amyparams.synth_send_calls(params):
        try:
            amy.send(synth=sn, **kw)
        except Exception:
            pass


def _apply_device_fx(cfg, synth_nums):
    """Apply the internal (Tulip) device's FX bus globally on its AMY:
    reverb/chorus/echo via the amy.<bus>() fns, and EQ via amy.send(eq=...) to
    one of the bus's synths. Boards' FX go over the per-board link later."""
    try:
        import amy
        import amyparams
    except Exception:
        return
    fx = deckcfg.device_fx('internal', cfg)
    for bus, kw in amyparams.fx_calls(fx):
        fn = getattr(amy, bus, None)
        if fn is not None:
            try:
                fn(**kw)
            except Exception:
                pass
    if synth_nums:
        try:
            amy.send(synth=synth_nums[0], bus=0)   # ensure it's on the device FX bus
            amy.send(synth=synth_nums[0], eq=amyparams.fx_eq_string(fx))
        except Exception:
            pass


def reapply_params(iid):
    """Re-send one instrument's params to its EXISTING synth (no rebuild), for
    live audition while editing. Falls back to a full start() if it has no synth
    yet."""
    syn = _state['synths'].get(iid)
    if syn is None:
        start()
        return
    instr = deckcfg.get_instrument(iid) or {}
    _apply_params(syn, instr.get('params', {}))


def reapply_fx():
    """Re-apply the internal device FX bus to the live synths (live audition)."""
    nums = [n for n in (getattr(s, 'synth', None)
                        for s in _state['synths'].values()) if n is not None]
    _apply_device_fx(deckcfg.load(), nums)


def start():
    """(Re)build the router from the current instruments. Safe to call
    repeatedly -- releases the previous internal synths first (no voice leak)."""
    import midi
    cfg = deckcfg.load()
    instruments = cfg.get('instruments', [])
    mpe_on = deckcfg.mpe_enabled(cfg)      # global gate (C.4)

    _release_synths()
    _state['routes'] = {}
    _state['notes'] = {}
    _state['mpe_members'] = set()
    internal_synths = []

    import synth as _synth
    for instr in instruments:
        if not instr.get('enabled', True):
            continue
        ch = instr.get('channel', 1)
        dev = instr.get('device')
        mpe = instr.get('mpe', {})
        is_mpe = bool(mpe_on and mpe.get('enabled'))
        route = _state['routes'].setdefault(ch, [])
        if dev == 'internal':
            # The router owns this synth; make sure midi.config has none on the
            # channel so Tulip's default handler doesn't double-play the note.
            try:
                midi.config.release_synth_for_channel(ch)
            except Exception:
                pass
            syn = None
            try:
                # An MPE instrument's synth number MUST equal its zone master
                # channel: AMY's C-layer MPE routing (configure_mpe) dispatches
                # member-channel notes to the synth whose number == master (the
                # spike finding). Non-MPE synths keep their auto-assigned ids
                # (16+), which never collide with lower-zone masters (1-15).
                if is_mpe:
                    syn = _synth.PatchSynth(patch=instr.get('patch', 0),
                                            num_voices=instr.get('num_voices', 10),
                                            channel=ch)
                else:
                    syn = _synth.PatchSynth(patch=instr.get('patch', 0),
                                            num_voices=instr.get('num_voices', 10))
                _state['synths'][instr['id']] = syn
            except Exception as e:
                print("forwarder: internal synth failed:", e)
            if syn is not None:
                # Force AMY instrument allocation now: PatchSynth defers the
                # amy.send(num_voices/patch) that creates instruments[synth]
                # until the first note_on, but per-bus routing (bus=/eq=) needs
                # the instrument to exist first, else AMY warns "synth N not
                # defined (get_bus)". deferred_init() is idempotent.
                di = getattr(syn, 'deferred_init', None)
                if di is not None:
                    try:
                        di()
                    except Exception as e:
                        print("forwarder: synth init failed:", e)
                _apply_params(syn, instr.get('params', {}))
                sn = getattr(syn, 'synth', None)
                if sn is not None:
                    internal_synths.append(sn)
            route.append({'kind': 'internal', 'iid': instr['id']})
            # MPE only when the global gate AND this instrument both enable it.
            if is_mpe and hasattr(midi, 'configure_mpe'):
                import channels
                members = mpe.get('members', 15)
                # Record this zone's member channels so _route defers them to
                # AMY's C layer (pass-through rendering, MPE-DESIGN.md).
                for mc in channels.member_channels(ch, members):
                    _state['mpe_members'].add(mc)
                try:
                    midi.configure_mpe(members, mpe.get('bend', 48), master=ch)
                except Exception:
                    pass
        else:
            # Board: push its patch (Program Change) then forward notes to it.
            try:
                _emit((0xC0 | ((ch - 1) & 0x0F), instr.get('patch', 0) & 0x7F), dev)
            except Exception:
                pass
            route.append({'kind': 'board', 'device': dev})

    _apply_device_fx(cfg, internal_synths)   # internal (Tulip) FX bus

    if not _state.get('registered'):
        try:
            midi.add_callback(_route)
            _state['registered'] = True
        except Exception as e:
            print("forwarder: add_callback failed:", e)
    _state['on'] = True


def _safe_off(syn, note):
    try:
        syn.note_off(note)
    except Exception:
        pass


def preview(iid, note=60, vel=0.8, ms=500):
    """Audition an instrument by id: play a note through the router's owned synth
    (internal) or out to the board (via midi_out). Used by the rack patch picker.
    Requires start() to have run (so the internal synth exists)."""
    instr = deckcfg.get_instrument(iid)
    if instr is None:
        return
    if instr.get('device') == 'internal':
        syn = _state['synths'].get(iid)
        if syn is not None:
            try:
                syn.note_on(note, vel)
                tulip.defer(lambda x: _safe_off(syn, note), 0, ms)
            except Exception:
                pass
    else:
        c = (instr.get('channel', 1) - 1) & 0x0F
        dev = instr.get('device')
        try:
            _emit((0x90 | c, int(note) & 0x7F, int(vel * 127) & 0x7F), dev)
            tulip.defer(lambda x: _emit((0x80 | c, int(note) & 0x7F, 0), dev),
                        0, ms)
        except Exception:
            pass


def stop():
    _state['on'] = False


def status():
    return {'on': _state['on'], 'channels': sorted(_state['routes'].keys()),
            'synths': len(_state['synths']), 'held': len(_state['notes'])}
