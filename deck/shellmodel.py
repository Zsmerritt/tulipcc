# shellmodel.py -- pure, LVGL-free logic behind the deck home shell.
#
# Kept deliberately import-light (no lvgl / deckui / tulip) so it unit-tests
# under plain CPython. homeshell.py does the actual LVGL drawing on top of these
# helpers; the shell hands opaque panel handles to PanelStack so the stack
# bookkeeping is testable with plain objects.

# Patch-index category boundaries, mirroring instrument.py's CATS:
#   0..127  Juno-6      128..255  DX7      256+  Piano
_JUNO_END = 128
_DX_END = 256


def name_short(name):
    """Abbreviate an instrument/device name to fit a chip: 'Board A' -> 'A'."""
    if not name:
        return "?"
    if name.startswith('Board '):
        return name[6:].strip() or name
    return name


def patch_short(patch):
    """A compact, category-tagged preset label for a chip (full name shows on
    the Instrument screen)."""
    try:
        p = int(patch)
    except (TypeError, ValueError):
        p = 0
    if p < _JUNO_END:
        return "Juno%d" % p
    if p < _DX_END:
        return "DX%d" % (p - _JUNO_END)
    return "Piano"


def patch_name(patch):
    """The real preset name (from patches.py), e.g. 'A11 Brass Set 1' -- not the
    'Juno0' index token. Falls back to the compact tag if the table is missing."""
    try:
        from patches import patches
        return patches[int(patch)]
    except Exception:
        return patch_short(patch)


def patch_category(patch):
    """The engine family label for a patch: 'Juno-6' / 'DX7' / 'Piano'."""
    try:
        p = int(patch)
    except (TypeError, ValueError):
        p = 0
    if p < _JUNO_END:
        return "Juno-6"
    if p < _DX_END:
        return "DX7"
    return "Piano"


def device_name(device):
    """Display name for a device id: 'internal' -> 'Tulip', 0 -> 'Board A'."""
    if device == 'internal':
        return 'Tulip'
    try:
        return 'Board ' + chr(65 + int(device))
    except (TypeError, ValueError):
        return 'Board ?'


def chip_label(instr):
    # NOTE: separator is a plain ASCII space -- the montserrat font compiled into
    # the firmware has no U+00B7 middot (it renders as tofu), so keep chip text
    # to ASCII + lv.SYMBOL.* glyphs.
    return "%s %s" % (name_short(instr.get('name', '?')),
                      patch_short(instr.get('patch', 0)))


def chip_specs(instruments, active_id):
    """One descriptor per instrument: {id, label, active}. Drives the top-bar
    chips for any number of instruments; the active one (by id) is highlighted."""
    specs = []
    for instr in instruments or []:
        specs.append({'id': instr.get('id'), 'label': chip_label(instr),
                      'active': instr.get('id') == active_id})
    return specs


_KIT_NAMES = {384: 'TR-808', 385: 'TR-909', 386: 'Linn 9000', 387: 'MR-12',
              388: 'Tokyo Synthetics', 389: '80s Power', 390: 'Percussion'}


def instrument_sound(instr):
    """The sound label for an instrument: its kit (drums) or patch name (synth)."""
    if instr.get('type') == 'drums':
        kit = instr.get('kit', 384)
        if isinstance(kit, str):
            # synth kits are string keys ('synth:tr909_d') -- the int-keyed
            # table always missed and every synth kit read "TR-808 kit" on
            # the Home row (fresh-eyes F-1)
            try:
                import drums_kit
                return drums_kit.kit_name(kit) + " kit"
            except Exception:
                return kit + " kit"
        return _KIT_NAMES.get(kit, 'TR-808') + " kit"
    return patch_name(instr.get('patch', 0))


def instrument_summary(instr):
    """One-line rack-row summary of an instrument (ASCII only). The device
    name only appears for BOARD instruments -- on internal ones it stuttered
    the row title ("Tulip / Tulip ch1 ...", UX-REVIEW-7 L10)."""
    dev = instr.get('device', 'internal')
    core = "ch%d %s" % (instr.get('channel', 1), instrument_sound(instr))
    if dev == 'internal':
        return core
    return "%s %s" % (device_name(dev), core)


def device_meter(dev):
    """Rack/Devices meter for one device_list() entry: name, 'used/cap', a
    clamped 0..1 fraction and a connection flag."""
    cap = dev.get('capacity', 1) or 1
    load = dev.get('load', 0)
    frac = load / cap
    frac = 0.0 if frac < 0 else (1.0 if frac > 1 else frac)
    return {'name': dev.get('name', ''),
            'text': "%d/%d" % (load, dev.get('capacity', 0)),
            'fraction': frac, 'connected': bool(dev.get('connected'))}


