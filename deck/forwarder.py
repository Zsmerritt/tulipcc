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

# Type-default output levels (levels/#96). The GM SoundFont banks bake no osc-0
# amp (AMY default 1.0) and rendered a good ~5 dB under the junos; ×1.7 on the
# patch's OWN baked osc-0 amp (read via amyparams, so a patch that DOES bake an
# amp is scaled, not stomped) brings them to parity. A user-stored `level`
# param still wins (it's already in params, so the inject is skipped).
GM_TYPE_GAIN = 1.7
# Piano velocity curve (levels/#97): the piano engine's response is ~vel**3.5,
# which leaves mezzo-forte thin, so _route remaps vel -> vel**PIANO_VEL_POW on
# piano instruments only (vel 1.0 -> 1.0, so no ff clipping); carried on the
# synth object. HISTORY: this was 0.5 (sqrt), but that was calibrated while the
# X32 monitor rig was L-R phase-cancelling the centered piano signal -- the
# reference was falsely quiet, and 0.5 (~+10.5 dB at mf) over-boosted once the
# rig was fixed. 0.8 keeps a gentle lift (~+4 dB at mf, effective loudness
# exponent ~2.8 instead of the raw ~3.5) without the over-boost.
PIANO_VEL_POW = 0.8
# Piano polyphony ceiling. Each piano voice (patch 256, interp_partials) claims
# a fixed block of 25 oscs (1 control + 24 partials; amy patch_oscs[256]=25 --
# the partial-detail knob does NOT shrink this, the full span is reserved
# regardless). The whole build has 250 oscs total (amy/src/api.c
# amy_default_config: max_oscs=250; tulip's amy_connector.c doesn't override
# it). 8 voices = 200 oscs, leaving 50 for a layered second instrument -- e.g.
# a juno at 8 voices (6 oscs/voice = 48), a dx7 at 6 (8/voice = 48), or a
# sampled drum kit (1 voice, ~42 oscs). 10 voices would eat the entire pool and
# the failed allocations play as silently SKIPPED notes, so keep real headroom.
# The rack "voices" slider drives num_voices up to this cap for the piano.
PIANO_MAX_VOICES = 8

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
    'c_router': False,   # the C route table is live (O-2): boards forwarded
                         # in C, Python woken only for layered channels/taps
    'py_tap': False,     # a Python consumer (midimon) wants every message
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
    # Top-level guard (MIDI-7): this runs inside midi.c_fired_midi_event's
    # drain loop. If it raises, the exception escapes the drain BEFORE the ring
    # empties, so the C coalescing flag (tulip_midi_py_pending) is never
    # cleared -- it latches at 1 and the C hook stops scheduling the drain
    # entirely. The result is a permanently blind Python MIDI path (the monitor
    # shows nothing while tulip.midi_activity() keeps climbing) until reboot.
    # The deck must never be that trigger, so swallow here.
    try:
        _route_impl(m)
    except Exception as e:
        try:
            import decklog
            decklog.dbg("forwarder: _route swallowed %r" % e)
        except Exception:
            pass


def _route_impl(m):
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
    if _state.get('c_router'):
        boards = ()      # the C router already forwarded board bytes (O-2)
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
                        # piano velocity curve: vel**PIANO_VEL_POW on piano
                        # instruments only, carried on the synth. Other
                        # types render vel unchanged. (C-owned solo channels
                        # play in AMY's C layer and never reach here.)
                        vp = getattr(syn, 'vel_pow', None)
                        syn.note_on(note, vel ** vp if vp else vel)
                    except Exception as e:
                        _synth_err(iid, e)
                    if played is None:
                        played = []
                    played.append(iid)
            if played:
                key = (ch, note)
                prior = _state['notes'].get(key)
                if prior is not None:
                    # Re-trigger of a still-held (ch,note) with no intervening
                    # note-off (legato overlap, a duplicated/stuck controller
                    # msg): the earlier note-on already allocated voices on
                    # these synths. ACCUMULATE instead of replacing, so the
                    # single note-off releases every triggered voice -- a plain
                    # overwrite popped only the latest set and stranded the
                    # first until a rebuild (MIDI-5). Only layered channels
                    # reach here (solo/MPE are C-owned).
                    prior.extend(played)
                else:
                    _state['notes'][key] = played
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
    # Rewind the auto synth-number counter: it only counts up, AMY caps
    # instruments at 64, and a synth kit consumes ~19 numbers per rebuild --
    # a few rebuilds used to march the allocator off the cliff. Everything we
    # allocated was just released, so the numbers are free again (and
    # allocation order makes each rebuild's numbers stable).
    try:
        import synth as _synth
        # 18, not 16: auto ids must clear the channel range AND the
        # audition scratch (17) -- a C-owned ch-16 instrument IS synth 16
        _synth.PatchSynth.amy_synth_next = 18
        _synth.PatchSynth.amy_synth_free = []
        _synth.PatchSynth.amy_synth_allocated = set()
    except Exception:
        pass


