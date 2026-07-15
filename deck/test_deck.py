# test_deck.py -- host-side unit tests for the deck's pure logic.
#
# The deck runs on the Tulip (MicroPython), but deckcfg (config model) and
# forwarder (MIDI routing) are plain Python. We mock the hardware modules
# (tulip / amy / synth / midi) so these can run under CPython:
#
#   pytest deck/test_deck.py
#
# UI modules (deckui/home/settings/...) are LVGL-heavy and not covered here.

import os
import sys
import types
import importlib
import tempfile

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _install_hw_mocks():
    """Fake tulip/amy/synth/midi so deck modules import on a host."""
    sent = []  # captured tulip.midi_out calls: (bytes, device)

    tulip = types.ModuleType('tulip')
    tulip.midi_out = lambda data, device=None: sent.append((bytes(data), device))
    # Present on any firmware whose midi_out takes a device arg -- the forwarder
    # probes for it (hasattr) instead of raising TypeError per message.
    tulip.num_midi_devices = lambda: 1
    tulip.defer = lambda fn, arg, ms: None
    tulip.board = lambda: 'DESKTOP'
    tulip.screen_size = lambda: (1024, 600)
    tulip._sent = sent

    def _run(name):
        # Mirror the firmware: running an already-running app just presents it
        # (leaving it in running_apps); otherwise it's a no-op under the mocks.
        u = sys.modules.get('ui')
        if u is not None and name in u.running_apps:
            u.running_apps[name].present()
    tulip.run = _run
    sys.modules['tulip'] = tulip

    amy = types.ModuleType('amy')
    amy._sends = []                 # recorded amy.send(**kwargs)
    amy._fx = []                    # recorded (bus, kwargs) FX calls
    amy.send = lambda **k: amy._sends.append(k)
    amy.volume = lambda *a, **k: None
    amy.reset = lambda *a, **k: None
    amy.reverb = lambda **k: amy._fx.append(('reverb', k))
    amy.chorus = lambda **k: amy._fx.append(('chorus', k))
    amy.echo = lambda **k: amy._fx.append(('echo', k))
    sys.modules['amy'] = amy

    synth = types.ModuleType('synth')

    class PatchSynth:
        instances = []
        _next = 16

        def __init__(self, patch=0, num_voices=10, **k):
            self.patch = patch
            self.num_voices = num_voices
            self.on = []
            self.released = False
            self.inited = False
            self.synth = PatchSynth._next     # the AMY synth number
            PatchSynth._next += 1
            PatchSynth.instances.append(self)

        def deferred_init(self):              # allocates the AMY instrument
            self.inited = True

        def note_on(self, note, vel, **k):
            self.on.append(note)

        def note_off(self, note, **k):
            if note in self.on:
                self.on.remove(note)

        def release(self):
            self.released = True

        def set_channel(self, c):
            self.channel = c

    synth.PatchSynth = PatchSynth
    synth.OscSynth = PatchSynth
    synth.DrumSynth = PatchSynth
    sys.modules['synth'] = synth

    midi = types.ModuleType('midi')

    class _Config:
        def __init__(self):
            self.channels = {}

        def add_synth(self, synth=None, patch=None, channel=1, num_voices=None):
            self.channels[channel] = synth or PatchSynth(patch=patch or 0)
            return self.channels[channel]

        def release_synth_for_channel(self, channel):
            self.channels.pop(channel, None)

        def get_synth(self, channel):
            return self.channels.get(channel)

    midi.config = _Config()
    midi._mpe_calls = []
    midi.configure_mpe = lambda *a, **k: midi._mpe_calls.append((a, k))
    midi.MPE_MEMBER_CHANNELS = set()
    midi.add_callback = lambda fn: None
    sys.modules['midi'] = midi

    return sent


def _install_ui_mocks():
    """Fake ui + lvgl so ui_patch imports and its task-bar/quit patches run.

    Mirrors just enough of the frozen ui.UIScreen (firmware) for the deck's
    Home-as-root monkeypatch to exercise: draw_task_bar draws a quit button on
    apps / a launcher on the repl, and screen_quit_callback cleans up then
    presents ui.repl_screen (which the patch retargets at Home).
    """
    class _NS:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    lv = types.ModuleType('lvgl')
    lv.SYMBOL = _NS(CLOSE='x', HOME='home', AUDIO='audio', SETTINGS='set',
                    LIST='list', DIRECTORY='dir', NEXT='next', FILE='file',
                    KEYBOARD='kb', POWER='pwr', SHUFFLE='shuf', WIFI='wifi')
    lv.PART = _NS(MAIN=0)
    lv.ALIGN = _NS(TOP_RIGHT=1, BOTTOM_RIGHT=2, OUT_LEFT_MID=3, TOP_LEFT=4)
    lv.EVENT = _NS(CLICKED=1, VALUE_CHANGED=2)
    lv.TEXT_ALIGN = _NS(CENTER=1)
    lv.font_montserrat_24 = object()
    lv.font_montserrat_18 = object()
    lv.font_montserrat_12 = object()

    class _Label:
        def __init__(self):
            self.text = None
        def set_style_text_font(self, *a): pass
        def set_text(self, t=None, *a): self.text = t
        def set_style_text_align(self, *a): pass
        def center(self): pass

    class _Button:
        def __init__(self):
            self.deleted = False
            self._label = _Label()
        def delete(self): self.deleted = True
        def set_width(self, *a): pass
        def set_height(self, *a): pass
        def set_style_bg_color(self, *a): pass
        def set_style_radius(self, *a): pass
        def align_to(self, *a): pass
        def add_event_cb(self, *a): pass
        def get_child(self, i): return self._label

    lv.button = lambda parent=None: _Button()
    lv.label = lambda parent=None: _Label()
    sys.modules['lvgl'] = lv

    ui = types.ModuleType('ui')
    ui.running_apps = {}
    ui.current_app_string = 'repl'
    ui.repl_screen = None
    ui.lv_launcher = None
    ui.keyboard = lambda: None
    ui.launcher = lambda *a, **k: None
    ui.pal_to_lv = lambda pal: pal

    class UIScreen:
        first_run = False

        def __init__(self, name):
            self.name = name
            self.group = object()
            self.quit_button = None
            self.alttab_button = None
            self.launcher_button = None
            self.home_button = None
            self.running = True
            self.presented = 0
            ui.running_apps[name] = self

        def draw_task_bar(self):        # firmware-like original
            # Every screen gets the shuffle (alttab) app-switcher; apps also get
            # a quit/power button, the repl gets a corner launcher instead.
            self.alttab_button = _Button()
            if self.name != 'repl':
                self.quit_button = _Button()
            else:
                self.launcher_button = _Button()

        def screen_quit_callback(self, e):   # firmware-like original
            if self.name != 'repl':
                self.running = False
                try:
                    del ui.running_apps[self.name]
                except KeyError:
                    pass
                ui.repl_screen.present()

        def present(self):
            ui.current_app_string = self.name
            self.presented += 1

    ui.UIScreen = UIScreen
    sys.modules['ui'] = ui
    return ui, lv


