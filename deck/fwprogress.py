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
    """Move the bar to `done` chunks (of show()'s total, or `total`)."""
    if _s['ov'] is None:
        return
    if total:
        _s['total'] = max(1, int(total))
    t = _s['total']
    done = min(int(done), t)
    frac = done / t
    try:
        _s['bar'].set_width(max(1, int(_s['barw'] * frac)))
        _s['pct'].set_text("%d%%   (%d/%d)" % (int(frac * 100), done, t))
        if note and _s['sub'] is not None:
            _s['sub'].set_text(note)
    except Exception:
        _close_refs()


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
