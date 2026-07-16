# drums_kit.py -- drum-instrument kit model.
#
# A drum instrument is a DrumSynth loaded with a KIT (an AMY drum-kit patch,
# 384-390). Each kit maps GM drum notes (35-81) to its samples, so playing MIDI
# drum notes just works -- whole-kit swap = change the patch number. All seven
# kits are compiled into the TULIP4_R11 firmware (verified on-device).
#
# Per-pad Tune/Decay is a planned follow-up (see deck/BACKLOG.md): DrumSynth bakes
# the per-drum config into the kit, so per-pad override needs an AMY per-note
# mechanism that still has to be verified by ear.
#
# Device-only (imports synth); not host-tested.

import synth

# (DrumSynth patch number, display name). 384 = the default TR-808.
KITS = [
    (384, 'TR-808'),
    (385, 'TR-909'),
    (386, 'Linn 9000'),
    (387, 'MR-12'),
    (388, 'Tokyo Synthetics'),
    (389, '80s Power'),
    (390, 'Percussion'),
]
KIT_NAMES = dict(KITS)
DEFAULT_KIT = 384


def synth_kits():
    """[(kit id, display name)] for the SYNTHESIZED kits (synthkits.json).
    Kit ids are 'synth:<key>' strings so they share the instrument's kit slot
    with the sampled patch numbers."""
    try:
        import synthkits
        return [('synth:' + k, n + ' (synth)')
                for k, n in sorted(synthkits.kits().items())]
    except Exception:
        return []


def kit_name(kit):
    if isinstance(kit, str) and kit.startswith('synth:'):
        for kid, name in synth_kits():
            if kid == kit:
                return name
        return kit[6:] + ' (synth)'
    return KIT_NAMES.get(kit, 'TR-808')


class SynthKit:
    """A synthesized drum kit: one 2-osc mini-synth per hit plus a KIT synth
    whose patch carries io note-map templates dispatching each GM note to its
    hit synth -- the same mechanism as the sampled DrumSynth kits (patch 384's
    baked io entries), so AMY's C layer plays the notes with zero added
    latency when the kit synth owns the MIDI channel.

    hit_overrides: {midi_note: {'tune','decay','level','snap'}} sound-design
    tweaks applied when each hit synth is built (the pad editor's model)."""

    def __init__(self, kit_key, channel=None, hit_overrides=None,
                 slot_base=None):
        import synthkits
        self.kit_key = kit_key
        self.slot_base = synthkits.SLOT_KITS if slot_base is None else slot_base
        self.hit_synths = {}       # midi_note -> PatchSynth
        self.hit_slots = {}        # midi_note -> RAM patch slot
        ov = hit_overrides or {}
        io_frags = []
        slot = self.slot_base + 1  # base slot holds the kit patch itself
        try:
            from time import sleep_ms as _yield
        except ImportError:
            _yield = lambda ms: None
        for note, hit_key in sorted(synthkits.kit_notes(kit_key).items()):
            _yield(2)   # ~20 store+create bursts starve the UI task otherwise
            # deterministic slot: store (overwrites on rebuild -- the pool is
            # finite and slots never free), then load by number
            synthkits.store_patch(slot, synthkits.hit_patch_string(
                hit_key, ov.get(note) or ov.get(str(note))))
            hs = synth.PatchSynth(num_voices=1, patch=slot)
            hs.deferred_init()
            self.hit_synths[note] = hs
            self.hit_slots[note] = slot
            slot += 1
            # kit 384's field layout: note,is_log,min,max,offset,<template>;
            # the template sends the hit synth a fixed-root note-on with the
            # played velocity. Entries are framed by DOUBLE-Z (the mapping
            # parser splits on "ZZ" so templates may contain single Zs).
            io_frags.append('io%d,0,0,1,0,i%dn60l%%vZZ' % (note, hs.synth))
        # silent placeholder osc + the note maps; flags 3 = route notes via
        # the map + ignore note-offs (one-shots self-terminate)
        synthkits.store_patch(self.slot_base, 'v0w0a0,0,0Z' + ''.join(io_frags))
        self.kit_synth = synth.PatchSynth(num_voices=1, channel=channel,
                                          patch=self.slot_base,
                                          synth_flags=3)
        self.kit_synth.deferred_init()
        self.synth = self.kit_synth.synth

    def deferred_init(self):
        pass                       # everything initialized in __init__

    def retweak(self, note, overrides):
        """Live per-hit sound design: rebuild ONE hit synth's patch."""
        import synthkits
        hs = self.hit_synths.get(note)
        hit_key = synthkits.kit_notes(self.kit_key).get(note)
        if hs is None or hit_key is None:
            return
        import amy
        slot = self.hit_slots[note]
        synthkits.store_patch(slot, synthkits.hit_patch_string(hit_key, overrides))
        amy.send(synth=hs.synth, num_voices=0)
        amy.send(synth=hs.synth, num_voices=1, patch=slot)

    def note_on(self, note, vel, **kw):
        hs = self.hit_synths.get(note)
        if hs is not None:
            hs.note_on(60, vel)

    def note_off(self, note, **kw):
        pass                       # drum one-shots self-terminate

    def release(self):
        # Clear the kit synth's note maps FIRST: mappings outlive the synth
        # (they key on the synth number), so without this the next instrument
        # on the same channel kept playing the drums. 255 = clear-all.
        try:
            import amy
            amy.send(synth=self.kit_synth.synth, midi_note_cmd='255')
        except Exception:
            pass
        for hs in self.hit_synths.values():
            try:
                hs.release()
            except Exception:
                pass
        try:
            self.kit_synth.release()
        except Exception:
            pass
        self.hit_synths = {}


def make_synth(kit=DEFAULT_KIT, num_voices=6, channel=None, hit_overrides=None,
               slot_base=None):
    """A DrumSynth (sampled kit patch) or SynthKit ('synth:<key>'). GM notes
    then trigger its sounds. channel= binds the AMY synth number to the MIDI
    channel so AMY's C layer plays the notes directly (see forwarder's
    C-owned channels). slot_base = this instrument's RAM-patch slot window
    (synthkits.SLOT_KITS + stride per drum instrument)."""
    if isinstance(kit, str) and kit.startswith('synth:'):
        return SynthKit(kit[6:], channel=channel, hit_overrides=hit_overrides,
                        slot_base=slot_base)
    return synth.DrumSynth(patch=kit, num_voices=num_voices, channel=channel)
