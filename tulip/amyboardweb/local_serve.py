#!/usr/bin/env python3
"""
local_serve.py — serve AMYboard Web locally WITHOUT an emscripten toolchain.

Like dev.py, but instead of compiling AMY + MicroPython to WASM (make web /
make), it vendors the already-built WASM/JS blobs from the live amyboard.com
editor. Use this on machines with no emsdk (e.g. Windows) when you mainly
need the site + firmware flasher (e.g. to flash a fork-built firmware, see
FIRMWARE_BASES in static/esptool/js/script.js). The bundled editor/simulator
runs the *live site's* AMY build, not this checkout's amy/ submodule.

Run from tulip/amyboardweb/:
    python3 local_serve.py           # build stage/ + serve on :8000
    python3 local_serve.py --build-only
"""

import os
import re
import shutil
import ssl
import sys
from urllib.request import Request, urlopen

import dev  # reuse paths + HTTP handler from dev.py

LIVE_BASE = "https://amyboard.com"

UA = {"User-Agent": "amyboard-local-serve"}


def fetch(url, binary=True):
    ctx = ssl.create_default_context()
    with urlopen(Request(url, headers=UA), context=ctx) as resp:
        data = resp.read()
    return data if binary else data.decode("utf-8", errors="replace")


def build_stage():
    print("[local] Building stage/ (no emscripten; vendoring live WASM) ...")
    if os.path.exists(dev.STAGE_DIR):
        # Best-effort clean; OneDrive/Windows can hold locks, and overwriting
        # in place (dirs_exist_ok below) is fine for a local server.
        try:
            shutil.rmtree(dev.STAGE_DIR)
        except OSError as exc:
            print(f"[local]   stage/ clean incomplete ({exc}); overwriting in place.")
    os.makedirs(dev.STAGE_DIR, exist_ok=True)

    # static files + shared www assets, same as dev.py full_build()
    shutil.copytree(dev.STATIC_DIR, dev.STAGE_DIR, dirs_exist_ok=True)
    for src, name in [(dev.WWW_IMG, "img"), (dev.WWW_FONTS, "fonts"),
                      (dev.WWW_CSS, "css"), (dev.WWW_JS, "js")]:
        if os.path.exists(src):
            shutil.copytree(src, os.path.join(dev.STAGE_DIR, name), dirs_exist_ok=True)
    if os.path.exists(dev.API_DIR):
        shutil.copytree(dev.API_DIR, os.path.join(dev.STAGE_DIR, "api"), dirs_exist_ok=True)

    # Discover the live editor's timestamped blob names.
    live_html = fetch(f"{LIVE_BASE}/editor/", binary=False)
    m_mjs = re.search(r"amyboard-(\d+)\.mjs", live_html)
    m_js = re.search(r"amy-(\d+)\.js", live_html)
    if not (m_mjs and m_js):
        print("[local] WARNING: could not find live WASM names; editor page will not run "
              "(the flasher does not need it).")
        return
    mjs_name = m_mjs.group(0)
    js_name = m_js.group(0)
    ts_board = m_mjs.group(1)
    ts_amy = m_js.group(1)
    blobs = [
        f"amy-{ts_amy}.js", f"amy-{ts_amy}.wasm", f"amy-{ts_amy}.aw.js",
        f"amyboard-{ts_board}.mjs", f"amyboard-{ts_board}.wasm", f"amyboard-{ts_board}.data",
    ]
    for name in blobs:
        try:
            data = fetch(f"{LIVE_BASE}/{name}")
        except Exception as exc:
            print(f"[local]   skipped {name} ({exc})")
            continue
        with open(os.path.join(dev.STAGE_DIR, name), "wb") as f:
            f.write(data)
        print(f"[local]   vendored {name} ({len(data)//1024} KB)")

    # Substitute the editor HTML placeholders with the vendored names
    # (the vendored blobs already reference each other by these names).
    editor_html = os.path.join(dev.STAGE_DIR, "editor", "index.html")
    if os.path.exists(editor_html):
        dev.replace_in_file(editor_html, [
            ("AMYBOARDMJS", mjs_name),
            ("AMYJS", js_name),
        ])
    print("[local] Build done.")


if __name__ == "__main__":
    os.chdir(dev.SCRIPT_DIR)
    build_stage()
    if "--build-only" in sys.argv:
        raise SystemExit(0)
    from http.server import HTTPServer
    httpd = HTTPServer(("", dev.PORT), dev.DevHandler)
    print(f"[local] Serving http://localhost:{dev.PORT}/  (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[local] Stopped.")
