#!/usr/bin/env python3
"""flashmode.py -- ping-pong dual-frequency flash update: device-side state.

Background: this board's 120MHz octal flash corrupts sustained multi-MB writes
under thermal drift (Espressif documents a >20 C post-power-on swing causing
random flash/PSRAM crashes). The fix (Shape A, see deck/PINGPONG.md and
deck/PINGPONG-FINDINGS.md) is to keep ONE OTA slot permanently holding an
80MHz "flasher" build and do all big writes booted into it, where writes are
thermally safe.

  ota_0 = FLASHER  (80MHz build; recovery anchor; runs "flash mode")
  ota_1 = PLAY     (120MHz build; the normal deck; the OTA target)

Update flow:
  play (120MHz)  -> request_update(): NVS flash_pending=1, set_boot(flasher),
                    reset
  flasher(80MHz) -> boot.py sees should_enter_flash_mode() -> enter_flash_mode():
                    Wi-Fi up, idle for the host to stream the new PLAY image
                    into the PLAY slot at 80MHz (flash_pingpong.py drives it)
                 -> finalize_to_play(): flash_pending cleared, set_boot(play),
                    reset -> back to 120MHz play

EVERYTHING here is fail-soft: the module must import on a host (no esp32) for
the unit tests, and on-device any missing NVS / Partition / Wi-Fi must degrade
to a normal boot rather than brick. Nothing in here is on the normal 120MHz
play path unless an update was explicitly armed.
"""

# --- slot identity (by LABEL, never "the other slot") -----------------------
# DECISION (Shape A convention): ota_0 is the 80MHz flasher, ota_1 is the
# 120MHz play image. Encoded here so every helper agrees. Changing the
# convention is a one-line edit here, not a hunt through the codebase.
FLASHER_LABEL = 'ota_0'
PLAY_LABEL = 'ota_1'

# --- NVS flag ----------------------------------------------------------------
NVS_NAMESPACE = 'deckboot'
KEY_FLASH_PENDING = 'flash_pending'

# --- build identity ----------------------------------------------------------
# The play image is the default; an UNKNOWN build is treated AS play so a
# missing/garbled marker can never trap the device in flash mode.
DEFAULT_FREQ = '120m'

# How long enter_flash_mode() idles waiting for a host before it gives up and
# auto-recovers to the (untouched) play slot. The host interrupts this idle
# with a ^C over serial to take control, so a real update is never bounded by
# it; this is purely the "nobody showed up" safety net so a stray flash_pending
# (or a Settings mis-tap) can't strand the deck.
FLASH_MODE_IDLE_TIMEOUT_S = 900


# ---------------------------------------------------------------------------
# build identity: is THIS running image the 80MHz flasher or the 120MHz play?
# ---------------------------------------------------------------------------
def flash_freq():
    """Best-effort compiled flash frequency of the RUNNING image, e.g. '80m'.

    Mechanism (chosen for lowest risk; see deck/PINGPONG.md "is_flasher_build"):

    1. If the firmware exposes a compiled binding `tulip.flash_freq()` (the
       CLEANEST single source of truth -- it returns the actual
       CONFIG_ESPTOOLPY_FLASHFREQ the image was built with), use it. No such
       binding exists yet; adding one is a tiny, guarded C change documented in
       PINGPONG.md, and this code picks it up automatically if it appears.
    2. Otherwise read a build-stamped Python constant from a frozen module
       `flashbuild.FLASH_FREQ`, which CI writes per artifact ('80m' for the
       flasher build, '120m'/absent for play). This needs no C change.
    3. If neither is present, fall back to the play default. Fail-soft: unknown
       => play => the deck boots normally and never hijacks itself.

    Deliberately does NOT look at which SLOT we booted from: during bring-up a
    play image may be flashed to either slot, so slot != build identity.
    """
    try:
        import tulip
        f = getattr(tulip, 'flash_freq', None)
        if callable(f):
            v = f()
            if v:
                return str(v)
    except Exception:
        pass
    try:
        import flashbuild
        return str(getattr(flashbuild, 'FLASH_FREQ', DEFAULT_FREQ))
    except Exception:
        return DEFAULT_FREQ


