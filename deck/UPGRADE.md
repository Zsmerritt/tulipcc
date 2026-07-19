# Automated firmware upgrade — design

Status: **design draft, no code.** Supersedes the host-driven ping-pong flow in
`PINGPONG.md` for the *user-facing* path; the ping-pong slot mechanism itself
(flasher in `ota_0`, play in `ota_1`, `flashmode.py`) is reused unchanged.

Goal: the user taps **Upgrade** and the deck does the whole thing on its own —
find the update, validate it, apply it at a thermally-safe clock, reboot,
self-check, and either finish or roll back — showing a two-tier progress screen
throughout, with no computer attached.

---

## 1. Hardware constraints that shape everything

These are not preferences; they're what the S3 can and cannot do.

- **You cannot boot or run from SD.** The CPU executes the app XIP from
  memory-mapped internal flash (the OTA slots). SD is not memory-mapped. The
  running app *must* live in `ota_0`/`ota_1`.
- **Banks must be in internal flash to sound.** PCM plays by mmap from the
  `fonts` partition (the vaddr pool). SD can't be mmap'd; a bank on SD can't
  play. So `fonts` is written to internal flash, always.
- **Internal flash is full.** Big bank + drums + GeneralUser need 258 of the
  256 vaddr pages ([[s3-vaddr-pcm-banks]]); one bank already rides in PSRAM.
  There is **no spare app-sized slot** in internal flash for a second copy of
  play — this is why true A/B needs off-chip storage.
- **120 MHz octal flash corrupts sustained multi-MB writes under thermal
  drift** (the reason ping-pong exists). Big writes must happen at 80 MHz, i.e.
  booted into the flasher. Small writes are far lower-risk.
- **otadata is the atomic commit.** One CRC'd sector; highest valid sequence
  wins; a torn write fails CRC and the other sector wins. Flipping it is the
  only truly atomic operation we have, and the entire recovery model hangs off
  that.

**Consequence:** SD is a **storage tier, not a compute tier.** It cannot run or
mmap anything, but it can archive/stage unlimited data. That is exactly what
true A/B rollback was missing.

---

## 2. Storage tiers

| Tier | Holds | Role in an upgrade |
|---|---|---|
| `ota_0` (internal) | **80 MHz flasher** | thermally-safe *writer*; no-SD recovery anchor. **Never overwritten by the flow.** |
| `ota_1` (internal) | **120 MHz play** | the deck. The target of an app update. |
| `fonts` / `vfs` / `system` (internal) | banks / `/user` / `/sys` | single-copy; written by the flasher. |
| **SD — user FAT partition** | user files + dropped update bundles | visible on a computer; the user drags a single-file bundle here. |
| **SD — reserved partition (~128 MB)** | staged selected upgrade + rollback archive | carved at prepare time (§9); non-FAT type so a computer won't auto-mount it; the flasher's canonical bundle source. Runs nothing. |
| **NVS** | Wi-Fi creds, chosen-update identity, phase record | the flasher's source of truth; survives every partition rewrite. |

The 80 MHz flasher is a **transient tool during the write phase only**. The deck
never runs there as its steady state; it returns to 120 MHz play after every
update.

---

## 3. The bundle + manifest format

A bundle is **manifest-driven and region-oriented**. The deck never cares
whether bytes come from SD or a URL — both are just "a manifest plus a way to
read each region."

- **On SD:** a bundle is a **single file** with our own extension (a zip
  container under the hood) — one file the user drags onto the SD's visible
  partition. The deck opens it as a zip and streams each region out; the big
  `fonts` region is STORED (uncompressed) inside so it streams straight to flash
  without inflating 14.85 MB into RAM. On Upgrade the chosen bundle is expanded
  into the SD's **reserved partition** as the canonical selected upgrade (§9),
  and its identity pinned in NVS.
- **From the internet:** no container — the deck pulls each region per the
  manifest URLs (CI already emits `app.bin`/`fonts.bin`/`sys.bin` separately),
  streaming each straight to flash (staging into the reserved partition when
  present). We control this download, so piecemeal needs no single-file wrapper.
- **Manifest** (sketch):

```json
{
  "format": 1,
  "min_engine_format": 1,
  "fw_version": "2026.07.17",
  "partition_table_sha256": "…",
  "regions": [
    {"label":"ota_1","file":"app.bin","offset":"0x3a0000","size":3326480,"sha256":"…"},
    {"label":"fonts","file":"fonts.bin","offset":"0xc30000","size":14848000,"sha256":"…"},
    {"label":"vfs","file":"user.bin","offset":"0xa30000","size":2097152,"sha256":"…","merge":"deck-code"}
  ],
  "notes":"GM rebake: piano loop fix"
}
```

Guardrails encoded in the format:

- **`format` / `min_engine_format`** — your v1/v2. The deck applies a bundle
  only if it understands its `format`; a bundle needing a newer engine than the
  deck has is *refused with a message* ("update the deck over USB first"), never
  half-applied. This is what lets the upgrade engine itself evolve without a
  future bundle bricking an old deck.
