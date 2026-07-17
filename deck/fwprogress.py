# fwprogress.py -- on-device firmware-update progress overlay.
#
# The host-side flasher (deck/flash_fw.py) drives this between chunk copies:
#
#   mpremote exec "import fwprogress; fwprogress.show(32)"
#   ... per verified chunk ...
#   mpremote exec "import fwprogress; fwprogress.update(12)"
#   mpremote exec "import fwprogress; fwprogress.stage('Writing to flash...')"
#   mpremote exec "import fwprogress; fwprogress.done()"   # or hide()
#
# Module state persists between mpremote sessions (resume), so each call finds
# the overlay from the previous one. Drawn on lv.layer_top so it sits above
# whatever app is running; cheap updates (a bar width + two labels per chunk).

import tulip
import deckui as dk
import lvgl as lv

_s = {'ov': None, 'bar': None, 'pct': None, 'sub': None, 'total': 0, 'barw': 0}


def show(total, title="Firmware update"):
    """Create (or reset) the overlay for `total` chunks."""
    hide()
    # also sweep ORPHANED overlays: if this module was reloaded while an
    # overlay was up, the refs were lost and hide() couldn't reach it (seen
    # live: a stuck 'Firmware update' card). During an update nothing else
    # legitimately owns a layer_top modal.
    try:
        top = lv.layer_top()
        for i in range(top.get_child_count() - 1, -1, -1):
            top.get_child(i).delete()
    except Exception:
        pass
    w, h = tulip.screen_size()
    ov = lv.obj(lv.layer_top())
    ov.set_size(w, h)
    ov.set_pos(0, 0)
    dk._flat(ov, bg=dk.BG)
    ov.set_style_bg_opa(235, 0)
    _s['ov'] = ov

    card = lv.obj(ov)
    card.set_size(640, 240)
    card.center()
    dk._flat(card, radius=20, bg=dk.SURFACE)
    dk.edge(card)
    card.remove_flag(lv.obj.FLAG.SCROLLABLE)

    dk.label(card, title, 32, 28, color=dk.WHITE, font=dk.FONT_L)
    _s['sub'] = dk.label(card, "Copying firmware to the device...", 32, 74,
                         color=dk.MUTED, font=dk.FONT_S, w=576)
    # playing MIDI (or anything that makes the console chatty) can disturb
    # the serial transfer -- warn, since idle hands reach for keys
    _s['warn'] = dk.label(
        card, "Please don't play or touch the deck until this finishes.",
        32, 96, color=dk.ORANGE, font=dk.FONT_S, w=576)

    barw = 640 - 64
    track = lv.obj(card)
    track.set_size(barw, 22)
    track.set_pos(32, 128)
    dk._flat(track, radius=11, bg=dk.BG)
    track.remove_flag(lv.obj.FLAG.SCROLLABLE)
    bar = lv.obj(track)
    bar.set_size(1, 22)
    bar.align(lv.ALIGN.LEFT_MID, 0, 0)
    dk._flat(bar, radius=11, bg=dk.GREEN)
    bar.remove_flag(lv.obj.FLAG.SCROLLABLE)
    _s['bar'] = bar
    _s['barw'] = barw

    _s['pct'] = dk.label(card, "0%%   (0/%d)" % total, 32, 170, color=dk.TEAL,
                         font=dk.FONT_MONO)
    _s['total'] = max(1, int(total))


def update(done, total=None, note=None):
    """Move the bar to `done` chunks (of show()'s total, or `total`).
    SELF-HEALING: if the overlay is missing (the initial show() exec was lost
    to a transient port error -- observed live as 'no modal during a running
    update'), recreate it here."""
    if _s['ov'] is None:
        show(total or _s.get('total') or 1)
    if total:
        _s['total'] = max(1, int(total))
    t = _s['total']
    done = min(int(done), t)
    frac = done / t
    try:
        _s['bar'].set_width(max(1, int(_s['barw'] * frac)))
        _s['pct'].set_text("%d%%   (%s/%s)" % (int(frac * 100),
                                               _amt(done), _amt(t)))
        if note and _s['sub'] is not None:
            _s['sub'].set_text(note)
    except Exception:
        _close_refs()


