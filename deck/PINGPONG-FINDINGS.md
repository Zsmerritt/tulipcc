# Ping-pong dual-flash-frequency scheme — feasibility findings

Board: TULIP4_R11 = ESP32-S3 N32R8, 32 MB octal flash, octal PSRAM,
ESP-IDF v5.4.1, MicroPython firmware. Read-only research; no device access.

---

## VERDICT (one paragraph)

**FEASIBLE — CAVEATED.** On ESP32-S3 the *runtime* flash clock is NOT owned
by the shared 2nd-stage bootloader; it is established by the **application**
that the bootloader hands off to — both because IDF's 2nd-stage bootloader
explicitly reconfigures the SPI flash from *the selected app image's header*
(not its own), and because on the S3 the high-speed (80/120 MHz) MSPI timing
is tuned by the app itself at startup from its own compiled
`CONFIG_ESPTOOLPY_FLASHFREQ`. So two app images living in the two existing OTA
slots CAN legitimately boot at different flash frequencies under one unchanged
bootloader. The catch is not "can it" but "at what cost": (a) the two
frequencies require **two real builds**, not a re-stamped header byte, because
on the S3 the frequency is baked into the app's timing-tuning code, not just
the 1-byte header field; (b) dedicating one of only two OTA slots to a
permanent "80 MHz flash-mode" image sacrifices A/B OTA rollback for the player
image; and (c) Espressif documents 120 MHz on the S3 as *experimental* and
explicitly warns that a >20 °C temperature swing after power-on causes random
flash/PSRAM access crashes — which is exactly the thermal corruption you are
trying to dodge, so dropping to 80 MHz for the write pass is a sound instinct.

---

## Q1 — What sets the runtime flash clock on ESP32-S3? Can two OTA slots boot at different flash frequencies under one shared bootloader?

**ANSWER: YES.** The app image governs the runtime flash frequency, not the
shared bootloader. Two apps in `ota_0` / `ota_1` built at different
`CONFIG_ESPTOOLPY_FLASHFREQ` values boot at their respective frequencies under
one unchanged bootloader.

Boot path traced (IDF v5.4.1):

1. **First-stage (ROM) bootloader** reads the *2nd-stage bootloader's* header
   and uses it to load the 2nd-stage bootloader — at a reduced clock, limited
   flash modes. (For octal flash the ROM relies on the `FLASH_TYPE` eFuse to
   reset the flash to default SPI mode so it can read at all.)
2. **Second-stage bootloader** (`bootloader_start.c` →
   `call_start_cpu0` → `bootloader_init()` configures flash from the
   *bootloader's own* header via `bootloader_flash_config`; then
   `select_partition_number()` → `bootloader_utility_load_boot_image()` selects
   the OTA slot from otadata and loads/validates the app image
   (`esp_image_load`)).
3. Per IDF's own bootloader guide, at hand-off the flash is reconfigured from
   the **app** header, not the bootloader header. Verbatim (ESP32-S3
   bootloader guide, master and v5.x): *"When the second-stage bootloader then
   runs, it will reconfigure the flash using values read from the currently
   selected app binary's header (and NOT from the second-stage bootloader
   header). This allows an OTA update to change the SPI flash settings in
   use."* This is the documented, supported mechanism for exactly this use
   case — an OTA image changing flash settings under a fixed bootloader.
4. **S3-specific reinforcement:** for the high-speed modes you actually use
   (80/120 MHz), the *effective* MSPI clock and its data-sampling timing are
   set by the **application** during startup MSPI timing tuning
   (`spi_flash_init` → MSPI timing-tuning path in `esp_hw_support`), gated by
   the app's compiled `CONFIG_ESPTOOLPY_FLASHFREQ` and
   `CONFIG_ESPTOOLPY_FLASH_SAMPLE_MODE`. The header's 4-bit `spi_speed` field
   is essentially informational for these experimental high-speed modes; the
   binding of "what frequency this image runs at" is compiled into the app.
   Either way the conclusion is identical: **the app, not the shared
   bootloader, determines the runtime flash frequency.**

Honest caveat / historical wrinkle: issue #9156 (IDF v4.3) was a *bug* where a
bootloader/app flash-frequency **mismatch** made the app fail to boot; it was
fixed. It confirms two things: (a) the app header freq really is consulted at
boot, and (b) freq-mismatch handling has been fragile enough to regress
before, so this scheme must be validated on v5.4.1 on real hardware, not
assumed.

