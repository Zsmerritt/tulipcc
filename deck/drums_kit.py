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
import samplepresets      # pure helpers (typed sample swaps + PCM one-shots)

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
    """A synthesized drum kit as ONE container synth (upstream drum-kit
    model, amy patches.h 384-390): a single 1-voice patch whose oscs are the
    kit's hits laid out as consecutive blocks, plus osc-targeted note maps
    (io entries) carrying each hit's gain in the velocity-scale field -- the
    exact mechanism of the sampled DrumSynth kits, so AMY's C layer plays
    the notes with zero added latency when the kit synth owns the MIDI
    channel. One synth number + one RAM patch slot per kit (the per-hit
    mini-synth model needed ~19 of each).

    Hits synthkits.classify_hit() can't place in the container (none in the
    current corpus) keep the legacy per-hit PatchSynth as a FALLBACK; their
    note maps target the hit synth instead of a container osc, so container
    and fallback pads coexist on the same kit.

    hit_overrides: {midi_note: {'tune','decay','level','snap'}} sound-design
    tweaks applied when the container is built (the pad editor's model).
    hit_swaps: {midi_note: hit_key} replaces a pad's hit with ANY corpus hit
    (the pad editor's alternates picker)."""

    def __init__(self, kit_key, channel=None, hit_overrides=None,
                 slot_base=None, hit_swaps=None):
        import synthkits
        self.kit_key = kit_key
        self.slot_base = synthkits.SLOT_KITS if slot_base is None else slot_base
        self.channel = channel
        # normalize override/swap keys to int so retweak can update records
        self.hit_overrides = {int(k): v for k, v in
                              (hit_overrides or {}).items()}
        self.hit_swaps = {int(k): v for k, v in (hit_swaps or {}).items()}
        self.hit_synths = {}       # midi_note -> PatchSynth (FALLBACK pads)
        self.hit_slots = {}        # midi_note -> RAM patch slot (fallback)
        self.pads = {}             # midi_note -> container pad record
        self.note_hits = {}        # midi_note -> hit key actually loaded
        self.note_alias = {}       # played note -> the pad that owns it
        self.kit_synth = None
        self.synth = None
        self._build()

    def _build(self):
        import synthkits
        import amy
        try:
            from time import sleep_ms as _yield
        except ImportError:
            _yield = lambda ms: None
        # SAMPLE swaps (Phase 2) are resident PCM one-shots, not corpus hits
        # -- they can't containerize (kit_container would skip them silent), so
        # split them out and build each as its OWN per-hit synth. In the
        # container model that is exactly a FALLBACK pad, so fold them into the
        # fallback set below and the per-hit loop plays them as one-shots.
        sample_swaps = {}
        corpus_swaps = {}
        for n, v in self.hit_swaps.items():
            if samplepresets.is_sample_swap(v):
                sample_swaps[int(n)] = v
            else:
                corpus_swaps[n] = v
        # One container patch for the whole kit. Unresolvable hit keys are
        # skipped inside kit_container (KITS-1: bad pad silent, kit lives).
        patch, pads, fallback = synthkits.kit_container(
            self.kit_key, self.hit_overrides, corpus_swaps)
        fallback.update(sample_swaps)   # sample pads join the per-hit loop
        self.pads = pads
        self.note_hits = {n: p['hit'] for n, p in pads.items()}
        # Store the container at the kit's base slot (overwrites on rebuild;
        # the pool is finite and slots never free). All-fallback/empty kit
        # keeps a silent placeholder osc so the synth still allocates.
        synthkits.store_patch(self.slot_base, patch or 'v0w0a0,0,0Z')
        _yield(2)
        # flags 3 = route notes via the note maps + ignore note-offs
        # (one-shots self-terminate)
        self.kit_synth = synth.PatchSynth(num_voices=1, channel=self.channel,
                                          patch=self.slot_base,
                                          synth_flags=3)
        self.kit_synth.deferred_init()
        self.synth = self.kit_synth.synth
        # FALLBACK pads: legacy per-hit mini-synths at slot_base+1.. (per-pad
        # guard as before -- a failing pad is skipped and never leaks a
        # partially-built synth). The slot window is the kit's stride; pads
        # beyond it are dropped loudly rather than corrupting a neighbour.
        # SAMPLE pads ride this same loop (they were folded into fallback).
        slot = self.slot_base + 1
        slot_top = self.slot_base + synthkits.SLOT_KIT_STRIDE
        for note in sorted(fallback):
            hit_key = fallback[note]
            if slot >= slot_top:
                try:
                    import decklog
                    decklog.log("synthkit %s: out of fallback slots, pad %d "
                                "dropped" % (self.kit_key, note))
                except Exception:
                    pass
                break
            _yield(2)
            hs = None
            eff = None                 # the pad's effective hit identity
            try:
                if samplepresets.is_sample_swap(hit_key):
                    # SAMPLE pad (Phase 2): load the WAV into its resident PCM
                    # preset, then arm a one-shot patch pointing at it. The
                    # synth is created AFTER the load, so it has no live voice
                    # yet -- no quiesce needed at BUILD time (unlike retweak).
                    preset = samplepresets.swap_preset(hit_key)
                    samplepresets.load_sample_into(
                        preset, samplepresets.swap_path(hit_key))
                    synthkits.store_patch(
                        slot, samplepresets.one_shot_patch_string(preset))
                else:
                    # deterministic slot: store (overwrites on rebuild -- the
                    # pool is finite and slots never free), then load by number
                    synthkits.store_patch(slot, synthkits.hit_patch_string(
                        hit_key, self.hit_overrides.get(note)))
                eff = hit_key
                hs = synth.PatchSynth(num_voices=1, patch=slot)
                hs.deferred_init()
            except Exception:
                if hs is not None:
                    try:
                        hs.release()   # partial: don't orphan its synth number
                    except Exception:
                        pass
                continue               # skip this pad; slot is reused next note
            self.note_hits[note] = eff
            self.hit_synths[note] = hs
            self.hit_slots[note] = slot
            slot += 1
        # Contiguous packing: lay the kit's distinct hits onto ADJACENT keys
        # from the lowest pad up (anchor = lowest built pad, one hit per key).
        # gm_fill's nearest-pad aliasing let one pad beside a big gap win half
        # the keyboard on a sparse kit (TR808 -> all cowbell); pack_fill keeps
        # hits compact so each key plays a DISTINCT hit. Hit patches have a 0
        # note-coefficient on freq, so every mapped key fires its hit's sound.
        # Built from the pads actually built, so a skipped pad is never a
        # map target.
        fill = synthkits.pack_fill(list(self.pads) + list(self.hit_synths))
        self.note_alias = fill
        # Register the note maps DIRECTLY on the live kit synth. They cannot
        # ride inside the stored patch: AMY registers io entries at
        # patch-STRING parse time keyed on that message's synth -- a store
        # message has no synth (garbage key) and loading by number replays
        # pre-parsed deltas, which skip mapping registration entirely. (This
        # is how MIDI kits went silent when patches moved to store-then-load
        # slots.) Field layout matches kit 384's baked entries:
        # note,is_log,min,max,offset,<template>. Container pads: the template
        # fires the hit's trigger osc(s) inside this synth's voice with the
        # played velocity scaled by the per-hit gain (max field) -- one
        # fragment per wire osc, just the parent for a partials hit (see the
        # synthkits container contract). Fallback pads: the template fires
        # the hit synth's fixed root note, gain baked in its amp.
        n = 0
        for note in sorted(fill):
            home, shift = fill[note]
            pad = self.pads.get(home)
            if pad is not None:
                cmd = synthkits.container_note_cmd(note, pad['trig'],
                                                   pad['velscale'], shift)
            else:
                # Fallback pads stay HOME-ONLY: the per-hit synth plays its
                # fixed root (n60), so an octave-shifted key over a fallback
                # home sounds the home hit unpitched -- never the pitch-shifted
                # container path, and never silent.
                cmd = '%d,0,0,1,0,i%dn60l%%v' % (
                    note, self.hit_synths[home].synth)
            amy.send(synth=self.synth, midi_note_cmd=cmd)
            n += 1
            if n % 8 == 0:
                _yield(1)   # ~47 sends in a row starve the UI task

    def deferred_init(self):
        pass                       # everything initialized in __init__

    def retweak(self, note, overrides, hit_key=None):
        """Live per-hit sound design. Same hit + new overrides (the slider
        hot path) re-sends just that hit's osc params to the live container
        (and refreshes its note maps' velocity scale), so other pads' tails
        keep ringing. A hit SWAP (hit_key differs) changes the container
        layout -> full kit rebuild -- and (re)pointing a pad at a typed SAMPLE
        swap (Phase 2) is exactly such a swap, so it rebuilds too. Fallback
        pads keep the legacy per-hit reload; sample pads don't yet apply
        Tune/Decay/Level/Snap (samplepresets validation #3), so an
        override-only tick on a sample pad is a no-op below."""
        import synthkits
        note = int(note)
        if overrides:
            self.hit_overrides[note] = dict(overrides)
        else:
            self.hit_overrides.pop(note, None)
        cur = self.note_hits.get(note)
        if hit_key is not None and cur is not None and hit_key != cur:
            # swap: record it and relayout the whole kit (rare user action)
            self.hit_swaps[note] = hit_key
            self._rebuild()
            return
        if hit_key is None:
            hit_key = cur
        if hit_key is None:
            return
        hs = self.hit_synths.get(note)
        if hs is not None:
            # fallback pad. A SAMPLE pad (Phase 2) is a fallback pad too, but
            # its tune/decay/level/snap are not yet applied (samplepresets
            # validation #3), and any actual sample SWAP already relayouted via
            # _rebuild above -- so an override-only tick here is a no-op.
            if samplepresets.is_sample_swap(hit_key):
                return
            # corpus fallback pad: legacy per-hit reload
            import amy
            slot = self.hit_slots[note]
            synthkits.store_patch(slot,
                                  synthkits.hit_patch_string(hit_key,
                                                             overrides))
            amy.send(synth=hs.synth, num_voices=0)
            amy.send(synth=hs.synth, num_voices=1, patch=slot)
            return
        pad = self.pads.get(note)
        if pad is None:
            return
        built = synthkits.hit_container_oscs(hit_key, overrides, pad['osc'])
        if built is None or len(built[0]) != pad['nosc']:
            self._rebuild()        # shape changed: relayout (corpus-rare)
            return
        import amy
        updates, g, trig = built
        for osc, kw in updates:
            # events with synth+osc are voice-relative in AMY, matching the
            # patch-string semantics at load
            amy.send(synth=self.synth, osc=osc, **kw)
        if g != pad['velscale']:
            pad['velscale'] = g    # level moved: refresh this pad's maps
            for played, (home, shift) in self.note_alias.items():
                if home == note:
                    amy.send(synth=self.synth,
                             midi_note_cmd=synthkits.container_note_cmd(
                                 played, trig, g, shift))

    def _rebuild(self):
        """Full teardown + rebuild (hit swap changed the container layout).
        Synth numbers are recycled through PatchSynth's free list; a C-owned
        kit keeps its channel number."""
        self.release()
        self._build()

    def note_on(self, note, vel, **kw):
        # Python-routed path (kit not C-owned, or pad-editor audition):
        # resolve through the same alias map the C note maps use, so off-pad
        # keys sound here too. Container pads fire their hit's trigger
        # osc(s) directly at note 60+shift (pack_fill's octave), with the
        # map's gain applied to the velocity; note_source_channel marks the
        # event as already-mapped so the flags-3 synth doesn't loop it back
        # through the MIDI note maps.
        ent = self.note_alias.get(note)
        if ent is None:
            return                 # unmapped key: silent (fill covers 35..81)
        home, shift = ent
        pad = self.pads.get(home)
        if pad is not None:
            import amy
            fire = 60 + shift
            for osc in pad['trig']:
                amy.send(synth=self.synth, osc=osc, note=fire,
                         vel=vel * pad['velscale'],
                         note_source_channel=self.synth)
            return
        hs = self.hit_synths.get(home)
        if hs is not None:
            hs.note_on(60, vel)    # fallback: fixed root (home-only)

    def note_off(self, note, **kw):
        pass                       # drum one-shots self-terminate

    def release(self):
        # Clear the kit synth's note maps FIRST: mappings outlive the synth
        # (they key on the synth number), so without this the next instrument
        # on the same channel kept playing the drums. 255 = clear-all.
        try:
            import amy
            amy.send(synth=self.synth, midi_note_cmd='255')
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
        self.hit_slots = {}
        self.pads = {}
        self.note_hits = {}
        self.note_alias = {}


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
    syn = synth.DrumSynth(patch=kit, num_voices=num_voices, channel=channel)
    if kit in SAMPLED_KIT_IDS:
        _apply_kit_caps(syn, kit)
    return syn