- **`partition_table_sha256` must match the running device.** If it differs the
  layout changed, and **layout changes are a USB job, not this flow** (rewriting
  the partition table from the device is the single scariest write and its only
  recovery is USB anyway). Refuse, don't attempt.
- **`sha256` per region** — integrity, checked after download *and* after the
  flash read-back.
- **`merge: "deck-code"` on `vfs`** — bakes in tonight's lesson: `/user` is
  **not** blindly overwritten. Deck code (top-level `*.py`, per `deploy.ps1`)
  takes the bundle version; `/var` (config, Wi-Fi, logs) is preserved
  byte-for-byte. See [[vacuous-verification]] for why this rule exists.

---

## 4. The flow

### 4a. Arm (running in play, 120 MHz)

1. Tap **Upgrade** → warning modal (§7).
2. **Quick-scan the SD** for bundles + offer **"From internet"** as a separate
   button. If several bundles exist, list them with `fw_version`; user picks
   one. Fetch the chosen manifest, validate its `format`/`partition_table`.
3. **Diff:** read back each on-device region's sha256 and mark only the ones
   that differ from the manifest. Most updates change only `ota_1` (app) and
   maybe `vfs`; `fonts` changes only on a rebake. A typical update becomes a
   ~30 s app-only write; tonight's bank rebake is the rare heavy one.
4. Pin into **NVS**: chosen-update identity (source + `fw_version` + manifest
   sha), Wi-Fi creds, and the changed-region list. Set `flash_pending`,
   `set_boot(flasher)`, reboot.

Wi-Fi creds go to NVS **here** so the flasher never needs `/user` to reach the
network — the current `flashmode._bring_up_wifi()` reads creds from `/user`,
which breaks the moment `/user` is mid-rewrite. (Fix required.)

### 4b. Apply (flasher, 80 MHz)

1. Bring up Wi-Fi from NVS. Redraw the progress screen from the NVS phase record
   (§6 — it must survive the reboot).
2. **If SD present: archive first.** Copy the current internal `ota_1` / `fonts`
   / `vfs` to an SD backup bundle. This is the rollback "B".
3. For each *changed* region: **stream-and-write** (read the region from SD or
   URL in chunks, write each straight to its partition), then **read back and
   verify sha256** against the manifest. `vfs` honours `merge: "deck-code"`.
   `ota_0` (the flasher) is never a target.
4. When *every* changed region verifies: `set_boot(ota_1 play)`, clear
   `flash_pending`, reboot. **This otadata flip is the commit.**

### 4c. Self-check (play, 120 MHz)

1. Boot, redraw the final progress step, run the self-check: DECKLOG present,
   banks loaded, no boot panic. (Integrity only — see §8.)
2. **Pass** → clear the chosen-update record, mark the SD backup as "previous",
   show "Updated to `fw_version`", done.
3. **Fail** →
   - *SD present:* the flasher restores the SD backup into internal flash and
     boots the **restored previous 120 MHz play**. Real rollback.
   - *No SD:* boot the flasher as a working (80 MHz) recovery deck and show the
     failure. Not bricked; not silent.

---

## 5. Recovery model — the invariant

> **otadata always points at a complete, bootable slot, and only flips to play
> after every changed region has been written and verified.**

Every risky region is written *in place* but is never the boot target until
committed, so a half-write is always thrown away, never booted. The flasher
restarts each region from scratch, so **no resume-journal is needed for
correctness** (a journal only saves re-download time). Power-cut walk:

| Power cut during… | State on flash | On reboot |
|---|---|---|
| Play preflight / manifest fetch | nothing written | boots play; stale flag cleared; no harm |
| Mid `ota_1` app write | half app | otadata still flasher → re-enter → rewrite from scratch |
| **Mid `fonts` write (~3 min, the big window)** | partial fonts | flasher redoes it; nothing ever boots a half `fonts` |
| Mid `vfs` write | half `/user` | flasher redoes; Wi-Fi from **NVS**, so still online to redo |
| Between verify and `set_boot` | all good, uncommitted | flasher redoes (cheap; nothing to rewrite, all verifies) |
| Between `set_boot` and reboot | committed | play boots; regions already verified |
| Flasher's own boot | flasher untouched (never written) | boots flasher, resumes |
| Play self-check fail | new fw written | rollback per §4c |

Two hard guardrails: **never write `ota_0`** (lose the anchor = brick, USB-only
recovery), and **the flasher must depend on nothing in `/user`** (creds + URL +
phase all in NVS).

---

## 6. Two-tier progress UI

Extends `fwprogress.py` from one bar to two:

- **Main bar** — overall progress + the current *step* name ("Downloading",
  "Writing banks", "Verifying", "Finalising"). **Weighted by bytes**, not
  "step N of 8", so it doesn't lurch — a 14.85 MB write and a 4 KB otadata
  write are not the same fifth of the job.
- **Sub bar** (thinner, below) — per-step progress + a *substep* label
  ("chunk 128 / 512", "verifying sha256", "archiving to SD").

