# navshell.py -- a deck-owned navigation shell (prototype for DOCKET item 2).
#
# Draws a persistent LEFT RAIL (Home + Back + a live status strip) and a
# panel-stack content area on top of a normal deck UIScreen, giving apps
# push/pop/Back navigation instead of the firmware's shuffle-cycle task bar.
#
# Design language borrowed (not ported) from KlipperScreen: big flat touch
# targets, dark theme, generous spacing, one always-visible nav bar, and
# screen-stack (push/pop) navigation with a clear Back.
#
# It is self-contained and importable. Nothing else has to change to try it:
#
#   import navshell
#   def run(screen):
#       navshell.run(screen)          # built-in demo, or:
#
#   def run(screen):
#       shell = navshell.NavShell(screen, root_title="Fleet")
#       shell.push(build_root_panel, "Fleet")   # your first panel
#       screen.present()
#
# A panel builder is `def build(parent, shell): ...` -- it fills `parent`
# (a full-size scrollable container) with dk.* widgets and may call
# `shell.push(other_builder, "Title")` from a button to drill in.
#
# LVGL-9 assumptions (matching deckui.py): lv.obj/lv.button/lv.label,
# obj.set_style_*(value, 0), obj.add_flag / remove_flag(lv.obj.FLAG.HIDDEN),
# obj.add_event_cb(cb, lv.EVENT.CLICKED, None), e.get_target_obj().
# All hardware/state lookups (deckcfg, tulip.ip, time) are guarded so the
# shell still renders on a bare board or under the host mocks.

import tulip
import deckui as dk
import lvgl as lv

# Left rail geometry. 104px is comfortably above the ~88px "big touch target"
# floor and still leaves 920px of content width on the 1024x600 panel.
RAIL_W = 104
HEADER_H = 64
_STATUS_H = 172

# lv.SYMBOL names vary slightly by build; fall back to a text glyph if missing.
def _sym(name, fallback):
    return getattr(lv.SYMBOL, name, fallback) if hasattr(lv, 'SYMBOL') else fallback


