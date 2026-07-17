#!/usr/bin/env python3
"""Re-image the `vfs` (/user) littlefs partition into a smaller one.

The GM rebake grows the `fonts` partition into `vfs` (5.69MB -> 2MB). A littlefs
image is sized to its partition -- block_count is baked in -- so the old 5.69MB
image cannot simply be written into a 2MB slot. This extracts /user out of a
full-flash backup and rebuilds it at the new size, preserving the files.

    # 1. what is in there, and will it fit?
    python tools/gm/migrate_user_vfs.py --backup backup.bin --list

    # 2. pull the files out to a directory you can inspect
    python tools/gm/migrate_user_vfs.py --backup backup.bin --extract userfiles/

    # 3. build the new 2MB image (verifies by re-mounting and diffing)
    python tools/gm/migrate_user_vfs.py --from-dir userfiles/ --out build/user-2mb.bin

Geometry defaults match tulip/fs_create.py (block_size 4096,
disk_version 0x00020000). The OLD offsets/size are the pre-change layout; the
NEW size is the post-change one. Both are overridable.
"""
import argparse
import os
import sys

from littlefs import LittleFS

OLD_VFS_OFFSET = 0xA30000
OLD_VFS_SIZE = 0x5B0000   # 5,963,776 B
NEW_VFS_SIZE = 0x200000   # 2,097,152 B
BLOCK_SIZE = 4096
DISK_VERSION = 0x00020000


def mount_image(data, block_size=BLOCK_SIZE):
    # Do NOT pin disk_version when READING. fs_create.py stamps 0x00020000 at
    # build time, but the running firmware rewrites the superblock, and a live
    # /user pulled off the deck then fails to mount with a pinned version:
    #   disk_version=0x20000 -> LFS_ERR_INVAL;  unpinned -> mounts, 42 entries.
    # Unpinned accepts whatever is actually on disk, which is the only correct
    # thing to do to an image someone else wrote. build() still pins, because
    # there we are the writer and must match fs_create.py.
    fs = LittleFS(block_size=block_size,
                  block_count=len(data) // block_size,
                  mount=False)
    fs.context.buffer = bytearray(data)
    fs.mount()
    return fs


def walk(fs, path="/"):
    out = []
    for name in fs.listdir(path):
        full = (path.rstrip("/") + "/" + name)
        st = fs.stat(full)
        if st.type == 2:  # directory
            out.append((full, None))
            out.extend(walk(fs, full))
        else:
            with fs.open(full, "rb") as f:
                out.append((full, f.read()))
    return out