def _with_type_level(params, instr, amyparams):
    """params with a type-default `level` injected for gm/gm2 (GM_TYPE_GAIN)
    when the user has stored none. The gain multiplies the patch's OWN baked
    osc-0 amp (read via amyparams.patch_params), so a patch that bakes an amp is
    scaled rather than stomped by a blind constant; GM banks bake none, so they
    default to 1.0 -> 1.7. A user `level` is already in params and wins (skip).
    Piano/juno6/dx7 are untouched -- piano is handled by the velocity curve, and
    DX7 per-patch normalization is a separate follow-up."""
    p = params or {}
    if not instr or instr.get('type') not in ('gm', 'gm2'):
        return p
    if 'level' in p:
        return p                          # user set it: wins
    try:
        baked = amyparams.patch_params(instr).get('level', 1.0)
    except Exception:
        baked = 1.0
    d = amyparams.PARAM_BY_NAME.get('level')
    hi = d.get('max', 7) if d else 7
    p = dict(p)
    p['level'] = min(hi, baked * GM_TYPE_GAIN)
    return p


def _apply_params(syn, params, instr=None):
    """Push an internal instrument's stored AMY params to its owned synth via
    amy.send(synth=<n>, ...). Also assigns the synth to the device's FX bus
    (internal device = bus 0) so per-bus FX (reverb/chorus/echo/EQ) apply.

    `instr` supplies the patch's own envelope as the fallback for the
    composite bp0/bp1 strings (see amyparams.synth_send_calls): without it, a
    user-touched attack restates decay/sustain/release from schema defaults and
    wipes the patch's baked envelope. It never causes an extra send."""
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
    try:
        penv = amyparams.patch_env(instr)
    except Exception:
        penv = {}
    params = _with_type_level(params, instr, amyparams)
    for kw in amyparams.synth_send_calls(params, penv):
        try:
            amy.send(synth=sn, **kw)
        except Exception as e:
            # a param send failing is a CODE bug (schema/firmware drift),
            # not environment -- log it once per instrument (E-15)
            _synth_err('params@%s' % sn, e)
    # reverb_send is BUS-directed (skipped by synth_send_calls): push it to
    # this instrument's FX bus and refresh the auto-room so editing it in
    # the Sound editor is audible immediately.
    rs = (params or {}).get('reverb_send')
    if rs is not None:
        try:
            for t in (_state.get('fx_targets') or ()):
                if t.get('synth') == sn:
                    amy.send(bus=t['bus'], reverb_send=rs)
                    t['send'] = rs
                    break
            refresh_room()
        except Exception:
            pass
    # piano partial-detail knob (OPT-8): device-global engine limit
    pq = (params or {}).get('piano_quality')
    if pq is not None:
        try:
            import tulip
            if hasattr(tulip, 'piano_partials'):
                tulip.piano_partials(int(pq))
        except Exception:
            pass
    # piano SUSTAIN (envelope time-stretch): device-global engine multiplier.
    # Slider is in seconds; amyparams.piano_sustain_arg maps that to the
    # stretch*1000 int the C binding wants (single tunable constant lives there).
    ps = (params or {}).get('piano_sustain')
    if ps is not None:
        try:
            import tulip
            if hasattr(tulip, 'piano_sustain'):
                import amyparams
                tulip.piano_sustain(amyparams.piano_sustain_arg(ps))
        except Exception:
            pass


def _apply_device_fx(cfg, targets, prev_buses=()):
    """Per-instrument FX buses (C.5). Baked patch strings always land their
    chorus/EQ on AMY bus 0, so with several instruments the LAST-loaded patch
    used to win for everyone. Each internal synth now renders into its own
    bus (0..3; a 5th+ instrument shares the last), and after ALL patches have
    loaded every bus is re-sent its own deterministic baseline -- defaults
    merged with ITS instrument's patch FX (patchfx table) -- then the user's
    device-level FX overlay on top. targets = [{'bus','synth','pfx'}...]."""
    try:
        import amy
        import amyparams
    except Exception:
        return
    fx = deckcfg.device_fx('internal', cfg)
    for t in targets:
        try:
            amy.send(synth=t['synth'], bus=t['bus'])
            base = amyparams.fx_bus_baseline(t['pfx'])
            amy.send(bus=t['bus'], chorus=base['chorus'], echo=base['echo'],
                     reverb_send=t.get('send', 0.0))
            amy.send(synth=t['synth'], eq=base['eq'])
            over = amyparams.fx_send_strings(fx, t['pfx'])
            if over:
                amy.send(bus=t['bus'], **over)
            eq = amyparams.fx_eq_string(fx, t['pfx'])
            if eq is not None:                     # None = user never set EQ
                amy.send(synth=t['synth'], eq=eq)
        except Exception as e:
            # FX apply failing is schema/firmware drift, a CODE bug --
            # log first failure per target instead of silence (E-15)
            _synth_err('fx@%s' % t.get('iid'), e)
    # Reverb is the shared per-device ROOM: one master reverb on the mixed
    # output (AMY_MASTER_REVERB), configured once without a bus. Sends
    # default DRY (0 -- built-in patches bake no reverb, so dry matches the
    # patch); when an instrument's send is raised and the user never
    # configured the room, the room auto-enables at a sensible default so
    # the send slider is audible by itself.
    try:
        amy.send(reverb=_room_string(fx, targets))
    except Exception:
        pass
    # Silence per-instrument FX on buses that fell out of use.
    used = {t['bus'] for t in targets}
    for b in set(prev_buses) - used:
        try:
            amy.send(bus=b, chorus='0', echo='0')
        except Exception:
            pass


