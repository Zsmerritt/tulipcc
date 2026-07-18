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

# Resync (amy/src/patches.h): a sampled drum kit is now ONE voice with N
# voice-specific oscs baked in (patch_oscs[]: 38 for 384, 42 for 385-390),
# not N identical voices -- upstream's amy_default_synths() creates the
# built-in drums synth with num_voices=1 to match. Passing more (the deck
# used to ask for 6-10) multiplies the osc count instead of adding polyphony
# and can blow past AMY's 250-osc pool (amy/src/api.c), starving voice
# allocation for every drum instrument. make_synth() below clamps any
# sampled kit to 1 regardless of what a saved config or the voices slider
# asks for. 258 is amy's own MIDI-channel-10 default kit -- not one of
# drums_kit.KITS and not reachable through the deck's kit selector today,
# but it bakes the identical if3iv1in.. one-voice format, so it is clamped
# here too as a defensive no-op.
SAMPLED_KIT_IDS = frozenset(KIT_NAMES) | {258}


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
    tweaks applied when each hit synth is built (the pad editor's model).
    hit_swaps: {midi_note: hit_key} replaces a pad's hit with ANY corpus hit
    (the pad editor's alternates picker)."""

    def __init__(self, kit_key, channel=None, hit_overrides=None,
                 slot_base=None, hit_swaps=None):
        import synthkits
        self.kit_key = kit_key
        self.slot_base = synthkits.SLOT_KITS if slot_base is None else slot_base
        self.hit_synths = {}       # midi_note -> PatchSynth
        self.hit_slots = {}        # midi_note -> RAM patch slot
        self.note_hits = {}        # midi_note -> hit key actually loaded
        ov = hit_overrides or {}
        sw = hit_swaps or {}
        self.note_alias = {}       # every GM note 35-81 -> the pad that owns it
        slot = self.slot_base + 1  # base slot holds the kit patch itself
        try:
            from time import sleep_ms as _yield
        except ImportError:
            _yield = lambda ms: None
        notes = synthkits.kit_notes(kit_key)
        for note in sorted(notes):
            hit_key = sw.get(note) or sw.get(str(note)) or notes[note]
            _yield(2)   # ~20 store+create bursts starve the UI task otherwise
            # Per-hit guard (KITS-1): an unresolvable hit key (partial qput
            # deploy, index/pack drift) used to raise KeyError out of the whole
            # loop -- silencing the ENTIRE kit and ORPHANING every hit synth
            # already created (they were never tracked, so never released ->
            # leaked AMY synth numbers). Now a bad pad is SKIPPED (left silent
            # and unmapped), the rest of the kit stays audible, and any synth
            # created before a mid-build failure is released so it can't leak.
            hs = None
            try:
                # deterministic slot: store (overwrites on rebuild -- the pool
                # is finite and slots never free), then load by number
                synthkits.store_patch(slot, synthkits.hit_patch_string(
                    hit_key, ov.get(note) or ov.get(str(note))))
                hs = synth.PatchSynth(num_voices=1, patch=slot)
                hs.deferred_init()
            except Exception:
                if hs is not None:
                    try:
                        hs.release()   # partial: don't orphan its synth number
                    except Exception:
                        pass
                continue               # skip this pad; slot is reused next note
            self.note_hits[note] = hit_key
            self.hit_synths[note] = hs
            self.hit_slots[note] = slot
            slot += 1
        # Contiguous packing: lay the kit's distinct hits onto ADJACENT keys
        # from the lowest pad up (anchor = lowest built pad, one hit per key).
        # gm_fill's nearest-pad aliasing let one pad beside a big gap win half
        # the keyboard on a sparse kit (TR808 -> all cowbell); pack_fill keeps
        # hits compact so each key plays a DISTINCT hit. Hit patches have a 0
        # note-coefficient on freq, so every mapped key fires its hit's sound.
        # Built from hit_synths, not kit_notes, so a skipped pad is never a
        # map target.
        fill = synthkits.pack_fill(self.hit_synths.keys())
        self.note_alias = fill
        # silent placeholder osc; flags 3 = route notes via the note maps +
        # ignore note-offs (one-shots self-terminate)
        synthkits.store_patch(self.slot_base, 'v0w0a0,0,0Z')
        self.kit_synth = synth.PatchSynth(num_voices=1, channel=channel,
                                          patch=self.slot_base,
                                          synth_flags=3)
        self.kit_synth.deferred_init()
        self.synth = self.kit_synth.synth
        # Register the note maps DIRECTLY on the live kit synth. They cannot
        # ride inside the stored patch: AMY registers io entries at
        # patch-STRING parse time keyed on that message's synth -- a store
        # message has no synth (garbage key) and loading by number replays
        # pre-parsed deltas, which skip mapping registration entirely. (This
        # is how MIDI kits went silent when patches moved to store-then-load
        # slots.) Field layout matches kit 384's baked entries:
        # note,is_log,min,max,offset,<template>; the template fires the hit
        # synth's fixed root note with the played velocity.
        import amy
        n = 0
        for note in sorted(fill):
            hsn = self.hit_synths[fill[note]].synth
            amy.send(synth=self.kit_synth.synth,
                     midi_note_cmd='%d,0,0,1,0,i%dn60l%%v' % (note, hsn))
            n += 1
            if n % 8 == 0:
                _yield(1)   # ~47 sends in a row starve the UI task

    def deferred_init(self):
        pass                       # everything initialized in __init__

    def retweak(self, note, overrides, hit_key=None):
        """Live per-hit sound design: rebuild ONE hit synth's patch.
        hit_key swaps the pad to a different corpus hit (alternates picker)."""
        import synthkits
        hs = self.hit_synths.get(note)
        if hit_key is not None:
            self.note_hits[note] = hit_key
        else:
            hit_key = self.note_hits.get(note)
        if hs is None or hit_key is None:
            return
        import amy
        slot = self.hit_slots[note]
        synthkits.store_patch(slot, synthkits.hit_patch_string(hit_key, overrides))
        amy.send(synth=hs.synth, num_voices=0)
        amy.send(synth=hs.synth, num_voices=1, patch=slot)

    def note_on(self, note, vel, **kw):
        # Python-routed path (kit not C-owned): resolve through the same alias
        # map the C note maps use, so off-pad keys sound here too.
        base = self.note_alias.get(note, note)
        hs = self.hit_synths.get(base)
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
               slot_base=None, hit_swaps=None):
    """A DrumSynth (sampled kit patch) or SynthKit ('synth:<key>'). GM notes
    then trigger its sounds. channel= binds the AMY synth number to the MIDI
    channel so AMY's C layer plays the notes directly (see forwarder's
    C-owned channels). slot_base = this instrument's RAM-patch slot window
    (synthkits.SLOT_KITS + stride per drum instrument).

    num_voices is ignored (forced to 1) for a sampled kit (SAMPLED_KIT_IDS):
    those patches are one voice with a fixed osc per drum, so "voices" here
    would multiply the osc count, not add polyphony -- see SAMPLED_KIT_IDS."""
    if isinstance(kit, str) and kit.startswith('synth:'):
        return SynthKit(kit[6:], channel=channel, hit_overrides=hit_overrides,
                        slot_base=slot_base, hit_swaps=hit_swaps)
    if kit in SAMPLED_KIT_IDS:
        num_voices = 1
    return synth.DrumSynth(patch=kit, num_voices=num_voices, channel=channel)
