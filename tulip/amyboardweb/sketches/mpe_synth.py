# AMYboard Sketch
# Top-level code runs once at boot. loop() is called every 32nd note.
# DESCRIPTION: MPE synth: per-note pitch bend, pressure -> level, slide (CC74) -> filter

import amy

# A 10-voice analog-style pad on synth 1, the MPE zone master.
amy.send(synth=1, patch=0, num_voices=10)
# Lower MPE zone: notes on member channels 2-8 all play this synth,
# each with its own pitch bend (+/-48 semitones), pressure, and slide.
amy.send(synth=1, mpe="7,48")
# Per-note expression arrives via the ext0/ext1 control coefficients:
# pressure (ext0) opens up the level, slide/CC74 (ext1) opens the filter.
amy.send(synth=1, amp={'vel': 1, 'ext0': 0.5})
amy.send(synth=1, filter_freq={'const': 600, 'note': 1, 'ext1': 2}, resonance=1.5)

def loop():
    pass