## Q2 — If the bootloader governed and the app header were ignored, is there another path to two frequencies?

Not the situation here (Q1 = YES via the app header + app-side tuning), so no
fallback is required. For completeness: a runtime MSPI reconfiguration from
within the running app (re-running timing tuning to switch 120↔80 MHz without
a reboot) is *theoretically* possible but is not a supported/public API, would
have to quiesce all flash/PSRAM access on both cores during the switch, and is
far riskier than the reboot-based two-image approach. The reboot-into-the-
other-slot design is the supported path and should be preferred.

## Q3 — Current partition layout (`boards/N32R8/tulip-partitions-32MB.csv`, selected by N32R8 `mpconfigboard.cmake` → `sdkconfig.board`)

| Name     | Type | SubType   | Offset     | Size       | Bytes / notes |
|----------|------|-----------|------------|------------|---------------|
| nvs      | data | nvs       | 0x9000     | 0x4000     | 16 KB |
| otadata  | data | ota       | 0xd000     | 0x2000     | 8 KB (boot-slot selector) |
| phy_init | data | phy       | 0xf000     | 0x1000     | 4 KB |
| **ota_0**| app  | ota_0     | 0x10000    | 0x390000   | **3,735,552 B ≈ 3.56 MB** |
| **ota_1**| app  | ota_1     | 0x3a0000   | 0x390000   | **3,735,552 B ≈ 3.56 MB** |
| system   | data | fat       | 0x730000   | 0x300000   | 3 MB |
| vfs      | data | fat       | 0xa30000   | 0x5b0000   | 5.94 MB |
| fonts    | data | undefined | 0xfe0000   | 0xc80000   | 12.5 MB (GM SoundFont PCM, mmapped by AMY) |
| drums    | data | undefined | 0x1c60000  | 0x3a0000   | 3.56 MB (Gamma9001 drum PCM, mmapped) |

**Two app OTA slots already exist** (`ota_0`, `ota_1`), each 0x390000 =
3,735,552 bytes ≈ 3.56 MB. A ~3.2 MB (~3,355,443 B) app fits in either slot
with ~380 KB (~10 %) headroom. The device's RUNNING=ota_1 /
get_next_update=ota_0 report is consistent with this standard A/B layout.

## Q4 — How `CONFIG_ESPTOOLPY_FLASHFREQ` is set, and what a valid 80 MHz octal build needs

Set in `tulip/esp32s3/boards/N32R8/sdkconfig.board` (last file in the N32R8
`SDKCONFIG_DEFAULTS` chain, so it wins). Active (120 MHz) block:

```
CONFIG_ESPTOOLPY_FLASHSIZE_32MB=y
CONFIG_SPIRAM_MODE_OCT=y
CONFIG_SPIRAM_SPEED_120M=y
CONFIG_ESPTOOLPY_FLASHFREQ_120M=y
CONFIG_ESPTOOLPY_FLASHFREQ="120m"
#CONFIG_ESPTOOLPY_FLASH_SAMPLE_MODE_DTR=y      <- commented; 120M octal here runs STR
CONFIG_ESPTOOLPY_OCT_FLASH=y
CONFIG_ESPTOOLPY_FLASHMODE_QIO=y
CONFIG_IDF_EXPERIMENTAL_FEATURES=y             <- required: 120M is experimental
```

The 80 MHz variant is already stubbed (commented) in the same file:

```
#CONFIG_SPIRAM_SPEED_80M=y
#CONFIG_ESPTOOLPY_FLASHFREQ_80M=y
#CONFIG_ESPTOOLPY_FLASHFREQ="80m"
```

Validity: **80m IS valid with `CONFIG_ESPTOOLPY_OCT_FLASH=y`.** Octal flash on
the S3 supports 80 MHz and (experimentally) 120 MHz. For a valid 80 MHz octal
build these must change together (they are mutually exclusive Kconfig choices,
so the 120M lines must be turned OFF as the 80M lines are turned ON):

