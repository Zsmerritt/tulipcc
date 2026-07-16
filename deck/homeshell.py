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


def _cancel_tick(key):
    """Unsubscribe a shared-ticker consumer (O-7); safe no-op without it."""
    try:
        import ticker
        ticker.cancel(key)
    except Exception:
        pass


def _clock_ms():
    # 30s is plenty for a clock; debug mode repaints every 2s so the RAM
    # readout is actually live.
    try:
        import decklog
        if decklog.debug_on():
            return 2000
    except Exception:
        pass
    return 30000


def _reset_cause_str():
    try:
        import machine
        rc = machine.reset_cause()
        names = {}
        for attr, short in (('PWRON_RESET', 'pwron'), ('HARD_RESET', 'hard'),
                            ('WDT_RESET', 'WDT'), ('DEEPSLEEP_RESET', 'dsleep'),
                            ('SOFT_RESET', 'soft')):
            v = getattr(machine, attr, None)
            if v is not None:
                names[v] = short
        return names.get(rc, 'rst%s' % rc)
    except Exception:
        return '?'


_BOOT_CAUSE = _reset_cause_str()
_boot_logged = False


def _debug_str():
    """One compact status-bar line for debug mode: why we booted + free RAM
    (MicroPython heap in PSRAM, and the always-tight internal SRAM)."""
    parts = [_BOOT_CAUSE]
    try:
        import gc
        parts.append("py %dK" % (gc.mem_free() // 1024))
    except Exception:
        pass
    try:
        import esp32
        info = esp32.idf_heap_info(esp32.HEAP_DATA)
        # internal SRAM regions are the small ones; the PSRAM region is MBs
        internal = sum(r[1] for r in info if r[0] < 400 * 1024)
        parts.append("int %dK" % (internal // 1024))
    except Exception:
        pass
    return "  ".join(parts)


def _clock_str():
    try:
        import time
        t = time.localtime()
        if t[0] < 2024:
            # RTC never set (fresh boot, no NTP): a confidently wrong time is
            # worse than none (UX-REVIEW-6 N1)
            return "--:--"
        try:
            import deckcfg
            h24 = bool(deckcfg.get('clock_24h', True))
        except Exception:
            h24 = True
        if h24:
            return "%02d:%02d" % (t[3], t[4])
        h = t[3] % 12
        if h == 0:
            h = 12
        return "%d:%02d%s" % (h, t[4], "a" if t[3] < 12 else "p")
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
        self._has_level = None    # firmware has tulip.amy_level()? (lazy probe)
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
        # debug readout (reset cause + free RAM), hidden unless debug mode
        self._dbg_lbl = dk.label(bar, "", self.W - RIGHT_ZONE - 260, 20,
                                 color=dk.MUTED, font=dk.FONT_S, w=250,
                                 align=lv.TEXT_ALIGN.RIGHT)
        self._dbg_lbl.add_flag(lv.obj.FLAG.HIDDEN)
        self._dbg_tick = 0
        global _boot_logged
        if not _boot_logged:
            _boot_logged = True
            try:
                import decklog
                decklog.log("boot: reset cause %s" % _BOOT_CAUSE)
            except Exception:
                pass

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
            # Live meter along the chip's bottom edge, with an ALWAYS-VISIBLE
            # track so the meter is discoverable at idle (the first cut's bare
            # 4px bar was invisible until something played). Driven by AMY's
            # real output level on firmware with tulip.amy_level(), else by
            # voices-in-use (forwarder.live_voices). _start_meter ticks it.
            dot.align(lv.ALIGN.LEFT_MID, 8, -4)
            lb.align(lv.ALIGN.LEFT_MID, 24, -4)
            # slim VU accent, LOW-contrast track (X-6: the old 8px near-black
            # track over the bright chip read as a progress bar stuck at
            # ~100% and pulled the eye top-center on every screen)
            track = lv.obj(b)
            track.set_size(cw - 14, 3)
            dk._flat(track, radius=2, bg=dk.SURFACE)
            track.remove_flag(lv.obj.FLAG.SCROLLABLE)
            track.align(lv.ALIGN.BOTTOM_MID, 0, -3)
            bar = lv.obj(track)
            bar.set_size(1, 3)
            dk._flat(bar, radius=2, bg=dk.GREEN)
            bar.remove_flag(lv.obj.FLAG.SCROLLABLE)
            bar.align(lv.ALIGN.LEFT_MID, 0, 0)
            self._chip_live.append(
                {'bar': bar, 'dot': dot, 'base': dotcol,
                 'cap': max(1, spec.get('capacity', 32)), 'w': cw - 14,
                 'lastw': 1, 'flick': False, 'disp': 0.0})
        return b

    def _on_chip(self, device):
        if self.on_chip is not None:
            try:
                self.on_chip(self, device)
            except Exception as e:
                print("homeshell: on_chip failed:", e)

    def refresh_status(self):
        """Public poke for the wifi + clock cluster -- Settings calls this
        right after a successful Wi-Fi connect so the bar doesn't say
        'offline' until the next rebuild."""
        try:
            self._render_wifi_clock()
        except Exception:
            pass

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
        # debug readout rides the same repaint cadence
        try:
            import decklog
            if decklog.debug_on():
                self._dbg_lbl.remove_flag(lv.obj.FLAG.HIDDEN)
                self._dbg_lbl.set_text(_debug_str())
            else:
                self._dbg_lbl.add_flag(lv.obj.FLAG.HIDDEN)
        except Exception:
            pass

    # ----- content / panel stack -----------------------------------------
    def _build_content(self):
        self.content = lv.obj(self.screen.group)
        self.content.set_size(self.W, self.H - BAR_H)
        self.content.set_pos(0, BAR_H)
        dk._flat(self.content, bg=dk.BG)

    def push(self, builder, title, key=None, slow=False):
        """Create a full-size panel, hide the current one, let builder fill it.

        slow=True paints a 'Loading...' placeholder first and runs the builder
        on the next tick -- long builds (Apps discovery scans /user) otherwise
        read as a frozen UI."""
        dk.close_keyboard()     # panels never share the keyboard (see back())
        dk.close_confirm()      # a modal never outlives its panel (F-12)
        self._refill_gen += 1   # cancel any pending back()-refill
        panel = lv.obj(self.content)
        panel.set_size(self.W, self.H - BAR_H)
        panel.set_pos(0, 0)
        dk._flat(panel, bg=dk.BG, scroll=True)
        prev = self.stack.push(panel, title, key, builder)
        if prev is not None:
            prev.add_flag(lv.obj.FLAG.HIDDEN)
        if slow:
            lbl = dk.label(panel, "Loading...", color=dk.MUTED, font=dk.FONT_M)
            try:
                lbl.center()
            except Exception:
                pass
            gen = self._refill_gen

            def _fill_later(_x):
                if not self._alive or gen != self._refill_gen:
                    return
                try:
                    panel.clean()
                except Exception:
                    return          # panel already gone
                self._fill(panel, builder)
            try:
                tulip.defer(_fill_later, 0, 30)
            except Exception:
                try:
                    panel.clean()
                except Exception:
                    pass
                self._fill(panel, builder)
        else:
            self._fill(panel, builder)
        self._sync_chrome()
        return panel

    def rebuild_top(self, builder, title, key=None):
        """Re-run `builder` on the current top panel in place (same handle, so
        Back still lands where it did) -- used when tapping a different chip
        while a panel of the same kind is already open."""
        dk.close_keyboard()     # never tear a textarea out from under the
        dk.close_confirm()      # global keyboard/modal (F-12) -- match
                                # push/back/reset_to_root before h.clean()
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
        # The global soft keyboard outlives panels and keeps a raw pointer
        # to its target textarea -- leaving a panel with the keyboard up,
        # then tapping its close/checkmark, use-after-freed the deleted
        # field and hard-crashed the device (seen live on Wi-Fi settings).
        dk.close_keyboard()
        dk.close_confirm()      # a modal never outlives its panel (F-12)
        removed, revealed = self.stack.pop()
        if removed is None:
            return
        if revealed is None:
            try:
                removed.delete()
            except Exception:
                pass
            self._sync_chrome()
            return
        # ONE visual swap (was 3-4 full repaints: delete popped -> reveal the
        # stale panel -> clean -> rebuild -> invalidate, each flashing the
        # screen in DIRECT mode). The popped panel STAYS on screen while the
        # revealed panel rebuilds underneath -- still HIDDEN, so the rebuild
        # paints nothing -- then reveal + delete land on the same tick.
        # Still deferred off the Back callback: rebuilding a heavy panel
        # inline inside the event tick is the interrupt-WDT pattern.
        self._refill_gen += 1
        gen = self._refill_gen

        def _do(_x):
            stale = (not self._alive) or (gen != self._refill_gen)
            if not stale:
                h = self.stack.top_handle()
                b = self.stack.top_builder()
                try:
                    if h is not None and b is not None:
                        h.clean()
                        self._fill(h, b)
                        h.remove_flag(lv.obj.FLAG.HIDDEN)
                except Exception:
                    pass
            try:
                removed.delete()
            except Exception:
                pass
        try:
            tulip.defer(_do, 0, 10)
        except Exception:
            _do(None)
        self._sync_chrome()

    def reset_to_root(self):
        dk.close_keyboard()     # same hazard as back(): don't strand the kb
        dk.close_confirm()      # F-12
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
    def _meter_fraction(self):
        """0..1 for the chip meter: AMY's real output peak when the firmware
        has the tap (tulip.amy_level -- sqrt for a perceptual curve), else
        voices-in-use / capacity as the fallback."""
        if self._has_level is None:
            self._has_level = hasattr(tulip, 'amy_level')
        if self._has_level:
            try:
                lvl = tulip.amy_level()
                return (lvl ** 0.5) if lvl > 0 else 0.0
            except Exception:
                self._has_level = False
        try:
            import forwarder
            cap = self._chip_live[0]['cap'] if self._chip_live else 32
            return min(1.0, forwarder.live_voices() / cap)
        except Exception:
            return 0.0

    def _start_meter(self):
        """A 10 Hz tick painting live state into the chips: a level bar
        (audio peak or voice count) + a MIDI-activity flicker on the dot.
        Dirty regions are a few hundred pixels, unchanged frames are skipped,
        and the tick pauses whenever Home isn't the presented screen."""
        if self._meter_running or not self._alive:
            return
        self._meter_running = True

        def _tick(x=None):
            if not self._alive:
                self._meter_running = False
                _cancel_tick('shell_meter')
                return
            if not self._home_presented():
                self._meter_running = False   # resumed by _on_activate
                _cancel_tick('shell_meter')
                return
            frac = self._meter_fraction()
            try:
                import forwarder
                a = forwarder.activity()
            except Exception:
                a = self._last_act
            active = (a != self._last_act)
            self._last_act = a
            for m in self._chip_live:
                try:
                    # VU-style fall: jump up instantly, drift down (~1s full-
                    # scale) so short notes still register visually
                    m['disp'] = frac if frac > m['disp'] else max(0.0, m['disp'] - 0.10)
                    bw = max(1, min(m['w'], int(m['w'] * m['disp'])))
                    if bw != m['lastw']:
                        m['bar'].set_width(bw)
                        m['bar'].set_style_bg_color(
                            dk.c(dk.RED if m['disp'] >= 0.9 else
                                 (dk.ORANGE if m['disp'] >= 0.7
                                  else dk.GREEN)), 0)
                        m['lastw'] = bw
                    if active != m['flick']:
                        # flicker: dot goes white while messages stream
                        m['dot'].set_style_bg_color(
                            dk.c(dk.WHITE if active else m['base']), 0)
                        m['flick'] = active
                except Exception:
                    self._meter_running = False
                    _cancel_tick('shell_meter')
                    return       # chips rebuilt/deleted; refresh restarts us
        # ONE shared tick source (O-7): the old per-tick tulip.defer re-arm
        # allocated a closure + burned a defer slot 10x/second
        try:
            import ticker
            ticker.every(100, _tick, key='shell_meter')
        except Exception:
            self._meter_running = False

    def _start_clock(self):
        if self._clock_running or not self._alive:
            return
        self._clock_running = True

        def _tick(x=None):
            if not self._alive:
                self._clock_running = False
                _cancel_tick('shell_clock')
                return
            if not self._home_presented():
                # Paused. _on_activate repaints and resumes when Home is back.
                self._clock_running = False
                _cancel_tick('shell_clock')
                return
            try:
                # full cluster, not just the time: wifi state changes (a
                # Settings connect) were stuck on 'offline' until a rebuild
                self._render_wifi_clock()
            except Exception:
                self._alive = False
                self._clock_running = False
                _cancel_tick('shell_clock')
                return
        try:
            import ticker
            ticker.every(_clock_ms(), _tick, key='shell_clock')
        except Exception:
            self._clock_running = False