def is_flasher_build():
    """True iff the running image is the dedicated 80MHz flasher build."""
    try:
        return str(flash_freq()).startswith('80')
    except Exception:
        return False


# ---------------------------------------------------------------------------
# NVS flag helpers (all fail-soft; import-safe off-device)
# ---------------------------------------------------------------------------
def _nvs():
    from esp32 import NVS
    return NVS(NVS_NAMESPACE)


def get_flash_pending():
    """Return the flash_pending int (0 if unset/unavailable). Never raises."""
    try:
        return int(_nvs().get_i32(KEY_FLASH_PENDING))
    except Exception:
        # key-not-found raises OSError; no esp32 raises ImportError -- both
        # mean "not pending".
        return 0


def set_flash_pending(value=1):
    """Persist flash_pending. Returns True on success, False (never raises)."""
    try:
        nvs = _nvs()
        nvs.set_i32(KEY_FLASH_PENDING, int(value))
        nvs.commit()
        return True
    except Exception as e:
        _log('flashmode: set_flash_pending failed: %r' % (e,))
        return False


def clear_flash_pending():
    """Erase flash_pending (or set it to 0 if erase_key is unavailable)."""
    try:
        nvs = _nvs()
        try:
            nvs.erase_key(KEY_FLASH_PENDING)
        except Exception:
            nvs.set_i32(KEY_FLASH_PENDING, 0)
        nvs.commit()
        return True
    except Exception as e:
        _log('flashmode: clear_flash_pending failed: %r' % (e,))
        return False


# ---------------------------------------------------------------------------
# slot identification by label
# ---------------------------------------------------------------------------
def find_partition(label):
    """Return the app Partition with `label`, or None. Never raises."""
    try:
        from esp32 import Partition
        found = Partition.find(Partition.TYPE_APP, label=label)
        return found[0] if found else None
    except Exception:
        return None


def flasher_partition():
    return find_partition(FLASHER_LABEL)


def play_partition():
    return find_partition(PLAY_LABEL)


def running_label():
    """Label of the currently-running slot (e.g. 'ota_0'), or None."""
    try:
        from esp32 import Partition
        return Partition(Partition.RUNNING).info()[4]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# gate consulted by boot.py
# ---------------------------------------------------------------------------
def should_enter_flash_mode():
    """True only when THIS is the flasher build AND an update was armed.

    Both conditions must hold, so a play build with a stray flag boots
    normally, and a flasher build with no armed update boots normally (it is a
    fully functional -- just slower -- fallback deck).
    """
    try:
        return bool(is_flasher_build()) and bool(get_flash_pending())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# play-side action: arm an update and hand control to the flasher
# ---------------------------------------------------------------------------
def request_update():
    """Arm the ping-pong update: set flash_pending, set_boot(flasher).

    Does NOT reset -- the caller (host tool or arm_and_reboot) does that, so a
    caller can verify success first. Returns True only if BOTH the flag stuck
    and the flasher slot's boot was set; on any failure it leaves things as
    untouched as it can and returns False (never bricks, never resets).
    """
    fl = flasher_partition()
    if fl is None:
        _log('flashmode: no flasher slot (%s); update not armed' % FLASHER_LABEL)
        return False
    if not set_flash_pending(1):
        return False
    try:
        fl.set_boot()
    except Exception as e:
        _log('flashmode: set_boot(flasher) failed: %r' % (e,))
        # roll the flag back so a play image doesn't sit "armed" pointing at
        # itself
        clear_flash_pending()
        return False
    _log('flashmode: update armed -> next boot is the %s flasher' % FLASHER_LABEL)
    return True


def arm_and_reboot():
    """request_update() then reset. Returns False (no reset) if arming failed.

    The device entry point a Settings button or the REPL can call. On failure
    it does NOT reset, so a misconfigured device stays on the working play
    image instead of rebooting into nowhere.
    """
    if not request_update():
        return False
    _reset()
    return True