- `CONFIG_ESPTOOLPY_FLASHFREQ_120M` → `CONFIG_ESPTOOLPY_FLASHFREQ_80M=y`
- `CONFIG_ESPTOOLPY_FLASHFREQ="120m"` → `"80m"`
- Pick a sample mode explicitly via `CONFIG_ESPTOOLPY_FLASH_SAMPLE_MODE`:
  octal 80 MHz commonly uses **DTR/DDR** (`CONFIG_ESPTOOLPY_FLASH_SAMPLE_MODE_DTR=y`,
  effective 160 MHz on both clock edges) — the very line that is commented out
  at line 11. STR is also permissible; DTR is the higher-throughput choice and
  is the one the file already hints at. Note: DTR vs STR must match what your
  specific octal flash part supports.
- PSRAM speed (`CONFIG_SPIRAM_SPEED_120M` vs `_80M`) is an independent axis;
  the commented 80M block also drops PSRAM to 80M. Decide deliberately — the
  flash-frequency question is separate from PSRAM, and you can hold PSRAM
  constant if desired. Keep `CONFIG_IDF_EXPERIMENTAL_FEATURES=y` only if any
  120M option remains anywhere in the build; a pure-80M build does not need it.

## Q5 — Can esptool re-stamp the flash-freq header byte on a built .bin (one CI build), or do we need two builds?

**You need two real builds — a header re-stamp is not sufficient on the S3.**