def _amt(n):
    """Counts read as-is; byte totals read as KB/MB (UX-REVIEW-8 R-5)."""
    if n >= 1024 * 1024:
        return "%.1fMB" % (n / (1024 * 1024))
    if n >= 10000:
        return "%dKB" % (n // 1024)
    return "%d" % n


def stage(text):
    """Switch the subtitle for a new phase (verify / flash-write / reboot)."""
    if _s['sub'] is not None:
        try:
            _s['sub'].set_text(text)
        except Exception:
            _close_refs()


def done(text="Update staged. Rebooting..."):
    """Fill the bar + final message (the reboot clears the screen anyway)."""
    update(_s['total'])
    stage(text)


def fail(text="Update failed. Please try again."):
    """Persistent failure notice: red bar + message, TAP TO DISMISS. Stays on
    screen until acknowledged -- a failed update must never look like a quiet
    return to Home."""
    if _s['ov'] is None:
        show(1)               # make sure there is something to paint red
    try:
        _s['bar'].set_width(_s['barw'])
        _s['bar'].set_style_bg_color(dk.c(dk.RED), 0)
    except Exception:
        pass
    stage(text)
    try:
        _s['sub'].set_style_text_color(dk.c(dk.RED), 0)
        _s['pct'].set_text("tap to dismiss")
        # the caution line contradicts "tap to dismiss" once we've failed
        if _s.get('warn') is not None:
            _s['warn'].add_flag(lv.obj.FLAG.HIDDEN)
    except Exception:
        pass
    ov = _s['ov']
    if ov is not None:
        try:
            ov.add_flag(lv.obj.FLAG.CLICKABLE)
            ov.add_event_cb(
                lambda e: hide() if e.get_code() == lv.EVENT.CLICKED else None,
                lv.EVENT.CLICKED, None)
        except Exception:
            pass


def _close_refs():
    _s['ov'] = None
    _s['bar'] = None
    _s['pct'] = None
    _s['sub'] = None


def hide():
    ov = _s['ov']
    _close_refs()
    if ov is not None:
        try:
            ov.delete()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# TWO-TIER overlay for the /user update engine (deck/update.py, UPGRADE.md 6).
#
# The single-bar API above stays as-is for the host-driven firmware flow. This
# section adds a SECOND, thinner bar: the main bar is OVERALL progress
# (byte-weighted across all files) + a step name; the sub bar is the CURRENT
# file + its step ("verifying" / "writing").
#
# It's a pure DISPLAY SHIM: all logic lives in the host-tested engine, which
# drives this through a single callback. Wire it up with:
#
#     import fwprogress, update
#     fwprogress.update_begin()
#     update.apply_bundle(bundle_dir, progress=fwprogress.update_progress)
#
# LVGL can't run on the host, so this is device-validated; the engine and its
# progress *math* are what the host tests cover.
# ---------------------------------------------------------------------------

_u = {'ov': None, 'main': None, 'mainw': 0, 'mpct': None, 'step': None,
      'sub': None, 'subw': 0, 'subl': None}


def update_begin(title="Updating deck"):
    """Build the two-tier overlay (main + sub bar). Idempotent-ish: replaces any
    existing update overlay."""
    _u_hide()
    hide()  # never stack on top of a stale single-bar overlay
    w, h = tulip.screen_size()
    ov = lv.obj(lv.layer_top())
    ov.set_size(w, h)
    ov.set_pos(0, 0)
    dk._flat(ov, bg=dk.BG)
    ov.set_style_bg_opa(235, 0)
    _u['ov'] = ov

    card = lv.obj(ov)
    card.set_size(640, 280)
    card.center()
    dk._flat(card, radius=20, bg=dk.SURFACE)
    dk.edge(card)
    card.remove_flag(lv.obj.FLAG.SCROLLABLE)

    dk.label(card, title, 32, 24, color=dk.WHITE, font=dk.FONT_L)
    _u['step'] = dk.label(card, "Preparing...", 32, 70, color=dk.MUTED,
                          font=dk.FONT_S, w=576)

    # main bar -- overall, byte-weighted
    mainw = 640 - 64
    track = lv.obj(card)
    track.set_size(mainw, 22)
    track.set_pos(32, 104)
    dk._flat(track, radius=11, bg=dk.BG)
    track.remove_flag(lv.obj.FLAG.SCROLLABLE)
    bar = lv.obj(track)
    bar.set_size(1, 22)
    bar.align(lv.ALIGN.LEFT_MID, 0, 0)
    dk._flat(bar, radius=11, bg=dk.GREEN)
    bar.remove_flag(lv.obj.FLAG.SCROLLABLE)
    _u['main'] = bar
    _u['mainw'] = mainw
    _u['mpct'] = dk.label(card, "0%", 32, 136, color=dk.TEAL, font=dk.FONT_MONO)

    # sub label + sub bar -- current file, thinner
    _u['subl'] = dk.label(card, "", 32, 186, color=dk.MUTED, font=dk.FONT_S,
                          w=576)
    subw = 640 - 64
    strack = lv.obj(card)
    strack.set_size(subw, 12)
    strack.set_pos(32, 214)
    dk._flat(strack, radius=6, bg=dk.BG)
    strack.remove_flag(lv.obj.FLAG.SCROLLABLE)
    sbar = lv.obj(strack)
    sbar.set_size(1, 12)
    sbar.align(lv.ALIGN.LEFT_MID, 0, 0)
    dk._flat(sbar, radius=6, bg=dk.TEAL)
    sbar.remove_flag(lv.obj.FLAG.SCROLLABLE)
    _u['sub'] = sbar
    _u['subw'] = subw

    dk.label(card, "Don't unplug the deck until this finishes.",
             32, 240, color=dk.ORANGE, font=dk.FONT_S, w=576)


_STEP_NAMES = {'start': 'Preparing...', 'verifying': 'Checking files...',
               'writing': 'Installing...', 'done': 'Finished'}


def update_progress(info):
    """Engine progress callback (see deck/update.py for the `info` schema).
    Maps overall bytes -> main bar and per-file bytes -> sub bar. Tolerant of a
    missing overlay (rebuilds) and never raises back into the engine."""
    try:
        stage = info.get('stage')
        if stage == 'error':
            update_fail(info.get('reason') or "Update failed.")
            return
        if _u['ov'] is None:
            update_begin()

        ot = info.get('overall_total') or 0
        od = info.get('overall_done') or 0
        frac = (od / ot) if ot else (1.0 if stage == 'done' else 0.0)
        frac = 0.0 if frac < 0 else (1.0 if frac > 1 else frac)
        _u['main'].set_width(max(1, int(_u['mainw'] * frac)))
        _u['mpct'].set_text("%d%%" % int(frac * 100))

        if _u['step'] is not None:
            _u['step'].set_text(_STEP_NAMES.get(stage, "Working..."))

        if stage in ('verifying', 'writing'):
            it = info.get('item_total') or 0
            idn = info.get('item_done') or 0
            sfrac = (idn / it) if it else 0.0
            sfrac = 0.0 if sfrac < 0 else (1.0 if sfrac > 1 else sfrac)
            _u['sub'].set_width(max(1, int(_u['subw'] * sfrac)))
            verb = 'Verifying' if stage == 'verifying' else 'Writing'
            _u['subl'].set_text("%s %s  (%s/%s)" % (
                verb, info.get('path', ''),
                info.get('file_index', '?'), info.get('file_count', '?')))
        elif stage == 'done':
            _u['sub'].set_width(_u['subw'])
            _u['subl'].set_text("Done")
    except Exception:
        _u_close_refs()


def update_fail(text="Update failed."):
    """Red main bar + message; taps to dismiss (mirrors fail())."""
    if _u['ov'] is None:
        update_begin()
    try:
        _u['main'].set_width(_u['mainw'])
        _u['main'].set_style_bg_color(dk.c(dk.RED), 0)
        _u['step'].set_style_text_color(dk.c(dk.RED), 0)
        _u['step'].set_text(text)
        _u['mpct'].set_text("tap to dismiss")
    except Exception:
        pass
    ov = _u['ov']
    if ov is not None:
        try:
            ov.add_flag(lv.obj.FLAG.CLICKABLE)
            ov.add_event_cb(
                lambda e: _u_hide() if e.get_code() == lv.EVENT.CLICKED else None,
                lv.EVENT.CLICKED, None)
        except Exception:
            pass


def _u_close_refs():
    for k in ('ov', 'main', 'mpct', 'step', 'sub', 'subl'):
        _u[k] = None


def _u_hide():
    ov = _u['ov']
    _u_close_refs()
    if ov is not None:
        try:
            ov.delete()
        except Exception:
            pass