@pytest.fixture
def uipatch():
    """ui_patch imported against the ui/lvgl mocks, with apply() installed."""
    _install_hw_mocks()
    ui, lv = _install_ui_mocks()
    sys.modules.pop('ui_patch', None)
    ui_patch = importlib.import_module('ui_patch')
    ui_patch._installed = False
    ui_patch.apply()
    return ui, ui_patch


@pytest.fixture
def deck(tmp_path):
    """Fresh deckcfg + forwarder with hardware mocked and a temp config file."""
    _install_hw_mocks()
    for m in ('deckcfg', 'forwarder'):
        sys.modules.pop(m, None)
    deckcfg = importlib.import_module('deckcfg')
    deckcfg.PATH = str(tmp_path / 'deck_config.json')
    deckcfg._state.clear()
    forwarder = importlib.import_module('forwarder')
    forwarder._state.update({'on': False, 'synths': {}, 'routes': {},
                             'notes': {}, 'registered': False})
    return deckcfg, forwarder


# --- deckcfg: config model ---
def test_defaults_single_internal_instrument(deck):
    deckcfg, _ = deck
    cfg = deckcfg.load()
    assert len(cfg['instruments']) == 1
    instr = cfg['instruments'][0]
    assert instr['device'] == 'internal'
    assert instr['channel'] == 1
    assert instr['mpe'] == {'enabled': False, 'members': 15, 'bend': 48,
                            'expression': True}
    assert cfg['active_instrument'] == instr['id']


def test_migrates_old_single_instrument_config(deck):
    deckcfg, _ = deck
    import json
    with open(deckcfg.PATH, 'w') as f:
        json.dump({'patch': 143, 'num_voices': 6, 'midi_channel': 4,
                   'mpe': True, 'volume': 7}, f)
    instr = deckcfg.instruments()[0]
    assert instr['patch'] == 143
    assert instr['num_voices'] == 6
    assert instr['channel'] == 4          # midi_channel -> channel
    assert instr['mpe']['enabled'] is True
    assert deckcfg.get('volume') == 7     # device settings preserved


def test_set_and_active_instrument(deck):
    deckcfg, _ = deck
    board = deckcfg.add_instrument(device=0, channel=2)
    deckcfg.set_instrument(board['id'], 'patch', 200)
    deckcfg.set_active_instrument(board['id'])
    assert deckcfg.get_instrument(board['id'])['patch'] == 200
    assert deckcfg.active_instrument() == board['id']


def test_set_instrument_mpe_nested(deck):
    deckcfg, _ = deck
    iid = deckcfg.instruments()[0]['id']
    deckcfg.set_instrument_mpe(iid, 'members', 10)
    deckcfg.set_instrument_mpe(iid, 'enabled', True)
    m = deckcfg.get_instrument(iid)['mpe']
    assert m['members'] == 10 and m['enabled'] is True


def test_remove_reassigns_active(deck):
    deckcfg, _ = deck
    board = deckcfg.add_instrument(device=0, channel=2)
    deckcfg.set_active_instrument(board['id'])
    deckcfg.remove_instrument(board['id'])
    # active falls back to a surviving instrument
    assert deckcfg.active_instrument() == deckcfg.instruments()[0]['id']


# --- deckcfg: instrument model + migration ---
def test_migrates_instances_to_instruments(deck):
    deckcfg, _ = deck
    import json
    with open(deckcfg.PATH, 'w') as f:
        json.dump({'instances': [
            {'kind': 'internal', 'channel': 1, 'patch': 5, 'num_voices': 6,
             'mpe': True, 'mpe_members': 10, 'mpe_bend': 24,
             'mpe_expression': False},
            {'kind': 'amyboard', 'device': 0, 'channel': 2, 'patch': 143,
             'num_voices': 8},
        ], 'active_instance': 1}, f)
    instr = deckcfg.instruments()
    assert len(instr) == 2
    assert instr[0]['device'] == 'internal'
    assert (instr[0]['channel'], instr[0]['patch'], instr[0]['num_voices']) == (1, 5, 6)
    assert instr[0]['mpe'] == {'enabled': True, 'members': 10, 'bend': 24,
                               'expression': False}
    assert instr[1]['device'] == 0          # board USB device index preserved
    assert (instr[1]['channel'], instr[1]['patch']) == (2, 143)
    # the old active index migrates to the matching instrument id
    assert deckcfg.active_instrument() == instr[1]['id']


def test_add_and_remove_instrument(deck):
    deckcfg, _ = deck
    base = len(deckcfg.instruments())              # 1 (default internal)
    board = deckcfg.add_instrument(device=0, channel=2)
    assert len(deckcfg.instruments()) == base + 1
    assert board['id'] != deckcfg.instruments()[0]['id']   # unique id
    deckcfg.remove_instrument(board['id'])
    assert len(deckcfg.instruments()) == base
    # removing the last instrument leaves one default (never zero)
    deckcfg.remove_instrument(deckcfg.instruments()[0]['id'])
    assert len(deckcfg.instruments()) == 1


def test_device_load_and_on_channel(deck):
    deckcfg, _ = deck
    iid0 = deckcfg.instruments()[0]['id']
    deckcfg.set_instrument(iid0, 'num_voices', 12)   # internal ch1, 12 voices
    deckcfg.add_instrument(device='internal', channel=1, num_voices=8)  # layer
    deckcfg.add_instrument(device=0, channel=2, num_voices=16)          # board
    assert deckcfg.device_load('internal') == 20    # 12 + 8 on the Tulip AMY
    assert deckcfg.device_load(0) == 16
    assert len(deckcfg.instruments_on_channel(1)) == 2   # two instruments layered
    assert len(deckcfg.instruments_on_channel(2)) == 1


def test_device_list_reports_internal_and_boards(deck):
    deckcfg, _ = deck
    sys.modules['tulip'].num_midi_devices = lambda: 2
    deckcfg.add_instrument(device=0, channel=2, num_voices=10)
    devs = deckcfg.device_list()
    by_dev = {d['device']: d for d in devs}
    assert 'internal' in by_dev and 0 in by_dev and 1 in by_dev
    assert by_dev['internal']['capacity'] == deckcfg.DEVICE_CAPACITY
    assert by_dev[0]['load'] == 10
    assert by_dev[1]['load'] == 0