**It must survive the reboots.** The play→flasher→play hop loses RAM, so the
overlay can't persist. The progress screen is therefore a **pure function of the
NVS phase record** — on each boot the running image reconstructs "step 4 of 8,
banks 60 %" from persistent state and redraws. This is what makes the screen
come *back* fast after each reboot instead of looking hung. `fwprogress` today
keeps state in a RAM dict; it gains a thin persisted phase record it renders
from.

---

## 7. The warning modal

On tapping Upgrade, before anything happens:

> **This will take a few minutes.** The screen will go dark and restart a couple
> of times — that's normal. **Don't unplug or power off the deck until it says
> it's finished.**  [ Start ]  [ Cancel ]

The "screen will restart" line is load-bearing: without it the two reboots look
like a crash.

---

## 8. Validation policy — integrity stays, semantics go

Per the shrink-the-validation ask: the checks that stay are the ones only the
device can do, and they're cheap.

- **Keep:** per-region sha256 (catches a corrupt download); post-write flash
  read-back verify (catches the thermal-corruption this whole scheme guards
  against); boot self-check (DECKLOG + banks loaded).
- **Drop from the device:** semantic checks (piano sustains, cymbals don't
  drone, bank offset sounds right). Those are *our* pre-release job, verified
  once by us and frozen into the manifest's hashes — not re-derived on every
  user's deck. See [[vacuous-verification]]: a hash the device can't fabricate
  is worth more than a semantic re-check it can fake.

---

## 9. SD layout, preparation, and the picker

**Prepared layout (carved once):** the deck partitions a card into
1. a **user FAT partition** — visible on a computer; holds the user's files and
   any single-file update bundles they drag over; and
2. a **reserved partition (~128 MB)** the deck owns — staged selected upgrade +
   rollback archive. Given a non-FAT type ID so a computer won't auto-mount it,
   which reserves the space against the user filling the card and keeps them
   from touching our images.

Why carve rather than use a directory: a directory can't *reserve* space, and a
user can fill or delete files in it. 128 MB comfortably holds a full rollback
set (~23 MB: app + fonts + user + sys) plus an incoming bundle, with room for a
couple of generations.

**Preparation is destructive** (it repartitions the card), so it sits behind a
hard confirmation. On insert the deck reads the MBR and checks for our reserved
partition + signature; if absent it shows *"SD not prepared for Tulip —
Prepare? (this erases the card)"* and refuses to use an unprepared card for
upgrades.

Caveat (documented, not a bug): **"invisible on a computer" is best-effort.** On
Windows through a card reader the reserved partition typically stays hidden
(Windows mounts only the first partition of removable media). On Linux/macOS it
may still *appear* as an unknown/unmountable volume. We guarantee no computer
will *mount or corrupt* it (non-FAT type), not that it is universally invisible.

**Picker:** on Upgrade, scan the user FAT partition for single-file bundles and
offer **"From internet"** as a separate button. Multiple bundles are listed with
`fw_version`; the user picks exactly one. The chosen bundle is staged into the
reserved partition as the canonical selected upgrade and pinned in NVS; the
flasher acts only on it.

---

## 10. Power-loss test plan

Split by what can actually go wrong:

- **Reboot-recovery cases** (most of §5) are testable *deterministically* with
  an NVS `crash_at` knob that injects a reset at a chosen phase. Fully
  repeatable, no physical yanking — the coordinator runs the whole matrix and
  confirms each lands in the right place. **Build this harness first**, so the
  logic is proven before any plug is pulled.
- **Real power-cut cases** — only a physical yank can half-write a flash *page*.
  Three or four unplugs at the `fonts` / `ota_1` / `vfs` writes, with the
  coordinator watching each recovery. These are the user's to perform.

---

## Decisions (resolved 2026-07-17)

1. **No-SD fallback → flasher-as-recovery-deck (option 1), plus a cheap NVS
   boot-attempt counter in `boot.py`** for Python-level failures. The user plans
   to recommend and always run an SD, so the no-SD path is the rare case.
   **IDF bootloader app-rollback is a documented follow-up, not now.** Effort:
   small code (one `esp_ota_mark_app_valid_cancel_rollback()` in play's
   healthy-boot path after the self-check; the flasher marks itself permanently
   valid), but it **rebuilds the bootloader** — the one component this scheme has
   kept untouched — and once enabled EVERY healthy boot must reach mark-valid or
   a plain power-cycle wrongly rolls back to the flasher, so the real cost is the
   mark-valid placement + a power-cycle validation matrix. Unique benefit:
   catches a C-level panic/hang that never reaches MicroPython (which the NVS
   counter can't). It layers on with no rework, so defer until the core flow is
   proven on hardware.
2. **SD preparation → carve a partition** (§9). Reserve ~128 MB in a
   deck-owned, non-auto-mounting partition; validate on insert and reject an
   unprepared card; prepare is destructive behind a hard confirm. Chosen over the
   directory approach because it actually reserves space and keeps the user out
   of our images.
3. **Small-update fast path → no.** Dropped (a dev convenience, and blocked by
   the full-flash bind anyway). One flasher-mediated path for all updates.
4. **Bundle packaging → single-file (custom-extension zip) on SD, piecemeal
   from the internet** (§3). One file for the user to drag over; per-region
   streaming when we control the download.
