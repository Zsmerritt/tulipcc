# Tulip fleet "box" — design doc

The end goal: **Tulip + N AMYboards as one integrated, extensible instrument in a
box** — screen accessible, required ports on the outside of the case, driven from
an external MIDI router (a CME H12MIDI Pro), with each internal board a fully
independent voice you can play with full MPE and control per-parameter.

This captures the architecture + the reasoning behind the rejected options, so we
don't re-litigate. Companion docs: `PLAN-rework.md` (the deck software rework),
`DOCKET.md` (parked items).

---

## 1. The instrument model (software)

Instrument-first (see `PLAN-rework.md` Phase 2, largely built):
- The unit is an **instrument** = `{device, channel/zone, patch, params, MPE,
  voices}`; `device` = `'internal'` (Tulip AMY) or a board index.
- **Layering** is allowed: multiple instruments may share a channel (or MPE zone)
  → stack/layer.
- The **forwarder** is the sole owner of sound: incoming MIDI → every matching
  instrument → its device (internal synth it owns, or a board over that board's
  link). No reliance on `midi.config`'s one-synth-per-channel.
- **MPE is off by default, gated by a global Settings toggle** (`mpe_enabled`).
  Until it's turned on, no MPE UI shows anywhere and no MPE is applied — keeps the
  advanced complexity out of the way for the common case. (Deck Milestone C.)
- **MPE is a zone**, not a channel: `{master, member_count}`, occupying a range of
  a device's 16 channels. Per-device channel budget is tracked/shown; over-
  subscription warns. Layered MPE = multiple instruments sharing one input zone.
  (The full zone/channel model + layered-MPE routing is a later milestone; the
  global on/off gate lands first.)

## 2. Connectivity — the core architecture

### The hard constraint
The ESP32-S3 has **exactly one USB-OTG** (host **or** device, never both) plus a
fixed USB-Serial/JTAG (console only). On the R11, the two USB ports are:
- **Native USB (OTG)** — the *only* USB-MIDI-capable interface. Currently the host.
- **CH340K USB-serial bridge** — the REPL/flashing port. Can only ever be USB-
  serial (CDC); **cannot present USB MIDI**.

So you cannot both host the AMYboards over USB *and* present USB MIDI to the CME
on the native controller.

### The decision
- **Native USB = device**, presenting **N virtual MIDI ports (cables)** — one per
  internal device (Tulip AMY, Board A, Board B, …). This is the box's single
  external USB, into the **CME host port**. The CME addresses each device
  independently → full 16-channel stream + full MPE per device.
- **AMYboards are NOT on USB.** They connect over **dedicated per-board serial/UART
  links** from the Tulip's GPIO/UART pins — one link per board so each gets its
  own full stream (extensible). The link carries live play data **and** the OTA
  firmware stream.
- **CH340 port = dev/recovery only**, internal or recessed. Not exposed for normal
  use.
- **3 TRS MIDI-in jacks** (one per device) as the wired alternative to the CME-USB
  path; they feed each device's serial link.