# ---------------------------------------------------------------------------
# flasher-side completion: hand control back to play
# ---------------------------------------------------------------------------
def finalize_to_play():
    """Clear flash_pending, set_boot(play), reset. Fail-soft at each step.

    Called by the host after the play slot is written+verified (and by
    enter_flash_mode's idle-timeout safety net). If the play slot can't be
    found we STILL clear the flag and reset -- clearing the flag means the next
    boot of the flasher won't re-enter flash mode, which is the safe direction.
    """
    clear_flash_pending()
    play = play_partition()
    if play is not None:
        try:
            play.set_boot()
        except Exception as e:
            _log('flashmode: set_boot(play) failed: %r' % (e,))
    else:
        _log('flashmode: no play slot (%s); rebooting without set_boot'
             % PLAY_LABEL)
    _reset()
    return True


# ---------------------------------------------------------------------------
# the flash-mode boot routine (called from boot.py)
# ---------------------------------------------------------------------------
def enter_flash_mode(idle_timeout_s=None):
    """Bring up Wi-Fi and idle so the host can stream the new play image.

    Deliberately does NOT launch the deck UI -- flash mode is a minimal,
    self-contained state. Sequence:

      1. print a clear banner (and a best-effort on-screen notice)
      2. bring up Wi-Fi from the saved credentials (so the host can find us by
         tulip.ip() and reach us over HTTP)
      3. idle. The host (flash_pingpong.py) interrupts this idle with ^C over
         serial, drives the write-verify pull into the PLAY slot, then calls
         finalize_to_play(). If NO host shows up within idle_timeout_s we
         auto-recover to the untouched play image so a stray flag can't strand
         the deck.

    KeyboardInterrupt is intentionally NOT caught: it is exactly how the host
    grabs the REPL. Only ordinary Exceptions are swallowed (fail-soft).
    """
    if idle_timeout_s is None:
        idle_timeout_s = FLASH_MODE_IDLE_TIMEOUT_S
    _banner()
    _bring_up_wifi()
    _idle_for_host(idle_timeout_s)
    # Only reached if the host never took over: recover to play.
    _log('flashmode: no host within %ss; auto-recovering to play'
         % idle_timeout_s)
    finalize_to_play()


def _idle_for_host(idle_timeout_s):
    import time
    t0 = time.time()
    beat = 0
    while time.time() - t0 < idle_timeout_s:
        # A busy-ish sleep so a host ^C lands promptly. NOTE: do NOT wrap this
        # in try/except -- KeyboardInterrupt must propagate to the REPL.
        time.sleep(1)
        beat += 1
        if beat % 15 == 0:
            _log('flashmode: waiting for host... (%ss)' % beat)


def _bring_up_wifi():
    try:
        import tulip
        import deckcfg
        cfg = deckcfg.load() or {}
        ssid = cfg.get('wifi_ssid', '')
        if ssid:
            tulip.wifi(ssid, cfg.get('wifi_pass', ''))
            _log('flashmode: Wi-Fi join requested for %r' % ssid)
        else:
            _log('flashmode: no saved Wi-Fi; host must use the serial path')
    except Exception as e:
        _log('flashmode: Wi-Fi bring-up failed: %r' % (e,))


def _banner():
    msg = ('FLASH MODE (80MHz) -- armed for a safe firmware update.\n'
           'Run: python deck/flash_pingpong.py <new-play.bin>\n'
           'The deck will return to normal play when the update finishes.')
    _log(msg)
    # Best-effort on-screen notice via the existing progress modal; never fatal.
    try:
        import fwprogress
        fwprogress.show(100, title='Flash mode (safe update)')
        fwprogress.stage('Waiting for host... do not power off.')
    except Exception:
        pass


# ---------------------------------------------------------------------------
# small utilities
# ---------------------------------------------------------------------------
def _reset():
    try:
        import machine
        machine.reset()
    except Exception as e:
        _log('flashmode: reset failed: %r' % (e,))


def _log(msg):
    # Prefer the deck log if present; always echo to the console. Never raises.
    try:
        import decklog
        decklog.log(msg)
    except Exception:
        pass
    try:
        print(msg)
    except Exception:
        pass