class NavShell:
    """A persistent nav rail + panel stack drawn onto a deck UIScreen.

    Public API:
      push(builder, title)  -> push a new panel (builder fills it), returns panel
      pop()                 -> pop the top panel back to the previous one
      back()                -> alias for pop() (what the rail Back button calls)
      reset_to_root()       -> pop everything above the first panel
      set_status(**fields)  -> override status-strip fields (instrument=, fleet=,
                               wifi=, clock=); refresh_status() re-derives them
      refresh_status()      -> re-read live values from deckcfg/tulip/time
    """

    def __init__(self, screen, root_title="Home", on_home=None, on_exit=None,
                 own_taskbar=False, auto_clock=True):
        self.screen = screen
        self.W, self.H = tulip.screen_size()
        self._cw = self.W - RAIL_W
        self._ch = self.H - HEADER_H
        self.stack = []                # [{'panel': obj, 'title': str}]
        self.on_home = on_home         # optional cb(shell); default = reset_to_root
        self.on_exit = on_exit         # optional cb(shell) for a root-level Home tap
        self._status = {}              # last rendered status fields
        self._alive = True

        # Own the whole screen: dark bg, and optionally suppress the firmware
        # task bar so the rail is the sole navigation (an app sets this when it
        # wants a full KlipperScreen-style shell rather than coexisting).
        screen.bg_color = dk.BG
        if own_taskbar:
            screen.hide_task_bar = True

        self.root_title = root_title
        self._build_rail()
        self._build_content()
        self._sync_chrome()
        self.refresh_status()
        if auto_clock:
            self._schedule_clock()

    # ----- rail (persistent nav bar) -------------------------------------
    def _build_rail(self):
        rail = lv.obj(self.screen.group)
        rail.set_size(RAIL_W, self.H)
        rail.set_pos(0, 0)
        dk._flat(rail, bg=dk.SURFACE)
        self.rail = rail
        self._home_btn = self._rail_button(
            rail, _sym('HOME', "Home"), "Home", 12, dk.ACCENT, self._on_home)
        self._back_btn = self._rail_button(
            rail, _sym('LEFT', "<"), "Back", 88, dk.SURFACE2, self._on_back)
        self._build_status(rail)

    def _rail_button(self, parent, icon, text, y, bg, cb):
        # A big square-ish target with the icon over its label (two lines).
        b = lv.button(parent)
        b.set_size(RAIL_W - 16, 68)
        b.set_pos(8, y)
        dk._flat(b, radius=14, bg=bg)
        lbl = lv.label(b)
        lbl.set_text("%s\n%s" % (icon, text))
        lbl.set_style_text_color(dk.c(dk.WHITE), 0)
        lbl.set_style_text_font(dk.FONT_S, 0)
        lbl.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
        lbl.center()
        b.add_event_cb(cb, lv.EVENT.CLICKED, None)
        return b

    def _build_status(self, rail):
        # A compact stacked status strip pinned to the bottom of the rail:
        # active instrument, fleet board count, wifi, clock. KlipperScreen keeps
        # temps/status always visible the same way.
        s = lv.obj(rail)
        s.set_size(RAIL_W - 16, _STATUS_H)
        s.set_pos(8, self.H - _STATUS_H - 8)
        dk._flat(s, radius=12, bg=dk.SURFACE2)
        s.set_style_pad_all(8, 0)
        self._status_box = s
        w = RAIL_W - 32
        self._st_inst = dk.label(s, "", 0, 4, color=dk.TEXT, font=dk.FONT_S, w=w)
        self._st_fleet = dk.label(s, "", 0, 48, color=dk.MUTED, font=dk.FONT_S, w=w)
        self._st_wifi = dk.label(s, "", 0, 92, color=dk.MUTED, font=dk.FONT_S, w=w)
        self._st_clock = dk.label(s, "", 0, 132, color=dk.WHITE, font=dk.FONT_M, w=w)

    # ----- content area (panel stack) ------------------------------------
    def _build_content(self):
        cx = RAIL_W
        # header: breadcrumb (small) over the current panel title (large)
        self.header = lv.obj(self.screen.group)
        self.header.set_size(self._cw, HEADER_H)
        self.header.set_pos(cx, 0)
        dk._flat(self.header, bg=dk.BG)
        self._crumb = dk.label(self.header, "", 24, 8, color=dk.MUTED,
                               font=dk.FONT_S, w=self._cw - 48)
        self._titlelbl = dk.label(self.header, "", 24, 28, color=dk.WHITE,
                                  font=dk.FONT_L, w=self._cw - 48)
        # thin divider under the header
        d = lv.obj(self.screen.group)
        d.set_size(self._cw - 24, 2)
        d.set_pos(cx + 24, HEADER_H - 2)
        dk._flat(d, bg=dk.SURFACE2)
        # the stack container; every panel is a full-size child of this
        self.content = lv.obj(self.screen.group)
        self.content.set_size(self._cw, self._ch)
        self.content.set_pos(cx, HEADER_H)
        dk._flat(self.content, bg=dk.BG)

    # ----- panel stack ---------------------------------------------------
    def push(self, builder, title):
        """Create a new panel, hide the current one, and let `builder` fill it.

        builder(parent, shell): `parent` is a full-size, vertically scrollable
        flex column (same feel as dk.scroll_body) already parented into the
        content area. Returns the new panel obj.
        """
        panel = lv.obj(self.content)
        panel.set_size(self._cw, self._ch)
        panel.set_pos(0, 0)
        dk._flat(panel, bg=dk.BG, scroll=True)
        panel.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        panel.set_style_pad_all(20, 0)
        panel.set_style_pad_row(12, 0)
        panel.set_scroll_dir(lv.DIR.VER)
        if self.stack:
            self.stack[-1]['panel'].add_flag(lv.obj.FLAG.HIDDEN)
        self.stack.append({'panel': panel, 'title': title})
        try:
            builder(panel, self)
        except Exception as e:
            dk.label(panel, "panel error: %s" % e, color=dk.RED, font=dk.FONT_S)
        self._sync_chrome()
        return panel

    def pop(self):
        if len(self.stack) <= 1:
            return
        top = self.stack.pop()
        try:
            top['panel'].delete()
        except Exception:
            pass
        self.stack[-1]['panel'].remove_flag(lv.obj.FLAG.HIDDEN)
        self._sync_chrome()

    def back(self):
        self.pop()

    def reset_to_root(self):
        while len(self.stack) > 1:
            self.pop()

    # ----- chrome sync ---------------------------------------------------
    def _sync_chrome(self):
        depth = len(self.stack)
        title = self.stack[-1]['title'] if depth else self.root_title
        self._titlelbl.set_text(title)
        # single Back is the primary affordance; the breadcrumb is a read-only
        # trail so a deep stack still tells you where you are.
        trail = " / ".join(f['title'] for f in self.stack)
        self._crumb.set_text(trail if depth > 1 else "")
        # hide Back at the root -- you don't back out of the root panel
        if depth > 1:
            self._back_btn.remove_flag(lv.obj.FLAG.HIDDEN)
        else:
            self._back_btn.add_flag(lv.obj.FLAG.HIDDEN)

    # ----- rail callbacks ------------------------------------------------
    def _on_home(self, e):
        if len(self.stack) <= 1 and self.on_exit is not None:
            self.on_exit(self)         # already home: e.g. return to launcher
            return
        if self.on_home is not None:
            self.on_home(self)
        else:
            self.reset_to_root()

    def _on_back(self, e):
        self.back()

    # ----- status strip --------------------------------------------------
    def set_status(self, **fields):
        self._status.update(fields)
        self._render_status()

    def refresh_status(self):
        """Re-derive status from deckcfg / tulip / time, all guarded."""
        st = {}
        try:
            import deckcfg
            inst = deckcfg.get_instance(deckcfg.active_index())
            name = inst.get('name', 'Tulip')
            patch = inst.get('patch', 0)
            st['instrument'] = "%s\np%d" % (name, patch)
            st['fleet'] = "Fleet: %d" % deckcfg.num_instances()
        except Exception:
            st.setdefault('instrument', "Tulip")
            st.setdefault('fleet', "Fleet: 1")
        try:
            ip = tulip.ip()
            st['wifi'] = (_sym('WIFI', "wifi") + " online") if ip else "offline"
        except Exception:
            st['wifi'] = "offline"
        st['clock'] = self._clock_str()
        self._status.update(st)
        self._render_status()

    def _clock_str(self):
        try:
            import time
            t = time.localtime()
            return "%02d:%02d" % (t[3], t[4])
        except Exception:
            return "--:--"

    def _render_status(self):
        s = self._status
        self._st_inst.set_text(s.get('instrument', ""))
        self._st_fleet.set_text(s.get('fleet', ""))
        wifi = s.get('wifi', "offline")
        self._st_wifi.set_text(wifi)
        self._st_wifi.set_style_text_color(
            dk.c(dk.GREEN if 'online' in wifi else dk.MUTED), 0)
        self._st_clock.set_text(s.get('clock', "--:--"))

    def _schedule_clock(self):
        # Refresh the clock (and live status) roughly once a minute.
        def _tick(x):
            if not self._alive:
                return
            try:
                self._st_clock.set_text(self._clock_str())
            except Exception:
                self._alive = False
                return
            tulip.defer(_tick, 0, 30000)
        try:
            tulip.defer(_tick, 0, 30000)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Demo: a tiny 3-level app that shows the rail, the panel stack and Back.