def reapply_params(iid):
    """Re-send one instrument's params to its EXISTING synth (no rebuild), for
    live audition while editing. Falls back to a full start() if it has no synth
    yet -- UNLESS it's a board instrument, which never has a local synth: its
    params live on the board, so we push JUST the params overlay there (via the
    zP control-API) instead of paying a full ~80-200ms router rebuild -- or the
    old silent no-op that left board edits inaudible until the next start()."""
    syn = _state['synths'].get(iid)
    if syn is None:
        instr = deckcfg.get_instrument(iid) or {}
        if instr.get('device') != 'internal':
            # Board instrument: push the params overlay to it, no rebuild.
            try:
                import amyfleet
                amyfleet.push_params(instr.get('device'), instr)
            except Exception:
                pass
            return
        start()
        return
    instr = deckcfg.get_instrument(iid) or {}
    # pass instr so the patch-envelope fallback applies on live re-sends too, not
    # just full builds (without it a user-touched attack wipes the baked envelope)
    _apply_params(syn, instr.get('params', {}), instr)


def _sig(instr, c_own):
    """The topology signature of a built instrument: if any of these change,
    routing/layering/slots must be recomputed -- full rebuild. Patch, kit,
    voices, params are NOT topology: rebuild_one handles those in place."""
    m = instr.get('mpe', {})
    return (instr.get('channel', 1), instr.get('device'),
            instr.get('type', 'juno6'), bool(instr.get('enabled', True)),
            bool(m.get('enabled')), c_own)


def rebuild_one(iid):
    """Rebuild ONE internal instrument's synth in place (O-5): a patch/kit/
    voices edit used to pay the full start() -- releasing and re-creating
    EVERY synth, ~80-200ms of wire traffic that audibly interrupted every
    other sounding instrument. Reuses the slot + FX bus recorded by the
    last full build; anything topological (channel/device/type/enable/MPE)
    falls back to start()."""
    instr = deckcfg.get_instrument(iid)
    rec = (_state.get('built') or {}).get(iid)
    old = _state['synths'].get(iid)
    if (instr is None or rec is None or old is None
            or _state.get('rebuilding')
            or instr.get('device') != 'internal'):
        return start()
    ch = instr.get('channel', 1)
    c_own = ch in _state['c_channels']
    if _sig(instr, c_own) != rec['sig']:
        return start()                      # topology changed

    # The in-place rebuild still emits ~60 amy.send() messages; batch them
    # into ONE MP->C call like start() does. The mid-build broken-half-state
    # fallback signals via the return value so start() runs OUTSIDE the batch
    # context (never nested) -- as do the early topology fallbacks above.
    def _rebuild_in_place():
        import synth as _synth
        try:
            old.release()
        except Exception:
            pass
        try:
            if instr.get('type') == 'drums':
                import drums_kit
                syn = drums_kit.make_synth(instr.get('kit', 384),
                                           num_voices=instr.get('num_voices', 6),
                                           channel=(ch if c_own else None),
                                           hit_overrides=instr.get('hits'),
                                           slot_base=rec['slot'],
                                           hit_swaps=instr.get('hit_swaps'))
            elif instr.get('type') in ('gm', 'gm2'):
                if instr.get('type') == 'gm2':
                    import gmbig as _gmmod
                else:
                    import gm as _gmmod
                import synthkits as _sk
                gpatch = instr.get('patch', 0)
                if (instr.get('type') == 'gm2'
                        and not _gmmod.has_program(gpatch)):
                    # same uncovered-program fallback as start() (KITS-2): a
                    # patch swap to an emu4-uncovered gm2 program would KeyError
                    # here too and fall back to a full router rebuild.
                    gpatch = _nearest_gm2_program(gpatch)
                _sk.store_patch(rec['slot'], _gmmod.patch_string(gpatch))
                syn = _synth.PatchSynth(patch=rec['slot'],
                                        num_voices=instr.get('num_voices', 10),
                                        channel=(ch if c_own else None))
            else:
                nv = instr.get('num_voices')
                # osc-budget cap for the piano (see PIANO_MAX_VOICES); the
                # rack voices slider drives nv up to that ceiling.
                nv = (min(nv or PIANO_MAX_VOICES, PIANO_MAX_VOICES)
                      if instr.get('type') == 'piano' else (nv or 10))
                syn = _synth.PatchSynth(patch=instr.get('patch', 0),
                                        num_voices=nv,
                                        channel=(ch if c_own else None))
        except Exception as e:
            _synth_err(iid, e)
            return False                    # broken half-state: rebuild all
        _state['synths'][iid] = syn
        if instr.get('type') == 'piano':
            try:
                syn.vel_pow = PIANO_VEL_POW       # _route applies vel**vel_pow
            except Exception:
                pass
        di = getattr(syn, 'deferred_init', None)
        if di is not None:
            try:
                di()
            except Exception:
                pass
        if c_own:
            try:
                import amy as _amy
                _amy.send(synth=ch, grab_midi_notes=1)
            except Exception:
                pass
        if instr.get('type') != 'drums':
            _apply_params(syn, instr.get('params', {}), instr)
        # refresh this instrument's FX-bus target + re-baseline ITS bus only
        sn = getattr(syn, 'synth', None)
        if sn is not None and 'bus' in rec:
            import amyparams as _ap
            pfx = (_ap.patch_fx(instr.get('patch', 0))
                   if instr.get('type', 'juno6') in ('juno6', 'dx7', 'piano')
                   else {})
            for t in _state.get('fx_targets') or ():
                if t.get('iid') == iid:
                    t['synth'] = sn
                    t['pfx'] = pfx
                    _apply_device_fx(deckcfg.load(), [t])
                    break
        try:
            import decklog
            decklog.dbg("router: rebuilt one (%s)" % iid)
        except Exception:
            pass
        return True

    with _AmyBatch():
        ok = _rebuild_in_place()
    if not ok:
        return start()                      # broken half-state: rebuild all


