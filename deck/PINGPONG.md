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

1. **Compiled binding `tulip.flash_freq()`** — the cleanest single source of
   truth (returns the actual `CONFIG_ESPTOOLPY_FLASHFREQ` the image was built
   with). **Not implemented yet**; adding it is a tiny, guarded C change
   (below). The Python already prefers it automatically if it appears.
2. **Build-stamped constant `flashbuild.FLASH_FREQ`** — a one-line frozen module
   CI writes per artifact (`'80m'` for the flasher, `'120m'`/absent for play).
   **This is the lowest-risk mechanism and the one to ship first**: no C change.
3. **Fallback** — neither present ⇒ `120m` ⇒ play ⇒ never hijacks boot.

### Recommended: ship the stamped constant (option 2)

CI writes, into the frozen `/user` (or frozen modules) of the **80MHz build
only**, a file `deck/flashbuild.py`:

```python
# generated per-build by CI; identifies the running image's flash clock
FLASH_FREQ = "80m"
```

The 120MHz play build ships **no** `flashbuild.py` (absent ⇒ play). That is the
entire mechanism — no C, no partition/bootloader change.

### Optional later: the C binding (option 1, cleanest)

A tiny addition to `tulip/shared/modtulip.c` exposing the compile-time define,
guarded so it is a no-op where undefined:

```c
STATIC mp_obj_t tulip_flash_freq(void) {
#ifdef CONFIG_ESPTOOLPY_FLASHFREQ
    return mp_obj_new_str(CONFIG_ESPTOOLPY_FLASHFREQ, strlen(CONFIG_ESPTOOLPY_FLASHFREQ));
#else
    return mp_const_none;
#endif
}
STATIC MP_DEFINE_CONST_FUN_OBJ_0(tulip_flash_freq_obj, tulip_flash_freq);
// ... add { MP_ROM_QSTR(MP_QSTR_flash_freq), MP_ROM_PTR(&tulip_flash_freq_obj) }
```

`flashmode.flash_freq()` uses it automatically once present; the stamped
constant can then be retired. **This C change is left for the coordinator** — it
needs a firmware rebuild and on-device validation.

## Build: two artifacts

Both builds are byte-identical **except** the flash-frequency / sample-mode
sdkconfig. The play (120MHz) build is unchanged and stays the default.

### The 80MHz flasher build — exact sdkconfig delta

Source of truth is the last file in the N32R8 `SDKCONFIG_DEFAULTS` chain:
`tulip/esp32s3/boards/N32R8/sdkconfig.board`. For the flasher artifact, apply
this delta (the 120M choice lines are mutually exclusive Kconfig choices, so
they must go OFF as the 80M lines go ON):

```diff
-CONFIG_ESPTOOLPY_FLASHFREQ_120M=y
-CONFIG_ESPTOOLPY_FLASHFREQ="120m"
-#CONFIG_ESPTOOLPY_FLASH_SAMPLE_MODE_DTR=y
+CONFIG_ESPTOOLPY_FLASHFREQ_80M=y
+CONFIG_ESPTOOLPY_FLASHFREQ="80m"
+CONFIG_ESPTOOLPY_FLASH_SAMPLE_MODE_DTR=y
```

Notes:
- `80m` is valid with `CONFIG_ESPTOOLPY_OCT_FLASH=y` (already set). Keep
  `CONFIG_ESPTOOLPY_OCT_FLASH=y` and `CONFIG_ESPTOOLPY_FLASHMODE_QIO=y`.
- Pick a sample mode explicitly. Octal 80MHz commonly uses **DTR/DDR**
  (`..._SAMPLE_MODE_DTR`, the line already stubbed at
  `sdkconfig.board:11`). STR is also permissible; DTR is higher-throughput.
  **DTR vs STR must match what the specific octal flash part supports —
  validate on hardware.**
- `CONFIG_IDF_EXPERIMENTAL_FEATURES=y` is only required while any `120M` option
  remains in a build; a pure-80M build does not need it, but leaving it on is
  harmless and keeps the two configs closer.
- **PSRAM speed is an independent axis.** `CONFIG_SPIRAM_SPEED_120M` can be held
  constant, or dropped to `_80M`, deliberately. The flasher only needs the
  *flash* clock lowered for safe writes; decide PSRAM separately and validate.

**Do NOT re-stamp the header of a 120M `.bin` to fake 80MHz** — on the S3 the
clock/timing is baked into the app's compiled MSPI tuning, not the header byte.
You get a mislabeled image, not an 80MHz image. Two real builds are required
(PINGPONG-FINDINGS.md Q5).

### CI change (for the coordinator to wire)

The existing workflow builds one N32R8 artifact. To produce BOTH, add a second
job/matrix-leg that builds N32R8 with the delta above and stamps
`deck/flashbuild.py` with `FLASH_FREQ="80m"` into its frozen payload. Concretely:

1. Duplicate the N32R8 firmware build step with an overlay sdkconfig carrying
   the four changed lines (e.g. a `sdkconfig.flasher` appended to
   `SDKCONFIG_DEFAULTS`, or `idf.py -D` overrides).
2. Before freezing that build, write `deck/flashbuild.py` containing
   `FLASH_FREQ = "80m"`. The play build writes nothing (absent ⇒ play).
3. Publish two artifacts: `...-play-...bin` (120M, default) and
   `...-flasher-...bin` (80M). The play artifact is what
   `flash_pingpong.py` streams; the flasher is flashed to `ota_0` once during
   provisioning.

This is more than a few lines, so it is documented here rather than wired blind.

## NEEDS ON-DEVICE VALIDATION (cannot be verified without hardware)

Everything below is unverified — no device was touched.

1. **The freq switch itself.** That `ota_0` (80M) and `ota_1` (120M) both boot
   cleanly under the one unchanged bootloader, and that 120M reads stay reliable
   on cold boot (IDF issue #9156 shows freq handling has regressed before).
2. **80MHz DTR vs STR** matches the actual octal flash part; that large writes
   at 80MHz are in fact corruption-free where 120MHz was not.
3. **`esp32.NVS`** get/set/erase/commit semantics on this firmware
   (`get_i32` raising on a missing key is assumed; `flashmode.get_flash_pending`
   treats any exception as "not pending").
4. **`Partition.find(TYPE_APP, label=...)` + `.set_boot()`** targeting a named
   slot, and that `finalize_to_play()` actually returns to `ota_1`.
5. **`enter_flash_mode()` interaction with the host serial transport** — that a
   host `^C` (raw-REPL entry) cleanly interrupts the idle loop and that the
   device sits at a REPL the host can drive; and that the 15-min idle
   auto-recovery reboots to play when no host connects.
6. **`is_flasher_build()` wiring** — that CI actually stamps `flashbuild.py`
   (or that the optional `tulip.flash_freq()` binding is added). Until one of
   those exists, every build reports `120m` and flash mode never engages
   (safe, but the feature is inert).
7. **Settings "Safe update" button** — the two-tap arm + reboot on real LVGL,
   and the on-screen flash-mode notice (`fwprogress`) rendering in the 80MHz
   image.
8. **Updating the flasher image itself** is the one genuinely awkward case
   (it would require writing `ota_0` from the 120MHz play image — the risky op
   this scheme avoids). Keep the flasher near-immutable; out of scope here.
