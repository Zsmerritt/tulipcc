# Migration runbook: repartition for the rebaked GM bank

Read it through once before starting.

Steps 1-4 have now been executed against the real deck (2026-07-17) and are
corrected below to match what the hardware actually does. Steps 6-7 have not.

## What changes

| partition | offset | size | change |
|---|---|---|---|
| ota_0 / ota_1 | 0x10000 / 0x3a0000 | 0x390000 ea | **unchanged** (app is 86.9% full) |
| system | 0x730000 | 0x300000 | **unchanged** |
| **vfs** (/user) | 0xa30000 | **0x200000** (2 MB) | was 0x5b0000 (5.69 MB) -- **re-imaged** |
| **fonts** | **0xc30000** | **0x1030000** (16.19 MB) | was 0xfe0000 / 0xc80000 -- **moved + grown** |
| drums | 0x1c60000 | 0x3a0000 | **untouched, not reflashed** |

Verified: every boundary is 64 KB aligned, `vfs` ends exactly where `fonts`
starts, `fonts` ends exactly at `drums` (0x1c60000), and `drums` ends exactly at
32 MB. Inside `fonts`: GeneralUser bank at 0 (3,617,870 B), big bank at 0x4B0000
(9,932,684 B, byte-identical), 2,125,940 B spare.

This CSV is shared by **N32R8, TULIP4_R11_DEBUG and TULIP4_R11_FLASHER** — all
three get the new layout.

> **This buys storage, not mmap.** The three banks need 258 of the S3's 256
> 64 KB vaddr pages, so one bank still falls back to a PSRAM copy regardless of
> layout. That is expected and is why the log line in step 7 is informational.

## The point of no return

**Writing `fonts` at 0xc30000 (step 6) destroys the old /user.** The old vfs
occupied 0xa30000–0xfe0000, so the new `fonts` region starts *inside* it. Once
step 6 begins, the only way back to your old /user is the backup from step 1.

Steps 1–4 are read-only / host-side and safe to abort at any time.

## 0. Prerequisites

```sh
pip install sf2utils resampy numpy audioop-lts littlefs-python
```

Have the device in download mode on its usual port. Substitute `$PORT`.
Use the same esptool flags your build uses (see `build/flash_args`); the N32R8 is
32 MB OPI flash.

## 1. Full backup — do this first, no exceptions

**A single 32 MB read does not work.** It dies around 7.8 MB with `Corrupt data,
expected 0x1000 bytes but received 0xf18` -- reproduced at both 921600 and
460800, so it is not a baud problem. Read it in 4 MB chunks and concatenate;
8/8 chunks come back clean:

```sh
for i in $(seq 0 7); do
  off=$(( i * 0x400000 ))
  esptool.py -p $PORT -b 921600 read_flash $off 0x400000 chunk$i.bin || echo "CHUNK $i FAILED"
done
cat chunk?.bin > backup-preGM-$(date +%Y%m%d).bin
```

Check each chunk's exit code and size (4194304 B) as it lands. Do not pipe
esptool into `tail`/`head` -- you get the pager's exit code, not esptool's, and
a backup that died at 23% reports success.

Verify it before trusting it — a short read here is how you lose /user:

```sh
python - <<'EOF'
import os
p = "backup-preGM-YYYYMMDD.bin"   # <- your filename
assert os.path.getsize(p) == 0x2000000, "NOT a full 32MB image -- redo the backup"
d = open(p,'rb').read()
print("size OK. vfs region non-blank:", any(b != 0xFF for b in d[0xA30000:0xA40000]))
print("drums region non-blank      :", any(b != 0xFF for b in d[0x1C60000:0x1C70000]))
EOF
```

Keep this file. It is your entire rollback.

## 2. Inspect /user and confirm it fits 2 MB

```sh
python tools/gm/migrate_user_vfs.py --backup backup-preGM-YYYYMMDD.bin --list
```

On the deck as of 2026-07-17 this reports **84 files, 827,108 B (0.79 MB) ->
FITS**, with 2.5x headroom. **If it says DOES NOT FIT, stop** and move samples to
the SD card first.

## 3-4. Build the new 2 MB /user image

```sh
python tools/gm/migrate_user_vfs.py --migrate     --backup backup-preGM-YYYYMMDD.bin --out build/user-2mb.bin
```

One step, on purpose. It mounts /user out of the backup, rebuilds it at 2 MB,
and diffs every file back **against the backup** before writing -- the source is
the oracle and never touches the disk in between.

Do **not** migrate via `--extract DIR` + `--from-dir DIR`. Those steps are joined
by a directory, so a failed extract leaves `--from-dir` imaging whatever was
already in it -- which is exactly what happened here: it imaged a leftover 4-file
test fixture and printed *"round-trip verified"*, because that check only proves
the image reads back what it wrote. It would have replaced 84 real files with 4
toys. `--from-dir` is for building an image from scratch, not for migrating.

If you want to eyeball or prune the files first, `--extract userfiles/` to look,
then still run `--migrate` to build (or prune and use `--from-dir` knowingly).

## 4b. Re-deploy the deck code into that image -- DO NOT SKIP

The backup's /user holds the deck code **as it was**, and the rebake changes
`gm.py` in lockstep with the bank. Restore /user alone and you get the NEW banks
paired with the OLD metadata: `gm.PRESET_LOOPED[gm.PROGRAM_PRESET[0]]` reads 0,
Grand Piano still dies at ~1.0 s, and it looks exactly like the rebake failed.
It did not -- the table just describes the previous bank.

