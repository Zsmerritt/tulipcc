# channels.py -- pure per-device MIDI channel-budget + MPE zone allocation.
#
# The MPE model (deck/MPE-DESIGN.md): each device is an independent AMY with its
# own 16 channels and hosts up to 16 instruments. An MPE instrument claims a
# contiguous block (master + N members) on its device; other instruments keep
# their channels. This module is the PURE logic behind that -- occupancy, zone
# math, fit/overlap checks, and a channel-map model for the UI. No lvgl/amy/
# deckcfg imports, so it unit-tests under CPython (test_deck.py).
#
# Rendering today is PASS-THROUGH: the render master == the input master == the
# instrument's channel (remap onto a non-natural block is a deferred, additive
# strategy -- see MPE-DESIGN.md). So "does the zone fit" == "do the master+member
# channels collide with other instruments on the same device".

N_CHANNELS = 16


def member_channels(master, members):
    """The member channels of a LOWER MPE zone anchored at `master` (ascending
    from master+1), clamped to 16. Upper-zone (master==16) descends."""
    if master == N_CHANNELS:                      # upper zone
        lo = max(1, N_CHANNELS - members)
        return list(range(lo, N_CHANNELS))
    hi = min(N_CHANNELS, master + members)
    return list(range(master + 1, hi + 1))


def zone_channels(master, members):
    """Master + members: every channel an MPE zone occupies."""
    return sorted(set([master] + member_channels(master, members)))


def instrument_channels(instr, mpe_on):
    """The channels `instr` occupies on its device: its single channel, or -- when
    MPE is globally on and the instrument enables it -- its full zone."""
    ch = instr.get('channel', 1)
    mpe = instr.get('mpe', {})
    if mpe_on and mpe.get('enabled'):
        return zone_channels(ch, mpe.get('members', 15))
    return [ch]


def occupancy(instruments, device, mpe_on, exclude_iid=None):
    """channel (1-16) -> list of instruments occupying it on `device`."""
    occ = {}
    for instr in instruments:
        if instr.get('device') != device:
            continue
        if not instr.get('enabled', True):
            continue
        if exclude_iid is not None and instr.get('id') == exclude_iid:
            continue
        for c in instrument_channels(instr, mpe_on):
            occ.setdefault(c, []).append(instr)
    return occ


def zone_fits(instruments, device, master, members, exclude_iid, mpe_on=True):
    """Would a lower MPE zone [master..master+members] on `device` overlap any
    OTHER instrument's channels? Returns (fits, sorted_conflict_channels)."""
    want = set(zone_channels(master, members))
    occ = occupancy(instruments, device, mpe_on, exclude_iid=exclude_iid)
    conflicts = sorted(c for c in want if c in occ)
    return (len(conflicts) == 0, conflicts)


def max_members_at(instruments, device, master, exclude_iid, mpe_on=True):
    """Largest member count whose zone at `master` fits without overlap (0..15).
    Lets the UI clamp/suggest a zone size when the device is busy."""
    occ = occupancy(instruments, device, mpe_on, exclude_iid=exclude_iid)
    n = 0
    for mc in range(master + 1, N_CHANNELS + 1):
        if mc in occ:
            break
        n += 1
    return n


def channel_map(instruments, device, mpe_on, active_iid=None):
    """A 16-slot model for the per-device channel map UI. Each slot:
      {'ch', 'names', 'busy', 'mine', 'master', 'member'} where 'mine' marks the
    active instrument's own channels and master/member mark its zone anchor/tail."""
    occ = occupancy(instruments, device, mpe_on)
    occ_others = occupancy(instruments, device, mpe_on, exclude_iid=active_iid)
    mine = set()
    master_ch = None
    members = set()
    for instr in instruments:
        if instr.get('id') == active_iid and instr.get('device') == device:
            mine = set(instrument_channels(instr, mpe_on))
            master_ch = instr.get('channel', 1)
            members = set(member_channels(master_ch,
                                          instr.get('mpe', {}).get('members', 15))) \
                if (mpe_on and instr.get('mpe', {}).get('enabled')) else set()
    slots = []
    for ch in range(1, N_CHANNELS + 1):
        here = occ.get(ch, [])
        slots.append({
            'ch': ch,
            'names': [i.get('name', '?') for i in here],
            'busy': len(here) > 0,
            'mine': ch in mine,
            'master': ch == master_ch and bool(members),
            'member': ch in members,
            # A conflict: this channel is in the active instrument's zone AND
            # another instrument on the device also claims it.
            'conflict': ch in mine and ch in occ_others,
        })
    return slots