def _room_string(fx, targets):
    """The device room's wire string: the user's FX settings when they set
    any, else a default audible room while ANY instrument sends into it,
    else off."""
    import amyparams
    user_set = bool(fx and isinstance(fx.get('reverb'), dict)
                    and fx['reverb'])
    if not user_set and any((t.get('send') or 0) > 0 for t in targets):
        return '0.35,0.85,0.5'
    return amyparams.fx_reverb_string(fx)


def refresh_room():
    """Re-assert the device room from current FX config + live sends -- the
    rack's Reverb send slider calls this so sliding up from dry is audible
    immediately (auto-room) without a rebuild."""
    try:
        import amy
        fx = deckcfg.device_fx('internal')
        amy.send(reverb=_room_string(fx, _state.get('fx_targets') or ()))
    except Exception as e:
        import decklog
        decklog.dbg("router: refresh_room failed: %r" % e)
        pass


def _push_board_fx(cfg):
    """Push each board device's stored device-FX overrides to the board (A).

    The Devices>FX editor stores FX for a board just like the internal device,
    but only internal FX was ever transmitted -- a board's FX config was
    placebo. Group board instruments by device -> their channels (EQ is
    per-synth == per-channel), and for each device that has stored FX push it
    via the zP control-API. Lazy import + fully guarded: an FX push must never
    take down a rebuild."""
    try:
        import amyfleet
    except Exception:
        return
    by_dev = {}
    for instr in deckcfg.instruments(cfg):
        dev = instr.get('device')
        if isinstance(dev, int) and instr.get('enabled', True):
            by_dev.setdefault(dev, set()).add(int(instr.get('channel', 2)))
    for dev, chs in by_dev.items():
        try:
            fx = deckcfg.device_fx(dev, cfg)
            if fx:
                amyfleet.push_fx(dev, fx, channels=sorted(chs))
        except Exception:
            pass


def reapply_fx():
    """Re-apply the per-bus FX to the live synths (live audition). Also pushes
    every board device's stored FX (A): the Devices>FX editor's _fx_apply calls
    this, so its board-FX edits go live with no change to devices.py."""
    cfg = deckcfg.load()
    _apply_device_fx(cfg, _state.get('fx_targets') or [])
    _push_board_fx(cfg)


def start():
    """(Re)build the router from the current instruments. Safe to call
    repeatedly -- releases the previous internal synths first (no voice leak).
    Reentrancy-guarded: SynthKit builds yield to the scheduler between hits,
    so a second kit tap used to re-enter mid-build and leave AMY with one
    kit's instrument and another's note maps. Now a rebuild in progress just
    queues one more pass."""
    if _state.get('rebuilding'):
        _state['rebuild_queued'] = True
        return
    _state['rebuilding'] = True
    try:
        while True:
            with _AmyBatch():
                _start_once()
            if not _state.pop('rebuild_queued', False):
                break
    finally:
        _state['rebuilding'] = False