This is easy to miss because **`gm.py` is the same 7414 bytes before and after**
(the rebake rewrites a fixed-length table in place). Only a hash catches it.

The rule, matching `deck/deploy.ps1`: top-level `deck/*.py` EXCLUDING `test_*.py`
(host pytest files -- they slow the Apps discovery scan) is CODE and takes the
repo version. Everything else on the device -- notably `/var` (config, wifi,
logs) -- is the user's and must be preserved byte-for-byte. Subdirectories of
`deck/` (`amyboard_companion/`, `device-backup/`) are NOT device code; deploy.ps1
does not recurse.

Mind the dependencies: the updated `amyparams.py` imports `patchparams`, which
may not exist in the old /user. Ship the update without it and the param editor
dies on import. Take the whole `deck/*.py` set, not just the files that differ.

Verify before flashing: every `deck/*.py` hash-equal on the device, `/var/*`
byte-identical to the backup, and no `test_*` in the image.

## 5. Build the firmware images

Build as usual. `fs_create.py` regenerates `build/<distro>-fonts.bin` from the
rebaked `amy/sounds/gm/fonts.bin`, and hard-fails if the bank overruns
`GM_BIG_OFFSET` — so a half-applied change breaks the build, not the sound.

Sanity-check the artefacts before flashing:

```sh
python - <<'EOF'
import os
f = "build/tulip-fonts.bin"      # <- your distro name
print("fonts image:", os.path.getsize(f), "B (expect 14,847,884)")
print("fits 0x1030000:", os.path.getsize(f) <= 0x1030000)
print("user image :", os.path.getsize("build/user-2mb.bin"), "B (expect 2,097,152)")
EOF
```

Note `build/<distro>-user.bin` produced by the build is a **fresh, empty**
filesystem — do **not** flash it, it would discard your files. Flash
`build/user-2mb.bin` from step 4 instead.

## 6. Flash — point of no return starts here

Order matters: partition table first so the new geometry is authoritative, then
the two data regions. Do these in one session; do not reboot in between.

```sh
# 6a. new partition table
esptool.py -p $PORT -b 921600 write_flash 0x8000 build/partition_table/partition-table.bin

# 6b. re-imaged /user (2MB)
esptool.py -p $PORT -b 921600 write_flash 0xa30000 build/user-2mb.bin

# 6c. the GM banks (14.85MB; this is the long one, several minutes)
esptool.py -p $PORT -b 921600 write_flash 0xc30000 build/tulip-fonts.bin
```

If the app binary also changed, flash `ota_0` as usual — its offset did not move.

**Do not** flash `drums`. Its contents and geometry are unchanged; writing it
only risks the one region this migration is guaranteed not to touch.

## 7. Verify on the device

0. Confirm the metadata matches the bank before trusting your ears -- this is
   the cheap check that would have caught the step-4b mistake:
   `gm.PRESET_LOOPED[gm.PROGRAM_PRESET[0]]` must be **1** (Grand Piano loops)
   while program 104 (Sitar) and 13 (Xylophone) stay **0**. If program 0 reads
   0, /user still has the old `gm.py` -- go back to 4b; do not touch the bank.
1. It boots and /user has your files (`os.listdir('/user')`).
2. Console at boot: a `gm: ... vaddr pool full -> loaded N bytes into PSRAM` line
   for exactly one bank is **expected and correct** (258 pages needed, 256
   available). A `mmap failed ... and PSRAM alloc failed; bank unavailable` line
   is **not** — that means the bank is silently gone.
3. **The headline test: hold a Grand Piano note (GM program 0) for 5+ seconds.**
   It must sustain, not stop dead at ~1.0 s. It should now loop indefinitely
   while held and release when you let go.
4. Play Violin (40), Brass Section (61), Timpani (47), Shakuhachi (77),
   Applause (126): all should sustain while held.
5. Play Sitar (104): should ring ~2.0 s (was 0.87 s) then stop — it is a
   one-shot by design, it must **not** loop.
6. **Regression check the drums**: crash/ride/splash cymbals, toms, hi-hat open.
   These were deliberately left alone and must sound exactly as before — in
   particular they must **not** drone or machine-gun.
7. Anything using the big bank (the 2350-preset GM set) must still sound right —
   that verifies `GM_BIG_BYTE_OFFSET` matches where `fs_create.py` put it. If the
   big bank plays garbage or noise, the offset is mismatched: reflash, do not
   "tune" it.

## Rollback

Full restore, any time:

```sh
esptool.py -p $PORT -b 921600 write_flash 0x0 backup-preGM-YYYYMMDD.bin
```

This rewrites the old partition table, the old 5.69 MB /user, the old fonts and
drums — i.e. the exact pre-migration device. Partial rollback is not supported:
the old table and the new `fonts` contents are mutually inconsistent, so restore
everything or nothing.

If you only want to undo the *sound* change but keep the layout, revert
`amy/sounds/gm/fonts.bin`, `amy/src/pcm_gm.h`, `deck/gm.py` and `amy/amy/gm.py`,
rebuild, and redo step 6c — the old 3.13 MB bank fits the 0x4B0000 slice fine.
