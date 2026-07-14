# Instrument editor + navigation plan

Goal: make **Instruments** the single root for sound design, with a full editor
per instrument whose controls mirror **the online AMYboard editor**
(`tulip/amyboardweb/stage/editor/`). "Voices" is a misnomer for the root — the
firmware `voices` app stays only as a legacy/advanced tool.

## Navigation

```
Home
 └── Instruments                         (the sound-design root)
      ├── [list of all active instruments]   name · device · channel · patch
      ├── + Add instrument                    (defaults: round-robin device, next free channel)
      └── tap an instrument → EDIT
           ├── Patch / Type      pick synth engine (Juno-6 / DX7 / Piano / FM / PCM …) + preset
           ├── Routing           device (hardware), MIDI channel (+ MPE zone if gate on), voices
           ├── Sound design      oscillators · filter · envelopes · LFO/mod · amp/pan
           └── FX                reverb · chorus · echo/delay · EQ
```

- **Instruments** shows every active instrument. **Add** creates one and, by
  **default, round-robins it onto the connected devices** (spreads voice load);
  the user can override the device in Routing.
- **Assign hardware / channel** live in the instrument's **Routing** sub-panel
  (device picker from `device_list()`, channel stepper, voices). Default channel =
  next free; default device = round-robin.
- The editor is **sub-panels** (pushed on the shell stack), because the full param
  set won't fit one screen: Patch · Routing · Sound · FX. Back pops one level.

## Parameter set (from the online editor / `amy_event_layout.generated.js`)

Whatever the online editor exposes, we expose — grouped as:

