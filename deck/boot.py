# boot.py -- Tulip startup (deck edition).
#
# Lives in /user (survives tulip.upgrade()). Everything is wrapped in try/except
# so a failure here can never stop you reaching the REPL. It:
#   * puts /user on the import path so the deck apps are runnable anywhere
#   * restores audio/display/instrument/MPE from /user/deck_config.json
#   * installs ui_patch (bigger task bar + launcher menu with the deck apps)
#   * lands on the Welcome screen (first boot) or the Home launcher

import sys
if '/user' not in sys.path:
    sys.path.append('/user')


def _boot():
    import tulip

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
        try:
            tulip.wifi(ssid, cfg.get('wifi_pass', ''))
        except Exception as e:
            print("wifi failed:", e)

    # Audio / display / instrument / MPE from config.
    try:
        import deckcfg
        deckcfg.apply(cfg)
    except Exception as e:
        print("deck: apply failed:", e)

    # Bigger task-bar buttons + launcher menu with the deck apps. Apply BEFORE
    # starting any background loop (the screensaver's deferred tick) -- otherwise
    # that tick can fire mid-import of ui_patch and intermittently leave the patch
    # uninstalled (Home task bar not stripped).
    try:
        import ui_patch
        ui_patch.apply()
    except Exception as e:
        print("deck: ui_patch failed:", e)

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

    # Touch calibration (adjust with run('calibrate') or Settings).
    try:
        tulip.touch_delta(1, 1, 0.8)
    except Exception:
        pass

    # Land on Welcome the first time, Home after that.
    try:
        app = 'home' if (cfg and cfg.get('setup_done')) else 'welcome'
        tulip.run(app)
    except Exception as e:
        print("deck: launch failed:", e)


_boot()
