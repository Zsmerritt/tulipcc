# instmove.py -- pure planning for "move this instrument to another device".
#
# The deck hosts instruments on several independent AMY devices: the Tulip's own
# 'internal' engine, plus any USB-MIDI boards (device = an int board index). Each
# device is its own 16-channel MPE world (see channels.py / MPE-DESIGN.md). Moving
# an instrument from one device to another therefore has to (a) check the target
# can actually host that KIND of sound, and (b) find it a channel -- or a whole
# MPE zone -- that fits alongside whatever already lives on the target.
#
# plan_move() is the PURE half: given the instrument list and a target, it returns
# a PLAN -- "ok, land on channel N" or "no, because <reason>". It performs no I/O,
# mutates nothing, and imports no tulip/lvgl/amy/deckcfg, so it unit-tests under
# CPython (test_deck.py). Keep plan_move() and its helpers import-clean.
#
# move_instrument() is the ORCHESTRATION half (added in the rack-move rework): it
# calls plan_move() first, commits the device/channel to deckcfg only when the
# plan is 'ok' and 'changed', re-enrolls the affected boards, and rebuilds the
# router. It uses LAZY imports (deckcfg/amyfleet inside the function) so importing
# this module for the pure planner never drags in the hardware layer.
#
# Channel/zone math is NOT reimplemented here: occupancy and MPE-zone footprints
# come from channels.py, the single source of truth the channel-map UI also uses.
# In particular we honour channels.occupancy semantics exactly -- disabled
# instruments (enabled=False) do NOT reserve channels, and the moved instrument
# itself is always excluded from the target's occupancy.

import channels

N_CHANNELS = channels.N_CHANNELS

# Types a bare board can't host: boards have no GM soundfonts, kit slots, or
# sampled-drum PCM banks -- only AMY's built-in melodic patches. The Tulip
# ('internal') hosts everything.
_BOARD_UNSUPPORTED = ('gm', 'gm2', 'drums')


def _find(instruments, iid):
    for instr in instruments:
        if instr.get('id') == iid:
            return instr
    return None


def _is_board(device):
    """A board target is an int index. bool is an int subclass, so exclude it;
    'internal' (the Tulip) is a str and hosts every type."""
    return isinstance(device, int) and not isinstance(device, bool)


def _footprint(instr, candidate_channel, mpe_on):
    """The channels `instr` would occupy if its master landed on
    `candidate_channel` -- a single channel, or its full MPE zone when the global
    gate and its own mpe.enabled are both set. Uses channels.instrument_channels
    semantics against a copy with the candidate channel substituted (no mutation
    of the caller's dict)."""
    probe = dict(instr)
    probe['channel'] = candidate_channel
    return channels.instrument_channels(probe, mpe_on)


def plan_move(instruments, iid, target_device, mpe_on=False):
    """Plan moving instrument `iid` to `target_device`.

    Returns a dict:
      {'ok': True, 'channel': <1-16>, 'changed': True/False}
    or
      {'ok': False, 'reason': 'full' | 'unsupported_type' | 'not_found',
       'device': target_device}

    'changed' is False only for the same-device no-op; any real cross-device move
    that succeeds is 'changed': True (the device changed even if the channel is
    kept). Channel choice honours MPE zones on BOTH sides: the moved instrument's
    own footprint (its zone when mpe_on and it enables MPE) must not collide with
    the target device's occupancy (channels.occupancy, which skips disabled
    instruments and excludes the moved one)."""
    instr = _find(instruments, iid)
    if instr is None:
        return {'ok': False, 'reason': 'not_found', 'device': target_device}

    current_device = instr.get('device')
    current_channel = instr.get('channel', 1)

    # (2) same device -> no-op; keep the current channel, nothing changes.
    if target_device == current_device:
        return {'ok': True, 'channel': current_channel, 'changed': False}

    # (3) a board can't host GM / sampled-drum instruments.
    if _is_board(target_device) and instr.get('type') in _BOARD_UNSUPPORTED:
        return {'ok': False, 'reason': 'unsupported_type', 'device': target_device}

    # (4/5) channel choice. Occupancy of the OTHER instruments already on the
    # target (disabled ones don't count; the moved instrument is excluded).
    occ = channels.occupancy(instruments, target_device, mpe_on, exclude_iid=iid)
    occupied = set(occ)

    def fits(candidate):
        return not any(c in occupied for c in _footprint(instr, candidate, mpe_on))

    # Prefer keeping the current channel if its whole footprint is free there.
    if fits(current_channel):
        return {'ok': True, 'channel': current_channel, 'changed': True}

    # Otherwise the lowest master channel whose footprint fits.
    for candidate in range(1, N_CHANNELS + 1):
        if fits(candidate):
            return {'ok': True, 'channel': candidate, 'changed': True}

    return {'ok': False, 'reason': 'full', 'device': target_device}