# --- forwarder: per-instrument layering router ---
def test_layering_routes_to_all_instruments_on_channel(deck):
    deckcfg, forwarder = deck
    deckcfg.add_instrument(device='internal', channel=1)   # 2nd internal on ch1
    forwarder.start()
    forwarder._route((0x90, 60, 100))
    synths = list(forwarder._state['synths'].values())
    assert len(synths) == 2                          # both internal synths exist
    assert all(60 in s.on for s in synths)           # both played the note (layer)
    assert len(forwarder._state['notes'][(1, 60)]) == 2


def test_internal_and_board_dispatch(deck):
    deckcfg, forwarder = deck
    deckcfg.add_instrument(device=0, channel=2)      # board on ch2
    sent = _install_and_reset_sent()
    forwarder.start()
    sent.clear()                                     # ignore the board's patch PC
    # ch1 has a SINGLE internal instrument -> C-OWNED: AMY's C layer plays the
    # notes (synth number == channel); Python must NOT double-play it.
    assert 1 in forwarder._state['c_channels']
    forwarder._route((0x90, 60, 100))
    assert sent == []
    assert list(forwarder._state['synths'].values())[0].on == []
    assert forwarder._state['notes'] == {}
    forwarder._route((0x91, 64, 100))                # ch2 -> board device 0
    assert len(sent) == 1
    data, device = sent[0]
    assert device == 0 and data[0] == 0x91 and data[1] == 64


def test_note_off_releases_internal_voices(deck):
    deckcfg, forwarder = deck
    # LAYERED channel (2 internals on ch1) -> Python-routed note tracking
    deckcfg.add_instrument(device='internal', channel=1)
    forwarder.start()
    assert 1 not in forwarder._state['c_channels']
    forwarder._route((0x90, 62, 100))
    syns = list(forwarder._state['synths'].values())
    assert forwarder._state['notes'] and all(62 in s.on for s in syns)
    forwarder._route((0x80, 62, 0))
    assert forwarder._state['notes'] == {}
    assert all(62 not in s.on for s in syns)


def test_note_on_velocity_zero_is_note_off(deck):
    deckcfg, forwarder = deck
    forwarder.start()
    forwarder._route((0x90, 64, 100))
    forwarder._route((0x90, 64, 0))                  # running-status note off
    assert forwarder._state['notes'] == {}


def test_board_note_off_forwarded_raw(deck):
    deckcfg, forwarder = deck
    deckcfg.add_instrument(device=0, channel=2)
    sent = _install_and_reset_sent()
    forwarder.start()
    sent.clear()
    forwarder._route((0x91, 64, 100))                # note on -> board
    forwarder._route((0x81, 64, 0))                  # note off -> board (raw)
    assert [d for _, d in sent] == [0, 0]
    assert sent[-1][0][0] == 0x81


def _install_and_reset_sent():
    sent = sys.modules['tulip']._sent
    sent.clear()
    return sent


def test_cc_and_bend_forwarded_to_board_only(deck):
    # CC / pitch bend on a board channel go to that board (with its device
    # index); on an internal-only channel they go nowhere (internal CC handling
    # is a later milestone).
    deckcfg, forwarder = deck
    deckcfg.add_instrument(device=0, channel=2)
    sent = _install_and_reset_sent()
    forwarder.start()
    sent.clear()
    forwarder._route((0xB1, 74, 100))              # CC74 on ch2 -> board 0
    forwarder._route((0xE1, 0, 64))                # bend on ch2 -> board 0
    assert [d for _, d in sent] == [0, 0]
    assert sent[0][0][0] == 0xB1 and sent[1][0][0] == 0xE1
    sent.clear()
    forwarder._route((0xB0, 74, 100))              # CC on ch1 (internal only)
    assert sent == []


# --- ui_patch: Home-as-root task bar + quit routing ---
def test_quit_target_and_hide_helpers(uipatch):
    _, ui_patch = uipatch
    # the root has no quit button; other apps (incl. the repl) keep one
    assert ui_patch._should_hide_quit('home') is True
    assert ui_patch._should_hide_quit('drums') is False
    assert ui_patch._should_hide_quit('repl') is False
    # normal apps return to Home, falling back to the repl if Home isn't running
    assert ui_patch._quit_target('drums', {'repl': 1, 'home': 1, 'drums': 1}) == 'home'
    assert ui_patch._quit_target('drums', {'repl': 1, 'drums': 1}) == 'repl'
    # neither the root nor the repl may be quit
    assert ui_patch._quit_target('home', {'repl': 1, 'home': 1}) is None
    assert ui_patch._quit_target('repl', {'repl': 1}) is None


def test_home_task_bar_has_no_quit_button(uipatch):
    ui, _ = uipatch
    home = ui.UIScreen('home')
    home.draw_task_bar()                       # patched
    assert home.quit_button is None            # root: power button stripped
    drums = ui.UIScreen('drums')
    drums.draw_task_bar()                       # patched
    assert drums.quit_button is not None       # ordinary apps keep it


def test_repl_task_bar_gets_home_button(uipatch):
    ui, _ = uipatch
    repl = ui.UIScreen('repl')
    repl.draw_task_bar()                        # patched
    assert repl.home_button is not None         # Terminal has a way back to Home


def test_home_task_bar_strips_all_firmware_buttons(uipatch):
    ui, _ = uipatch
    home = ui.UIScreen('home')
    home.draw_task_bar()                         # patched
    # Home owns its own top-bar nav, so no firmware buttons survive on it.
    assert home.quit_button is None
    assert home.alttab_button is None            # shuffle/app-switcher gone
    assert home.launcher_button is None


# --- Phase 1: standalone-app Back / keep-alive task bar ---
class _Click:
    """A minimal lv event that reports a CLICKED code (matches the ui mock)."""
    def get_code(self):
        return sys.modules['lvgl'].EVENT.CLICKED


def test_keep_alive_membership(uipatch):
    _, ui_patch = uipatch
    assert 'drums' in ui_patch.KEEP_ALIVE
    assert 'settings' not in ui_patch.KEEP_ALIVE
    assert 'files' not in ui_patch.KEEP_ALIVE


def test_standalone_app_taskbar_is_back_only(uipatch):
    ui, ui_patch = uipatch
    s = ui.UIScreen('settings')
    s.draw_task_bar()                              # patched
    assert s.alttab_button is None                 # shuffle removed
    assert s.quit_button is not None               # quit repurposed as Back
    assert s.quit_button.get_child(0).text == "%s Back" % ui_patch._sym('LEFT', "<")
    assert getattr(s, 'back_button', None) is None  # no separate Back button
    assert s._quit_is_back is True


