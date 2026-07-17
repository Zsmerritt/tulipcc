# Migration runbook: repartition for the rebaked GM bank

You execute this; nothing here was run against hardware. Read it through once
before starting.

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

```sh
esptool.py -p $PORT -b 921600 read_flash 0 0x2000000 backup-preGM-$(date +%Y%m%d).bin
```

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

Expect roughly "1,056,768 B of data ... target 0x200000 -> FITS". **If it says
DOES NOT FIT, stop** and move samples to the SD card first.

## 3. Extract /user to the host

```sh
python tools/gm/migrate_user_vfs.py --backup backup-preGM-YYYYMMDD.bin --extract userfiles/
```

Look through `userfiles/`. This is exactly what will be written back — delete
anything that belongs on the SD card now (the point of the shrink is that /user
is config-only going forward).

## 4. Build the new 2 MB /user image

```sh
python tools/gm/migrate_user_vfs.py --from-dir userfiles/ --out build/user-2mb.bin
```

It re-mounts the image it just produced and diffs every file back before
writing, and refuses to emit an image that does not round-trip.

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
