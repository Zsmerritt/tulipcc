#!/usr/bin/env python3
# make_update_bundle.py -- build a /user update bundle for deck/update.py.
#
# Produces a bundle DIRECTORY that deck/update.apply_bundle() can apply:
#     <out>/manifest.json
#     <out>/<path>            one copy of each file, at its /user-relative path
#
# Deterministic and re-runnable: given the same inputs it writes byte-identical
# output (files sorted by path, manifest JSON sorted + indented). This is the
# Phase-1 (/user-only) bundle maker -- no firmware/fonts/SD, no single-file zip
# container yet (UPGRADE.md Phase 1). Every entry is tagged merge:"deck-code":
# deck code overwrites, /var is preserved on-device.
#
# USAGE
#   Explicit files (dest defaults to basename, matching deck/deploy.ps1 which
#   copies each top-level deck/*.py to /user/<name>):
#     python tools/make_update_bundle.py --out build/bundle \
#         --fw-version 2026.07.17 deck/deckui.py deck/update.py
#
#   Remap a file to an explicit /user-relative path with DEST=SRC:
#     python tools/make_update_bundle.py --out build/bundle \
#         --fw-version 2026.07.17 sub/foo.py=deck/foo.py
#
#   From a git ref/range (changed top-level deck/*.py, excluding test_*):
#     python tools/make_update_bundle.py --out build/bundle \
#         --fw-version 2026.07.17 --git-range main..HEAD
#
# The bundle SOURCE is an abstract local directory; how it later reaches the
# deck (SD / internet) is deferred (UPGRADE.md Phase 1).

import argparse
import hashlib
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_DECK = os.path.join(os.path.dirname(_HERE), 'deck')
if _DECK not in sys.path:
    sys.path.insert(0, _DECK)

# reuse the engine's format level + path rules so maker and applier never drift.
import update as _engine  # noqa: E402


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest(), os.path.getsize(path)


def _parse_item(item):
    """'DEST=SRC' -> (dest, src); 'SRC' -> (basename(SRC), SRC)."""
    if '=' in item:
        dest, src = item.split('=', 1)
        dest = dest.strip()
    else:
        src = item
        dest = os.path.basename(item)
    src = os.path.normpath(src)
    # validate the /user-relative dest through the same guard the engine uses.
    dest = _engine._safe_relpath(dest.replace(os.sep, '/'))
    return dest, src


def _git_range_files(git_range, repo_root):
    """Top-level deck/*.py changed across git_range, excluding test_*.py."""
    out = subprocess.check_output(
        ['git', 'diff', '--name-only', '--diff-filter=d', git_range,
         '--', 'deck/*.py'],
        cwd=repo_root).decode()
    items = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        # top-level deck/ only (deploy.ps1 copies just the deck root), no tests
        rel = line[len('deck/'):]
        if '/' in rel or rel.startswith('test_'):
            continue
        items.append(os.path.join(repo_root, line))
    return items


def build_bundle(items, out_dir, fw_version, label=None, git_range=None,
                 repo_root=None):
    """Core builder. `items` is a list of 'SRC'/'DEST=SRC' strings; if
    git_range is given its changed deck files are appended. Returns the
    manifest dict."""
    repo_root = repo_root or os.path.dirname(_HERE)
    pairs = [_parse_item(it) for it in items]
    if git_range:
        for src in _git_range_files(git_range, repo_root):
            pairs.append((os.path.basename(src), os.path.normpath(src)))

    if not pairs:
        raise SystemExit('make_update_bundle: no input files')

    # de-dup by dest (last wins) then sort for determinism.
    by_dest = {}
    for dest, src in pairs:
        by_dest[dest] = src
    dests = sorted(by_dest)

    os.makedirs(out_dir, exist_ok=True)
    entries = []
    for dest in dests:
        src = by_dest[dest]
        if not os.path.isfile(src):
            raise SystemExit('make_update_bundle: not a file: %s' % src)
        sha, size = _sha256(src)
        # lay the file into the bundle at its /user-relative path
        out_path = os.path.join(out_dir, dest.replace('/', os.sep))
        parent = os.path.dirname(out_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(src, 'rb') as rf, open(out_path, 'wb') as wf:
            wf.write(rf.read())
        entries.append({'path': dest, 'sha256': sha, 'size': size,
                        'merge': 'deck-code'})

    manifest = {
        'format': _engine.ENGINE_FORMAT,
        'min_engine_format': 1,
        'fw_version': fw_version,
        'files': entries,
    }
    if label:
        manifest['label'] = label

    with open(os.path.join(out_dir, _engine.MANIFEST_NAME), 'w') as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write('\n')
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description='Build a /user update bundle.')
    ap.add_argument('--out', required=True, help='output bundle directory')
    ap.add_argument('--fw-version', required=True,
                    help='version/label pinned into the manifest')
    ap.add_argument('--label', default=None, help='optional human note')
    ap.add_argument('--git-range', default=None,
                    help='git ref/range; adds changed top-level deck/*.py')
    ap.add_argument('files', nargs='*',
                    help="SRC or DEST=SRC (dest is /user-relative)")
    args = ap.parse_args(argv)

    manifest = build_bundle(args.files, args.out, args.fw_version,
                            label=args.label, git_range=args.git_range)
    print('bundle: %s  (%d file(s), fw_version=%s)'
          % (args.out, len(manifest['files']), manifest['fw_version']))
    for ent in manifest['files']:
        print('  %-24s %8d  %s' % (ent['path'], ent['size'], ent['sha256'][:12]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
