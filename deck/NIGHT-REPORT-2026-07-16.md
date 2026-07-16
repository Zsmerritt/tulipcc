# Night run report — 2026-07-15/16

Everything done during the overnight autonomous run, every reviewer finding
with its accept/reject/defer decision, and the items that need YOUR input.
Reviewer source docs: `deck/ux-fresh/` (fresh-eyes UX), `deck/ENGINEERING-REVIEW.md`
(generalist engineer), `deck/ENGINEERING-REVIEW-FIRMWARE.md` (C/firmware
engineer), `deck/SERIAL-PROTOCOL.md` (serial audit + architecture decision).

## 1. What is on the device right now

- **Firmware** (sha `3d284e55…`, OTA'd + boot-validated, no rollback):
  flash fence, AMY task priorities two below max (flash-guard can always
  park them), **GC-rooted defer/sequencer/MIDI callback stores**, **reverb
  double-run fix**, **output unity fast-path** (samples were truncated to
  ~13-bit effective), `render_pcm` phase hoist, profiler `%lu` format fix.
- **Deck code**: everything in `main` @ `40966d04` is deployed and hash-verified.
- Verified post-flash: clean boot, config save + full router rebuild with
  **zero** `TypeError: 'float' object isn't callable` (previously 1 per save).

## 2. The big fixes of the night (chronological)

1. **76 synth kits** (was 38) incl. TB-303 Acid, TR-707/727/505/626, LM-1,
   DMX, KR-55, DR-110, RZ-1; variant fan-out; kit gain 2.5x with amp cap
   (4x latched a resonant hit into an infinite tone — reverted same night).
2. **MIDI synth kits fixed**: AMY only registers io note maps at
   patch-STRING parse time keyed on that message's synth; the slot-store
   discipline silently dropped them. Maps now registered per-hit on the
   live kit synth. Channel scrub + rebuild reentrancy guard + arpeggiator
   removal killed the "still plays drums on EMU / too loud / infinite
   notes" family.
3. **The crash class**: any flash write while AMY rendered PCM from the
   memory-mapped banks hard-crashed the S3 (reproduced on demand). Layered
   fix: flash fence (C) + task priorities + quiet-gate fallback + write
   coalescing. The write+navigate repro that crashed the deck now survives
   10/10 rounds.
4. **The float-callable mystery** (and the scarier version: silently dead
   MIDI): `tulip.defer`/sequencer/MIDI callback stores were plain C globals
   invisible to the GC — lambdas got collected and their reused heap blocks
   CALLED. All four stores are GC root pointers now.
5. **Reverb crackle root cause** (both engineers converged): the per-bus
   insert reverb ALSO ran under `AMY_AUX_REVERB` — the same delay lines
   advanced twice per frame, self-interfering (crackle scaled with level)
   and burning a spare reverb of CPU. Guard fixed; ~4% of core 1 refunded.
6. **Reverb semantics**: room asserted after every rebuild (patch-baked
   reverb can never stick); per-instrument `reverb_send` lives in the Sound
   editor, defaults DRY (patches bake zero reverb — verified), auto-enables
   the room on first raise; liveness capped 0.95.
7. **Settings**: persistent Volume+Brightness strip + Network/Display/System
   left-rail tabs (reviewer-spec'd); Restart vs Factory-reset split with
   destructive styling (the old ambiguous Reset button factory-wiped your
   config when you tested it — that "crash" was the wipe).
8. **Keyboard**: full-flash root-caused to theme style-transition
   animations invalidating the whole button matrix per keypress — killed;
   render mode now follows Settings only. Phantom double-typing into the
   hidden console filtered. Keyboard detaches+closes on every panel
   change (the Wi-Fi use-after-free hard crash).
9. **No-reset tooling**: `tools/qexec.py`/`qget.py` (DTR/RTS held low —
   every mpremote port-open was power-cycling the deck and racing its
   boot); flash_ota rebuilt on the same transport with the prep modal you
   asked for; serial protocol audit + typed-message convention everywhere
   (`deck/SERIAL-PROTOCOL.md`), nonce+checksum on file fetches.
10. **UX rounds**: review-9 (13 findings, all closed or dispositioned) and
    the fresh-eyes round (16 findings — see §3).

## 3. Reviewer ledger — accepted / rejected / deferred

### Fresh-eyes UX (deck/ux-fresh/, agent had zero prior-review context)
| ID | Sev | Decision | Note |
|---|---|---|---|
| F-1 kit mislabel on Home | high | **FIXED** | string-key kits resolve via drums_kit.kit_name |
| F-2 picker scroll-to-current | med | **FIXED** | instant jump, no animation (cost rule) |
| F-3 inverted tab hierarchy | med | **FIXED** | active tab painted explicitly; CHECKED selector never rendered |
| F-4 disabled sliders olive | med | **FIXED** | theme's DISABLED color-filter zeroed (same mechanism as Files) |
| F-5 truncated GM names | med | **FIXED** | full names stored; labels ellipsize where needed |
| F-6 Delete never red | med | **FIXED** | enabled-color cache pinned to RED |
| F-7 swap-picker affordances | low | **FIXED** | pack highlight + empty-state prompt |
| F-8 hash-suffix hit names | low | **FIXED** | display-only strip |
| F-9 star states | low | **NO CHANGE** | correct behavior: no favorites existed; off-state stars are uniformly dark by design |
| F-10 MPE ch-17 overflow | low | **FIXED** | display clamps; shows "N of M fit" |
| F-11 deletable system files | low | **FIXED** | Files refuses deck runtime modules |
| F-12 confirm-modal lifecycle | low | **DEFERRED** | reparenting the top-layer modal needs careful z-order work; tracked |
| F-13 legacy Apps tiles | nit | **FIXED** | removed |
| F-14/15/16 + costed ideas | nit | **per REVIEW.md** | costed ideas quarantined by the reviewer per your cost-first rule |

**Round-2 verification** (v2_* shots in deck/ux-fresh/shots/, statuses in
findings.json): 9 confirmed FIXED on-device, F-9 confirmed
no-change-needed, and three misses that were re-fixed the same night:

- **F-3 had regressed** — the active tab rendered the LVGL default maroon
  because the theme's exact CHECKED-state style outranks local
  default-state colors. Re-fix pins the palette at the CHECKED selector
  too.
- **F-4 wasn't the sliders** — deckui's DISABLED styles were live, but
  mpe.py dimmed whole rows with 40% opacity, and that per-pixel blend on
  RGB332 is what made olive. Replaced with a flat muted text color
  (cheaper AND correct).
- **F-10 residual** — the zone footer quoted the raw member setting,
  contradicting the clamped "(N of M fit)" line above it; footer now uses
  the same clamp.
- Round-2 nits also taken: swap-picker rows that collapse to the same
  stripped name get numbered "(2)"; Delete no longer arms (renders
  disabled, not red) for protected system modules.

### Generalist engineer (deck/ENGINEERING-REVIEW.md, E-1…E-16, O-1…O-11)
| Item | Decision | Note |
|---|---|---|
| E-1 GC-unrooted callbacks (critical) | **FIXED** (firmware) | validated: zero TypeErrors post-flash |
| E-2 fence is opt-in Python discipline | **PARTIAL / DEFERRED** | priorities make the guard park AMY during any flash op; the full C block-device fence needs a micropython-port patch — next firmware cycle |
| E-3 reverb double-run (high) | **FIXED** (firmware) | |
| E-4 RAM-slot window overflow | **DEFERRED** | real but needs 6+ melodic instruments; bounds-check queued |
| Mystery 2 littlefs corruption | **NEEDS YOUR INPUT** | see §4 |
| Mystery 3 "luus" | **FIXED** | `%lu` casts (reviewer 2 prefers `%.0f` double — either works; noted) |
| Mystery 4 quiet hits | **PARTIALLY FIXED / QUEUED** | display dedupe done; the assembler loudness gate + envelope-sort fix + content-hash dedupe queued (regenerating kits changes sounds you haven't heard yet — held for your ears first) |
| O-1 reverb guard | **FIXED** | |
| O-2 C-side MIDI route table | **DEFERRED (design captured)** | biggest remaining win (150-400 µs + 3 allocs per MIDI msg in Python); interface sketch in both reviews |
| O-5 per-instrument apply | **DEFERRED** | every channel tweak costs a full 80-200 ms rebuild; queued |
| Serial protocol review | **ACCEPTED (concur)** | minor notes (random nonce, DECK_PORT env) queued |

### C/firmware engineer (deck/ENGINEERING-REVIEW-FIRMWARE.md, C-1…C-13)
| Item | Decision | Note |
|---|---|---|
| C-1 reverb verdict | **FIXED** | same fix as E-3, independently derived with numbers |
| C-4 13-bit output truncation (med) | **FIXED** (firmware) | unity fast-path |
| C-5 render_pcm pointer-chase | **FIXED** (firmware) | phase hoisted; 1–4% of a core under kits |
| C-7 dead max-prio sequencer define | **FIXED** | dropped to MAX-3 |
| C-2 fence redesign + AUTO_SUSPEND prototype | **DEFERRED** | same as E-2; AUTO_SUSPEND unsupported on this octal flash per IDF docs, reviewer wants it prototyped anyway — queued behind your call |
| Remaining optimization list (PIE adds, block ERs, -O3 per file, piano partial-thinning knob) | **QUEUED** | est. 8–15% of the audio cores total; next session's menu |
| Boundary APIs (MIDI route table, amy_send_batch/kit_load) | **DESIGN CAPTURED** | for the Python→C migration you asked about |
| Serial C-router verdict | **CONCUR: stay Python** | both engineers + me: a C mux risks the recovery path for a solved problem |

## 4. Needs YOUR input
1. **littlefs corruption on /user** (`Corrupted dir pair {0x1,0x0}`): both
   engineers say interrupted commits from the (now fixed) crash class, on
   the root metadata pair. Remedy = backup, reformat /user, restore + move
   hot-write files into a subdirectory. I did NOT reformat your storage
   overnight — say the word.
2. **32MB soundfont briefly committed**: `GeneralUser-GS.sf2` slipped into
   one commit via `git add -A` (removed from the tree next commit, but the
   blob is in history). If you care, a history rewrite scrubs it; if not,
   ignore.
3. **SD card**: mount + log-preference code is live but pin-gated
   (`cfg['sd_pins']`) — the v4r9 mainboard schematic has NO SD nets, so I
   would not guess pins for your physical unit. Where is your slot wired?
4. **/sys migration** deferred per your call until near release.
5. **Kit regeneration** (loudness gate + dedupe + envelope-sort fix) is
   ready to run but will change kit sounds — after your by-ear pass.

## 5. Your by-ear checklist (final firmware)
1. **Reverb**: turn the room on (FX) or just raise an instrument's reverb
   send (Sound editor — auto-room). The crackle should be GONE and the
   tail correct (it was double-running before — everything reverby you
   heard previously was wrong).
2. Overall output quality: everything should sound subtly cleaner (the
   13-bit truncation fix affects every note).
3. The 76 kits at gain 2.5 (TB-303 especially), the weak tr909_d snare
   (Swap picker is the tool until the assembler regen).
4. GM Bank vs E-mu GM authenticity (blocks 1/4 still unauthenticated).
5. First-note-after-power-on (R-2, still unconfirmed by ears).
6. Keyboard: type in Wi-Fi settings — no full flash, no double input, no
   crash on Back-with-keyboard-up.
7. Settings tabs + persistent Volume/Brightness strip feel.
8. Tweak-while-sequencing: saves land instantly mid-note now.

## 6. Session commits (fork mains)
tulipcc: `70799ebe` 76 kits → `44cb5226` MIDI/crash fixes → `100891df`
keyboard UAF + OTA modal → `e4746381` reverb semantics + reset split →
`3628cc84` kb flash root cause + send-in-Sound + SD → `affed35e` no-reset
transport → `f6d8e411` UX-9 fixes → `3ef47da8` Settings tabs →
`da0dae26` review purge → `a9def998` serial protocol → `cd113543`
GC-root + fresh-eyes round → `c99ef9c6` soundfont removal → `40966d04`
C-4/5/7.
amy: `65cd842` fence → `4b33ce5` priorities → `df24325` reverb guard +
profiler → `311d825` output precision + pcm hoist.
