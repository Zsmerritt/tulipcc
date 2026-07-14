# UX Research — Modern Touchscreen UI for an Embedded Music Instrument

Research foundation for the Tulip Deck UX audit. This document gathers current
(2025–2026) best practices for **fixed-panel, landscape, embedded/appliance
touch UIs** (not phones), plus synth/DAW editor conventions, the anti-patterns
that matter here, and a concrete **grading rubric** used in `UX-REVIEW.md`.

The target device: a **1024×600 landscape** resistive/capacitive touchscreen, a
standalone music computer / synth. It is operated at a desk/tabletop, usually
with a finger or two, often while also playing a MIDI keyboard — so it is a
**two-handed, glanceable, tabletop appliance**, closer to a POS terminal or a
hardware synth than a phone.

---

## 1. Touch target sizing (the single most-cited rule)

Consensus minimums across the major standards:

| Standard | Minimum | Notes |
|---|---|---|
| Apple HIG | **44×44 pt** | Long-standing iOS floor. |
| Google Material / Android a11y | **48×48 dp** | "At least 48×48dp for most platforms." |
| WCAG 2.5.8 (AA, 2.2) | **24×24 CSS px** | An absolute floor, OR adequate spacing. |
| WCAG 2.5.5 (AAA) | **44×44 CSS px** | The comfortable target. |
| Research (Parhi/Karlson/Bederson; MIT Touch Lab) | **~9–10 mm (≈1 cm)** | Average fingertip 16–20 mm wide; error rates up to 75% higher on small targets for users with motor impairment. |

Practical rules that fall out of this:
- **Interactive height ≥ 44–48 px; ≥ 9 mm physically.** A 1 cm target is the
  research floor for *fast, accurate* selection.
- **Spacing/exclusion zone:** ≥ 8 px between targets; a ~7 mm buffer of dead
  space around small targets so a fat finger doesn't hit the neighbor.
- **Decouple visual size from hit size:** a control can *look* small (a 24 px
  icon, a thin slider track) as long as its **touch area is padded out to
  44–48 px**. This is the standard fix for thin sliders/switches.
- **Resistive panels penalize small targets more** than capacitive: they need
  deliberate pressure and are less precise, so err larger.

> **Slider caveat (directly relevant here):** a slider's *grabbable* region is
> the knob + track height. A 14 px-tall track with a small knob is far below the
> 44 px floor. The accepted pattern is a **taller invisible hit area** (pad the
> knob), a **bigger knob**, and ideally **+/− nudge buttons** for fine values.

---

## 2. Layout, reach & ergonomics on a fixed landscape panel

Phone "thumb-zone" maps assume one hand holding the device; a **fixed tabletop
panel** is different. What carries over vs. what changes:

- **Horizontal scanning is natural; vertical is not.** Landscape panels suit
  wide, row-based layouts; humans scan left→right comfortably. Long vertical
  scrolls are more tiring and hide content below a "false bottom."
- **Corners are the hardest to reach and the easiest to mis-hit**, but on a
  *fixed* panel the whole surface is reachable with a reach — so the constraint
  is less "thumb arc" and more **visual hierarchy + not wasting the panel**.
- **Fill the panel; don't cluster.** A common embedded mistake is porting a
  phone portrait layout onto a wide panel, leaving huge dead margins. Use the
  width: multi-column, side-by-side controls, persistent context.
- **Group by frequency & task**, not by internal architecture. Put the controls
  a performer touches often where they're quickest to hit and read.
- **Glanceability:** appliance UIs are read at arm's length, sometimes in
  peripheral vision while playing. Status must be readable in <1 s: big type,
  high contrast, unambiguous state color.

---

## 3. Navigation patterns

**Tabs — top vs. left (NN/g, Eleken, UXDworld, Lollypop 2025):**
- Horizontal top tabs: best for **3–6 peer categories**, all visible at once,
  single row (never wrap to two rows — it breaks the tab↔content mapping).
- **Left / vertical tabs:** better when there are **many sections**, long
  labels, or tall content; save horizontal space; scale down a list well.
- **Stick to two levels of tab depth.** Deeper → use accordions / drill-in /
  progressive disclosure instead of nested tabs.
- **Active vs. inactive must be obvious** — color + weight + an indicator
  (underline/rail). Don't rely on a single subtle cue.

**Drill-in stacks + Back + breadcrumbs (LogRocket, NN/g):**
- **Back** suits shallow, linear paths and small screens: one clear affordance
  that pops one level. It should be **in a consistent place on every screen.**
- **Breadcrumbs** shine at 3+ levels deep, letting users jump up multiple
  levels without repeated Back taps. Keep them near the top; the current page is
  the last crumb and is **not** a link.
- **Consistency is the whole game:** Back must live in the same spot and behave
  the same way everywhere. Moving it (e.g. top-left on some screens, top-right
  on others) is a documented orientation failure.

