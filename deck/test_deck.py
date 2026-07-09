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
    tulip.defer = lambda fn, arg, ms: None
    tulip.board = lambda: 'DESKTOP'
    tulip.screen_size = lambda: (1024, 600)
    tulip._sent = sent
    sys.modules['tulip'] = tulip

    amy = types.ModuleType('amy')
    amy.send = lambda *a, **k: None
    amy.volume = lambda *a, **k: None
    amy.reset = lambda *a, **k: None
    sys.modules['amy'] = amy

    synth = types.ModuleType('synth')

    class PatchSynth:
        instances = []

        def __init__(self, patch=0, num_voices=10, **k):
            self.patch = patch
            self.num_voices = num_voices
            self.on = []
            self.released = False
            PatchSynth.instances.append(self)

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
    midi.configure_mpe = lambda *a, **k: None
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
    lv.ALIGN = _NS(TOP_RIGHT=1, BOTTOM_RIGHT=2, OUT_LEFT_MID=3)
    lv.EVENT = _NS(CLICKED=1, VALUE_CHANGED=2)
    lv.TEXT_ALIGN = _NS(CENTER=1)
    lv.font_montserrat_24 = object()
    lv.font_montserrat_18 = object()
    lv.font_montserrat_12 = object()

    class _Label:
        def set_style_text_font(self, *a): pass
        def set_text(self, *a): pass
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
    forwarder._state.update({'on': False, 'stack': None, 'notes': {}, 'rr': 0})
    return deckcfg, forwarder


# --- deckcfg: config model ---
def test_defaults_and_single_internal_instance(deck):
    deckcfg, _ = deck
    cfg = deckcfg.load()
    assert cfg['mode'] == 'multi'
    assert len(cfg['instances']) == 1
    assert cfg['instances'][0]['kind'] == 'internal'
    assert cfg['instances'][0]['channel'] == 1
    assert cfg['detune']['unison_voices'] == 3


def test_migrates_old_single_instrument_config(deck):
    deckcfg, _ = deck
    import json
    with open(deckcfg.PATH, 'w') as f:
        json.dump({'patch': 143, 'num_voices': 6, 'midi_channel': 4,
                   'mpe': True, 'volume': 7}, f)
    cfg = deckcfg.load()
    inst = cfg['instances'][0]
    assert inst['patch'] == 143
    assert inst['num_voices'] == 6
    assert inst['channel'] == 4          # midi_channel -> channel
    assert inst['mpe'] is True
    assert cfg['volume'] == 7            # device settings preserved


def test_ensure_count_grows_and_shrinks(deck):
    deckcfg, _ = deck
    deckcfg.ensure_count(3)
    insts = deckcfg.instances()
    assert len(insts) == 3
    assert insts[0]['kind'] == 'internal'
    assert insts[1]['kind'] == 'amyboard'
    assert insts[1]['channel'] == 2
    assert insts[2]['channel'] == 3
    deckcfg.ensure_count(1)
    assert deckcfg.num_instances() == 1


def test_ensure_count_floor_is_one(deck):
    deckcfg, _ = deck
    deckcfg.ensure_count(0)
    assert deckcfg.num_instances() == 1


def test_set_instance_and_active(deck):
    deckcfg, _ = deck
    deckcfg.ensure_count(2)
    deckcfg.set_instance(1, 'patch', 200)
    deckcfg.set_active(1)
    assert deckcfg.get_instance(1)['patch'] == 200
    assert deckcfg.active_index() == 1


def test_active_index_clamped_when_instances_shrink(deck):
    deckcfg, _ = deck
    deckcfg.ensure_count(3)
    deckcfg.set_active(2)
    deckcfg.ensure_count(1)
    assert deckcfg.active_index() == 0


def test_set_detune(deck):
    deckcfg, _ = deck
    deckcfg.set_detune('unison_voices', 7)
    deckcfg.set_detune('enabled', True)
    d = deckcfg.load()['detune']
    assert d['unison_voices'] == 7 and d['enabled'] is True


# --- forwarder: routing ---
def _stack(deckcfg, forwarder, instances=1, unison=3, detune=True):
    deckcfg.ensure_count(instances)
    deckcfg.set_mode('stack')
    deckcfg.set_detune('enabled', detune)
    deckcfg.set_detune('unison_voices', unison)
    forwarder.start()


def test_unison_expands_to_configured_voice_count(deck):
    deckcfg, forwarder = deck
    _stack(deckcfg, forwarder, instances=1, unison=5)
    forwarder._route((0x90, 60, 100))
    targets = forwarder._state['notes'][(1, 60)]
    assert len(targets) == 5
    notes = sorted(t[1] for t in targets)
    # symmetric spread around 60 (default 8 cents)
    assert notes[0] == pytest.approx(59.92, abs=0.001)
    assert notes[-1] == pytest.approx(60.08, abs=0.001)
    assert 60.0 in notes


def test_note_off_clears_the_note_table(deck):
    deckcfg, forwarder = deck
    _stack(deckcfg, forwarder, instances=1, unison=4)
    forwarder._route((0x90, 62, 100))
    assert forwarder._state['notes']
    forwarder._route((0x80, 62, 0))
    assert forwarder._state['notes'] == {}


def test_note_on_velocity_zero_is_a_note_off(deck):
    deckcfg, forwarder = deck
    _stack(deckcfg, forwarder, instances=1, unison=2)
    forwarder._route((0x90, 64, 100))
    forwarder._route((0x90, 64, 0))   # running-status note off
    assert forwarder._state['notes'] == {}


def test_stack_round_robin_cycles_instances(deck):
    deckcfg, forwarder = deck
    _stack(deckcfg, forwarder, instances=3, unison=1, detune=False)
    start = forwarder._state['rr']
    for n in (60, 61, 62):
        forwarder._route((0x90, n, 100))
    assert forwarder._state['rr'] == start + 3


def test_prioritize_boards_uses_boards_only(deck):
    deckcfg, forwarder = deck
    deckcfg.ensure_count(3)                 # Tulip + Board A + Board B
    deckcfg.set_mode('stack')
    deckcfg.set_detune('enabled', False)
    deckcfg.set('prioritize_boards', True)
    forwarder.start()
    for n in range(60, 66):
        forwarder._route((0x90, n, 100))
    kinds = set(t[0] for tg in forwarder._state['notes'].values() for t in tg)
    assert kinds == {'board'}               # Tulip AMY offloaded when boards present


def test_priority_even_includes_tulip(deck):
    deckcfg, forwarder = deck
    deckcfg.ensure_count(2)                 # Tulip + Board A
    deckcfg.set_mode('stack')
    deckcfg.set_detune('enabled', False)
    deckcfg.set('prioritize_boards', False)
    forwarder.start()
    for n in range(60, 64):
        forwarder._route((0x90, n, 100))
    kinds = set(t[0] for tg in forwarder._state['notes'].values() for t in tg)
    assert 'internal' in kinds              # even mode also uses the Tulip AMY


def test_multi_mode_forwards_only_board_channels(deck):
    deckcfg, forwarder = deck
    deckcfg.ensure_count(2)          # Tulip ch1 + Board A ch2
    deckcfg.set_mode('multi')
    sent = _install_and_reset_sent()
    forwarder.start()
    forwarder._route((0x90, 60, 100))          # ch1 -> internal, not forwarded
    assert sent == []
    forwarder._route((0x91, 60, 100))          # ch2 -> board, forwarded
    assert len(sent) == 1 and sent[0][0][0] == 0x91


def _install_and_reset_sent():
    sent = sys.modules['tulip']._sent
    sent.clear()
    return sent


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