def test_standalone_back_action_frees_and_returns_home(uipatch):
    ui, _ = uipatch
    repl = ui.UIScreen('repl'); ui.repl_screen = repl
    home = ui.UIScreen('home')
    s = ui.UIScreen('settings'); s.draw_task_bar()
    # Back is the relabeled quit button; its action is still screen_quit_callback
    s.screen_quit_callback(None)
    assert 'settings' not in ui.running_apps        # freed, like the old quit
    assert home.presented == 1                      # landed on Home


def test_keepalive_app_has_back_and_power(uipatch):
    ui, _ = uipatch
    d = ui.UIScreen('drums')
    d.draw_task_bar()                               # patched
    assert d.alttab_button is None                  # shuffle removed
    assert d.quit_button is not None                # Power kept
    assert d.back_button is not None                # separate Back added
    assert d._quit_is_back is False


def test_keepalive_back_keeps_alive_when_busy(uipatch):
    ui, ui_patch = uipatch
    repl = ui.UIScreen('repl'); ui.repl_screen = repl
    home = ui.UIScreen('home')
    d = ui.UIScreen('drums')
    d.drum_seq = types.SimpleNamespace(events=[('kick', 0)])
    d.draw_task_bar()
    assert ui_patch._back_keeps_alive('drums', d) is True
    ui_patch._make_back_cb(d)(_Click())
    assert 'drums' in ui.running_apps               # kept playing in background
    assert home.presented == 1                      # but Home is shown


def test_keepalive_back_quits_when_empty(uipatch):
    ui, ui_patch = uipatch
    repl = ui.UIScreen('repl'); ui.repl_screen = repl
    home = ui.UIScreen('home')
    d = ui.UIScreen('drums')
    d.drum_seq = types.SimpleNamespace(events=[])
    d.draw_task_bar()
    assert ui_patch._back_keeps_alive('drums', d) is False
    ui_patch._make_back_cb(d)(_Click())
    assert 'drums' not in ui.running_apps           # idle -> freed
    assert home.presented == 1


def test_keepalive_busy_probe_guards_missing_attrs(uipatch):
    _, ui_patch = uipatch
    ui, _lv = sys.modules['ui'], sys.modules['lvgl']
    d = ui.UIScreen('drums')                        # no drum_seq attribute at all
    assert ui_patch._back_keeps_alive('drums', d) is False
    assert ui_patch._back_keeps_alive('settings', d) is False  # no probe


# --- shellmodel: pure home-shell logic (chips, meters, panel stack) ---
def _instruments(n):
    """Instrument dicts shaped like deckcfg.default_instrument(), for shellmodel."""
    out = []
    for i in range(n):
        if i == 0:
            out.append({'id': 0, 'name': 'Tulip', 'device': 'internal',
                        'channel': 1, 'patch': 0})
        else:
            out.append({'id': i, 'name': 'Board ' + chr(64 + i),
                        'device': i - 1, 'channel': 1 + i, 'patch': 128 + i})
    return out


def _devices(boards):
    devs = [{'device': 'internal', 'name': 'Tulip', 'kind': 'internal',
             'connected': True, 'capacity': 32, 'load': 0}]
    for d in range(boards):
        devs.append({'device': d, 'name': 'Board ' + chr(65 + d),
                     'kind': 'amyboard', 'connected': True, 'capacity': 32,
                     'load': 0})
    return devs


def test_chip_specs_single_instrument():
    import shellmodel as sm
    specs = sm.chip_specs(_instruments(1), 0)
    assert len(specs) == 1
    assert specs[0]['id'] == 0
    assert specs[0]['active'] is True
    assert specs[0]['label'].startswith('Tulip')


def test_chip_specs_active_by_id():
    import shellmodel as sm
    specs = sm.chip_specs(_instruments(3), 2)      # active id = 2
    assert [s['active'] for s in specs] == [False, False, True]
    assert specs[1]['label'].startswith('A')       # 'Board A' -> 'A'
    assert specs[2]['label'].startswith('B')


def test_patch_short_categorizes_by_index():
    import shellmodel as sm
    assert sm.patch_short(0) == 'Juno0'
    assert sm.patch_short(130) == 'DX2'
    assert sm.patch_short(256) == 'Piano'
    assert sm.patch_short(None) == 'Juno0'        # guards bad input


def test_device_name_and_instrument_summary():
    import shellmodel as sm
    assert sm.device_name('internal') == 'Tulip'
    assert sm.device_name(0) == 'Board A'
    assert sm.device_name(1) == 'Board B'
    summ = sm.instrument_summary({'device': 1, 'channel': 2, 'patch': 130})
    assert summ == 'Board B  ch2  DX2'
    assert all(ord(c) < 128 for c in summ)


def test_device_meter_clamps_fraction():
    import shellmodel as sm
    m = sm.device_meter({'name': 'Tulip', 'load': 16, 'capacity': 32,
                         'connected': True})
    assert m['text'] == '16/32' and abs(m['fraction'] - 0.5) < 1e-9
    assert m['connected'] is True
    over = sm.device_meter({'name': 'x', 'load': 99, 'capacity': 32})
    assert over['fraction'] == 1.0                 # clamped to 1
    zero = sm.device_meter({'name': 'x', 'load': 0, 'capacity': 0})
    assert zero['fraction'] == 0.0                 # guards divide-by-zero


def test_devices_subtitle_counts_boards():
    import shellmodel as sm
    assert sm.devices_subtitle(_devices(0)) == 'internal only'
    assert sm.devices_subtitle(_devices(1)) == '1 board'
    assert sm.devices_subtitle(_devices(2)) == '2 boards'


def test_device_chip_specs():
    import shellmodel as sm
    devs = [
        {'device': 'internal', 'name': 'Tulip', 'kind': 'internal',
         'connected': True, 'capacity': 32, 'load': 30},     # 30/32 -> hot
        {'device': 0, 'name': 'Board A', 'kind': 'amyboard',
         'connected': True, 'capacity': 32, 'load': 4},
        {'device': 1, 'name': 'Board B', 'kind': 'amyboard',
         'connected': False, 'capacity': 32, 'load': 0},
    ]
    specs = sm.device_chip_specs(devs)
    assert [s['device'] for s in specs] == ['internal', 0, 1]
    assert specs[0]['text'] == '30/32' and specs[0]['warn'] is True
    assert specs[1]['warn'] is False
    assert specs[2]['connected'] is False