- esptool *can* mechanically change the header flash-mode/freq/size bytes.
  `write-flash` overrides them when writing and **recalculates the appended
  SHA-256 digest** so the image stays valid (per esptool "Firmware Image
  Format" docs). Simply editing the header bytes by hand WITHOUT recomputing
  the digest invalidates the image (`--dont-append-digest` at `elf2image` time
  is the only way to have no digest to invalidate).
- BUT that only rewrites *metadata*. On the ESP32-S3 the actual 80/120 MHz
  clock and its data-sampling timing are produced by the **app's compiled
  MSPI timing-tuning code** (selected by `CONFIG_ESPTOOLPY_FLASHFREQ` /
  `..._SAMPLE_MODE` at build time), not by the runtime interpreting the header
  byte. Flipping the header's `spi_speed` nibble on a 120M-built binary does
  not change the tuning routine that binary runs; you would get a mislabeled
  image, not an 80 MHz image. (Additionally, esptool's freq override is aimed
  at the image written to the boot offset, not arbitrary OTA-slot app images
  streamed in by MicroPython — see Q6; the deck's OTA path never runs esptool
  against the app image anyway.)
- Conclusion: produce **two firmware artifacts** from CI — one 120m, one 80m —
  differing only in the flash-frequency/sample-mode sdkconfig. This is one
  extra build job, not a re-stamp trick.

## Q6 — otadata / boot-slot selection and cross-slot writes from the app (MicroPython `esp32.Partition`)

`deck/flash_ota.py` already exercises the exact APIs; confirmed:

- **(a) read which slot is active/running:** `Partition(Partition.RUNNING)`
  returns the running app partition object. Its `.info()` tuple gives
  `(type, subtype, addr, size, label, encrypted)`; `.info()[3]` is size,
  `[4]` is the label (e.g. `ota_0`/`ota_1`).
- **(b) set_boot to a specific slot:** call `.set_boot()` on the target
  partition object — it maps to `esp_ota_set_boot_partition`, writing otadata
  so that slot boots next. To target a *named* slot rather than "the other
  one", select it with `Partition.find(Partition.TYPE_APP, label="ota_0")[0]`
  then `.set_boot()`.
- **(c) write the OTHER slot while running:** yes.
  `Partition(Partition.RUNNING).get_next_update()` returns the inactive OTA
  partition; `flash_ota.py` writes it live with `ota.writeblocks(blk, buf)`
  and reads it back with `ota.readblocks(blk, vbuf)` (4096-byte sectors),
  per-block write-verify-retry, then `ota.set_boot()`. MicroPython's
  `Partition.writeblocks` writes the flash directly (no explicit
  `esp_ota_begin` call is exposed; the block writes are the OTA write), and
  the ESP bootloader re-validates the image SHA at next boot and rolls back on
  failure. This is the standard "write inactive slot, set_boot, reset" flow
  and is already proven on this device.

---

## Recommended implementation shape (since FEASIBLE)

The physics is on your side; the cost is OTA rollback and a bit of
orchestration. Two shapes, pick per how much you value A/B safety:

**Shape A — dedicated flasher slot (simplest, loses player A/B).**
- `ota_0` (or `ota_1`) permanently holds a small **80 MHz "flash-mode"**
  firmware whose only job is: pull the new image, write the *player* slot at
  80 MHz (thermally safe), verify, `set_boot(player)`, reset.
- The other slot is the **120 MHz "play"** firmware and is the OTA target.
- Update flow: player marks an NVS flag "update pending", `set_boot(flasher)`,
  reset → boots 80 MHz flasher → flasher writes the player slot at 80 MHz →
  `set_boot(player)` → reset back to 120 MHz play. otadata + one NVS flag
  drive the whole ping-pong, exactly as the brief envisioned.
- Cost: you no longer have two *player* copies, so a bad player image can't
  auto-roll-back to a known-good player — but the flasher slot survives and
  can always re-flash, which is arguably a *better* recovery story than plain
  A/B. The genuinely awkward case is updating the flasher image itself (you'd
  have to write the flasher slot from the player at 120 MHz — the risky
  operation you're avoiding; keep the flasher tiny and near-immutable).

**Shape B — same firmware, two frequency variants, true A/B.**
- Both slots hold the *full* firmware; `ota_0` built 120m, `ota_1` built 80m
  (or carry both variants and choose by which you flash where). A boot picks
  frequency by slot. To perform a write, reboot into the 80m slot, do the
  write into whichever slot is being replaced, reboot into the 120m slot for
  play. This keeps a bootable image in each slot but doubles the "which build
  goes where" bookkeeping and still can't write the slot it is currently
  executing from.

Common requirements either way:
1. **Two CI artifacts** (120m + 80m) — see Q5. Keep them byte-identical except
   the flash-freq/sample-mode sdkconfig so behavior matches.
2. **Shared, unchanged bootloader** — do not reflash the bootloader between
   modes; Q1 shows it does not need to change. Keep the bootloader's own
   header at a conservative freq (its header only governs the initial slow
   bootloader read).
3. **Validate the freq-switch on real v5.4.1 hardware** before trusting it —
   issue #9156 shows freq handling has regressed before. Confirm both slots
   boot cleanly and that flash reads at 120 MHz remain reliable on cold boot
   (they should; corruption is write-thermal, per your own field notes).
4. **Do the risky writes at 80 MHz only**, and keep large writes off the
   120 MHz path entirely, consistent with the documented S3 >20 °C
   temperature-drift crash warning.

## Sources
- ESP-IDF Bootloader guide (ESP32-S3), master + v5.x — "the second-stage
  bootloader reconfigures the flash using values from the currently selected
  app binary's header (and NOT the bootloader header)… allows an OTA update to
  change the SPI flash settings in use."
  https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-guides/bootloader.html
  and docs/en/api-guides/bootloader.rst (github.com/espressif/esp-idf).
- ESP-IDF "SPI Flash and External SPI RAM Configuration" (ESP32-S3), v5.x —
  120 MHz experimental (requires CONFIG_IDF_EXPERIMENTAL_FEATURES); octal flash
  requires CONFIG_ESPTOOLPY_OCT_FLASH and the FLASH_TYPE eFuse; sample-mode
  (STR/DTR) selection; ">20 °C temperature change ⇒ random flash/PSRAM access
  crash."
  https://docs.espressif.com/projects/esp-idf/en/v5.3.2/esp32s3/api-guides/flash_psram_config.html
- ESP-IDF issue #9156 — app fails to boot on bootloader/app flash-freq
  mismatch (a bug, since fixed); confirms the app header freq is consulted at
  boot and that freq handling has regressed historically.
  https://github.com/espressif/esp-idf/issues/9156
- esptool "Firmware Image Format" / "Basic Commands" — write-flash overrides
  flash mode/freq/size in the header and recalculates the SHA-256 digest;
  editing the header without recomputing the digest invalidates the image.
  https://docs.espressif.com/projects/esptool/en/latest/esp32s3/advanced-topics/firmware-image-format.html
- Repo: tulip/esp32s3/boards/N32R8/{sdkconfig.board, mpconfigboard.cmake,
  tulip-partitions-32MB.csv}; deck/flash_ota.py (Partition RUNNING /
  get_next_update / writeblocks / readblocks / set_boot).
