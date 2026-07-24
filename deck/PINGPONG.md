# Ping-pong dual-frequency flash update (Shape A)

Reboot-based scheme that writes big firmware images at a **thermally safe 80MHz
flash clock**, then returns to the normal **120MHz "play"** firmware. It exists
because this board's 120MHz octal flash corrupts sustained multi-MB writes under
thermal drift (Espressif documents a >20 C post-power-on swing causing random
flash/PSRAM access crashes). See `deck/PINGPONG-FINDINGS.md` for the feasibility
research this builds on.

**No partition-table change, no bootloader change, no device touched to ship
this.** Two OTA app slots already exist (`ota_0`, `ota_1`, ~3.56MB each).

## Slot convention (Shape A)

| Slot    | Build            | Role                                              |
|---------|------------------|---------------------------------------------------|
| `ota_0` | **80MHz flasher**| recovery anchor; runs "flash mode"; writes `ota_1`|
| `ota_1` | **120MHz play**  | the normal deck; the OTA target                   |

Encoded once in `deck/flashmode.py` (`FLASHER_LABEL` / `PLAY_LABEL`). Slots are
always identified **by label**, never as "the other slot".

## Update flow

```
play (120MHz)   flashmode.request_update():  NVS flash_pending=1,
                                             set_boot(ota_0 flasher), reset
      │
      ▼
flasher(80MHz)  boot.py -> flashmode.should_enter_flash_mode() -> enter_flash_mode():
                  Wi-Fi up from saved creds; idle for the host (NO deck UI)
      │         host (flash_pingpong.py) streams new play image into ota_1 at
      │         80MHz with the proven write-verify-retry pull
      ▼
                flashmode.finalize_to_play(): flash_pending cleared,
                                              set_boot(ota_1), reset
      │
      ▼
play (120MHz)   normal deck
```

The host tool `deck/flash_pingpong.py` orchestrates all of this over the same
no-reset serial transport + temporary HTTP server as `flash_ota.py` (shared via
`deck/flashlib.py`). Flash mode auto-recovers to the (untouched) play slot if no
host shows up within `FLASH_MODE_IDLE_TIMEOUT_S` (15 min), so a stray flag or a
Settings mis-tap can never strand the deck.

Run it:

```
python deck/flash_pingpong.py path/to/new-play-TULIP4_R11.bin --port COM11
```

## `is_flasher_build()` mechanism

A running image must know whether IT is the 80MHz flasher or the 120MHz play
build **without** relying on which slot it booted from (during bring-up a play
image may be written to either slot, so slot != build identity).

`flashmode.flash_freq()` resolves it in priority order, failing soft to the play
default (`120m`) so an unknown/garbled marker can never trap the deck in flash
mode:

1. **Compiled binding `tulip.flash_freq()`** — **this is now the mechanism.** It
   returns the compile-time frequency **choice**
   (`CONFIG_ESPTOOLPY_FLASHFREQ_120M` ⇒ `'120m'`, `_80M` ⇒ `'80m'`), so build
   identity comes straight from the build itself: nothing to stamp, nothing to
   keep in sync, and the two artifacts differ *only* by their board config.
   **It must NOT read the `CONFIG_ESPTOOLPY_FLASHFREQ` string** — that is the
   image-header *boot* frequency, which the IDF caps at `"80m"` even for 120MHz
   builds (`esptool_py/Kconfig.projbuild`: `default '80m' if
   ESPTOOLPY_FLASHFREQ_120M`; the runtime clock is raised to 120MHz by MSPI
   timing tuning after boot). The first version of the binding read the string
   and therefore reported `'80m'` on **both** images — falsified on hardware
   and in the resolved `build/sdkconfig`, 2026-07-16.
2. **Build-stamped constant `flashbuild.FLASH_FREQ`** — a frozen one-liner
   (`FLASH_FREQ = "80m"`). **Superseded by (1) and NOT produced by CI.** The
   lookup is kept in `flashmode.py` purely as a fail-soft escape hatch (e.g. an
   old image predating the binding, or a hand-stamped bring-up build).
3. **Fallback** — neither present ⇒ `120m` ⇒ play ⇒ never hijacks boot.

### The C binding (shipped)

`tulip/shared/modtulip.c` defines `tulip_flash_freq` next to `tulip_board`, and
registers `flash_freq` in `tulip_module_globals_table`. It reports from the
frequency-choice symbols (`CONFIG_ESPTOOLPY_FLASHFREQ_120M` / `_80M`), with the
header string only as a fallback for unusual frequencies; on desktop/web —
where none of the defines exist (`sdkconfig.h` is included only under
`ESP_PLATFORM`) — it compiles fine and returns `None`;
`flashmode.flash_freq()` then falls through to the `120m` default, i.e.
off-device is always "play". `tulip.py` does `from _tulip import *`, so no
Python-side plumbing was needed.