def test_screensaver_option_mapping():
    import shellmodel as sm
    assert sm.screensaver_seconds(0) == 0          # Never
    assert sm.screensaver_seconds(3) == 60         # 1m
    assert sm.screensaver_seconds(99) == 0         # out of range -> Never
    assert sm.screensaver_index(0) == 0
    assert sm.screensaver_index(300) == 5          # 5m
    assert sm.screensaver_index(999) == 0          # unknown -> Never
    labels = sm.screensaver_options_str().split("\n")
    assert labels[0] == "Never" and "1m" in labels and len(labels) == 8


def test_panel_stack_submenu_nav():
    import shellmodel as sm
    # Home -> System submenu -> Back -> Home (the C.2 home reorg nav).
    st = sm.PanelStack('Home')
    home, system = object(), object()
    st.push(home, 'Home')
    st.push(system, 'System', key='system')
    assert st.back_visible() is True and st.title() == 'System'
    removed, revealed = st.pop()
    assert removed is system and revealed is home
    assert st.back_visible() is False


def test_panel_stack_push_pop_back():
    import shellmodel as sm
    st = sm.PanelStack('Home')
    root, p1, p2 = object(), object(), object()
    assert st.push(root, 'Home') is None          # nothing to hide at the root
    assert st.back_visible() is False
    assert st.depth() == 1
    assert st.push(p1, 'Fleet') is root            # hide the root when drilling in
    assert st.back_visible() is True
    assert st.title() == 'Fleet'
    assert st.crumb() == 'Home / Fleet'
    st.push(p2, 'Board A')
    removed, revealed = st.pop()
    assert removed is p2 and revealed is p1        # reveal the panel beneath
    assert st.depth() == 2
    st.pop()                                       # back to root
    assert st.back_visible() is False
    assert st.pop() == (None, None)                # can't pop below the root
    assert st.depth() == 1


def test_panel_stack_reset_to_root():
    import shellmodel as sm
    st = sm.PanelStack('Home')
    root = object()
    st.push(root, 'Home')
    st.push(object(), 'A')
    st.push(object(), 'B')
    removed = st.reset_to_root()
    assert len(removed) == 2                        # both panels above root
    assert st.depth() == 1
    assert st.top_handle() is root


def test_chip_label_is_ascii_only():
    import shellmodel as sm
    # the montserrat font has no middot; chip text must stay ASCII (+ symbols)
    label = sm.chip_label({'name': 'Tulip', 'kind': 'internal', 'patch': 0})
    assert label == 'Tulip Juno0'
    assert all(ord(ch) < 128 for ch in label)


def test_open_panel_action_push_vs_rebuild():
    import shellmodel as sm
    # tapping a chip with the Instrument panel already on top rebuilds it in
    # place (Back unchanged); from anywhere else it pushes a new panel.
    assert sm.open_panel_action('instrument', 'instrument') == 'rebuild'
    assert sm.open_panel_action(None, 'instrument') == 'push'
    assert sm.open_panel_action('fleet', 'instrument') == 'push'


def test_panel_stack_tracks_keys_for_rebuild():
    import shellmodel as sm
    st = sm.PanelStack('Home')
    st.push(object(), 'Home')                       # root, no key
    assert st.top_key() is None
    st.push(object(), 'Tulip  -  Instrument', key='instrument')
    assert st.top_key() == 'instrument'
    # rebuild-in-place: same handle, updated title/key
    handle = st.top_handle()
    st.set_top('Board A  -  Instrument', key='instrument')
    assert st.top_handle() is handle
    assert st.title() == 'Board A  -  Instrument'
    assert st.top_key() == 'instrument'


def test_panel_stack_multilevel_mpe_expression():
    import shellmodel as sm
    # grid -> MPE panel -> per-note expression sub-panel -> back -> back -> grid,
    # popping exactly one level at a time.
    st = sm.PanelStack('Home')
    grid, mpe_panel, expr = object(), object(), object()
    st.push(grid, 'Home')
    st.push(mpe_panel, 'Tulip  -  MPE', key='mpe')
    st.push(expr, 'Per-note expression', key='mpe_expr')
    assert st.depth() == 3
    assert st.back_visible() is True
    assert st.crumb() == 'Home / Tulip  -  MPE / Per-note expression'
    # back once: expression -> MPE panel
    removed, revealed = st.pop()
    assert removed is expr and revealed is mpe_panel
    assert st.top_key() == 'mpe'
    assert st.depth() == 2
    # back again: MPE panel -> grid
    removed, revealed = st.pop()
    assert removed is mpe_panel and revealed is grid
    assert st.depth() == 1
    assert st.back_visible() is False
    assert st.pop() == (None, None)                 # can't leave the grid


def test_chip_specs_track_instrument_changes(deck):
    # The top-bar chips render chip_specs over deckcfg.instruments(); add/remove
    # and set-active must be reflected.
    import shellmodel as sm
    deckcfg, _ = deck
    specs = sm.chip_specs(deckcfg.instruments(), deckcfg.active_instrument())
    assert len(specs) == 1                          # Tulip only
    a = deckcfg.add_instrument(device=0, channel=2)
    deckcfg.set_active_instrument(a['id'])
    specs = sm.chip_specs(deckcfg.instruments(), deckcfg.active_instrument())
    assert len(specs) == 2
    assert [s['active'] for s in specs] == [False, True]   # the board is active
    deckcfg.remove_instrument(a['id'])
    specs = sm.chip_specs(deckcfg.instruments(), deckcfg.active_instrument())
    assert len(specs) == 1


# --- forwarder: preview (rack patch audition) ---
def test_preview_internal_plays_owned_synth(deck):
    deckcfg, forwarder = deck
    forwarder.start()
    iid = deckcfg.instruments()[0]['id']
    forwarder.preview(iid, 60)
    assert 60 in forwarder._state['synths'][iid].on


def test_preview_board_sends_midi_out(deck):
    deckcfg, forwarder = deck
    board = deckcfg.add_instrument(device=0, channel=2)
    sent = _install_and_reset_sent()
    forwarder.start()
    sent.clear()
    forwarder.preview(board['id'], 60)
    assert sent and sent[0][1] == 0                 # to USB device 0
    assert sent[0][0][0] == 0x91                    # note-on on ch2


# --- C.4: global MPE gate ---
def test_mpe_enabled_default_off(deck):
    deckcfg, _ = deck
    assert deckcfg.mpe_enabled() is False           # off by default
    deckcfg.set_value('mpe_enabled', True)
    assert deckcfg.mpe_enabled() is True


