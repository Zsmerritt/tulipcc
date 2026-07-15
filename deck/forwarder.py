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
    'routes': {},        # channel (1-16) -> ([internal iids], [board devices])
    'notes': {},         # (channel, note) -> list of internal instrument ids
    'mpe_members': set(),  # member channels of active MPE zones (AMY C handles)
    'registered': False,
    'has_device_arg': None,  # firmware supports tulip.midi_out(msg, device)?
    'err_iids': set(),   # instrument ids whose synth already logged a failure
    'seen': 0,           # messages seen (activity flicker in the top-bar chips)
    'c_channels': set(),  # channels whose notes AMY's C layer plays directly
}

_EMPTY = ((), ())        # shared empty route (no per-message allocation)


def _has_device_arg():
    # tulip.num_midi_devices shipped in the same firmware as midi_out's device
    # arg, so its presence is the capability probe -- checked once, not by
    # raising TypeError per MIDI message on older firmware.
    flag = _state['has_device_arg']
    if flag is None:
        flag = hasattr(tulip, 'num_midi_devices')
        _state['has_device_arg'] = flag
    return flag


def _emit(data, device=None):
    if not isinstance(data, bytes):
        data = bytes(data)
    if device is not None and _has_device_arg():
        tulip.midi_out(data, device)
    else:
        tulip.midi_out(data)


def _synth_err(iid, e):
    # Log the first failure per instrument (a broken synth would otherwise
    # throw -- silently, or worse noisily -- on every note).
    if iid in _state['err_iids']:
        return
    _state['err_iids'].add(iid)
    try:
        import decklog
        decklog.log_exc("forwarder: synth for instrument %s failed" % iid, e)
    except Exception:
        pass