**Home / launcher grids:**
- Large, labeled tiles are great for a small, fixed set of destinations.
- **Color/icon should encode meaning**, not decoration. If two unrelated tiles
  share a color, the color is noise (or worse, implies a false relationship).
- Fill the grid or justify the whitespace; a 6-tile grid stranded in the
  top-left of a 1024×600 panel reads as unfinished.

**Modal vs. non-modal:**
- Modal = blocks everything until dismissed; reserve for **destructive
  confirms** and must-answer decisions. Non-modal = adjust and keep working;
  right for **live parameter tweaking** (a synth editor should be non-modal so
  sound changes are auditioned live).

---

## 4. Feedback, affordance & state (the flat-design "usability ceiling")

- After a decade of flat design there's a recognized **affordance problem**:
  users can't tell what's tappable. 2025–2026 guidance is a **hybrid /
  neo-skeuomorphic** correction — keep it clean but restore depth cues
  (subtle shadow, border, pressed state) so controls read as controls.
- **Every touch needs immediate feedback** — pressed/active state, color change,
  ripple, or motion — so the user knows the tap registered (critical on
  resistive panels where a press can be missed).
- **Toggles must show current state *and* be unambiguous about the action.** A
  button whose label is its current state ("On"/"Off") is a known ambiguity: is
  "On" telling me it's on, or offering to turn it on? Prefer a real **switch**
  with a clear thumb + track, or label the *state* and reinforce with color +
  position, never color alone.
- **Show the value.** A control that changes a number (cutoff, level, delay ms)
  must display that number. A blind slider forces trial-and-error.
- **Motion**: use it to explain transitions (where did this panel come from);
  keep it fast and subtle; respect reduce-motion. Never gate meaning on
  animation alone.
- **Contrast**: WCAG AA text contrast (4.5:1 body, 3:1 large). Muted-gray labels
  on mid-gray cards, or a colored status word on a colored fill, routinely fail.

---

## 5. Synth / DAW / hardware-instrument editor UX

From hardware-editor conventions (UNO Synth Editor, Yamaha Montage, DX/Juno
editors, touch DAW control surfaces) and the deck's own reference (the AMYboard
web editor):

- **Param pages, grouped by signal flow:** Osc → Filter → Env → LFO → Amp → FX.
  A page/tab per group with only that group's controls beats one giant scroll.
- **Basic/Advanced (progressive disclosure):** show a small performance-friendly
  set by default; reveal the deep parameter surface on demand. The reveal must
  be **discoverable and clearly labeled as a mode**, and Basic must still be
  *useful* (not one lonely control).
- **On touch, sliders/steppers replace knobs**, but must be **big and show
  their value**; vertical faders or horizontal sliders with numeric readout and
  nudge buttons. Endless-rotary "knobs" are hard on touch and should show value.
- **Live audition:** selecting a patch or moving a param should sound
  immediately (non-modal), ideally with a preview note.
- **Name things musically:** "A11 Brass Set 1", "Cutoff", "Attack (ms)" — not
  raw engine tokens ("Juno0", coef indices). The editor should hide AMY's
  internal encoding, not leak it.
- **Don't offer two parallel editors** for the same thing (legacy + new) unless
  one is clearly badged "advanced/legacy" and out of the main path.

---

## 6. Anti-patterns to flag (watchlist)

- **Mystery-meat navigation** — controls whose destination/function isn't clear
  until you tap. Includes unlabeled icons and cards that are secretly buttons.
- **Tiny targets / thin sliders / small knobs** below the 44 px floor.
- **Hidden state / hidden features** — functionality that only appears after an
  unrelated global toggle, with no in-context hint of how to reveal it.
- **Ambiguous toggles** — On/Off buttons where label vs. action is unclear;
  state conveyed by color alone.
- **Inconsistent controls / nav** — Back or primary actions moving between
  screens; the same concept edited two different ways in two places.
- **False bottom / wasted panel** — content clustered so the rest of the screen
  looks empty or "done."
- **Low-contrast text** — muted labels on mid surfaces; colored text on colored
  fills.
- **Destructive action without confirm** — Reset/Delete one tap from firing.
- **Value-less controls** — sliders with no numeric feedback.
- **Design-system whiplash** — crossing from a polished shell into unstyled
  borrowed screens with a totally different look and control set.
- **Truncated wayfinding** — a breadcrumb/title ellipsized to "Edit…" tells the
  user nothing about where they are.

---

## 7. Grading rubric

Each screen and the app as a whole are graded on these dimensions. Severity for
findings: **Critical** (breaks a task / data loss / crash), **High** (frequent
friction, likely mis-taps, wrong mental model), **Med** (noticeable, has a
workaround), **Low** (polish).

