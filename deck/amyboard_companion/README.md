# AMYboard companion sketch (Tulip Deck)

`sketch.py` here runs **on each AMYboard** so the Tulip can address and fully
control it as a fleet instrument: it listens on its assigned MIDI channel(s) and
maps Program Change → patch and a CC set → AMY params, and it remembers its
channels across reboots.

**Multi-channel:** the deck can host several instruments on one board, each on
its own channel. This sketch keeps a *list* of channels (and per-channel state),
so a second instrument moved to a board on a different channel still sounds —
lifting the old one-channel-per-board limit.

## Deploy to a board

One copy per board (the assigned channels make them distinct). Either:

- **AMYboard Online** (amyboard.com/editor): open the board, paste `sketch.py`,
  "write to sketch". Simplest.
- **Control API** (`tools/amyboardctl`): `amyboardctl upload_sketch sketch.py`.
- **mpremote** (board on its own USB): copy to `/user/current/sketch.py`, then
  `import amyboard; amyboard.restart_sketch()`.

## Enrollment (assign each board its channels from the Tulip)

Needs firmware with per-device `tulip.midi_out(bytes, device=N)` (multi-device
USB). From the Tulip, `deck/amyfleet.py` targets one board's USB device and
writes its channel list:

```python
import amyfleet
amyfleet.enroll_channels(device=0, chs=[2, 5])  # board 0 -> MIDI channels 2 and 5
amyfleet.enroll(device=0, channel=2)            # back-compat: a single channel
amyfleet.enroll_from_config()                   # enroll every configured board at once
```

The Fleet app's **rescan** does this automatically for detected boards. The
channels are stored as a csv in `/user/deck_channels` on the board (the legacy
single `/user/deck_channel` is still read for migration), so they survive
reboots.

## CC map (received on any of the board's channels)

| Message | Effect |
|---|---|
| Program Change | patch (0-127 Juno, 128-255 DX7, 256 piano) |
| CC 74 | filter cutoff / brightness |
| CC 71 | resonance |
| CC 70 | detune (best-effort) |
| CC 73 | amp attack (best-effort) |
| CC 72 | amp release (best-effort) |
| CC 75 | polyphony (1-16 voices) |

Note on/off play the synth on the board's channel via the default handler.
"Best-effort" params are wrapped so an AMY build lacking them can't break MIDI.