**Sound design (per instrument's synth):**
- **Oscillator / synthesis:** wave, osc count (`oscs_per_voice`), pitch
  (`freq_coefs`), FM ratio + `algorithm`/`algo_source`, pulse width (`duty`),
  `chained_osc`, `mod_source`.
- **Filter:** `filter_type` (LP/HP/BP), cutoff (`filter_freq_coefs`), `resonance`.
- **Envelopes:** amp EG (`eg0` breakpoints), filter/mod EG (`eg1`), `eg_type`
  (AMY uses breakpoint envelopes → present as ADSR with an "advanced" breakpoint
  view).
- **Amp / dynamics:** `amp_coefs`, `volume`, velocity sensitivity, `pan`.
- **Modulation / misc:** LFO (an osc as `mod_source`), `feedback`, `portamento_ms`.

**FX (per instrument, mirroring the editor):**
- **Reverb:** level, liveness, damping, xover_hz.
- **Chorus:** level, depth, lfo_freq, max_delay.
- **Echo/Delay:** level, delay_ms, feedback, filter_coef, max_delay_ms.
- **EQ:** low / mid / high (`eq_l/eq_m/eq_h`, per-synth).

**FX granularity — corrected against AMY source (`amy/src/amy.c`):** all four FX
(`reverb`/`chorus`/`echo`/`eq`) are **per-BUS** — `config_eq(bus,...)`,
`config_chorus(bus,...)` etc. write `amy_global.bus[bus]->...`. A synth is
assigned to a bus with `amy.send(synth=N, bus=B)`; then `amy.send(synth=N, eq=..)`
routes to that bus (verified on-device: unassigned synth warns
`synth N not defined (get_bus)`; after `bus=` it's clean). So:
- **Per-device (chosen now, "hybrid"):** put all of a device's instrument synths
  on **one bus** (e.g. bus 0); reverb/chorus/echo/EQ configure that bus → one FX
  chain per device. Simple, low DSP. FX + EQ live in per-device storage.
- **Per-instrument (now known-feasible, deferred):** give each instrument its
  **own bus** → independent reverb/chorus/echo/EQ per sound. Bounded by AMY's bus
  count + DSP (each active reverb bus costs CPU) and needs the Python per-bus FX
  config surfaced. This is the option to revisit "if we miss it" — it's a routing
  choice, not a missing AMY feature.
- **`eq` is per-bus, not per-synth** — it moves OUT of the per-instrument param
  set into the per-device FX (alongside reverb/chorus/echo). Filter/osc/env/amp
  stay per-instrument.

## Tabbed layout — DECIDED: left vertical tabs

Instead of one long scroll of all params/FX, the editor uses **left (vertical)
tabs**, each tab a short list. Rationale (from research — vertical tabs suit many
sections + tall content + saving horizontal space; horizontal tabs are for 3–6
categories and would double-stack under our top bar):
- **Sound editor** = an `lv.tabview` with the tab bar on the **left**; one tab per
  param **group**: **Osc · Filter · Envelope · LFO · Amp** (Osc B / advanced tabs
  appear when "Advanced" is on). Each tab shows just that group's few controls.
- **FX editor** (per device) = left tabs **Reverb · Chorus · Echo · EQ**.
- Built on `lv.tabview` with `set_tab_bar_position(lv.DIR.LEFT)` + a ~140px tab bar;
  each tab's content is a `ParamEditor` fed that group's defs. The **Basic/
  Advanced** toggle filters params within tabs (and can hide advanced-only tabs).
- Keep the tab labels short (icons optional later). Back (shell) still exits the
  editor; the tabview handles within-editor navigation.

## Editor architecture — a generic, inheritable param editor (DECIDED)

The editor is a **generic, data-driven param-editor framework** that curated
per-instrument editors can inherit or implement:

- A **`ParamEditor` base** takes a list of **param definitions** and renders the
  right `deckui` control per type (slider / stepper / dropdown / toggle), reads/
  writes the value on the instrument, and emits the AMY change on edit. It knows
  nothing about "Juno" vs "DX7".
- A **param definition** = `{name, group, type, min/max/step or options, target
  (amy param / change_code), default}` — **mirrored from the online editor's knob
  definitions** (`amy_parameters.js` / `editor_knobs.js`) so deck + web stay in
  lockstep. Ideally generated from the same source as `amy_event_layout` (from
  `amy.h`) so new AMY params flow in with a regen, not a hand-port.
- **The generic instrument editor** = `ParamEditor` fed the full AMY param set,
  grouped (Oscillator / Filter / Envelope / Amp / FX...).
- **Curated views** (e.g. a Juno screen with familiar slider names) **subclass /
  implement** the base with a param subset + labels/order. Optional, added later,
  still just data — so a new instrument never forces a bespoke editor (that was
  the legacy `juno6` trap we're avoiding).
  - **D4 builds three:** **Juno-6**, **DX7**, and **Piano** curated views, each a
    thin `ParamEditor` subclass over the relevant param subset with engine-native
    labels/grouping. The generic editor remains available as the fallback for any
    engine (incl. future ones) with no curated view.

This is why we don't "port everything" per instrument: new instrument = new
patches (auto-listed) + generic editing; a curated view is optional data on top.

## Implementation notes
- Reuse the online editor's **knob definitions** as the source of truth for which
  params, ranges, and `change_code` each control emits (`editor_knobs.js` +
  `amy_parameters.js`) so the deck and web stay consistent.
- `deckcfg` instrument gains a `params` (synth) + `fx` dict; the **forwarder**
  applies them — internal via `amy.send(synth=…, <param>=…)`, board via the
  per-board link (later: the UART/USB device path in BOX-DESIGN.md).
- Extend the existing **rack** (`rack.py`) editor: add **Sound** and **FX** nav
  rows next to Patch, each pushing a sub-panel of `deckui` sliders/steppers
  generated from the editor's knob list.
- Keep the current Patch picker (Juno/DX7/Piano + presets) as the "Patch/Type"
  sub-panel; add engines the editor supports (FM/PCM) as more categories.

## Open questions
- How much of the AMY param surface to expose at once vs a Basic/Advanced split
  (the editor is deep; a Basic view + "Advanced" reveal likely keeps the touch UI
  usable — the `ParamEditor` can drive both from a `basic`/`advanced` flag on each
  param def).
- Applying params to a *board* instrument before the UART/USB-device transport
  exists (interim: internal instruments fully via `amy.send`; boards get Program
  Change + a subset over the current per-device USB).