class _AmyBatch:
    """Collect every amy.send() wire message emitted inside the block and
    flush them through tulip.amy_send_batch in ONE MP->C call (review opt
    1): a rebuild emits 50-150 messages, and each individual send paid a
    kwargs walk + an MP call round-trip -- the bulk of the 80-200ms rebuild
    gap that audibly interrupted other sounding instruments. Message ORDER
    is preserved; timestamped messages carry their own 't'. No-op (sends
    pass straight through) without the firmware binding."""

    def __enter__(self):
        self._on = False
        try:
            import amy
            import tulip
            if hasattr(tulip, 'amy_send_batch') and hasattr(amy, 'override_send'):
                self._amy, self._tulip = amy, tulip
                self._orig = amy.override_send
                self._msgs = []
                amy.override_send = self._msgs.append
                self._on = True
        except Exception:
            pass
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._on:
            self._amy.override_send = self._orig
            if self._msgs:
                # amy_send_batch's C line buffer (MAX_MESSAGE_LEN, 1023) SILENTLY
                # truncates any single message that reaches it >= 1023 chars.
                # Walk the list: batch the normal messages, but when a long one
                # (>= 1000, leaving headroom) appears, flush the batch-so-far,
                # send the long one alone via the plain override, then keep
                # batching -- preserving wire ORDER.
                batch = []
                for m in self._msgs:
                    if len(m) >= 1000:
                        self._flush_batch(batch)
                        batch = []
                        try:
                            self._orig(m)     # plain path: no line-buffer limit
                        except Exception:
                            pass
                    else:
                        batch.append(m)
                self._flush_batch(batch)
        return False

    def _flush_batch(self, batch):
        if not batch:
            return
        try:
            self._tulip.amy_send_batch('\n'.join(batch))
        except Exception as e:
            import decklog
            decklog.dbg("router: batch send failed, per-message fallback: %r" % e)
            for m in batch:                   # never lose the rebuild
                try:
                    self._orig(m)
                except Exception:
                    pass