def read_backup(path, offset, size):
    total = os.path.getsize(path)
    if total < offset + size:
        raise SystemExit("backup %s is %d B; too small for vfs at 0x%X+0x%X "
                         "(is it a full `read_flash 0 0x2000000` image?)"
                         % (path, total, offset, size))
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read(size)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", help="full flash backup (read_flash 0 0x2000000)")
    ap.add_argument("--image", help="a raw vfs image instead of a full backup")
    ap.add_argument("--offset", type=lambda s: int(s, 0), default=OLD_VFS_OFFSET)
    ap.add_argument("--size", type=lambda s: int(s, 0), default=OLD_VFS_SIZE)
    ap.add_argument("--new-size", type=lambda s: int(s, 0), default=NEW_VFS_SIZE)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--extract", metavar="DIR")
    ap.add_argument("--from-dir", metavar="DIR",
                    help="UNSAFE for migration: images whatever is in DIR, "
                         "including a stale/partial extract. Use --migrate.")
    ap.add_argument("--migrate", action="store_true",
                    help="backup -> --out image in one step, verified against "
                         "the backup itself. The safe path.")
    ap.add_argument("--out", metavar="FILE")
    args = ap.parse_args()

    if args.migrate:
        if not (args.backup and args.out):
            raise SystemExit("--migrate needs --backup and --out")
        migrate(args.backup, args.offset, args.size, args.out, args.new_size)
        return

    if args.from_dir:
        if not args.out:
            raise SystemExit("--from-dir needs --out")
        build(args.from_dir, args.out, args.new_size)
        return

    if not (args.backup or args.image):
        raise SystemExit("need --backup or --image (or --from-dir)")
    data = (read_backup(args.backup, args.offset, args.size) if args.backup
            else open(args.image, "rb").read())
    fs = mount_image(data)
    entries = walk(fs)
    files = [(p, b) for p, b in entries if b is not None]
    used = sum(len(b) for _, b in files)
    print("/user: %d files, %d dirs, %d B of data (partition 0x%X = %d B)"
          % (len(files), len(entries) - len(files), used, args.size, args.size))
    print("target partition 0x%X = %d B -> %s (%d B spare)"
          % (args.new_size, args.new_size,
             "FITS" if used < args.new_size else "*** DOES NOT FIT ***",
             args.new_size - used))
    if args.list:
        for p, b in entries:
            print("  %-52s %s" % (p, "<dir>" if b is None else "%d B" % len(b)))
    if args.extract:
        for p, b in entries:
            dst = os.path.join(args.extract, p.lstrip("/"))
            if b is None:
                os.makedirs(dst, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as f:
                    f.write(b)
        print("extracted to %s" % args.extract)


def migrate(backup, offset, size, out, new_size):
    """Backup -> new-size image in ONE step, verified against the SOURCE.

    This exists because the two-step (--extract DIR, then --from-dir DIR) path
    is unsafe by construction: the steps are joined by a directory on disk, so
    a failed/skipped extract leaves --from-dir to image whatever was already
    there. That is not hypothetical -- it imaged a leftover 4-file test fixture
    and reported "round-trip verified", because build()'s round-trip only
    proves the image reads back what it wrote. Fed the wrong input it verifies
    the wrong thing perfectly. Here the source fs is the oracle and never
    leaves memory, so there is nothing to go stale.
    """
    src = mount_image(read_backup(backup, offset, size))
    entries = walk(src)
    files = [(p, b) for p, b in entries if b is not None]
    used = sum(len(b) for _, b in files)
    print("source /user: %d files, %d B (%.2f MB)" % (len(files), used, used / 1048576.0))
    if used >= new_size:
        raise SystemExit("/user holds %d B; will not fit 0x%X" % (used, new_size))

    img = _image_from(entries, new_size)

    # Verify against the SOURCE, not against ourselves.
    back = mount_image(img)
    got = {p: b for p, b in walk(back) if b is not None}
    want = dict(files)
    missing = sorted(set(want) - set(got))
    extra = sorted(set(got) - set(want))
    differs = sorted(p for p in want if p in got and want[p] != got[p])
    if missing or extra or differs:
        raise SystemExit("MIGRATION VERIFY FAILED\n  missing: %s\n  extra: %s\n"
                         "  differs: %s" % (missing[:5], extra[:5], differs[:5]))
    if not want:
        raise SystemExit("source /user is EMPTY -- refusing to write a blank image")
    with open(out, "wb") as f:
        f.write(img)
    print("wrote %s: %d B, all %d files verified byte-for-byte against the backup"
          % (out, new_size, len(want)))


def _image_from(entries, size):
    """Build a littlefs image of `size` from (path, bytes|None) entries."""
    if size % BLOCK_SIZE:
        raise SystemExit("size 0x%X is not a multiple of the 4096 block size" % size)
    # PIN disk_version here: we are the writer, and fs_create.py -- the
    # generator whose images the firmware has always mounted -- pins it.
    fs = LittleFS(block_size=BLOCK_SIZE, block_count=size // BLOCK_SIZE,
                  disk_version=DISK_VERSION)
    for path, data in entries:
        if data is None:
            fs.makedirs(path, exist_ok=True)
    for path, data in entries:
        if data is not None:
            parent = os.path.dirname(path)
            if parent not in ("", "/"):
                fs.makedirs(parent, exist_ok=True)
            with fs.open(path, "wb") as f:
                f.write(data)
    img = bytes(fs.context.buffer)
    assert len(img) == size
    return img


def build(src_dir, out, size):
    if size % BLOCK_SIZE:
        raise SystemExit("size 0x%X is not a multiple of the 4096 block size" % size)
    fs = LittleFS(block_size=BLOCK_SIZE, block_count=size // BLOCK_SIZE,
                  disk_version=DISK_VERSION)
    payload = []
    for root, dirs, names in os.walk(src_dir):
        for d in sorted(dirs):
            rel = os.path.relpath(os.path.join(root, d), src_dir).replace(os.sep, "/")
            fs.makedirs("/" + rel, exist_ok=True)
        for n in sorted(names):
            full = os.path.join(root, n)
            rel = os.path.relpath(full, src_dir).replace(os.sep, "/")
            data = open(full, "rb").read()
            with fs.open("/" + rel, "wb") as f:
                f.write(data)
            payload.append(("/" + rel, data))
    img = bytes(fs.context.buffer)
    assert len(img) == size

    # Verify by re-mounting the image we are about to flash and diffing it.
    back = mount_image(img)
    got = {p: b for p, b in walk(back) if b is not None}
    bad = [p for p, d in payload if got.get(p) != d]
    if bad or len(got) != len(payload):
        raise SystemExit("round-trip verify FAILED for: %s" % (bad[:5] or "file count"))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "wb") as f:
        f.write(img)
    print("wrote %s: %d B, %d files, round-trip verified" % (out, size, len(payload)))


if __name__ == "__main__":
    main()
