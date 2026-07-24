# update.py -- manifest-driven /user update apply engine (deck/UPGRADE.md Phase 1).
#
# This is the LIGHTWEIGHT, /user-only path: apply a bundle of deck-code files
# into /user, atomically and PCM-safely, with per-region sha256 integrity.
# NO firmware, NO fonts, NO SD carving, NO ping-pong, NO otadata -- those are
# later phases. How a bundle ARRIVES (SD / internet) is deferred; the engine
# only sees an abstract local directory path.
#
# A bundle is a directory:
#     bundle_dir/manifest.json
#     bundle_dir/<path>            for each file entry (path is /user-relative)
#
# manifest.json schema (format 1):
#     {
#       "format": 1,                # bundle wire format; engine refuses > ENGINE_FORMAT
#       "min_engine_format": 1,     # oldest engine that may apply this bundle
#       "fw_version": "2026.07.17", # label pinned into the update record
#       "label": "GM rebake ...",   # optional human note
#       "files": [
#         {"path": "deckui.py", "sha256": "<hex>", "size": 1234,
#          "merge": "deck-code"}
#       ]
#     }
#
# Guardrails honoured (UPGRADE.md 3 / 8):
#   * format / min_engine_format guard -- refuse (never half-apply) a bundle a
#     future engine emitted or an older engine can't run.
#   * per-file sha256 -- integrity that only the device can't fabricate. A file
#     whose bytes don't match its manifest hash is NEVER written.
#   * merge:"deck-code" -- deck code (top-level *.py) is overwritten; /var
#     (config, Wi-Fi, logs) is preserved byte-for-byte. The engine therefore
#     REFUSES any entry that targets /var (or escapes /user), guarding against a
#     malformed bundle -- this bakes in the migration lesson.
#
# ABORT-vs-CONTINUE POLICY (recommended in the task, chosen here):
#   VERIFY ALL hashes FIRST; apply only if EVERY file passes. A corrupt bundle
#   therefore writes NOTHING -- /user is never left half-updated. One bad file
#   aborts the whole bundle cleanly (atomic write means no torn file either way).
#
# HOST-TESTABLE: the engine is pure stdlib (json + hashlib + binascii + os) and
# takes an injectable `writer`. The default writer routes through
# deckcfg.fenced_write (atomic write-beside-rename, PCM-safe) and is imported
# LAZILY so this module imports under CPython with no tulip/lvgl/deckcfg.
#
# PROGRESS CALLBACK (drives fwprogress.py's two-tier overlay, UPGRADE.md 6):
#   progress(info) is called with a dict. Keys:
#     stage        'start' | 'verifying' | 'writing' | 'done' | 'error'
#     path         current file's /user-relative path (not on start/done)
#     file_index   1-based index of the current file
#     file_count   total number of files
#     item_done    bytes processed for the CURRENT file (sub bar)
#     item_total   size of the current file (sub bar)
#     overall_done bytes processed across the whole job (main bar, byte-weighted)
#     overall_total total weighted bytes = 2 * sum(size)  (a verify pass + a
#                  write pass; a 40 KB file weighs more than a 400 B file)
#     reason       on 'error': why the bundle was refused / aborted
#   Any callback exception is swallowed -- a UI glitch must never break an update.

import json
import hashlib
import binascii

# The newest bundle `format` this engine understands, and this engine's own
# format level (compared against a bundle's min_engine_format). Bump when the
# manifest/apply semantics change in a way older code can't handle.
ENGINE_FORMAT = 1

MANIFEST_NAME = 'manifest.json'
_CHUNK = 4096
# deck-code lives at the /user root (per deck/deploy.ps1). /var holds user data.
DEFAULT_USER_ROOT = '/user'


# ---------------------------------------------------------------- path helpers

def _join(a, b):
    """Join with '/', no os.path (MicroPython has none). Forward slashes open()
    fine on the host too."""
    if not a:
        return b
    if a[-1] == '/':
        return a + b
    return a + '/' + b


