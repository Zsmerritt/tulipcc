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


def kit_name(kit):
    return KIT_NAMES.get(kit, 'TR-808')


def make_synth(kit=DEFAULT_KIT, num_voices=6):
    """A DrumSynth for the given kit patch. GM notes then trigger its samples."""
    return synth.DrumSynth(patch=kit, num_voices=num_voices)