def devices_subtitle(devices):
    """Board-count subtitle for the Devices tile ('2 boards' / 'internal only')."""
    boards = sum(1 for d in (devices or []) if d.get('kind') == 'amyboard')
    if boards == 0:
        return "internal only"
    return "%d board%s" % (boards, "" if boards == 1 else "s")


def device_chip_specs(devices):
    """One descriptor per device for the top-bar meter strip: name, 'used/cap'
    text, connection state, and a warn flag (>=85% load)."""
    specs = []
    for d in devices or []:
        m = device_meter(d)
        specs.append({'device': d.get('device'), 'name': m['name'],
                      'text': m['text'], 'connected': m['connected'],
                      'warn': m['fraction'] >= 0.85,
                      'capacity': d.get('capacity', 32)})
    return specs


# Screensaver dim/sleep choices: (label, seconds); 0 = Never.
SCREENSAVER_CHOICES = [("Never", 0), ("15s", 15), ("30s", 30), ("1m", 60),
                       ("2m", 120), ("5m", 300), ("10m", 600), ("30m", 1800)]


def screensaver_options_str():
    """The newline-joined labels for an lv.dropdown."""
    return "\n".join(label for label, _ in SCREENSAVER_CHOICES)


def screensaver_seconds(index):
    """Dropdown index -> seconds (0/out-of-range -> 0 = Never)."""
    if 0 <= index < len(SCREENSAVER_CHOICES):
        return SCREENSAVER_CHOICES[index][1]
    return 0


def screensaver_index(seconds):
    """Seconds -> dropdown index (unknown value -> 0 = Never)."""
    for i, (_, s) in enumerate(SCREENSAVER_CHOICES):
        if s == seconds:
            return i
    return 0


def open_panel_action(current_top_key, target_key):
    """When opening a panel identified by `target_key`, decide whether to rebuild
    the panel already on top (same key -- e.g. tapping a different chip while the
    same panel is open) or push a fresh one."""
    return 'rebuild' if current_top_key == target_key else 'push'


class PanelStack:
    """LVGL-free bookkeeping for a push/pop panel stack.

    Stores opaque panel handles + titles; push()/pop() report which handle to
    hide/reveal/delete so the shell can do the LVGL work while this class stays
    pure and testable. Depth 1 is the root panel -- Back is hidden there.
    """

    def __init__(self, root_title="Home"):
        self.root_title = root_title
        self._entries = []            # [{'handle': h, 'title': t, 'key': k}]

    def push(self, handle, title, key=None, builder=None):
        """Append a panel; return the handle the caller should HIDE (or None).

        `key` is an optional identity (e.g. 'instrument') used by the shell to
        decide rebuild-in-place vs push (see open_panel_action). `builder` is the
        panel's builder fn, kept so the shell can re-run it to refresh the panel
        when it's revealed by Back."""
        prev = self._entries[-1]['handle'] if self._entries else None
        self._entries.append({'handle': handle, 'title': title, 'key': key,
                              'builder': builder})
        return prev

    def pop(self):
        """Return (removed_handle, revealed_handle); (None, None) at/below root."""
        if len(self._entries) <= 1:
            return (None, None)
        removed = self._entries.pop()['handle']
        revealed = self._entries[-1]['handle']
        return (removed, revealed)

    def reset_to_root(self):
        """Pop everything above the root; return the removed handles to delete."""
        removed = []
        while len(self._entries) > 1:
            removed.append(self._entries.pop()['handle'])
        return removed

    def depth(self):
        return len(self._entries)

    def back_visible(self):
        return len(self._entries) > 1

    def top_handle(self):
        return self._entries[-1]['handle'] if self._entries else None

    def top_key(self):
        return self._entries[-1]['key'] if self._entries else None

    def top_builder(self):
        return self._entries[-1].get('builder') if self._entries else None

    def set_top(self, title, key=None, builder=None):
        """Update the top entry's title/key/builder in place (rebuild-in-place)."""
        if self._entries:
            self._entries[-1]['title'] = title
            self._entries[-1]['key'] = key
            self._entries[-1]['builder'] = builder

    def title(self):
        return self._entries[-1]['title'] if self._entries else self.root_title

    def crumb(self):
        """A breadcrumb trail once we're below the root, else ''."""
        if len(self._entries) <= 1:
            return ""
        return " / ".join(e['title'] for e in self._entries)