def _safe_relpath(path):
    """Normalise a /user-relative entry path, or raise ValueError.

    Rejects: absolute paths, empty, '.'/'..' traversal, and anything whose
    first component is 'var' (that's user data -- never written by a deck-code
    update; UPGRADE.md 3)."""
    if not path or not isinstance(path, str):
        raise ValueError('empty path')
    p = path.replace('\\', '/')
    if p[0] == '/':
        raise ValueError('absolute path not allowed: %s' % path)
    parts = []
    for seg in p.split('/'):
        if seg == '' or seg == '.':
            continue
        if seg == '..':
            raise ValueError('path escapes /user: %s' % path)
        parts.append(seg)
    if not parts:
        raise ValueError('empty path')
    if parts[0] == 'var':
        raise ValueError('refuses to write under /var: %s' % path)
    return '/'.join(parts)


# ------------------------------------------------------------------- hashing

def _sha256_file(path, on_chunk=None):
    """Stream `path` through sha256. Returns (hexdigest, size). `on_chunk(n)` is
    called with the running byte count after each chunk (sub-bar progress).
    Uses .digest()+hexlify -- MicroPython's hashlib has no .hexdigest()."""
    h = hashlib.sha256()
    size = 0
    f = open(path, 'rb')
    try:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
            if on_chunk is not None:
                on_chunk(size)
    finally:
        f.close()
    return binascii.hexlify(h.digest()).decode(), size


# ------------------------------------------------------------------- progress

def _emit(progress, info):
    if progress is None:
        return
    try:
        progress(info)
    except Exception:
        pass  # a UI error must never abort an in-flight update


# ------------------------------------------------------------------- manifest

def load_manifest(bundle_dir):
    """Read + parse bundle_dir/manifest.json. Raises on missing/invalid JSON."""
    with open(_join(bundle_dir, MANIFEST_NAME), 'r') as f:
        return json.load(f)


def check_format(manifest):
    """Enforce the format / min_engine_format guard (UPGRADE.md 3).
    Returns (ok, reason). reason is a user-facing refusal string when not ok."""
    try:
        fmt = int(manifest.get('format'))
    except (TypeError, ValueError):
        return False, 'bundle has no valid "format"'
    if fmt > ENGINE_FORMAT:
        return (False,
                'bundle format %d is newer than this deck understands '
                '(max %d) -- update the deck over USB first' % (fmt, ENGINE_FORMAT))
    min_eng = manifest.get('min_engine_format', 1)
    try:
        min_eng = int(min_eng)
    except (TypeError, ValueError):
        return False, 'bundle has an invalid "min_engine_format"'
    if min_eng > ENGINE_FORMAT:
        return (False,
                'bundle needs update engine >= %d, this deck is %d '
                '-- update the deck over USB first' % (min_eng, ENGINE_FORMAT))
    return True, None


# -------------------------------------------------------------------- verify