- **CME is external and self-powered** — the controller front-end (merges/routes
  the user's controllers). The box is exactly one USB device to it.

### Rejected options (and why)
- **Shared MIDI stream (daisy-chain one Tulip MIDI-out to all boards):** one
  16-channel stream shared across boards — fine for a single board, but not
  extensible and can't give each board full independent MPE. **Rejected.**
- **Tulip hosts the CME (Tulip host ← CME client, cables):** breaks the "one
  device" model, and daisy-chains power Tulip→CME→boards. The CME client port is
  for *computer* config, not this. **Rejected by owner.**
- **Boards on USB host + CME over USB:** impossible on one OTG. Would need an added
  USB-host chip. **Not pursued.**

### Latency note
Serial MIDI at 31250 baud ≈ 1 ms for a note-on (same as USB full-speed's 1 ms
frame). Board↔board UART can run 1 Mbaud+ → ~30 µs. "Press key → note on" stays
instant. **Caveat:** dense multi-board MPE (continuous per-note bend/pressure)
wants headroom, so the per-board links should run a **high baud raw UART**, not
31250 through an opto — which is why they need real GPIO pins, not the MIDI jack.

## 3. AMYboard firmware: mode + updates

- **Boot handshake:** board boots to a safe default and handshakes with the Tulip
  over its link. Handshake received → Tulip commands the operating mode; no
  handshake (e.g. plugged into a plain host) → run standalone/compatible.
- **Updates via the Tulip (no per-board flashing, no mpremote):** the AMYboard
  image ships *with* the Tulip — `tulip.upgrade()` already reflashes the app +
  `/sys`, so it also carries `amyboard.bin`. The Tulip reads each board's version
  over the link; if behind, it commands "update mode," streams the `.bin`
  (app-level OTA over the serial link), the board writes its OTA partition and
  reboots. Image can also come from the SD card or a URL.
- Trade-off accepted: we lose the boards' USB CDC REPL. We keep MIDI/CC/SysEx/MPE
  and gain link-based OTA. Each board still has its own USB-C internally for
  emergency recovery.

## 4. Power
- **One 5V supply** for the box (budget ~2.5–3A), or a Eurorack supply since the
  AMYboards are Eurorack modules. Powers Tulip + boards (via power pins / Eurorack
  bus). **CME self-powered.** No chaining Tulip→CME→boards.
- Prefer a clean native-5V source (5V/3A brick) over a noisy buck for audio; keep
  a 20V-PD→buck only as break-glass, filtered. (See DOCKET power notes.)

## 5. Audio
Each device renders its own audio (independent synths). The Tulip can't cheaply
re-ingest board audio digitally, so combine in **analog**:
- **Per-device jacks** (each board's Line-out) for full separation, **or**
- **Analog summing** (small op-amp) → one main stereo out, **or** both.
- Possible elegant alternative: the AMYboards have **S/PDIF in + out** — if their
  firmware mixes S/PDIF-in with its own output, boards could **daisy-chain audio
  digitally** into one combined S/PDIF out (no analog summing). *To confirm.*
- Default: mixed main stereo out + optional individual outs. Watch levels + star
  ground so boards don't inject noise into each other.

## 6. SD card
- **Recovery without opening the box:** button-on-boot → Tulip reads a firmware
  image from SD and reflashes itself (and can push `amyboard.bin` out over the
  links). SD support already enabled in the build.
- **Samples:** AMY loads PCM/samples from the same card.

## 7. Screen dim / sleep (deck Phase 4 — module built)
- `screensaver.py`: LVGL inactivity timer as the idle source; touch resets it,
  MIDI wakes it via `trigger_activity()`. Steps brightness full → dim → off on
  timeouts (`dim_after`/`sleep_after` in Settings; 0 = Never).
- **Firmware:** `display_brightness(0)` now fully cuts the backlight (max PWM duty
  on the active-low 13-bit channel). `SLEEP_LEVEL=0` = true off.
- Do **not** animate while off (LCD, no burn-in; nothing visible). An optional
  ambient "now-playing/visualizer" screensaver could be a *third* sleep style
  later — trades power for looks; not for protection.

## 8. The carrier board
Physical integration. The AMYboards are **Eurorack 10HP modules**, so power +
mounting can be **off-the-shelf Eurorack** (power distro board, rails, case). The
custom part is small:
- **Power:** 5V/Eurorack in → distribution → each module's power header.
- **Data:** Tulip GPIO/UART lines → one dedicated link per board (the part that
  needs Tulip pins broken out; caps board count by available GPIOs).
- **Audio:** board Line-outs → individual jacks and/or a summing op-amp → main out.
- **Mounted external ports:** native USB→CME, 3× TRS MIDI-in, audio out, SD, power.
- Boards attach via their existing headers/jacks into matching connectors or
  cables — no soldering for the assembler.
- **Build approach:** prototype **hand-wired first** (jumpers/Grove cables, bench
  supply) and get it *playing*; the PCB is the last step that tidies proven
  wiring. KiCad + JLCPCB/OSHPark. It's a connector/route board — the gentle kind.

## 9. Open questions / to verify
- **Tulip exposed GPIO/UART pins** — how many independent board links we can drive
  without modifying the Tulip. **This caps board count and defines the carrier.**
- AMYboard link: raw UART vs I2C-slave; whether the AMYboard exposes suitable
  header pins; baud for dense-MPE bandwidth.
- S/PDIF-in mixing on the AMYboard (for digital audio chaining).
- Exact connectors (Grove/JST/Eurorack) for power/data/audio.
- USB-device firmware: does the S3 device stack cleanly present N MIDI cables
  alongside (or instead of) CDC.

## 10. Firmware/software work items
- **Tulip firmware:** native USB **device** MIDI with N cables; per-board UART TX
  + a thin board-ID framing; UART OTA sender; SD recovery/boot-button; boot
  handshake master. Backlight-0 — **done** (PR #2).
- **AMYboard firmware:** per-board UART MIDI in + address/handshake; UART OTA
  receiver; mode switching.
- **Deck (in progress):** instrument rack + Devices (done, Milestone B); Milestone
  C = device meter strip + home reorg + screensaver wiring; MPE-zone model +
  layered MPE; map deck "devices" → the N per-board links / USB cables.