def _apply_kit_caps(syn, patch):
    """Re-register a SAMPLED kit's io note maps with capped velocities
    (kitcaps.py). The baked Gamma9001 velscales (2.34..13.1) clipped hard on
    device (measured bus peaks 2.35..4.79 FS at vel 1.0); kitcaps scales them so
    the loudest hit lands ~1.25 FS and the rest stay proportional. Each capped
    entry overwrites its baked map by note (AMY midi_store_mapping replaces by
    channel+code). No-op if kitcaps is absent, the patch isn't sampled, or the
    synth never allocated. The DrumSynth must be initialized first -- the maps
    key on its live synth number -- so we force deferred_init() (idempotent)."""
    try:
        import kitcaps
        ents = kitcaps.entries(patch)
    except Exception:
        return
    if not ents:
        return
    try:
        import amy
        di = getattr(syn, 'deferred_init', None)
        if di is not None:
            di()
        sn = getattr(syn, 'synth', None)
        if sn is None:
            return
        try:
            from time import sleep_ms as _yield
        except ImportError:
            _yield = lambda ms: None
        for i, (note, cmd) in enumerate(ents):
            amy.send(synth=sn, midi_note_cmd=cmd)
            if (i + 1) % 8 == 0:
                _yield(1)   # ~40 sends in a row starve the UI task
    except Exception:
        pass