def _start_once():
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
    # per-instrument FX bus map, rebuilt each pass (C.5)
    prev_fx_buses = {t['bus'] for t in (_state.get('fx_targets') or ())}
    _state['fx_targets'] = []
    _next_bus = 0
    # deterministic RAM-patch slots (see synthkits.py slot map): raw
    # patch_string sends allocate a fresh slot per rebuild and leak the pool
    _next_melodic_slot = [0]
    _next_kit_slot = [0]
    _state['built'] = {}    # iid -> {'sig','bus','slot'}: what a later
                            # rebuild_one(iid) may reuse in place (O-5)
    _state['err_iids'] = set()
    _state['c_channels'] = set()
    _state['has_device_arg'] = None    # re-probe (firmware can't change, but
                                       # tests swap the tulip module)
    internal_synths = []
    board_instrs = []      # (iid, channel) for the MPE-member-channel warning
                           # pass below -- board notes route in _route()
                           # BEFORE mpe_members is checked, so a board on a
                           # member channel is muted just like a layered
                           # internal instrument would be.

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
            except Exception as e:
                import decklog
                decklog.dbg("router: release_synth_for_channel(%s) failed: %r"
                            % (ch, e))
                pass
            syn = None
            # C-OWN the channel when this is its only internal instrument (or
            # MPE, whose synth number MUST equal the zone master anyway): a
            # synth numbered == channel makes AMY's C MIDI layer play the
            # notes directly. Layered channels keep auto ids (16+) + Python
            # routing (auto ids never collide with channels 1-15).
            solo = internal_count.get(ch, 0) == 1
            c_own = solo or is_mpe
            if is_mpe and not solo:
                # the zone C-owns the channel, so _route skips it entirely and
                # anything layered here is silently muted -- say so out loud
                try:
                    import decklog
                    decklog.log("forwarder: MPE zone on ch%d C-owns the channel; "
                                "other instruments layered on ch%d will not sound" % (ch, ch))
                except Exception:
                    pass
            if c_own:
                # Scrub the channel's C-layer state before taking it over:
                # AMY NOTE MAPS are keyed by channel and OUTLIVE the synth
                # that registered them -- a drum kit's io entries (amps up
                # to ~9) kept firing loud, note-off-ignoring one-shots into
                # whatever instrument took the channel next. A stale
                # instrument left on the channel number likewise makes AMY
                # ignore the new definition. The stock arpeggiator (midi.py
                # pins one on channel 1) is popped too: when toggled it
                # flips grab_midi_notes on the channel synth, which silences
                # the map dispatch drums depend on.
                try:
                    import amy as _amy
                    _amy.send(synth=ch, midi_note_cmd='255')
                    _amy.send(synth=ch, num_voices=0)
                except Exception as e:
                    import decklog
                    decklog.dbg("router: ch%s C-layer scrub failed: %r" % (ch, e))
                    pass
                try:
                    midi.config.arpeggiator_per_channel.pop(ch, None)
                except Exception as e:
                    import decklog
                    decklog.dbg("router: arpeggiator pop ch%s failed: %r"
                                % (ch, e))
                    pass
            try:
                if instr.get('type') == 'drums':
                    # A drum instrument is a DrumSynth loaded with a kit patch;
                    # GM notes on its channel trigger the kit's samples.
                    import drums_kit
                    import synthkits as _sk
                    if _next_kit_slot[0] >= _sk.MAX_KIT_SLOTS:
                        # E-4: the next window would walk past AMY's 128-slot
                        # pool (or into rejection range) -- refuse loudly
                        # instead of corrupting another kit's patches
                        _synth_err(instr['id'], RuntimeError(
                            "kit slot map full (%d kits max)" % _sk.MAX_KIT_SLOTS))
                        continue
                    kslot = _sk.SLOT_KITS + _sk.SLOT_KIT_STRIDE * _next_kit_slot[0]
                    _next_kit_slot[0] += 1
                    syn = drums_kit.make_synth(instr.get('kit', 384),
                                               num_voices=instr.get('num_voices', 6),
                                               channel=(ch if c_own else None),
                                               hit_overrides=instr.get('hits'),
                                               slot_base=kslot,
                                               hit_swaps=instr.get('hit_swaps'))
                elif instr.get('type') in ('gm', 'gm2'):
                    # A GM instrument plays one program from a SoundFont
                    # bank; its 'patch' slot holds the GM program number.
                    # 'gm' = GeneralUser bank, 'gm2' = E-mu 4MB font from
                    # the big bank. Stored at a deterministic RAM slot so
                    # rebuilds don't leak the patch pool.
                    if instr.get('type') == 'gm2':
                        import gmbig as _gmmod
                    else:
                        import gm as _gmmod
                    import synthkits as _sk
                    if _next_melodic_slot[0] >= _sk.MAX_MELODIC_SLOTS:
                        # E-4: the 6th melodic store would land in the first
                        # kit's slot window -- refuse loudly instead
                        _synth_err(instr['id'], RuntimeError(
                            "melodic slot map full (%d max)"
                            % _sk.MAX_MELODIC_SLOTS))
                        continue
                    mslot = _sk.SLOT_MELODIC + _next_melodic_slot[0]
                    _next_melodic_slot[0] += 1
                    gpatch = instr.get('patch', 0)
                    if (instr.get('type') == 'gm2'
                            and not _gmmod.has_program(gpatch)):
                        # emu4 covers only 92 of 128 GM programs; a stale/
                        # hand-edited gm2 patch used to KeyError here and mute
                        # the instrument. Degrade to the nearest covered
                        # program so it still sounds (KITS-2).
                        gpatch = _nearest_gm2_program(gpatch)
                    _sk.store_patch(mslot, _gmmod.patch_string(gpatch))
                    syn = _synth.PatchSynth(
                        patch=mslot,
                        num_voices=instr.get('num_voices', 10),
                        channel=(ch if c_own else None))
                else:
                    # osc-budget cap for the piano: 25 oscs/voice, capped at
                    # PIANO_MAX_VOICES (see the comment there) so a layered
                    # second instrument keeps headroom. The rack voices slider
                    # drives nv up to that ceiling.
                    nv = instr.get('num_voices')
                    if instr.get('type') == 'piano':
                        nv = min(nv or PIANO_MAX_VOICES, PIANO_MAX_VOICES)
                    else:
                        nv = nv or 10
                    syn = _synth.PatchSynth(patch=instr.get('patch', 0),
                                            num_voices=nv,
                                            channel=(ch if c_own else None))
                _state['synths'][instr['id']] = syn
                if instr.get('type') == 'piano':
                    try:
                        syn.vel_pow = PIANO_VEL_POW   # _route applies vel**vel_pow
                    except Exception:
                        pass
                if c_own:
                    _state['c_channels'].add(ch)
                _state['built'][instr['id']] = {
                    'sig': _sig(instr, c_own),
                    'slot': (kslot if instr.get('type') == 'drums' else
                             mslot if instr.get('type') in ('gm', 'gm2')
                             else None),
                }
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
                if c_own:
                    # fresh instruments default to grab_midi_notes=1, but a
                    # leftover arpeggiator toggle can have cleared it -- and
                    # without it the C layer drops the channel's notes.
                    try:
                        import amy as _amy
                        _amy.send(synth=ch, grab_midi_notes=1)
                    except Exception:
                        pass
                sn = getattr(syn, 'synth', None)
                if instr.get('type') != 'drums':
                    # (drums carry no osc/filter params)
                    _apply_params(syn, instr.get('params', {}), instr)
                if sn is not None:
                    internal_synths.append(sn)
                    # per-instrument FX bus: routing + this patch's FX baseline
                    # are applied in one deterministic pass after ALL patches
                    # load (_apply_device_fx) -- see patchfx.py.
                    import amyparams as _ap
                    pfx = (_ap.patch_fx(instr.get('patch', 0))
                           if instr.get('type', 'juno6') in
                           ('juno6', 'dx7', 'piano') else {})
                    b = _state['built'].get(instr['id'])
                    if b is not None:
                        b['bus'] = min(_next_bus, 3)
                    _state['fx_targets'].append(
                        {'bus': min(_next_bus, 3), 'synth': sn, 'pfx': pfx,
                         'iid': instr['id'],
                         # default DRY: built-in patches bake no reverb, so
                         # the send starts where the patch is (slider left).
                         # Lives in params (Sound editor) now; the top-level
                         # key is the legacy location.
                         'send': (instr.get('params') or {}).get(
                             'reverb_send',
                             instr.get('reverb_send', 0.0))})
                    _next_bus += 1
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
            # Board: push its FULL sound state, then forward notes to it. The
            # old bare Program Change sent `patch & 0x7F`, which aliased every
            # DX7 patch (130 -> Juno 2) and carried no params/voice count.
            # push_instrument goes through tulip.midi_out (zP), NOT amy.send, so
            # it is unaffected by the _AmyBatch context that wraps this build.
            try:
                import amyfleet
                amyfleet.push_instrument(dev, instr)
            except Exception:
                pass
            route[1].append(dev)
            board_instrs.append((instr['id'], ch))

    # A board instrument on an MPE MEMBER channel is muted the same way a
    # layered internal instrument is (_route returns early for member
    # channels before it ever looks at `boards`) -- but silently, since
    # unlike the layered-internal case above there was no warning for it.
    # Checked here, after the full pass, because a zone's member channels
    # are only known once its master instrument has been processed.
    if _state['mpe_members']:
        for iid, ch in board_instrs:
            if ch in _state['mpe_members']:
                try:
                    import decklog
                    decklog.log(
                        "forwarder: ch%d is an MPE member channel; the board "
                        "instrument there will not sound" % ch)
                except Exception:
                    pass

    # Tear down MPE zones that are no longer configured (toggle turned off,
    # instrument disabled, or master channel moved).
    if hasattr(midi, 'configure_mpe'):
        for m in prev_mpe_masters - _state['mpe_masters']:
            try:
                midi.configure_mpe(0, master=m)
            except Exception:
                pass

    _apply_device_fx(cfg, _state['fx_targets'], prev_fx_buses)
    # Restore each board device's stored FX too (A): board FX must survive a
    # boot / rebuild / instrument move, not just live edits via reapply_fx.
    _push_board_fx(cfg)
    _upload_c_routes()   # C router table matches the routes just built (O-2)

    # Re-assert the configured GLOBAL VOLUME: the synth rebuild resets AMY
    # state, so without this every patch switch reverted volume to the default
    # -- the preset-audition note (and everything after) ignored Settings.
    # Sent as a 4-slot list: with per-instrument FX buses in play, a single
    # value would only set bus 0.
    try:
        import amy
        vol = cfg.get('volume', 4)
        amy.send(volume="%s,%s,%s,%s" % ((vol,) * 4))
    except Exception:
        pass

    if not _state.get('registered'):
        try:
            midi.add_callback(_route)
            _state['registered'] = True
            _state['register_failed'] = False
        except Exception as e:
            # If this fails the forwarder never sees a single MIDI message: no
            # notes, no layering, no board forwarding, no monitor. That is a
            # total MIDI outage and must be LOUD -- a lone print scrolls away
            # unseen. Record it (register_ok() surfaces it in the UI) and log
            # it where the user actually looks.
            _state['register_failed'] = True
            try:
                import decklog
                decklog.log_exc("forwarder: midi.add_callback(_route) FAILED -- "
                                "MIDI routing is DISABLED (no notes will play)", e)
            except Exception:
                print("forwarder: add_callback failed:", e)
    _ensure_watchdog()
    _state['on'] = True
    try:
        import decklog
        decklog.dbg("router: %d synths, c_channels=%s, mpe_masters=%s, "
                    "mpe_members=%s, fx_buses=%s"
                    % (len(_state['synths']),
                       sorted(_state['c_channels']),
                       sorted(_state['mpe_masters']),
                       sorted(_state['mpe_members']),
                       [(t['synth'], t['bus'])
                        for t in _state['fx_targets']]))
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