# Run it as its own app (drop this file in /user and `run('navshell')`) or from
# the REPL: `import navshell; run(navshell)` via a UIScreen. It does NOT touch
# any other deck app -- the sub-panels are self-contained stand-ins.
# ---------------------------------------------------------------------------

def _menu_tile(parent, label, color, cb):
    b = lv.button(parent)
    b.set_size(lv.pct(100), 84)
    dk._flat(b, radius=16, bg=color)
    lb = lv.label(b)
    lb.set_text(label)
    lb.set_style_text_color(dk.c(dk.WHITE), 0)
    lb.set_style_text_font(dk.FONT_M, 0)
    lb.set_align(lv.ALIGN.LEFT_MID)
    lb.set_x(16)
    b.add_event_cb(cb, lv.EVENT.CLICKED, None)
    return b


def _build_detail(name):
    def build(parent, shell):
        dk.label(parent, "This is the %s panel." % name,
                 color=dk.TEXT, font=dk.FONT_M)
        dk.label(parent,
                 "Pushed onto the stack. Tap Back (rail) to pop, or Home to "
                 "return to the root menu.", color=dk.MUTED, font=dk.FONT_S,
                 w=shell._cw - 80)
        _menu_tile(parent, "Drill deeper  " + name + " > Advanced", dk.PURPLE,
                   lambda e: shell.push(_build_detail(name + " > Advanced"),
                                        name + " Advanced"))
    return build


def _build_root(parent, shell):
    dk.label(parent, "Menus are full panels, not a corner list.",
             color=dk.MUTED, font=dk.FONT_S, w=shell._cw - 80)
    items = [("Instrument", dk.ACCENT), ("MPE", dk.PURPLE),
             ("Fleet", dk.TEAL), ("Files", dk.GREEN), ("Settings", dk.GREEN)]
    for name, color in items:
        _menu_tile(parent, name, color,
                   (lambda n: (lambda e: shell.push(_build_detail(n), n)))(name))


def run(screen):
    shell = NavShell(screen, root_title="Home", own_taskbar=False)
    shell.push(_build_root, "Home")
    screen.present()
    return shell