def verify_bundle(bundle_dir, manifest, progress=None, _overall_total=None,
                  _overall_base=0):
    """VERIFY-ALL pass: check every entry's path, size and sha256 WITHOUT
    writing anything. Returns (ok, entries, failures) where `entries` is the
    normalised, verified list (dicts with 'rel', 'src', 'size', 'merge') and
    `failures` is a list of {'path', 'reason'}. ok is True only if failures is
    empty."""
    files = manifest.get('files')
    if not isinstance(files, list):
        return False, [], [{'path': None, 'reason': 'manifest has no "files" list'}]

    if _overall_total is None:
        _overall_total = 2 * _sum_sizes(files)
    count = len(files)
    entries = []
    failures = []
    overall = _overall_base

    for i, ent in enumerate(files):
        try:
            rel = _safe_relpath(ent.get('path'))
        except (ValueError, AttributeError) as e:
            failures.append({'path': ent.get('path') if isinstance(ent, dict)
                             else None, 'reason': str(e)})
            continue
        want = (ent.get('sha256') or '').lower()
        declared = ent.get('size')
        src = _join(bundle_dir, rel)
        base = overall
        _emit(progress, {'stage': 'verifying', 'path': rel,
                         'file_index': i + 1, 'file_count': count,
                         'item_done': 0, 'item_total': declared or 0,
                         'overall_done': base, 'overall_total': _overall_total})
        try:
            got, size = _sha256_file(
                src,
                on_chunk=lambda n, b=base: _emit(progress, {
                    'stage': 'verifying', 'path': rel,
                    'file_index': i + 1, 'file_count': count,
                    'item_done': n, 'item_total': declared or n,
                    'overall_done': b + n, 'overall_total': _overall_total}))
        except OSError as e:
            failures.append({'path': rel, 'reason': 'cannot read: %s' % e})
            continue
        if declared is not None and size != declared:
            failures.append({'path': rel,
                             'reason': 'size %d != manifest %d' % (size, declared)})
            continue
        if not want:
            failures.append({'path': rel, 'reason': 'no sha256 in manifest'})
            continue
        if got != want:
            failures.append({'path': rel,
                             'reason': 'sha256 mismatch (corrupt bundle)'})
            continue
        entries.append({'rel': rel, 'src': src, 'size': size,
                        'merge': ent.get('merge')})
        overall = base + size

    return (len(failures) == 0), entries, failures


def _sum_sizes(files):
    total = 0
    for ent in files:
        if isinstance(ent, dict):
            try:
                total += int(ent.get('size') or 0)
            except (TypeError, ValueError):
                pass
    return total


# --------------------------------------------------------------------- writer

def _default_writer(dest_path, src_path):
    """Device writer: streams `src_path` into `dest_path` in _CHUNK-sized
    read/write pieces -- never holds a whole file in RAM, matching the
    verify path's (_sha256_file) memory safety -- then completes with an
    atomic write-beside-rename, fenced against AMY's PCM rendering via
    deckcfg.fenced_write (do NOT hand-roll a flash-write path). Imported
    lazily so the engine stays host-importable."""
    import os
    import deckcfg

    # make sure the parent dir exists (deck-code sits at /user root, but a
    # nested entry could need a subdir).
    slash = dest_path.rfind('/')
    if slash > 0:
        _mkdirs(dest_path[:slash])

    tmp = dest_path + '.new'

    def do():
        fin = open(src_path, 'rb')
        try:
            fout = open(tmp, 'wb')
            try:
                while True:
                    chunk = fin.read(_CHUNK)
                    if not chunk:
                        break
                    fout.write(chunk)
            finally:
                fout.close()
        finally:
            fin.close()
        try:
            os.rename(tmp, dest_path)         # atomic clobber on littlefs
        except OSError:
            try:
                os.remove(dest_path)          # host: rename won't clobber
            except OSError:
                pass
            os.rename(tmp, dest_path)

    # fenced_write returns False only on the oldest firmware when it's not quiet
    # enough to write; during an update the deck is idle, but retry a bounded
    # number of times then force it rather than silently drop the file.
    for _ in range(40):
        if deckcfg.fenced_write(do):
            return
        try:
            from time import sleep_ms
            sleep_ms(50)
        except Exception:
            break
    do()


def _mkdirs(path):
    import os
    parts = path.split('/')
    cur = ''
    for seg in parts:
        if seg == '':
            cur = '/' if cur == '' else cur
            continue
        cur = seg if cur == '' else (cur + '/' + seg)
        try:
            os.mkdir(cur)
        except OSError:
            pass


# ---------------------------------------------------------------------- apply