def _route(m):
    # The per-MIDI-message hot path: no list/dict allocation for CC/bend/
    # pressure streams (MPE controllers send 100+ msgs/sec), which keeps
    # MicroPython GC pauses out of the note timing.
    if not _state['on'] or not m:
        return
    _state['seen'] += 1          # activity counter (chip flicker reads this)
    status = m[0] & 0xF0
    ch = (m[0] & 0x0F) + 1
    if ch in _state['mpe_members']:
        # Member channel of an active MPE zone: AMY's C layer routes the note +
        # per-note expression to the zone synth (== the zone master channel).
        # The forwarder -- like midi.midi_event_cb -- must not also handle it.
        return
    iids, boards = _state['routes'].get(ch, _EMPTY)
    if not iids and not boards:
        return
    # Channels with a SINGLE internal instrument are C-OWNED: their synth
    # number equals the channel, so AMY's C MIDI layer plays the notes
    # directly (same as stock Tulip) -- zero Python in the note path, immune
    # to any MP-task stall (the residual missed-note/latency reports). Python
    # only handles layered channels, board forwarding, and UI state.
    c_owned = ch in _state['c_channels']

    if status == 0x90 and len(m) > 2 and m[2] > 0:
        if not c_owned:
            note = m[1]
            vel = m[2] / 127.0
            played = None
            synths = _state['synths']
            for iid in iids:
                syn = synths.get(iid)
                if syn is not None:
                    try:
                        syn.note_on(note, vel)
                    except Exception as e:
                        _synth_err(iid, e)
                    if played is None:
                        played = []
                    played.append(iid)
            if played:
                _state['notes'][(ch, note)] = played
        for dev in boards:
            _emit(m, dev)                      # raw forward to the board
    elif status == 0x80 or (status == 0x90 and len(m) > 2 and m[2] == 0):
        if not c_owned:
            note = m[1]
            for iid in _state['notes'].pop((ch, note), ()):
                syn = _state['synths'].get(iid)
                if syn is not None:
                    try:
                        syn.note_off(note)
                    except Exception as e:
                        _synth_err(iid, e)
        for dev in boards:
            _emit(m, dev)                      # forward the note-off raw
    else:
        # CC / pitch bend / aftertouch: forward to boards on this channel (MPE +
        # expression reach the board). Internal CC handling lands in a later
        # milestone.
        for dev in boards:
            _emit(m, dev)


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
    pfx = amyparams.device_patch_fx('internal')   # patch-applied baseline
    for bus, kw in amyparams.fx_calls(fx, pfx):
        fn = getattr(amy, bus, None)
        if fn is not None:
            try:
                fn(**kw)
            except Exception:
                pass
    if synth_nums:
        try:
            amy.send(synth=synth_nums[0], bus=0)   # ensure it's on the device FX bus
            eq = amyparams.fx_eq_string(fx, pfx)
            if eq is not None:                     # None = user never set EQ
                amy.send(synth=synth_nums[0], eq=eq)
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
    # Masters whose MPE zone was configured in AMY's C layer last rebuild.
    # Zones OUTLIVE the rebuild (AMY keeps grabbing member-channel notes and
    # applying per-note expression), so any master not re-enabled this pass
    # must be explicitly cleared or MPE keeps playing with the toggle off.
    prev_mpe_masters = set(_state.get('mpe_masters') or ())
    _state['mpe_masters'] = set()
    _state['mpe_members'] = set()
    _state['err_iids'] = set()
    _state['c_channels'] = set()
    _state['has_device_arg'] = None    # re-probe (firmware can't change, but
                                       # tests swap the tulip module)
    internal_synths = []

    # Pre-pass: channels with exactly ONE enabled internal instrument get a
    # synth whose number == the channel, so AMY's C layer grabs and plays the
    # notes directly (stock Tulip's zero-latency path). Layered channels
    # (2+ internals) keep auto synth numbers and Python routing.
    internal_count = {}
    for instr in instruments:
        if instr.get('enabled', True) and instr.get('device') == 'internal':
            ch_ = instr.get('channel', 1)
            internal_count[ch_] = internal_count.get(ch_, 0) + 1

    import synth as _synth
    for instr in instruments:
        if not instr.get('enabled', True):
            continue
        ch = instr.get('channel', 1)
        dev = instr.get('device')
        mpe = instr.get('mpe', {})
        is_mpe = bool(mpe_on and mpe.get('enabled'))
        route = _state['routes'].setdefault(ch, ([], []))
        if dev == 'internal':
            # The router owns this synth; make sure midi.config has none on the
            # channel so Tulip's default handler doesn't double-play the note.
            try:
                midi.config.release_synth_for_channel(ch)
            except Exception:
                pass
            syn = None
            # C-OWN the channel when this is its only internal instrument (or
            # MPE, whose synth number MUST equal the zone master anyway): a
            # synth numbered == channel makes AMY's C MIDI layer play the
            # notes directly. Layered channels keep auto ids (16+) + Python
            # routing (auto ids never collide with channels 1-15).
            solo = internal_count.get(ch, 0) == 1
            c_own = solo or is_mpe
            try:
                if instr.get('type') == 'drums':
                    # A drum instrument is a DrumSynth loaded with a kit patch;
                    # GM notes on its channel trigger the kit's samples.
                    import drums_kit
                    syn = drums_kit.make_synth(instr.get('kit', 384),
                                               num_voices=instr.get('num_voices', 6),
                                               channel=(ch if c_own else None))
                elif instr.get('type') in ('gm', 'gm2'):
                    # A GM instrument plays one program from a SoundFont
                    # bank; its 'patch' slot holds the GM program number.
                    # 'gm' = GeneralUser bank, 'gm2' = E-mu 4MB font from
                    # the big bank.
                    if instr.get('type') == 'gm2':
                        import gmbig as _gmmod
                    else:
                        import gm as _gmmod
                    syn = _synth.PatchSynth(
                        patch_string=_gmmod.patch_string(instr.get('patch', 0)),
                        num_voices=instr.get('num_voices', 10),
                        channel=(ch if c_own else None))
                else:
                    syn = _synth.PatchSynth(patch=instr.get('patch', 0),
                                            num_voices=instr.get('num_voices', 10),
                                            channel=(ch if c_own else None))
                _state['synths'][instr['id']] = syn
                if c_own:
                    _state['c_channels'].add(ch)
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
                sn = getattr(syn, 'synth', None)
                if instr.get('type') == 'drums':
                    # drums carry no osc/filter params; just route to the FX bus
                    if sn is not None:
                        try:
                            import amy
                            amy.send(synth=sn, bus=0)
                        except Exception:
                            pass
                else:
                    _apply_params(syn, instr.get('params', {}))
                if sn is not None:
                    internal_synths.append(sn)
            route[0].append(instr['id'])
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
                    _state['mpe_masters'].add(ch)
                except Exception:
                    pass
        else:
            # Board: push its patch (Program Change) then forward notes to it.
            try:
                _emit((0xC0 | ((ch - 1) & 0x0F), instr.get('patch', 0) & 0x7F), dev)
            except Exception:
                pass
            route[1].append(dev)

    # Tear down MPE zones that are no longer configured (toggle turned off,
    # instrument disabled, or master channel moved).
    if hasattr(midi, 'configure_mpe'):
        for m in prev_mpe_masters - _state['mpe_masters']:
            try:
                midi.configure_mpe(0, master=m)
            except Exception:
                pass

    _apply_device_fx(cfg, internal_synths)   # internal (Tulip) FX bus

    # Re-assert the configured GLOBAL VOLUME: the synth rebuild resets AMY
    # state, so without this every patch switch reverted volume to the default
    # -- the preset-audition note (and everything after) ignored Settings.
    try:
        import amy
        vol = cfg.get('volume', 4)
        vfn = getattr(amy, 'volume', None)
        if vfn is not None:
            vfn(vol)
        else:
            amy.send(volume=vol)
    except Exception:
        pass

    if not _state.get('registered'):
        try:
            midi.add_callback(_route)
            _state['registered'] = True
        except Exception as e:
            print("forwarder: add_callback failed:", e)
    _state['on'] = True
    try:
        import decklog
        decklog.dbg("router: %d synths, c_channels=%s, mpe_masters=%s, "
                    "mpe_members=%s" % (len(_state['synths']),
                                        sorted(_state['c_channels']),
                                        sorted(_state['mpe_masters']),
                                        sorted(_state['mpe_members'])))
    except Exception:
        pass


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


def live_voices():
    """Internal voices sounding right now (from the held-note table -- already
    maintained in RAM, so this is just sums, no AMY query)."""
    n = 0
    for iids in _state['notes'].values():
        n += len(iids)
    return n


def activity():
    """Monotonic count of MIDI messages routed; delta > 0 = activity."""
    return _state['seen']