def test_mpe_gate_governs_configure_mpe(deck):
    deckcfg, forwarder = deck
    iid = deckcfg.instruments()[0]['id']
    deckcfg.set_instrument_mpe(iid, 'enabled', True)   # the instrument wants MPE
    midi = sys.modules['midi']
    # global gate OFF (default): the router must NOT configure MPE
    midi._mpe_calls.clear()
    forwarder.start()
    assert midi._mpe_calls == []
    # global gate ON: now it configures MPE for the instrument's channel
    deckcfg.set_value('mpe_enabled', True)
    midi._mpe_calls.clear()
    forwarder.start()
    assert len(midi._mpe_calls) == 1


# --- C.3: screensaver defaults ---
def test_screensaver_thresholds_default_never(deck):
    deckcfg, _ = deck
    cfg = deckcfg.load()
    assert cfg['dim_after'] == 0 and cfg['sleep_after'] == 0


# --- D1: AMY param schema (amyparams; pure) ---
def test_amyparams_validate_and_groups():
    import amyparams as ap
    assert ap.validate() is True
    assert len(ap.PARAMS) > 20
    for g in ('Osc A', 'Filter', 'Amp Env', 'LFO'):
        assert g in ap.groups()
    assert 'EQ' not in ap.groups()          # EQ is per-device FX now, not synth


def test_amyparams_defaults_and_tier_filter():
    import amyparams as ap
    d = ap.default_params()
    assert set(d) == {p['name'] for p in ap.PARAMS}
    assert d['filter_freq'] == 1000 and d['amp_sustain'] == 1.0
    basic = ap.filter_tier(ap.PARAMS, False)
    adv = ap.filter_tier(ap.PARAMS, True)
    assert len(basic) < len(adv) == len(ap.PARAMS)
    assert all(x['tier'] == 'basic' for x in basic)


def test_synth_send_calls_only_explicit_params():
    import amyparams as ap
    calls = ap.synth_send_calls({'oscA_wave': 3, 'resonance': 2.0})
    assert {'osc': ap.OSC_A, 'wave': 3} in calls          # scalar on osc A
    assert {'osc': ap.OSC_CTL, 'resonance': 2.0} in calls  # scalar on ctl osc
    # UNSET params must NOT be sent: the schema defaults are editor display
    # fallbacks, not patch overrides (stamping them rewrote every patch)
    assert not any('filter_freq' in c for c in calls)
    assert len(calls) == 2
    assert ap.synth_send_calls({}) == []                   # untouched: nothing


def test_synth_send_calls_coef_vector_and_lfo_multi():
    import amyparams as ap
    calls = ap.synth_send_calls({'oscA_freq': 660, 'lfo_pitch': 0.5,
                                 'filter_freq': 800, 'filter_kbd': 1,
                                 'filter_env': 4})
    # osc A freq: base(coef0)=660 + LFO depth(coef5)=0.5
    assert {'osc': ap.OSC_A, 'freq': '660,,,,,0.5'} in calls
    # LFO depth lands on osc B freq too -- base slot EMPTY (patch value kept)
    assert {'osc': ap.OSC_B, 'freq': ',,,,,0.5'} in calls
    # filter_freq: base=800, kbd(1)=1, env(4)=4; lfo slot unset -> not emitted
    assert {'osc': ap.OSC_CTL, 'filter_freq': '800,1,,,4'} in calls


def test_synth_send_calls_envelopes():
    import amyparams as ap
    calls = ap.synth_send_calls({'amp_attack': 10, 'amp_decay': 200,
                                 'amp_sustain': 0.5, 'amp_release': 300})
    assert {'osc': ap.OSC_CTL, 'bp0': '10,1,200,0.5,300,0'} in calls
    # EQ is no longer emitted per-synth (moved to per-device FX)
    assert not any('eq' in c for c in calls)


def test_fx_calls_map_to_amy_kwargs():
    import amyparams as ap
    calls = dict(ap.fx_calls({'reverb': {'level': 0.4}}))
    assert calls['reverb'] == {'level': 0.4, 'liveness': 0.85, 'damping': 0.5}
    # ONLY touched buses are emitted: patch strings set their own chorus/EQ
    # (juno character), so untouched buses must not be zeroed by the UI.
    assert 'chorus' not in calls and 'echo' not in calls
    assert dict(ap.fx_calls({})) == {}
    calls = dict(ap.fx_calls({'chorus': {'level': 0.3}}))
    assert calls['chorus']['level'] == 0.3 and calls['chorus']['amp'] == 0.5
    # eq is not a fn-applied bus
    assert 'eq' not in calls


def test_fx_layering_patch_values_are_the_baseline():
    import amyparams as ap
    pfx = {'chorus': {'level': 1, 'freq': 0.83, 'depth': 0.5}}
    # editor shows the patch's value when the user never touched the bus
    assert ap.fx_value({}, pfx, 'chorus', 'level') == 1
    assert ap.fx_value({}, pfx, 'chorus', 'freq') == 0.83
    # a user override wins...
    assert ap.fx_value({'chorus': {'level': 0.2}}, pfx, 'chorus', 'level') == 0.2
    # ...and the resulting SEND keeps the patch's other fields, not defaults
    calls = dict(ap.fx_calls({'chorus': {'level': 0.2}}, pfx))
    assert calls['chorus']['level'] == 0.2 and calls['chorus']['freq'] == 0.83
    # untouched bus still not sent even though the patch sets it
    assert dict(ap.fx_calls({}, pfx)) == {}
    # eq: user layer over patch values
    pfx2 = {'eq': {'low': 7, 'mid': -3, 'high': -3}}
    assert ap.fx_eq_string({}, pfx2) is None
    assert ap.fx_eq_string({'eq': {'low': 0}}, pfx2) == '0,-3,-3'


def test_patchfx_table():
    import patchfx
    # juno chorus II patches carry level 1 / rate 0.83
    assert any(v.get('chorus', {}).get('freq') == 0.83
               for v in patchfx.FX.values())
    assert patchfx.patch_fx(103)['eq'] == {'low': -15, 'mid': 8, 'high': 8}
    assert patchfx.patch_fx(9999) == {}


# --- D2: EQ-as-device-FX + FX defs + live re-apply ---
def test_eq_is_device_fx_not_synth_param():
    import amyparams as ap
    names = {p['name'] for p in ap.PARAMS}
    assert 'eq_low' not in names            # not a per-instrument param
    assert 'eq' in ap.FX                     # a per-device FX bus
    assert ap.default_fx()['eq'] == {'low': 0, 'mid': 0, 'high': 0}
    defs = ap.fx_defs()
    assert any(d['bus'] == 'eq' and d['name'] == 'low' for d in defs)
    assert any(d['bus'] == 'reverb' for d in defs)


