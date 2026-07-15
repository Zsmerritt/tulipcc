# homeshell.py -- the deck's home navigation shell.
#
# A slim, full-width TOP BAR over a panel-stack content area:
#   * left:   Home breadcrumb, and a "< Back" button that appears only once
#             you've drilled into a panel (stack depth > 1);
#   * center: one tappable chip per DEVICE (Tulip + each AMYboard) -- name +
#             voices used/capacity + a connection dot (dim when offline, red at
#             >=85% load). Tapping a chip opens the Devices panel;
#   * right:  wifi indicator + clock.
#
# Replaces navshell.py's left-rail / full-width-bars prototype. The firmware
# task bar (shuffle + power/quit + corner launcher) is stripped on Home by
# ui_patch.py, so this bar is the sole navigation and Home reads as the root.
#
# home.py builds the app tile grid as the root panel. Pure/LVGL-free logic
# (chip specs, fleet subtitle, panel-stack bookkeeping) lives in shellmodel.py.

import tulip
import deckui as dk
import shellmodel as sm
import lvgl as lv

BAR_H = 56
LEFT_ZONE = 288     # left region: Back button + breadcrumb (wide enough that
                    # titles like "Edit instrument" no longer truncate to "Edit…")
RIGHT_ZONE = 190    # right region: wifi + clock
CHIP_H = 40


def _sym(name, fallback):
    return getattr(lv.SYMBOL, name, fallback) if hasattr(lv, 'SYMBOL') else fallback


def _clock_str():
    try:
        import time
        t = time.localtime()
        if t[0] < 2024:
            # RTC never set (fresh boot, no NTP): a confidently wrong time is
            # worse than none (UX-REVIEW-6 N1)
            return "--:--"
        return "%02d:%02d" % (t[3], t[4])
    except Exception:
        return "--:--"