def _nearest_gm2_program(program):
    """The emu4 GM program covered by gm2 nearest to `program` (ties -> lower),
    or 0 if the font somehow covers nothing. Only used when a stale/hand-edited
    gm2 config names one of the 36 GM programs the emu4 font doesn't carry, so
    it degrades to the closest sound instead of a KeyError-silenced instrument
    (KITS-2)."""
    try:
        import gmbig
        progs = gmbig.programs()
        if not progs:
            return 0
        return min(progs, key=lambda p: (abs(p - program), p))
    except Exception:
        return 0


def activity():
    """Monotonic count of MIDI messages seen; delta > 0 = activity. With the C
    router active its counter already counts EVERY non-sysex message before
    routing -- including layered-channel traffic that also reaches _route -- so
    it alone is the total; adding _state['seen'] on top double-counted every
    layered message (MIDI-6). Without the C router (or when its counter is
    unavailable) the Python router sees everything, so use _state['seen']."""
    if _state.get('c_router'):
        try:
            import tulip
            return tulip.midi_activity()
        except Exception:
            pass
    return _state['seen']


def _has_c_router():
    """True if this firmware carries the C MIDI router (tulip.midi_routes).
    Only that firmware can suppress Python for C-owned channels, so only
    there does a tap need engaging -- older firmware wakes Python for every
    message unconditionally."""
    try:
        import tulip
        return hasattr(tulip, 'midi_routes')
    except Exception:
        return False