def apply_bundle(bundle_dir, progress=None, writer=None,
                 user_root=DEFAULT_USER_ROOT):
    """Apply a bundle from `bundle_dir` into `user_root` (default /user).

    Reads the manifest, enforces the format guard, VERIFIES every file's sha256
    (aborting the WHOLE bundle if any fail -- writes nothing), then writes each
    verified file atomically via `writer(dest_path, src_path)` (default:
    deckcfg.fenced_write, streaming _CHUNK-sized pieces -- this engine never
    reads a whole bundle file into RAM). Never writes under /var.

    Returns a result dict:
        {'ok': bool, 'fw_version': str|None, 'applied': [rel,...],
         'skipped': [{'path','reason'},...], 'failed': [{'path','reason'},...],
         'reason': str|None}
    'reason' carries a bundle-level refusal/abort message; 'ok' is True only
    when every listed file was applied."""
    result = {'ok': False, 'fw_version': None, 'applied': [], 'skipped': [],
              'failed': [], 'reason': None}

    # 1. manifest
    try:
        manifest = load_manifest(bundle_dir)
    except (OSError, ValueError) as e:
        result['reason'] = 'cannot read manifest: %s' % e
        _emit(progress, {'stage': 'error', 'reason': result['reason']})
        return result
    result['fw_version'] = manifest.get('fw_version')

    # 2. format / min_engine_format guard
    ok, reason = check_format(manifest)
    if not ok:
        result['reason'] = reason
        _emit(progress, {'stage': 'error', 'reason': reason})
        return result

    files = manifest.get('files')
    if not isinstance(files, list):
        result['reason'] = 'manifest has no "files" list'
        _emit(progress, {'stage': 'error', 'reason': result['reason']})
        return result

    overall_total = 2 * _sum_sizes(files)
    _emit(progress, {'stage': 'start', 'file_count': len(files),
                     'overall_done': 0, 'overall_total': overall_total})

    # 3. VERIFY ALL FIRST -- one bad hash aborts the whole bundle (nothing written)
    ok, entries, failures = verify_bundle(bundle_dir, manifest, progress=progress,
                                          _overall_total=overall_total)
    if not ok:
        result['failed'] = failures
        result['reason'] = ('bundle failed verification (%d bad file(s)); '
                            'nothing written' % len(failures))
        _emit(progress, {'stage': 'error', 'reason': result['reason']})
        return result

    if writer is None:
        writer = _default_writer

    # 4. APPLY -- every hash already verified; write atomically.
    verified_bytes = _sum_sizes(files)      # bytes consumed by the verify pass
    overall = verified_bytes
    count = len(entries)
    for i, ent in enumerate(entries):
        rel = ent['rel']
        size = ent['size']
        dest = _join(user_root, rel)
        _emit(progress, {'stage': 'writing', 'path': rel,
                         'file_index': i + 1, 'file_count': count,
                         'item_done': 0, 'item_total': size,
                         'overall_done': overall, 'overall_total': overall_total})
        try:
            # Hand the writer the SRC PATH, not the file's bytes: the writer
            # streams src -> dest in _CHUNK-sized pieces (matching
            # _sha256_file's memory safety), so a large bundle file is never
            # held whole in RAM.
            writer(dest, ent['src'])
        except OSError as e:
            # A verified file that won't write now is a real failure. The atomic
            # writer means no half-file was left behind.
            result['failed'].append({'path': rel, 'reason': 'write failed: %s' % e})
            continue
        result['applied'].append(rel)
        overall += size
        _emit(progress, {'stage': 'writing', 'path': rel,
                         'file_index': i + 1, 'file_count': count,
                         'item_done': size, 'item_total': size,
                         'overall_done': overall, 'overall_total': overall_total})

    result['ok'] = (len(result['failed']) == 0)
    if not result['ok'] and result['reason'] is None:
        result['reason'] = 'some files failed to write'
    _emit(progress, {'stage': 'done', 'file_count': count,
                     'overall_done': overall_total,
                     'overall_total': overall_total})
    return result