So on the two firmware artifacts:

| Image                          | `tulip.flash_freq()` | `is_flasher_build()` |
|--------------------------------|----------------------|----------------------|
| `TULIP4_R11` (play, 120MHz)    | `'120m'`             | False                |
| `TULIP4_R11_FLASHER` (flasher) | `'80m'`              | True                 |
| desktop / web                  | `None` ⇒ `'120m'`    | False                |

## Build: two artifacts

The flasher is a **new board, `TULIP4_R11_FLASHER`**, whose whole content is the
80MHz delta on top of the play config. Both boards share the same partition
table (`boards/N32R8/tulip-partitions-32MB.csv`), so the two images are
OTA-swappable by construction.

| Role    | `MICROPY_BOARD`      | sdkconfig chain                                  |
|---------|----------------------|--------------------------------------------------|
| play    | `TULIP4_R11`         | base, 240mhz, sdkconfig.tulip, **N32R8**, TULIP4_R11 — **unchanged, do not touch** |
| flasher | `TULIP4_R11_FLASHER` | base, 240mhz, sdkconfig.tulip, **N32R8**, TULIP4_R11_FLASHER |

The two chains are **identical but for the last entry**, and
`boards/TULIP4_R11_FLASHER/mpconfigboard.cmake` is otherwise a byte-for-byte
copy of the play board's (same `BOARD_DEFINITION1 TULIP4_R11` — same hardware —
same `MICROPY_SOURCE_BOARD` list); `mpconfigboard.h` and `pins.csv` are exact
copies. Everything the flasher is comes from `N32R8/sdkconfig.board`, i.e. from
play, and then:

```
CONFIG_SPIRAM_SPEED_120M=n
CONFIG_SPIRAM_SPEED_80M=y
CONFIG_ESPTOOLPY_FLASHFREQ_120M=n
CONFIG_ESPTOOLPY_FLASHFREQ_80M=y
CONFIG_ESPTOOLPY_FLASHFREQ="80m"
```

That is the entire file. The `=n` lines are load-bearing: frequency and PSRAM
speed are mutually exclusive Kconfig choices, so N32R8's 120M selections have to
go OFF as the 80M ones come on.

Why this matters: **the flasher is the recovery anchor.** It sits in `ota_0`
permanently, and if it ever fails to boot there is no way back. So it must
differ from play by the flash clock and *nothing else* — every extra delta is
unproven risk in the one image that may never fail.

Two deliberate properties, neither of them free choices (both still on the
validation list at the bottom):

- **Sample mode is inherited, not set.** `TULIP4_R11_FLASHER/sdkconfig.board`
  does not mention `CONFIG_ESPTOOLPY_FLASH_SAMPLE_MODE_DTR`, so it stays
  commented out exactly as N32R8 leaves it and both images run octal **STR**.
  Inheriting play's setting is the point. DTR is the higher-throughput octal-80M
  mode and would be the next knob if STR@80M disappoints — but it would be a
  second delta, so it needs a hardware reason first.
- **PSRAM drops with the flash clock.** On the S3 flash and PSRAM share the MSPI
  clock domain, and both configs that exist in this tree set the pair together
  (N32R8: 120/120; the unrelated `TULIP4_R11_DEBUG` board: 80/80). An 80/120
  split is untried on this hardware, so we follow the established pairing rather
  than invent a third combination in the recovery anchor.

**Note: `TULIP4_R11_DEBUG` is NOT the flasher**, despite looking like one (it is
80MHz on the same partition table). Its cmake chain *skips*
`boards/N32R8/sdkconfig.board` entirely — losing e.g.
`CONFIG_ESP_MAIN_TASK_STACK_SIZE=16384` — and adds `sdkconfig.usb`, which the
play board never includes and which can move USB/console routing. It is
therefore not play-minus-clock, and it is someone else's debug board. Left
alone.

**Do NOT re-stamp the header of a 120M `.bin` to fake 80MHz** — on the S3 the
clock/timing is baked into the app's compiled MSPI tuning, not the header byte.
You get a mislabeled image, not an 80MHz image. Two real builds are required
(PINGPONG-FINDINGS.md Q5).

### CI (wired)

`.github/workflows/amyboard-pr-preview.yml` builds all three targets in the ONE
esp-idf container (so ccache carries the shared translation units), adding a
third leg after the existing AMYBOARD and TULIP4_R11 legs:

```
idf.py -B build-TULIP4_R11_FLASHER -DMICROPY_BOARD=TULIP4_R11_FLASHER build
cp build-TULIP4_R11_FLASHER/micropython.bin dist/tulip-flasher-TULIP4_R11_FLASHER.bin
```