def set_midi_tap(on):
    """Full-stream Python notification toggle (midimon): with the C router
    active, C-owned channels never wake Python -- unless a tap asks for
    every message.

    Returns True if the requested state is now IN EFFECT in the C router (or
    there is no C router to gate, so Python already sees everything); returns
    False only when the router IS present but the upload FAILED -- which means
    a stale table may still be suppressing Python with the tap off, i.e. the
    monitor would go silently blind. midimon surfaces that False loudly (#85)."""
    _state['py_tap'] = bool(on)
    r = _upload_c_routes()
    # None = no C router present (nothing to gate; monitor sees all).
    return r is None or bool(r)


def tap_engaged():
    """True if the full MIDI stream currently reaches Python callbacks: either
    there is no C router (all messages already come through), or the router is
    live AND our tap flag is set. midimon polls this to SELF-HEAL a tap that a
    rebuild or transient error dropped, instead of going silently blind."""
    if not _has_c_router():
        return True
    return bool(_state.get('c_router')) and bool(_state.get('py_tap'))


def register_ok():
    """False if midi.add_callback(_route) failed at build time -- a total MIDI
    outage the UI must surface (not just a scrolled-away print)."""
    return not _state.get('register_failed', False)


# --- MIDI drain watchdog (router-tap-fix) ---------------------------------
# The C input hook wakes Python by scheduling a drain callback and coalescing
# on a C-global flag (tulip_midi_py_pending, amy_connector.c). That flag
# SURVIVES a MicroPython soft-reset but the scheduled callback it refers to
# does NOT, so a crash/reload can leave the flag latched with no callback
# queued -- after which the C hook never re-schedules and the last_midi ring
# fills forever, killing ALL Python MIDI (observed live: ring full, 1023 stale
# messages, midi_in_drops climbing). The firmware now self-heals on the next
# burst, but this watchdog is belt-and-suspenders that ALSO recovers on
# firmware without that fix: when the ring-drop counter climbs (ring
# overflowing => Python not draining), force-drain to clear the flag and log
# loudly. Runs on the shared deck ticker, ~1 Hz, essentially free when idle.
_wd = {'last_ring_drops': None, 'stalls': 0, 'started': False}


def midi_stalls():
    """Count of MIDI-drain stalls the watchdog has detected and recovered."""
    return _wd['stalls']


def _watchdog(sid=None):
    try:
        import tulip
        if not hasattr(tulip, 'midi_in_drops'):
            return
        drops = tulip.midi_in_drops()
        ring = (drops[1] if isinstance(drops, (tuple, list)) and len(drops) > 1
                else 0)
    except Exception:
        return
    last = _wd['last_ring_drops']
    _wd['last_ring_drops'] = ring
    if last is None or ring <= last:
        return
    # The ring-drop counter climbed since the last check: the ring is
    # overflowing, i.e. Python is not draining it. Force a drain to pop the
    # ring empty -- which clears tulip_midi_py_pending (modtulip.c clears it on
    # an empty read) and unwedges the MIDI path. A stall means the backlog is
    # stale, so discard rather than replay (replaying old note-ons without
    # their note-offs would strand voices).
    drained = 0
    try:
        import tulip
        for _ in range(4096):
            m = tulip.midi_in()
            if not m:
                break
            drained += 1
    except Exception:
        pass
    _wd['stalls'] += 1
    try:
        import decklog
        decklog.log("MIDI DRAIN STALLED: ring overflowing (+%d drops); "
                    "force-drained %d stale messages to recover (stall #%d)"
                    % (ring - last, drained, _wd['stalls']))
    except Exception:
        pass


def _ensure_watchdog():
    if _wd['started']:
        return
    try:
        import ticker
        ticker.every(1000, _watchdog, key='midiwd')
        _wd['started'] = True
    except Exception:
        pass


def _upload_c_routes():
    """Upload the channel route table to the C router (O-2): per-channel
    board masks + which channels still need Python (layered internals).
    After this, C-owned channels cost ZERO Python per message: the C layer
    plays the synth, forwards the boards, bumps the activity counter, and
    never touches the scheduler.

    Returns True on a successful upload, False if the router is present but the
    upload raised (a genuine failure -- a prior table may still be gating
    Python), or None if this firmware has no C router at all (fails soft)."""
    if not _has_c_router():
        _state['c_router'] = False
        return None
    try:
        import tulip
        masks = [0] * 16
        py_mask = 0
        for ch, (iids, boards) in _state['routes'].items():
            if not 1 <= ch <= 16:
                continue
            for d in boards:
                if isinstance(d, int) and 0 <= d < 16:
                    masks[ch - 1] |= 1 << d
            # layered channels (2+ internals) still route in Python; the
            # C layer can only own solo/MPE channels (synth number ==
            # channel)
            if iids and ch not in _state['c_channels']:
                py_mask |= 1 << (ch - 1)
        tulip.midi_routes(masks, py_mask, bool(_state.get('py_tap')))
        _state['c_router'] = True
        return True
    except Exception as e:
        # The router exists but we could not push the table: it may be gating
        # Python with a stale mask + the tap off. Report the failure so the
        # caller (midimon) can warn on screen instead of showing an empty log.
        _state['c_router'] = False
        try:
            import decklog
            decklog.dbg("forwarder: midi_routes upload FAILED %r" % e)
        except Exception:
            pass
        return False