def move_instrument(iid, target_device):
    """One call: plan + commit a move of instrument `iid` to `target_device`.

    Returns the plan_move() dict. On the ok+committed path it is augmented with
    'device': target_device so the UI can name the target the same way it names
    a failure. A not-ok plan, or an ok-but-'changed'-False plan (the same-device
    tap), is returned UNTOUCHED with NO config mutation, enrollment, or apply --
    committing a no-op would cost a pointless flash write and a router rebuild.

    On commit it: (1) writes device then channel to deckcfg (one flash write via
    flush=False on the first); (2) re-enrolls every affected BOARD with the full
    channel list now configured on it -- the target gains the moved channel, and
    the source (if a board) is re-enrolled WITHOUT the vacated channel, even to
    an EMPTY list, so the board's deck listener releases the orphaned synth
    instead of leaving it sounding; (3) apply_all(), whose router rebuild both
    rebuilds the internal synths and pushes each board instrument's full sound
    state (so no separate push is needed here). Enrollment is guarded per board:
    a board that fails to enroll must not abort the config commit."""
    import deckcfg
    import amyfleet

    instruments = deckcfg.instruments()
    plan = plan_move(instruments, iid, target_device, deckcfg.mpe_enabled())
    if not plan.get('ok') or not plan.get('changed'):
        return plan

    # Record the source BEFORE mutating (we re-enroll it minus the vacated ch).
    instr = _find(instruments, iid)
    source_device = instr.get('device') if instr is not None else None
    channel = plan['channel']

    # Commit device+channel as ONE flash write: flush=False caches the device,
    # the channel setter saves both.
    deckcfg.set_instrument(iid, 'device', target_device, flush=False)
    deckcfg.set_instrument(iid, 'channel', channel)

    # Re-enroll affected boards from the POST-commit config. An empty list for a
    # fully-vacated source board is intentional (a release frame).
    affected = []
    for dev in (target_device, source_device):
        if _is_board(dev) and dev not in affected:
            affected.append(dev)
    if affected:
        fresh = deckcfg.instruments()
        for dev in affected:
            chs = sorted({int(i.get('channel', 2)) for i in fresh
                          if i.get('device') == dev})
            try:
                amyfleet.enroll_channels(dev, chs)
            except Exception:
                pass   # a board enroll failure must not abort the committed move

    # Router rebuild: rebuilds internal synths AND pushes board sound state.
    deckcfg.apply_all()

    plan['device'] = target_device
    return plan


def unsupported_reason_text(instr, target_device):
    """Human-readable one-liner explaining why `instr` can't move to a board
    (the 'unsupported_type' case). For the UI's error toast."""
    t = instr.get('type')
    if t == 'drums':
        return ("Drum kits need the Tulip's sample banks and kit slots "
                "and can't move to a board.")
    if t in ('gm', 'gm2'):
        return ("General MIDI patches use soundfonts that live only on the "
                "Tulip, not on a board.")
    return "This instrument type can only run on the Tulip, not on a board."