Notes on that leg:

- **It uses its own build dir.** `esp32s3/CMakeLists.txt` puts the resolved
  `sdkconfig` inside the build dir, and an existing `sdkconfig` beats
  `SDKCONFIG_DEFAULTS` — so reusing `build/` after the TULIP4_R11 leg would
  silently carry 120MHz settings into the "80MHz" image. That is precisely the
  mislabeled-image failure above, except harder to spot.
- **No `fs_create.py` pass.** Only the flasher's *app* image is ever written
  (into an OTA slot); it never gets a full/vfs/sys image of its own, and it runs
  on the play image's filesystem. So the leg publishes `micropython.bin` and
  nothing else.
- **Nothing is stamped.** `tulip.flash_freq()` makes `flashbuild.py` unnecessary.

Artifacts (bundle name `tulip-firmware`, unchanged):

| File in the artifact                  | What it is                                    |
|---------------------------------------|-----------------------------------------------|
| `tulip-firmware-TULIP4_R11.bin`       | play app image — what `flash_pingpong.py` streams into `ota_1` |
| `tulip-full-TULIP4_R11.bin`           | play full-flash image (unchanged)             |
| `tulip-sys.bin`                       | system filesystem image (unchanged)           |
| `tulip-flasher-TULIP4_R11_FLASHER.bin`| **80MHz flasher app image** — flashed to `ota_0` once, at provisioning |

The three play files keep their exact names: HW CI and `tulip.upgrade(pr=N)`
depend on them.

## NEEDS ON-DEVICE VALIDATION (cannot be verified without hardware)

Everything below is unverified — no device was touched. The scheme is now fully
*wired* (binding + CI + host tool), which means it is now capable of engaging;
none of it is *proven*.

1. **The freq switch itself.** That `ota_0` (80M) and `ota_1` (120M) both boot
   cleanly under the one unchanged bootloader, and that 120M reads stay reliable
   on cold boot (IDF issue #9156 shows freq handling has regressed before).
2. **80MHz STR.** `TULIP4_R11_FLASHER` inherits N32R8's commented-out
   `CONFIG_ESPTOOLPY_FLASH_SAMPLE_MODE_DTR`, so the flasher is octal 80MHz
   **STR** — a deliberate inherit (don't differ from play), not a tested choice.
   Whether STR matches this octal part, and whether large writes at 80MHz are in
   fact corruption-free where 120MHz was not, is provable only on hardware. If
   STR@80M disappoints, DTR is the next knob — one line in
   `boards/TULIP4_R11_FLASHER/sdkconfig.board` — but it would make the flasher
   differ from play on a second axis, so it wants a hardware reason first.
3. **PSRAM at 80MHz in the flasher.** The flasher runs `CONFIG_SPIRAM_SPEED_80M`
   where play runs `_120M`. This rides along with the flash clock because the S3
   shares the MSPI clock domain between the two and every config in this tree
   pairs them (120/120 or 80/80); an 80/120 split is untried. Expected to be a
   non-issue (flash mode does no audio/graphics work), but it is still a second
   changed knob, and only hardware confirms the flasher boots and runs its idle
   loop with it.
4. **`esp32.NVS`** get/set/erase/commit semantics on this firmware
   (`get_i32` raising on a missing key is assumed; `flashmode.get_flash_pending`
   treats any exception as "not pending").
5. **`Partition.find(TYPE_APP, label=...)` + `.set_boot()`** targeting a named
   slot, and that `finalize_to_play()` actually returns to `ota_1`.
6. **`enter_flash_mode()` interaction with the host serial transport** — that a
   host `^C` (raw-REPL entry) cleanly interrupts the idle loop and that the
   device sits at a REPL the host can drive; and that the 15-min idle
   auto-recovery reboots to play when no host connects.
7. **`is_flasher_build()` wiring end-to-end.** The binding compiles from a define
   nobody has read back on-device: confirm `tulip.flash_freq()` returns exactly
   `'80m'` on the `TULIP4_R11_FLASHER` image and `'120m'` on the play image (the
   `startswith('80')` test in `is_flasher_build()` assumes that spelling). If it
   returned something unexpected, the deck reports play — safe, but the feature
   goes inert rather than loud.
8. **Settings "Safe update" button** — the two-tap arm + reboot on real LVGL,
   and the on-screen flash-mode notice (`fwprogress`) rendering in the 80MHz
   image.
9. **Updating the flasher image itself** is the one genuinely awkward case
   (it would require writing `ota_0` from the 120MHz play image — the risky op
   this scheme avoids). Keep the flasher near-immutable; out of scope here.
