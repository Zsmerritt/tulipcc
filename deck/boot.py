# boot.py -- Tulip startup (deck edition).
#
# Lives in /user (survives tulip.upgrade()). Everything is wrapped in try/except
# so a failure here can never stop you reaching the REPL. It:
#   * puts /user on the import path so the deck apps are runnable anywhere
#   * restores audio/display/instrument/MPE from /user/var/deck_config.json
#   * installs ui_patch (bigger task bar + launcher menu with the deck apps)
#   * lands on the Welcome screen (first boot) or the Home launcher

import sys
if '/user' not in sys.path:
    sys.path.append('/user')


def _boot():
    import tulip

    # PING-PONG flash mode (see deck/flashmode.py, deck/PINGPONG.md): if THIS
    # is the 80MHz flasher build AND an update was armed (NVS flash_pending),
    # do NOT boot the deck UI. Bring up Wi-Fi and idle so the host can stream
    # the new play image into the play slot at 80MHz -- the thermally safe
    # write path. This is checked FIRST and is completely fail-soft: any error
    # (no esp32/NVS, unknown build, missing slots) falls through to a normal
    # boot, and on the 120MHz play image should_enter_flash_mode() is always
    # False so normal-play behaviour is untouched.
    try:
        import flashmode
        if flashmode.should_enter_flash_mode():
            flashmode.enter_flash_mode()
            return
    except Exception as e:
        print("deck: flash mode check failed:", e)

    # FIRST, before anything slow or interruptible (Wi-Fi join, config apply):
    # the task-bar/launcher patch is what gives the stock REPL screen its
    # "Home" button. A boot interrupted by a host serial tool used to strand
    # the deck in the stock REPL with no touch path back into the deck UI.
    try:
        import ui_patch
        ui_patch.apply()
    except Exception as e:
        print("deck: ui_patch failed:", e)

    try:
        import decklog
        # Include WHY we booted: a watchdog reset never runs Python again, so
        # the log can't capture the crash itself (UX-REVIEW-6 H1) -- but the
        # NEXT boot can at least say "WDT" instead of looking like a power-on.
        cause = "?"
        try:
            import machine
            rc = machine.reset_cause()
            cause = {machine.PWRON_RESET: 'PWRON', machine.HARD_RESET: 'HARD',
                     machine.WDT_RESET: 'WDT', machine.SOFT_RESET: 'SOFT',
                     machine.DEEPSLEEP_RESET: 'DEEPSLEEP'}.get(rc, str(rc))
        except Exception:
            pass
        decklog.log("=== deck boot === reset_cause=%s" % cause)
    except Exception:
        pass

    try:
        import deckcfg
        cfg = deckcfg.load()
    except Exception as e:
        print("deck: config load failed:", e)
        cfg = {}

    # Wi-Fi -- only if you've saved a network (Settings does this).
    ssid = cfg.get('wifi_ssid', '') if cfg else ''
    if ssid:
        # BaseException, not Exception: a stray serial ^C arriving during the
        # ~10s Wi-Fi join raises KeyboardInterrupt (a BaseException, NOT an
        # Exception subclass). It escaped a bare `except Exception`, aborted
        # boot.py mid-way and stranded the deck on the REPL with no touch path
        # back into the UI (UX10-13). Swallow it here so boot always continues
        # to Home; a real ^C at the REPL still works once boot has finished.
        try:
            tulip.wifi(ssid, cfg.get('wifi_pass', ''))
            if tulip.ip():
                try:
                    import deckcfg as _dc
                    _dc.sync_time()    # NTP + geo-IP localize (real clock)
                except Exception:
                    pass
        except BaseException as e:
            print("wifi failed:", e)

    # Optional microSD as a TRUE second write channel: SD goes over its own
    # peripheral, so writing there can never race the flash cache AMY's
    # sample banks read through (see deckcfg.fenced_write). The v4r9 main
    # board has no SD nets in its schematic, so pins are NOT guessed --
    # set cfg['sd_pins'] = {'sck':.., 'miso':.., 'mosi':.., 'cs':..}
    # (AMYboard uses 12/13/11/10) and decklog will prefer /sd/deck.log.
    sdp = (cfg or {}).get('sd_pins')
    if isinstance(sdp, dict):
        try:
            import machine, uos
            sd = machine.SDCard(sck=sdp['sck'], miso=sdp['miso'],
                                mosi=sdp['mosi'], cs=sdp['cs'], slot=2)
            uos.mount(uos.VfsFat(sd), '/sd')
            print("deck: SD mounted at /sd")
        except Exception as e:
            print("deck: SD mount failed:", e)

    # Audio / display / instrument / MPE from config.
    try:
        import deckcfg
        deckcfg.apply(cfg)
    except Exception as e:
        print("deck: apply failed:", e)

    # (ui_patch.apply() moved to the top of _boot: it must land before any
    # slow/interruptible step, and it still precedes the screensaver's
    # deferred tick, which was the original ordering constraint.)

    # Idle screensaver: dim then sleep the backlight (thresholds from Settings).
    try:
        import screensaver
        screensaver.start()
    except Exception as e:
        print("deck: screensaver failed:", e)

    # MIDI fleet router (Tulip + AMYboards).
    try:
        import forwarder
        forwarder.start()
    except Exception as e:
        print("deck: forwarder failed:", e)
        try:
            import decklog
            decklog.log_exc("boot: forwarder.start failed", e)
            decklog.flush()
        except Exception:
            pass

    # Touch calibration: RESTORE the saved 5-point result (calib.py writes
    # cfg['touch_delta']) -- the old hardcoded (1,1,0.8) silently threw the
    # user's calibration away on every reboot (review F-2).
    try:
        td = (cfg or {}).get('touch_delta') or (1, 1, 0.8)
        tulip.touch_delta(int(td[0]), int(td[1]), float(td[2]))
    except Exception:
        pass

    # Land on Welcome the first time, Home after that.
    try:
        app = 'home' if (cfg and cfg.get('setup_done')) else 'welcome'
        tulip.run(app)
    except Exception as e:
        print("deck: launch failed:", e)


_boot()