class HomeShell:
    """Top-bar + panel-stack shell drawn onto a deck UIScreen.

    Public API:
      push(builder, title)  -- push a panel; builder(parent, shell) fills it
      back()                -- pop one panel (what the Back button calls)
      reset_to_root()       -- pop everything back to the root panel
      refresh_chips()       -- re-read the per-instance chips from deckcfg
    """

    def __init__(self, screen, root_title="Home"):
        self.screen = screen
        self.W, self.H = tulip.screen_size()
        self.stack = sm.PanelStack(root_title)
        self._chip_btns = []
        self._chip_live = []
        self._meter_running = False
        self._last_act = -1
        self._alive = True
        # Generation counter for deferred back()-refills: bumped on every nav so a
        # pending refill for a panel that's since been popped/replaced is cancelled
        # (avoids rebuilding -- or worse, touching -- a deleted panel).
        self._refill_gen = 0
        # optional cb(shell, device) for a device-chip tap; home.py wires this
        # to open the Devices panel.
        self.on_chip = None
        screen.bg_color = dk.BG
        self._build_bar()
        self._build_content()
        self.refresh_chips()
        self._sync_chrome()
        self._schedule_clock()
        self._start_meter()

    # ----- top bar --------------------------------------------------------
    def _build_bar(self):
        bar = lv.obj(self.screen.group)
        bar.set_size(self.W, BAR_H)
        bar.set_pos(0, 0)
        dk._flat(bar, bg=dk.SURFACE)
        self.bar = bar

        # left: Back (hidden at the root) + breadcrumb/title
        self._nav_btn = lv.button(bar)
        self._nav_btn.set_size(96, BAR_H - 12)
        self._nav_btn.set_pos(8, 6)
        dk._flat(self._nav_btn, radius=12, bg=dk.ACCENT)
        nl = lv.label(self._nav_btn)
        nl.set_text("%s Back" % _sym('LEFT', "<"))
        nl.set_style_text_color(dk.c(dk.WHITE), 0)
        nl.set_style_text_font(dk.FONT_S, 0)
        nl.center()
        self._nav_btn.add_event_cb(self._on_nav, lv.EVENT.CLICKED, None)
        self._title_lbl = dk.label(bar, "", 16, 18, color=dk.WHITE, font=dk.FONT_M,
                                   w=LEFT_ZONE - 24)
        # one line, ellipsized -- never wrap/clip into the chip zone
        try:
            self._title_lbl.set_long_mode(lv.label.LONG.DOT)
        except Exception:
            pass

        # center: per-instance instrument chips, centered in the middle zone
        chips = lv.obj(bar)
        chips.set_size(self.W - LEFT_ZONE - RIGHT_ZONE, BAR_H)
        chips.set_pos(LEFT_ZONE, 0)
        dk._flat(chips, bg=dk.SURFACE)
        chips.set_flex_flow(lv.FLEX_FLOW.ROW)
        chips.set_style_pad_column(8, 0)
        chips.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER,
                             lv.FLEX_ALIGN.CENTER)
        self._chips_box = chips

        # right: wifi + clock
        self._wifi_lbl = dk.label(bar, "", self.W - RIGHT_ZONE + 12, 20,
                                  color=dk.MUTED, font=dk.FONT_S, w=RIGHT_ZONE - 90)
        self._clock_lbl = dk.label(bar, "", self.W - 76, 18, color=dk.WHITE,
                                   font=dk.FONT_M, w=68, align=lv.TEXT_ALIGN.RIGHT)

    def refresh_chips(self):
        for b in self._chip_btns:
            try:
                b.delete()
            except Exception:
                pass
        self._chip_btns = []
        self._chip_live = []      # (bar, dot, base_color, capacity) for meters
        specs = sm.device_chip_specs(self._read_devices())
        n = max(1, len(specs))
        cw = 168 if len(specs) <= 3 else max(110, int((self.W - LEFT_ZONE -
                                                       RIGHT_ZONE - 32) / n))
        for spec in specs:
            self._chip_btns.append(self._make_device_chip(spec, cw))
        self._render_wifi_clock()
        self._start_meter()      # (re)arm: a chip rebuild kills the old tick

    def _read_devices(self):
        try:
            import deckcfg
            return deckcfg.device_list()
        except Exception:
            return []

    def _make_device_chip(self, spec, cw):
        b = lv.button(self._chips_box)
        b.set_size(cw, CHIP_H)
        dk._flat(b, radius=12, bg=(dk.SURFACE2 if spec['connected'] else dk.SURFACE))
        # connection/load dot: red when hot, green when connected, gray offline
        dot = lv.obj(b)
        dot.set_size(10, 10)
        dotcol = dk.RED if spec['warn'] else (dk.GREEN if spec['connected']
                                              else dk.GRAY)
        dk._flat(dot, radius=5, bg=dotcol)
        dot.align(lv.ALIGN.LEFT_MID, 8, 0)
        lb = lv.label(b)
        lb.set_text("%s %s" % (spec['name'],
                               spec['text'] if spec['connected'] else "off"))
        lb.set_style_text_color(
            dk.c(dk.WHITE if spec['connected'] else dk.MUTED), 0)
        lb.set_style_text_font(dk.FONT_S, 0)
        lb.align(lv.ALIGN.LEFT_MID, 24, 0)
        b.add_event_cb((lambda d: (lambda e: self._on_chip(d)))(spec['device']),
                       lv.EVENT.CLICKED, None)
        if spec['device'] == 'internal':
            # live voice meter along the chip's bottom edge (the router knows
            # exactly how many internal voices are sounding -- see
            # forwarder.live_voices); updated by _start_meter's tick.
            bar = lv.obj(b)
            bar.set_size(1, 4)
            dk._flat(bar, radius=2, bg=dk.GREEN)
            bar.align(lv.ALIGN.BOTTOM_LEFT, 6, -2)
            self._chip_live.append(
                {'bar': bar, 'dot': dot, 'base': dotcol,
                 'cap': max(1, spec.get('capacity', 32)), 'w': cw - 12,
                 'lastw': 1, 'flick': False})
        return b

    def _on_chip(self, device):
        if self.on_chip is not None:
            try:
                self.on_chip(self, device)
            except Exception as e:
                print("homeshell: on_chip failed:", e)

    def _render_wifi_clock(self):
        online = False
        try:
            online = bool(tulip.ip())
        except Exception:
            online = False
        self._wifi_lbl.set_text((_sym('WIFI', "wifi") + " on") if online
                                else "offline")
        self._wifi_lbl.set_style_text_color(
            dk.c(dk.GREEN if online else dk.MUTED), 0)
        self._clock_lbl.set_text(_clock_str())

    # ----- content / panel stack -----------------------------------------
    def _build_content(self):
        self.content = lv.obj(self.screen.group)
        self.content.set_size(self.W, self.H - BAR_H)
        self.content.set_pos(0, BAR_H)
        dk._flat(self.content, bg=dk.BG)

    def push(self, builder, title, key=None):
        """Create a full-size panel, hide the current one, let builder fill it."""
        self._refill_gen += 1   # cancel any pending back()-refill
        panel = lv.obj(self.content)
        panel.set_size(self.W, self.H - BAR_H)
        panel.set_pos(0, 0)
        dk._flat(panel, bg=dk.BG, scroll=True)
        prev = self.stack.push(panel, title, key, builder)
        if prev is not None:
            prev.add_flag(lv.obj.FLAG.HIDDEN)
        self._fill(panel, builder)
        self._sync_chrome()
        return panel

    def rebuild_top(self, builder, title, key=None):
        """Re-run `builder` on the current top panel in place (same handle, so
        Back still lands where it did) -- used when tapping a different chip
        while a panel of the same kind is already open."""
        self._refill_gen += 1   # cancel any pending back()-refill
        h = self.stack.top_handle()
        if h is None:
            return self.push(builder, title, key)
        try:
            h.clean()
        except Exception:
            pass
        self.stack.set_top(title, key, builder)
        self._fill(h, builder)
        self._sync_chrome()
        return h

    def top_key(self):
        return self.stack.top_key()

    def _fill(self, panel, builder):
        try:
            builder(panel, self)
        except Exception as e:
            try:
                import decklog
                decklog.log_exc("panel build failed (builder=%s)"
                                % getattr(builder, '__name__', builder), e)
            except Exception:
                pass
            dk.label(panel, "panel error: %s" % e, 20, 20, color=dk.RED,
                     font=dk.FONT_S)

    def back(self):
        removed, revealed = self.stack.pop()
        if removed is None:
            return
        try:
            removed.delete()
        except Exception:
            pass
        if revealed is not None:
            try:
                revealed.remove_flag(lv.obj.FLAG.HIDDEN)
            except Exception:
                pass
            # Refresh the revealed panel from its builder so any state changed in
            # the panel we just left (patch, params, instrument list) shows now.
            # CRITICAL: do this on a DEFERRED tick, not inline. Rebuilding a heavy
            # panel (the tabbed Sound/FX editor) synchronously here -- back-to-back
            # with the sub-panel teardown above, all inside the Back event callback
            # -- starves CPU1 long enough to trip the interrupt WDT and reboot the
            # device. Splitting teardown and rebuild across ticks avoids it.
            self._schedule_refill()
        self._sync_chrome()

    def _schedule_refill(self):
        """Rebuild the current top panel from its builder, on a later tick.

        Re-derives the top handle/builder at fire time (never captures a specific
        panel) and is generation-guarded, so a refill queued for a panel that has
        since been popped or replaced is skipped -- no use-after-free, no redundant
        heavy rebuild."""
        self._refill_gen += 1
        gen = self._refill_gen

        def _do(_x):
            if not self._alive or gen != self._refill_gen:
                return
            h = self.stack.top_handle()
            b = self.stack.top_builder()
            if h is None or b is None:
                return
            try:
                h.clean()
                self._fill(h, b)
            except Exception:
                pass
        try:
            tulip.defer(_do, 0, 10)
        except Exception:
            # No defer available (host tests): fall back to inline.
            _do(None)

    def reset_to_root(self):
        self._refill_gen += 1   # cancel any pending back()-refill
        for h in self.stack.reset_to_root():
            try:
                h.delete()
            except Exception:
                pass
        root = self.stack.top_handle()
        if root is not None:
            try:
                root.remove_flag(lv.obj.FLAG.HIDDEN)
            except Exception:
                pass
        self._sync_chrome()

    # ----- chrome sync ----------------------------------------------------
    def _sync_chrome(self):
        # Show only the CURRENT panel's title (the active instance is already
        # conveyed by the highlighted chip), single-line and ellipsized, so it
        # never wraps or bleeds into the centered chips.
        if self.stack.back_visible():
            self._nav_btn.remove_flag(lv.obj.FLAG.HIDDEN)
            self._title_lbl.set_style_text_font(dk.FONT_S, 0)
            self._title_lbl.set_width(LEFT_ZONE - 116)
            self._title_lbl.set_pos(110, 20)
            self._title_lbl.set_text(self.stack.title())
        else:
            self._nav_btn.add_flag(lv.obj.FLAG.HIDDEN)
            self._title_lbl.set_style_text_font(dk.FONT_M, 0)
            self._title_lbl.set_width(LEFT_ZONE - 24)
            self._title_lbl.set_pos(16, 18)
            self._title_lbl.set_text(self.stack.root_title)

    def _on_nav(self, e):
        self.back()

    # ----- clock ----------------------------------------------------------
    def _schedule_clock(self):
        """Tick the clock label every 30s -- but only while Home is the
        presented screen. Switching to another app pauses the chain (no point
        repainting a hidden label forever); presenting Home again fires the
        screen's activate_callback, which repaints the time/wifi immediately
        (so the clock is never stale on return) and resumes the ticking."""
        self._clock_running = False
        prev_activate = getattr(self.screen, 'activate_callback', None)

        def _on_activate(scr):
            if prev_activate is not None:
                try:
                    prev_activate(scr)
                except Exception:
                    pass
            self._render_wifi_clock()      # snap time + wifi current NOW
            self._start_clock()
            self._start_meter()            # live chip meter resumes too
        try:
            self.screen.activate_callback = _on_activate
        except Exception:
            pass
        self._start_clock()

    def _home_presented(self):
        try:
            import ui
            return ui.current_app_string == getattr(self.screen, 'name', None)
        except Exception:
            return True    # can't tell: keep ticking (the old behavior)

    # ----- live chip meter ------------------------------------------------
    def _start_meter(self):
        """A ~4 Hz tick painting live state into the chips: voice-meter bar
        width (internal AMY) + a MIDI-activity flicker on the dot. Dirty
        regions are a few hundred pixels, unchanged frames are skipped, and
        the tick pauses whenever Home isn't the presented screen."""
        if self._meter_running or not self._alive:
            return
        self._meter_running = True

        def _tick(x):
            if not self._alive:
                self._meter_running = False
                return
            if not self._home_presented():
                self._meter_running = False   # resumed by _on_activate
                return
            try:
                import forwarder
                v = forwarder.live_voices()
                a = forwarder.activity()
            except Exception:
                v, a = 0, self._last_act
            active = (a != self._last_act)
            self._last_act = a
            for m in self._chip_live:
                try:
                    bw = max(1, min(m['w'], int(m['w'] * v / m['cap'])))
                    if bw != m['lastw']:
                        m['bar'].set_width(bw)
                        m['bar'].set_style_bg_color(
                            dk.c(dk.RED if v >= m['cap'] * 0.9 else
                                 (dk.ORANGE if v >= m['cap'] * 0.7
                                  else dk.GREEN)), 0)
                        m['lastw'] = bw
                    if active != m['flick']:
                        # flicker: dot goes white while messages stream
                        m['dot'].set_style_bg_color(
                            dk.c(dk.WHITE if active else m['base']), 0)
                        m['flick'] = active
                except Exception:
                    self._meter_running = False
                    return       # chips rebuilt/deleted; refresh restarts us
            tulip.defer(_tick, 0, 250)
        try:
            tulip.defer(_tick, 0, 250)
        except Exception:
            self._meter_running = False

    def _start_clock(self):
        if self._clock_running or not self._alive:
            return
        self._clock_running = True

        def _tick(x):
            if not self._alive:
                self._clock_running = False
                return
            if not self._home_presented():
                # Paused. _on_activate repaints and resumes when Home is back.
                self._clock_running = False
                return
            try:
                self._clock_lbl.set_text(_clock_str())
            except Exception:
                self._alive = False
                self._clock_running = False
                return
            tulip.defer(_tick, 0, 30000)
        try:
            tulip.defer(_tick, 0, 30000)
        except Exception:
            self._clock_running = False