| # | Dimension | What "good" looks like |
|---|---|---|
| R1 | **Touch ergonomics** | All interactive targets ≥ 44 px effective; sliders have big knobs/hit areas or nudge buttons; ≥ 8 px spacing. |
| R2 | **Hierarchy & density** | Panel width used; clear primary vs. secondary; no stranded whitespace; grouped by task/frequency. |
| R3 | **Navigation consistency** | Back in the same place & behavior everywhere; ≤ 2 tab levels; breadcrumb/title always informative. |
| R4 | **Affordance & feedback** | Tappable looks tappable; pressed/active states; live audition; motion explains transitions. |
| R5 | **State clarity** | Toggles unambiguous; current selection obvious; values shown; status glanceable in <1 s. |
| R6 | **Labeling & language** | Musical, human labels; no engine tokens; no truncation of key wayfinding. |
| R7 | **Consistency of system** | One palette, one control vocabulary, one look across shell + editors + borrowed apps. |
| R8 | **Contrast & legibility** | AA contrast; no muted-on-mid or color-on-color text; readable at arm's length. |
| R9 | **Discoverability** | Features reachable without hidden global switches; disclosure is signposted. |
| R10 | **Safety** | Destructive actions confirmed / clearly marked; no accidental data loss. |
| R11 | **Robustness** | UI survives rapid navigation without crashing/rebooting; no dead-end panels. |

---

## Sources

- Nielsen Norman Group — [Touch Target Size](https://www.nngroup.com/articles/touch-target-size/), [Tabs, Used Right](https://www.nngroup.com/articles/tabs-used-right/)
- Apple — Human Interface Guidelines (44 pt target); Google — [Material / Android touch target 48dp](https://support.google.com/accessibility/android/answer/7101858?hl=en)
- W3C WCAG 2.2 — [2.5.8 Target Size (Minimum)](https://testparty.ai/blog/wcag-target-size-guide) (AA, 24px), [2.5.5 Target Size (AAA, 44px)](https://testparty.ai/blog/wcag-2-5-5-target-size-2025-guide)
- LogRocket — [All accessible touch target sizes](https://blog.logrocket.com/ux-design/all-accessible-touch-target-sizes/), [Breadcrumbs vs. back arrow](https://blog.logrocket.com/ux-design/breadcrumbs-vs-back-arrow-ux-best-practices/), [Progressive disclosure](https://blog.logrocket.com/ux-design/progressive-disclosure-ux-types-use-cases/)
- Eleken — [Tabs UX best practices](https://www.eleken.co/blog-posts/tabs-ux); UX Design World — [Tabs navigation](https://uxdworld.com/tabs-navigation-design-best-practices/); Lollypop — [Tab design 2025](https://lollypop.design/blog/2025/december/tabs-design/); DesignMonks — [Nested tab UI](https://www.designmonks.co/blog/nested-tab-ui)
- iViewTouch — [Touch UI design best practices 2025](https://iviewtouch.com/2025/11/touch-ui-design-7-best-practices-that/); Capiproduct — [Touch targets in mobile UI](https://www.capiproduct.com/post/10-best-practices-for-designing-effective-touch-targets-in-mobile-ui)
- Wikipedia / Grokipedia — [Mystery meat navigation](https://en.wikipedia.org/wiki/Mystery_meat_navigation); NumberAnalytics — [Avoiding UI anti-patterns](https://www.numberanalytics.com/blog/avoiding-ui-pitfalls-anti-patterns)
- Flat vs. skeuomorphic / affordance revival: [edesignify](https://edesignify.com/blogs/flat-vs-skeuomorphic-design-which-is-better-for-user-experience), [Kryzalid](https://kryzalid.net/en/web-marketing-blog/skeuomorphism-an-unexpected-comeback-in-2025/), [Timothy Graf 2026](https://timgraf.com/ui/glassmorphism-vs-neumorphism-high-end-ui-guide-2026/); Uxcel — [Affordance](https://uxcel.com/glossary/affordance)
- Fresh Consulting — [Progressive disclosure](https://www.freshconsulting.com/insights/blog/uiux-principle-51-progressive-disclosure-hides-complexity/); IxDF — [Progressive disclosure (2026)](https://ixdf.org/literature/topics/progressive-disclosure); Designlab — [Microinteractions](https://designlab.com/blog/microinteractions-enhancing-user-experience-through-small-details)
- Synth editors: [UNO Synth Editor](https://www.ikmultimedia.com/products/unosynth/index.php?p=editor); [Yamaha Montage forum](https://yamahasynth.com/community/montage-series-synthesizers/daw-montage-controls-effects-assignable-etc/); [Touch DAW control surface tips](https://inairspace.com/blogs/learn-with-inair/touch-screen-daw-control-surface-tips-for-faster-smarter-music-production)