def test_fx_eq_string():
    import amyparams as ap
    # None = user never touched EQ -> leave the patch's own EQ alone
    assert ap.fx_eq_string({}) is None
    assert ap.fx_eq_string({'eq': {'low': -3, 'high': 6}}) == '-3,0,6'


def test_fx_defs_only_bus():
    import amyparams as ap
    defs = ap.fx_defs('echo')
    assert defs and all(d['bus'] == 'echo' for d in defs)


def test_forwarder_assigns_bus_and_applies_eq(deck):
    deckcfg, forwarder = deck
    deckcfg.set_device_fx('internal', 'eq', 'low', -3)
    amy = sys.modules['amy']
    amy._sends.clear()
    forwarder.start()
    syn = forwarder._state['synths'][deckcfg.instruments()[0]['id']]
    assert {'synth': syn.synth, 'bus': 0} in amy._sends       # bus assignment
    assert any(k.get('eq') == '-3,0,0' for k in amy._sends)   # device EQ applied


def test_forwarder_reapply_params_reuses_synth(deck):
    deckcfg, forwarder = deck
    forwarder.start()
    iid = deckcfg.instruments()[0]['id']
    syn_before = forwarder._state['synths'][iid]
    deckcfg.set_instrument_param(iid, 'oscA_wave', 3)
    amy = sys.modules['amy']
    amy._sends.clear()
    forwarder.reapply_params(iid)
    assert forwarder._state['synths'][iid] is syn_before      # no rebuild
    assert any(k.get('synth') == syn_before.synth and k.get('wave') == 3
               for k in amy._sends)


def test_forwarder_reapply_fx(deck):
    deckcfg, forwarder = deck
    forwarder.start()
    deckcfg.set_device_fx('internal', 'reverb', 'level', 0.4)
    amy = sys.modules['amy']
    amy._fx.clear()
    forwarder.reapply_fx()
    assert dict(amy._fx)['reverb']['level'] == 0.4


# --- D3.2: force synth allocation before per-bus routing ---
def test_forwarder_inits_synth_before_routing(deck):
    deckcfg, forwarder = deck
    forwarder.start()
    syn = forwarder._state['synths'][deckcfg.instruments()[0]['id']]
    # deferred_init() was forced so instruments[synth] exists before bus/eq sends
    assert syn.inited is True


# --- D3.1: tabbed editor derives from schema groups ---
def test_tabbed_groups_basic_vs_advanced():
    import amyparams as ap
    basic = [g for g, _ in ap.tabbed_groups(False)]
    adv = [g for g, _ in ap.tabbed_groups(True)]
    assert 'Osc A' in basic and 'Filter' in basic
    assert 'Osc B' not in basic          # all-advanced group hidden in Basic
    assert 'Osc B' in adv and 'Filter Env' in adv
    for g, defs in ap.tabbed_groups(False):
        assert defs and all(d['tier'] == 'basic' for d in defs)


def test_fx_tabbed_groups():
    import amyparams as ap
    tabs = ap.fx_tabbed_groups()
    assert [t for t, _ in tabs] == ['Reverb', 'Chorus', 'Echo', 'EQ']
    assert [d['name'] for d in dict(tabs)['EQ']] == ['low', 'mid', 'high']


# --- D3.3: panel stack remembers builders (refresh-on-return) ---
def test_panel_stack_stores_builder():
    import shellmodel as sm
    st = sm.PanelStack('Home')
    b1 = lambda p, s: None
    b2 = lambda p, s: None
    st.push(object(), 'Home', builder=b1)
    assert st.top_builder() is b1
    st.push(object(), 'X', key='x', builder=b2)
    assert st.top_builder() is b2
    st.pop()
    assert st.top_builder() is b1          # revealed panel's builder is recovered


# --- D1: deckcfg params + per-device FX ---
def test_instrument_params_get_set_default(deck):
    deckcfg, _ = deck
    iid = deckcfg.instruments()[0]['id']
    assert deckcfg.get_instrument_param(iid, 'filter_freq') == 1000  # schema default
    deckcfg.set_instrument_param(iid, 'filter_freq', 800)
    assert deckcfg.get_instrument_param(iid, 'filter_freq') == 800
    assert deckcfg.get_instrument(iid)['params']['filter_freq'] == 800


def test_params_survive_migration(deck):
    deckcfg, _ = deck
    import json
    with open(deckcfg.PATH, 'w') as f:
        json.dump({'instances': [{'kind': 'internal', 'channel': 1,
                                  'patch': 0}]}, f)
    instr = deckcfg.instruments()[0]
    assert instr['params'] == {}                     # migrated -> empty params
    assert deckcfg.get_instrument_param(instr['id'], 'resonance') == 0.7


def test_device_fx_get_set(deck):
    deckcfg, _ = deck
    assert deckcfg.device_fx('internal') == {}
    deckcfg.set_device_fx('internal', 'reverb', 'level', 0.4)
    assert deckcfg.device_fx('internal')['reverb']['level'] == 0.4
    deckcfg.set_device_fx(0, 'echo', 'level', 1.0)   # a board device (key '0')
    assert deckcfg.device_fx(0)['echo']['level'] == 1.0


# --- deckcfg: cache + drag-time flush semantics ---
def _read_config_file(deckcfg):
    import json
    with open(deckcfg.PATH) as f:
        return json.load(f)


def test_load_returns_cached_config(deck):
    deckcfg, _ = deck
    assert deckcfg.load() is deckcfg.load()      # same dict: no re-read/parse


def test_set_param_flush_false_defers_flash_write(deck):
    deckcfg, _ = deck
    iid = deckcfg.instruments()[0]['id']
    deckcfg.set_instrument_param(iid, 'filter_freq', 500)   # creates the file
    # A drag tick: cache updated, file NOT rewritten.
    deckcfg.set_instrument_param(iid, 'filter_freq', 900, flush=False)
    assert deckcfg.get_instrument_param(iid, 'filter_freq') == 900
    on_disk = _read_config_file(deckcfg)['instruments'][0]['params']
    assert on_disk['filter_freq'] == 500
    # Release: flush() commits the cached value.
    deckcfg.flush()
    on_disk = _read_config_file(deckcfg)['instruments'][0]['params']
    assert on_disk['filter_freq'] == 900


def test_set_device_fx_flush_false_defers_flash_write(deck):
    deckcfg, _ = deck
    deckcfg.set_device_fx('internal', 'reverb', 'level', 0.2)
    deckcfg.set_device_fx('internal', 'reverb', 'level', 0.8, flush=False)
    assert deckcfg.device_fx('internal')['reverb']['level'] == 0.8
    assert _read_config_file(deckcfg)['fx']['internal']['reverb']['level'] == 0.2
    deckcfg.flush()
    assert _read_config_file(deckcfg)['fx']['internal']['reverb']['level'] == 0.8


def test_invalidate_rereads_file(deck):
    deckcfg, _ = deck
    import json
    deckcfg.set_value('volume', 3)
    with open(deckcfg.PATH, 'w') as f:           # external edit behind the cache
        json.dump({'volume': 9}, f)
    assert deckcfg.get('volume') == 3            # cache still serves the old value
    deckcfg.invalidate()
    assert deckcfg.get('volume') == 9


# --- D1: forwarder applies params + FX ---
def test_forwarder_applies_params_to_synth(deck):
    deckcfg, forwarder = deck
    iid = deckcfg.instruments()[0]['id']
    deckcfg.set_instrument_param(iid, 'oscA_wave', 3)
    amy = sys.modules['amy']
    amy._sends.clear()
    forwarder.start()
    syn = forwarder._state['synths'][iid]
    assert any(k.get('synth') == syn.synth and k.get('osc') == 2
               and k.get('wave') == 3 for k in amy._sends)


def test_forwarder_applies_device_fx(deck):
    deckcfg, forwarder = deck
    deckcfg.set_device_fx('internal', 'reverb', 'level', 0.4)
    amy = sys.modules['amy']
    amy._fx.clear()
    forwarder.start()
    fx = dict(amy._fx)
    assert fx['reverb']['level'] == 0.4


def test_quitting_app_returns_to_home(uipatch):
    ui, _ = uipatch
    repl = ui.UIScreen('repl'); ui.repl_screen = repl
    home = ui.UIScreen('home')
    drums = ui.UIScreen('drums')
    drums.screen_quit_callback(None)            # patched
    assert 'drums' not in ui.running_apps       # firmware cleanup still ran
    assert home.presented == 1                  # landed on Home
    assert repl.presented == 0                  # not the REPL
    assert ui.repl_screen is repl               # module global restored


def test_quitting_app_falls_back_to_repl_without_home(uipatch):
    ui, _ = uipatch
    repl = ui.UIScreen('repl'); ui.repl_screen = repl
    drums = ui.UIScreen('drums')                # no Home running
    drums.screen_quit_callback(None)
    assert repl.presented == 1                   # nothing orphaned


def test_root_and_repl_cannot_be_quit(uipatch):
    ui, _ = uipatch
    repl = ui.UIScreen('repl'); ui.repl_screen = repl
    home = ui.UIScreen('home')
    home.screen_quit_callback(None)             # root: no-op
    assert 'home' in ui.running_apps
    assert repl.presented == 0
    repl.screen_quit_callback(None)             # repl: no-op
    assert 'repl' in ui.running_apps


# ---------------------------------------------------------------------------
# channels.py -- pure MPE channel-budget / zone allocation (no hardware)
# ---------------------------------------------------------------------------
import channels


def _instr(iid, ch, device='internal', mpe=None, name=None, enabled=True):
    d = {'id': iid, 'channel': ch, 'device': device, 'enabled': enabled,
         'name': name or iid}
    if mpe is not None:
        d['mpe'] = mpe
    return d


def test_channels_member_and_zone_math():
    assert channels.member_channels(1, 4) == [2, 3, 4, 5]
    assert channels.member_channels(1, 15) == list(range(2, 17))
    assert channels.member_channels(5, 6) == [6, 7, 8, 9, 10, 11]
    # clamp at 16
    assert channels.member_channels(13, 15) == [14, 15, 16]
    # upper zone descends
    assert channels.member_channels(16, 4) == [12, 13, 14, 15]
    assert channels.zone_channels(1, 3) == [1, 2, 3, 4]


def test_channels_instrument_channels_respects_gate():
    mpe_instr = _instr('a', 1, mpe={'enabled': True, 'members': 4})
    # gate off -> single channel even though instrument enables MPE
    assert channels.instrument_channels(mpe_instr, False) == [1]
    # gate on -> full zone
    assert channels.instrument_channels(mpe_instr, True) == [1, 2, 3, 4, 5]
    plain = _instr('b', 3)
    assert channels.instrument_channels(plain, True) == [3]


def test_channels_zone_fits_and_conflicts():
    insts = [
        _instr('lead', 1, mpe={'enabled': True, 'members': 4}),
        _instr('bass', 3),   # sits inside the lead's would-be zone
    ]
    # lead's zone (1..5) overlaps bass on ch3
    fits, conflicts = channels.zone_fits(insts, 'internal', 1, 4,
                                         exclude_iid='lead', mpe_on=True)
    assert not fits and conflicts == [3]
    # a device with just the lead: zone fits
    fits2, _ = channels.zone_fits([insts[0]], 'internal', 1, 4,
                                  exclude_iid='lead', mpe_on=True)
    assert fits2
    # cross-device instrument never conflicts
    other = [insts[0], _instr('bass', 3, device=0)]
    fits3, conf3 = channels.zone_fits(other, 'internal', 1, 4,
                                      exclude_iid='lead', mpe_on=True)
    assert fits3 and conf3 == []


def test_channels_max_members_at():
    insts = [_instr('lead', 1, mpe={'enabled': True, 'members': 15}),
             _instr('pad', 6)]
    # from master ch1, free members run 2..5 (ch6 taken) -> 4
    assert channels.max_members_at(insts, 'internal', 1, 'lead', True) == 4
    # empty device from ch1 -> 15
    assert channels.max_members_at([], 'internal', 1, None, True) == 15


def test_channels_channel_map():
    insts = [_instr('lead', 1, mpe={'enabled': True, 'members': 2}, name='Lead'),
             _instr('bass', 8, name='Bass')]
    slots = channels.channel_map(insts, 'internal', True, active_iid='lead')
    assert len(slots) == 16
    assert slots[0]['ch'] == 1 and slots[0]['master'] and slots[0]['mine']
    assert slots[1]['member'] and slots[2]['member']       # ch2,3 members
    assert slots[7]['busy'] and slots[7]['names'] == ['Bass']
    assert not slots[4]['busy']                            # ch5 free

    # a bass that sits inside the lead's zone shows as a conflict on that channel
    insts2 = [_instr('lead', 1, mpe={'enabled': True, 'members': 4}, name='Lead'),
              _instr('bass', 3, name='Bass')]
    slots2 = channels.channel_map(insts2, 'internal', True, active_iid='lead')
    assert slots2[2]['conflict']       # ch3: lead member + bass
    assert not slots2[3]['conflict']   # ch4: lead member only, no conflict
