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


# Every call the ORIGINAL (unfiltered) ui.lv_soft_kb_cb receives. ui_patch's
# keyboard filter delegates to it only for the close key, so this doubles as
# the record of which keys were treated as "close".
_ORIG_KB_CB_CALLS = []


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
    ui.lv_soft_kb = None
    ui.lv_soft_kb_cb = lambda e: _ORIG_KB_CB_CALLS.append(e)
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


# --- MIDI monitor tap engagement (router-tap-fix) ---
def _add_c_router(tulip_mod):
    """Attach a capturing tulip.midi_routes so the C-router path is live in a
    test (the shared mock omits it, keeping other tests on the no-router path).
    Returns the list of (masks, py_mask, notify_all) uploads."""
    calls = []
    tulip_mod.midi_routes = (lambda masks, py_mask, notify_all=False:
                             calls.append((list(masks), py_mask, bool(notify_all))))
    tulip_mod.midi_activity = lambda: 0
    return calls


def test_c_router_tap_preserved_across_rebuild(deck):
    # H1 regression pin: an instrument rebuild re-uploads the route table, and
    # it MUST carry the tap flag forward -- else an open monitor silently loses
    # its tap on the next rebuild (the reported "sometimes blind" behavior).
    deckcfg, forwarder = deck
    calls = _add_c_router(sys.modules['tulip'])
    forwarder.start()                          # first upload: tap off
    assert calls and calls[-1][2] is False
    assert forwarder.set_midi_tap(True) is True
    assert calls[-1][2] is True                # tap now live in C
    forwarder.start()                          # rebuild an instrument
    assert calls[-1][2] is True                # tap SURVIVED the rebuild
    assert forwarder.tap_engaged() is True


def test_set_midi_tap_reports_upload_failure(deck):
    # #85: a router present but a failed upload must be reported (returns
    # False), not swallowed -- a stale table could be gating Python with the
    # tap off, i.e. a silently blind monitor.
    deckcfg, forwarder = deck

    def _boom(*a, **k):
        raise RuntimeError('upload failed')
    tulip = sys.modules['tulip']
    tulip.midi_routes = _boom
    tulip.midi_activity = lambda: 0
    assert forwarder.set_midi_tap(True) is False
    assert forwarder.tap_engaged() is False


def test_set_midi_tap_engaged_without_c_router(deck):
    # No C router in firmware: nothing suppresses Python, so the tap is
    # vacuously engaged (the monitor sees everything) and must not report a
    # failure.
    deckcfg, forwarder = deck
    tulip = sys.modules['tulip']
    if hasattr(tulip, 'midi_routes'):
        delattr(tulip, 'midi_routes')
    assert forwarder.set_midi_tap(True) is True
    assert forwarder.tap_engaged() is True


class _FakeLabel:
    def __init__(self):
        self.text = ''
        self.color = None
        self.deleted = False

    def get_text(self):
        if self.deleted:
            raise RuntimeError('label deleted')
        return self.text

    def set_text(self, t):
        if self.deleted:
            raise RuntimeError('label deleted')
        self.text = t

    def set_style_text_color(self, color, sel):
        self.color = color


@pytest.fixture
def midimon_env():
    """Import midimon with lvgl/deckui/forwarder faked so the tick self-heal
    and loud-banner logic can run under CPython (the real modules are LVGL)."""
    names = ('tulip', 'deckui', 'lvgl', 'forwarder', 'midi', 'midimon')
    saved = {n: sys.modules.get(n) for n in names}
    _install_hw_mocks()

    lv = types.ModuleType('lvgl')

    class _EVENT:
        CLICKED = 1
        DELETE = 2
    lv.EVENT = _EVENT
    sys.modules['lvgl'] = lv

    dk = types.ModuleType('deckui')
    dk.c = lambda x: x
    dk.RED = 'red'
    dk.GREEN = 'green'
    dk.ORANGE = 'orange'
    dk.SURFACE = 'surface'
    dk.SURFACE2 = 'surface2'
    dk.MUTED = 'muted'
    dk.FONT_S = 'font_s'

    class _Font:
        line_height = 17
    dk.FONT_MONO = _Font()
    sys.modules['deckui'] = dk

    ff = types.ModuleType('forwarder')
    ff._engaged = True
    ff._ok = True
    ff._calls = []
    ff._reg_ok = True
    ff._stalls = 0
    ff.tap_engaged = lambda: ff._engaged
    ff.register_ok = lambda: ff._reg_ok
    ff.midi_stalls = lambda: ff._stalls

    def _set_tap(on):
        ff._calls.append(bool(on))
        if on:
            ff._engaged = ff._ok
        return ff._engaged if on else True
    ff.set_midi_tap = _set_tap
    sys.modules['forwarder'] = ff

    sys.modules.pop('midimon', None)
    midimon = importlib.import_module('midimon')
    yield midimon, ff

    for n, m in saved.items():
        if m is None:
            sys.modules.pop(n, None)
        else:
            sys.modules[n] = m
    sys.modules.pop('midimon', None)


def test_midimon_tick_selfheals_dropped_tap(midimon_env):
    midimon, ff = midimon_env
    lbl = _FakeLabel()
    midimon._s.update({'open': True, 'lbl': lbl, 'cntlbl': None,
                       'dirty': False, 'buf': [], 'count': 0, 'paused': False})
    ff._engaged = False                        # a rebuild dropped the tap
    midimon._tick()
    assert ff._calls and ff._calls[-1] is True  # tick re-asserted it
    assert midimon._s['tap_ok'] is True


def test_midimon_tick_shows_loud_banner_when_tap_fails(midimon_env):
    midimon, ff = midimon_env
    lbl = _FakeLabel()
    midimon._s.update({'open': True, 'lbl': lbl, 'cntlbl': None,
                       'dirty': False, 'buf': [], 'count': 0, 'paused': False})
    ff._engaged = False
    ff._ok = False                             # cannot engage at all
    midimon._tick()
    assert 'FAILED' in lbl.text                # loud, not an empty log
    assert lbl.color == 'red'


def test_midimon_tick_render_error_does_not_disengage_tap(midimon_env):
    midimon, ff = midimon_env
    lbl = _FakeLabel()
    midimon._s.update({'open': True, 'lbl': lbl, 'cntlbl': None,
                       'dirty': True, 'buf': [], 'count': 0, 'paused': False,
                       'tap_ok': True})

    def _boom():
        raise RuntimeError('render boom')
    orig = midimon._render
    midimon._render = _boom
    try:
        midimon._tick()
    finally:
        midimon._render = orig
    assert midimon._s['open'] is True          # transient error != teardown
    assert False not in ff._calls              # tap was never disengaged


def test_midimon_tick_closes_when_label_deleted(midimon_env):
    midimon, ff = midimon_env
    lbl = _FakeLabel()
    lbl.deleted = True
    midimon._s.update({'open': True, 'lbl': lbl, 'cntlbl': None,
                       'buf': [], 'count': 0})
    midimon._tick()
    assert midimon._s['open'] is False         # real teardown still works


def test_midimon_tick_shows_routing_disabled_banner(midimon_env):
    midimon, ff = midimon_env
    lbl = _FakeLabel()
    midimon._s.update({'open': True, 'lbl': lbl, 'cntlbl': None,
                       'dirty': False, 'buf': [], 'count': 0, 'paused': False})
    ff._reg_ok = False                         # add_callback(_route) failed
    midimon._tick()
    assert 'ROUTING DISABLED' in lbl.text
    assert lbl.color == 'red'


# --- MIDI drain watchdog (the lost-wakeup recovery) ---
def test_watchdog_force_drains_on_ring_overflow(deck):
    deckcfg, forwarder = deck
    tulip = sys.modules['tulip']
    st = {'ring': 0, 'backlog': 0}
    tulip.midi_in_drops = lambda: (0, st['ring'])

    def _midi_in():
        if st['backlog'] > 0:
            st['backlog'] -= 1
            return b'\x93\x37\x06'             # ch4 note-on vel 6 (the live one)
        return None
    tulip.midi_in = _midi_in
    forwarder._wd.update({'last_ring_drops': None, 'stalls': 0, 'started': False})

    forwarder._watchdog()                      # baseline sample: no stall yet
    assert forwarder.midi_stalls() == 0
    st['ring'] = 1024                          # ring overflowed since baseline
    st['backlog'] = 1023                       # 1023 stale messages wedged
    forwarder._watchdog()
    assert forwarder.midi_stalls() == 1        # detected + recovered
    assert st['backlog'] == 0                  # force-drained to empty
    forwarder._watchdog()                      # steady state: no new drops
    assert forwarder.midi_stalls() == 1        # no false re-trigger


def test_register_ok_reports_add_callback_failure(deck):
    deckcfg, forwarder = deck
    midi = sys.modules['midi']

    def _boom(fn):
        raise RuntimeError('no callback slot')
    midi.add_callback = _boom
    forwarder._state['registered'] = False
    forwarder._state.pop('register_failed', None)
    forwarder.start()
    assert forwarder.register_ok() is False


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
    assert summ == 'Board B ch2 DX2'
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


# ---------------------------------------------------------------------------
# The envelope editor must show the patch's REAL envelope, not schema defaults.
# A GM patch bakes 'A5,1,60000,0.85,220,0'; the editor used to draw its own
# 0/100/1.0/100 for every instrument, which on the 0..2000 / 0..8000 ms ranges
# renders as "attack 0, decay 0, sustain full, release 0" -- numbers the patch
# never had, and which sent a debugging session in the wrong direction.
# ---------------------------------------------------------------------------

def test_parse_bp_reads_the_real_adsr():
    import amyparams as ap
    # the GM looped-preset envelope, with and without its wire letter
    want = {'attack': 5, 'decay': 60000, 'sustain': 0.85, 'release': 220}
    assert ap.parse_bp('A5,1,60000,0.85,220,0') == want
    assert ap.parse_bp('5,1,60000,0.85,220,0') == want
    # the GM one-shot envelope
    assert ap.parse_bp('A0,1,30000,1,4000,0') == {
        'attack': 0, 'decay': 30000, 'sustain': 1, 'release': 4000}
    # exact inverse of the string we emit, for both envelopes' defaults
    for which in ('amp', 'filter'):
        e = ap.ENV_DEFAULTS[which]
        assert ap.parse_bp(ap._adsr_string(which, e)) == e


def test_parse_bp_unknown_beats_a_wrong_guess():
    import amyparams as ap
    # absent / malformed -> {} (UNKNOWN), never a fabricated ADSR
    for bad in (None, '', 'A', 'Z', 'garbage', 'A5,1,60000,0.85,220',
                'A5,1,60000,0.85,220,0,7', 'A5,x,60000,0.85,220,0',
                'A,,,,,'):
        assert ap.parse_bp(bad) == {}, bad
    # a NON-ADSR breakpoint set our four sliders cannot represent: an AD pair,
    # and shapes whose peak/end aren't 1/0. Misreading these as ADSR would be
    # the same lie in a new place.
    assert ap.parse_bp('0,1.0,300,0.0') == {}
    assert ap.parse_bp('A5,0.5,60000,0.85,220,0') == {}
    assert ap.parse_bp('A5,1,60000,0.85,220,0.3') == {}


def test_patch_env_known_for_gm_unknown_for_patch_number_engines():
    import amyparams as ap
    import gm
    # (a) GM: the deck BUILDS the patch string, so the real envelope is readable.
    # Program 2 uses a LOOPED preset -> the 'A5,1,60000,0.85,220,0' recipe.
    assert gm.PRESET_LOOPED[gm.PROGRAM_PRESET[2]] == 1
    penv = ap.patch_env({'type': 'gm', 'patch': 2})
    assert penv['amp'] == ap.parse_bp(ap._wire_bp(gm.patch_string(2), 'A'))
    assert penv['amp']['decay'] == 60000        # not the schema's 100 ms
    assert penv['amp']['sustain'] == 0.85       # not the schema's 1.0
    assert penv['amp']['release'] == 220        # not the schema's 100 ms
    # A ONE-SHOT preset carries a different real recipe, and the editor must
    # show THAT one -- not one recipe for all GM. Program 13 (Xylophone) is the
    # example on purpose: a struck bar physically cannot sustain, so it stays a
    # one-shot across bank rebakes. This assertion used to name program 0
    # (Grand Piano) and broke the day the bank was rebaked to give the piano
    # back its real 1.02s sustain loop -- the test was right, its example rotted.
    assert gm.PRESET_LOOPED[gm.PROGRAM_PRESET[13]] == 0
    assert ap.patch_env({'type': 'gm', 'patch': 13})['amp'] == {
        'attack': 0, 'decay': 30000, 'sustain': 1, 'release': 4000}
    # gm2 reads the same recipe off the emu4 font
    assert ap.patch_env({'type': 'gm2', 'patch': 0})['amp']['decay'] == 60000
    # GM patch strings carry NO bp1 -> filter envelope stays unknown, not made up
    assert 'filter' not in penv
    # (b) juno6/dx7/piano patches live in AMY's patches.h and reach the device
    # as a NUMBER, so the deck cannot read them AT RUNTIME -- but patches.h is
    # in the tree, so they were never unknowable, only unavailable.
    # tools/gen_patchparams.py distils them into patchparams.py at build time
    # (exactly what patchfx.py already does for a patch's FX), so the juno
    # bank's real envelopes now read back like GM's.
    assert ap.patch_env({'type': 'juno6', 'patch': 11})['amp'] == {
        'attack': 62, 'decay': 142057, 'sustain': 1, 'release': 22}
    # the DX7 bank stays honestly unknown: its osc-0 bp0 is a 10-field,
    # 5-segment envelope that four ADSR sliders cannot represent, so parse_bp
    # refuses it rather than mangling it into a shape it is not
    assert ap.patch_env({'type': 'dx7', 'patch': 128}) == {}
    assert ap.patch_env(None) == {}
    assert ap.patch_env({}) == {}
    # a gm2 program the font does not cover raises KeyError internally -> {}
    assert ap.patch_env({'type': 'gm2', 'patch': 1}) == {}


def test_param_value_layers_user_over_patch_over_default():
    import amyparams as ap
    ppv = ap.patch_params({'type': 'gm', 'patch': 2})
    # untouched: the editor shows the PATCH's value, flagged as such
    assert ap.param_value_source({}, ppv, 'amp_decay') == (60000, 'patch')
    assert ap.param_value_source({}, ppv, 'amp_sustain') == (0.85, 'patch')
    # a user override wins
    assert ap.param_value_source({'amp_decay': 250}, ppv,
                                 'amp_decay') == (250, 'user')
    # unknown patch envelope -> schema fallback, flagged 'default' so the UI
    # can say "patch default" instead of drawing an invented number
    v, src = ap.param_value_source({}, {}, 'amp_decay')
    assert (v, src) == (100, 'default')
    assert ap.is_env_param('amp_decay') and ap.is_env_param('filt_sustain')
    assert ap.is_env_param('filter_freq') is False
    # a GM patch bakes no filter, so cutoff falls through to the schema -- and
    # the schema's 1000 Hz is not AMY's default either (an unpatched osc has
    # filter_type=FILTER_NONE), so it is flagged as ours to disown
    assert ap.param_value_source({}, ppv, 'filter_freq') == (1000, 'default')
    assert ap.is_fabricated(ap.PARAM_BY_NAME['filter_freq'], 'default') is True


def test_seeding_the_display_never_sends_to_amy():
    import amyparams as ap
    penv = ap.patch_env({'type': 'gm', 'patch': 2})
    assert penv['amp']['decay'] == 60000        # we DO know the envelope...
    # ...and knowing it must still send NOTHING while the user has touched
    # nothing. Seeding is display-only; only stored params are sent.
    assert ap.synth_send_calls({}, penv) == []
    assert ap.synth_send_calls(None, penv) == []
    # a non-envelope edit must not drag the seeded envelope along
    calls = ap.synth_send_calls({'pan': 0.5}, penv)
    assert not any('bp0' in c for c in calls)


def test_touching_one_env_slot_keeps_the_patchs_other_values():
    import amyparams as ap
    penv = ap.patch_env({'type': 'gm', 'patch': 2})
    # bp0 is ONE composite string, so a touched attack restates D/S/R. They
    # must come from the PATCH (60000/0.85/220), not the schema (100/1/100):
    # filling from the schema silently destroyed the patch's baked envelope.
    calls = ap.synth_send_calls({'amp_attack': 10}, penv)
    assert {'osc': ap.OSC_CTL, 'bp0': '10,1,60000,0.85,220,0'} in calls
    # with no patch envelope known, the schema fallback stands (old behaviour)
    calls = ap.synth_send_calls({'amp_attack': 10})
    assert {'osc': ap.OSC_CTL, 'bp0': '10,1,100,1,100,0'} in calls
    # the user still wins over the patch for the slot they actually set
    calls = ap.synth_send_calls({'amp_decay': 250}, penv)
    assert {'osc': ap.OSC_CTL, 'bp0': '5,1,250,0.85,220,0'} in calls


# ---------------------------------------------------------------------------
# The log curve. A linear 0..2000 ms decay slider could not express the 60000 ms
# a GM patch bakes (nor the 142057 ms a juno does): the knob pinned at the stop,
# and a tap on a pinned knob is a real touch event with nothing to guard it, so
# it silently rewrote a 60 s decay to 2 s. These tests pin the curve's two
# load-bearing properties: it is a bijection (so a stored value survives every
# re-render), and it reaches the values that actually exist.
# ---------------------------------------------------------------------------

_LOG_PARAMS = ('amp_attack', 'amp_decay', 'amp_release',
               'filt_attack', 'filt_decay', 'filt_release', 'filter_freq')


def test_log_curve_is_exactly_the_params_with_decade_spanning_reality():
    import amyparams as ap
    logged = {d['name'] for d in ap.PARAMS if d.get('curve') == 'log'}
    assert logged == set(_LOG_PARAMS)
    # the six envelope TIMES share one range that contains every real value:
    # juno bp0 decay reaches 142057 ms, release 23282 ms, attack 1022 ms
    for n in _LOG_PARAMS[:6]:
        d = ap.PARAM_BY_NAME[n]
        assert (d['min'], d['max']) == (0, 150000), n
        assert d['unit'] == 'ms'
    # portamento is a time param too and is deliberately NOT logged: no patch
    # bakes it (wire letter 'm' appears in none), so nothing can seed it past
    # its stop, and glide time is linear to the ear
    assert ap.PARAM_BY_NAME['portamento'].get('curve') is None
    assert ap.PARAM_BY_NAME['portamento']['unit'] == 'ms'


def test_log_curve_round_trips_every_storable_value_exactly():
    import amyparams as ap
    # THE property. Every value the control can store must map back to the
    # position that made it, and thence to the same value -- otherwise opening
    # the panel would quietly restate the user's setting as something else.
    for n in _LOG_PARAMS:
        d = ap.PARAM_BY_NAME[n]
        steps = ap.curve_steps(d)
        assert steps == ap.LOG_STEPS
        seen = {}
        for pos in range(steps + 1):
            v = ap.curve_value(d, pos)
            assert v not in seen, (
                "%s: positions %d and %d both produce %r -- the curve is not "
                "invertible, so a re-render can move a stored value" % (
                    n, seen.get(v), pos, v))
            seen[v] = pos
            assert ap.curve_pos(d, v) == pos, (
                "%s: value %r stored from position %d renders at position %d"
                % (n, v, pos, ap.curve_pos(d, v)))
        # strictly increasing, and the ends are exactly the advertised range
        vals = [ap.curve_value(d, p) for p in range(steps + 1)]
        assert vals == sorted(vals) and len(set(vals)) == len(vals)
        assert vals[0] == d['min'] and vals[-1] == d['max']


def test_log_curve_reaches_the_values_that_actually_exist():
    import amyparams as ap
    d = ap.PARAM_BY_NAME['amp_decay']
    # the whole point: the values a linear 0..2000 slider pinned at its stop
    for ms in (0, 5, 25, 50, 220, 1355, 4000, 30000, 60000, 142057):
        pos = ap.curve_pos(d, ms)
        assert 0 <= pos <= ap.LOG_STEPS
        # ...land somewhere real, not at the stop
        assert pos < ap.LOG_STEPS, "%d ms still pins the knob" % ms
    # 1 ms resolution where the music is: every whole ms from 0..228 is its own
    # position (AMY's bp times are whole ms -- nothing finer is expressible)
    for ms in range(0, 229):
        assert ap.curve_value(d, ap.curve_pos(d, ms)) == ms
    # ...and the long tail is still reachable, at <2.5% per step
    assert ap.curve_value(d, ap.LOG_STEPS) == 150000
    a, b = ap.curve_value(d, 400), ap.curve_value(d, 401)
    assert 1.0 < b / a < 1.025
    # cutoff spans the junos' real 17.5 Hz .. 84288 Hz, both inside the range
    f = ap.PARAM_BY_NAME['filter_freq']
    assert f['min'] <= 17.527 and f['max'] >= 84288
    assert 0 < ap.curve_pos(f, 17.527) and ap.curve_pos(f, 84288) < ap.LOG_STEPS


def test_log_curve_never_rewrites_a_value_it_cannot_produce():
    import amyparams as ap
    d = ap.PARAM_BY_NAME['amp_decay']
    # A patch's 60000 ms and a setting saved by the OLD linear slider (any
    # whole ms) are not always on the curve. Rendering one must place the knob
    # nearby WITHOUT changing the value: curve_pos only reads. This is the
    # class of bug the whole workstream exists to kill, so it is asserted, not
    # assumed -- the value that gets displayed and sent is the stored one.
    for ms in (1053, 1750, 60000, 142057):
        pos = ap.curve_pos(d, ms)
        shown = ap.curve_value(d, pos)
        assert abs(shown - ms) / float(ms) < 0.025      # knob within one step
        # the STORED value is untouched: nothing here writes
        assert ap.param_value_source({'amp_decay': ms}, {},
                                     'amp_decay') == (ms, 'user')
        # and it is the stored value that reaches AMY, not the knob's
        calls = ap.synth_send_calls({'amp_decay': ms})
        assert any(str(ms) in c.get('bp0', '') for c in calls)


def test_linear_params_map_positions_exactly_as_before():
    import amyparams as ap
    # the curve helpers are the identity for every non-log param: position is
    # value*scale rebased to 0, which is what the sliders always did
    for name, val in (('pan', 0.25), ('resonance', 1.2), ('lfo_freq', 4),
                      ('portamento', 250), ('amp_sustain', 0.85)):
        d = ap.PARAM_BY_NAME[name]
        scale = d.get('scale', 1)
        assert ap.curve_steps(d) == int(round(d['max'] * scale)) - \
            int(round(d['min'] * scale))
        pos = ap.curve_pos(d, val)
        assert pos == int(round(val * scale)) - int(round(d['min'] * scale))
        assert abs(ap.curve_value(d, pos) - val) < 1e-9


def test_filter_env_defaults_come_from_the_schema_not_a_second_hardcode():
    import amyparams as ap
    # bp1's sustain slider defaults to 0, but _adsr_string used to hardcode a
    # sustain default of 1 for BOTH envelopes -- an untouched filter sustain
    # was SENT as 1 while the editor showed 0.
    assert ap.ENV_DEFAULTS['filter']['sustain'] == 0
    assert ap.ENV_DEFAULTS['amp']['sustain'] == 1
    calls = ap.synth_send_calls({'filt_attack': 10})
    assert {'osc': ap.OSC_CTL, 'bp1': '10,1,100,0,100,0'} in calls


# NOTE: test_fx_calls_map_to_amy_kwargs was removed here: the concurrent
# group-B change (commit 8f6f2b05) deleted the dead amyparams.fx_calls (its
# REVIEW-KITS finding 5 -- the production apply path uses fx_send_strings /
# fx_eq_string / the reverb room string instead). The layering test below keeps
# its still-live fx_value / fx_eq_string coverage.


def test_fx_layering_patch_values_are_the_baseline():
    import amyparams as ap
    pfx = {'chorus': {'level': 1, 'freq': 0.83, 'depth': 0.5}}
    # editor shows the patch's value when the user never touched the bus
    assert ap.fx_value({}, pfx, 'chorus', 'level') == 1
    assert ap.fx_value({}, pfx, 'chorus', 'freq') == 0.83
    # a user override wins...
    assert ap.fx_value({'chorus': {'level': 0.2}}, pfx, 'chorus', 'level') == 0.2
    # ...and the resulting SEND keeps the patch's other fields, not defaults
    o = ap.fx_send_strings({'chorus': {'level': 0.2}}, pfx)
    assert o['chorus'] == '0.2,,0.83,0.5'
    # untouched bus still not sent even though the patch sets it
    assert ap.fx_send_strings({}, pfx) == {}
    # eq: user layer over patch values
    pfx2 = {'eq': {'low': 7, 'mid': -3, 'high': -3}}
    assert ap.fx_eq_string({}, pfx2) is None
    assert ap.fx_eq_string({'eq': {'low': 0}}, pfx2) == '0,-3,-3'


def test_fx_bus_baseline_and_overlay():
    import amyparams as ap
    juno = {'chorus': {'level': 1, 'freq': 0.83, 'depth': 0.5},
            'eq': {'low': 7, 'mid': -3, 'high': -3}}
    base = ap.fx_bus_baseline(juno)
    assert base['chorus'] == '1,,0.83,0.5'
    assert base['eq'] == '7,-3,-3'
    # an instrument whose patch sets no FX gets a clean (defaults) baseline --
    # this is what makes multi-instrument bus state deterministic
    assert ap.fx_bus_baseline({})['chorus'].startswith('0,')
    # user overlay: touched bus only, unset fields keep the patch's values
    o = ap.fx_send_strings({'chorus': {'level': 0.2}}, juno)
    assert o == {'chorus': '0.2,,0.83,0.5'}
    assert ap.fx_send_strings({}, juno) == {}


# ---------------------------------------------------------------------------
# Task 2: the same divergence, established by evidence for the OTHER params.
# The junos' patch strings prove it beyond the envelopes -- all 128 diverge on
# cutoff, 86 on resonance, 120 on kbd track, 108 on level -- so the editor's
# numbers were wrong there in exactly the way they were wrong for the ADSR.
# ---------------------------------------------------------------------------

def test_patch_params_reads_what_the_juno_patch_really_bakes():
    import amyparams as ap
    # Juno A11 Brass Set 1: the editor used to draw the schema's cutoff
    # 1000 Hz / resonance 0.7 / level 1.0 on a patch whose real values are
    # these. Numbers on a knob read as authoritative; these were invented.
    pp = ap.patch_params({'type': 'juno6', 'patch': 0})
    assert pp['filter_freq'] == 179.93           # not 1000
    assert pp['resonance'] == 0.93               # not 0.7
    assert pp['filter_kbd'] == 0.677             # not 0
    assert pp['level'] == 0.85                   # not 1.0
    assert pp['lfo_freq'] == 0.945               # not 4
    assert pp['amp_decay'] == 1355               # not 100
    for name, v in pp.items():
        assert ap.param_value_source({}, pp, name) == (v, 'patch'), name
    # the user still wins over the patch
    assert ap.param_value_source({'filter_freq': 800}, pp,
                                 'filter_freq') == (800, 'user')


def test_patch_params_are_a_pure_view_of_the_patch_string():
    import amyparams as ap
    # The generated table and the live GM path are ONE parser, so the table
    # cannot drift into saying something the patch string does not.
    s = "v0w20F179.93,0.677,,5.024,0,0R0.93a0.85,,1,1,0A30,1,1355,0.354,232,0Z"
    pp = ap.patch_params_from_string(s)
    assert pp['filter_freq'] == 179.93
    assert pp['filter_kbd'] == 0.677       # coef slot 1 (COEF_NOTE)
    assert pp['filter_env'] == 0           # coef slot 4 (COEF_EG1), really 0
    assert pp['lfo_filter'] == 0           # coef slot 5 (COEF_MOD)
    assert pp['resonance'] == 0.93
    assert pp['level'] == 0.85
    assert pp['amp_decay'] == 1355
    # a field the patch does not carry is ABSENT, never guessed at
    assert 'pan' not in pp and 'portamento' not in pp and 'filt_decay' not in pp
    assert ap.patch_params_from_string('') == {}
    assert ap.patch_params_from_string('garbage') == {}


def test_patch_params_reads_oscs_1_to_3_only_where_the_layout_matches():
    import amyparams as ap
    # The deck's Osc A/Osc B/LFO are oscs 2/3/1 of the four-osc layout whose
    # signature is a SILENT (w20) control osc -- what the juno bank uses.
    juno = ap.patch_params({'type': 'juno6', 'patch': 0})
    assert juno['oscA_wave'] == 1 and juno['oscB_wave'] == 3
    assert juno['oscA_duty'] == 0.902
    # The DX7 bank does NOT: its oscs 2..7 are FM operators. Calling operator 2
    # "Osc A" would be a category error dressed as a fact -- and operator
    # levels run to 2.0, which oscA_level's 0..1 range cannot even hold, so
    # seeding it would have re-created the pinned-knob trap on a fiction.
    dx7 = ap.patch_params({'type': 'dx7', 'patch': 128})
    for n in ('oscA_level', 'oscA_wave', 'oscB_level', 'lfo_freq', 'lfo_wave'):
        assert n not in dx7, n
    # its osc-0 amp IS the control osc's, but it equals the schema default, so
    # it carries no information and the generator drops the row entirely
    assert dx7 == {}


def test_the_honesty_marker_is_scoped_by_evidence_not_sprayed():
    import amyparams as ap
    # Exactly the params whose schema default matches NEITHER AMY's
    # reset_osc_params() state NOR anything any patch bakes. Ten, not
    # twenty-five: a marker on every control teaches the eye to skip it.
    fabricated = {d['name'] for d in ap.PARAMS
                  if ap.is_fabricated(d, 'default')}
    assert fabricated == {
        'filter_freq',              # AMY resets filter_type to FILTER_NONE
        'lfo_freq',                 # AMY's logfreq default is 440 Hz, not 4
        'amp_attack', 'amp_decay', 'amp_sustain', 'amp_release',
        'filt_attack', 'filt_decay', 'filt_sustain', 'filt_release',
    }
    # These are NOT marked, because the schema default IS the truth: it is
    # exactly what reset_osc_params() leaves in the engine when no patch
    # speaks. Verified against amy/src/amy.c, value by value.
    for name, val in (('level', 1.0),            # amp_coefs[COEF_CONST]=1.0
                      ('oscA_level', 1.0), ('oscB_level', 1.0),
                      ('pan', 0.5),              # pan_coefs[COEF_CONST]=0.5
                      ('portamento', 0),         # portamento_alpha=0
                      ('resonance', 0.7),        # resonance=0.7f
                      ('oscA_duty', 0.5), ('oscB_duty', 0.5),  # duty=0.5
                      ('oscA_freq', 440), ('oscB_freq', 440),  # logfreq 0=440Hz
                      ('filter_kbd', 0), ('filter_env', 0),    # coefs zeroed
                      ('lfo_pitch', 0), ('lfo_pwm', 0), ('lfo_filter', 0)):
        d = ap.PARAM_BY_NAME[name]
        assert d['default'] == val, name
        assert d['truth'] == ap.TRUTH_AMY, name
        assert ap.is_fabricated(d, 'default') is False, name
    # deck-side constructs no patch can set: true by construction
    for name in ('reverb_send', 'piano_quality'):
        assert ap.PARAM_BY_NAME[name]['truth'] == ap.TRUTH_DECK
        assert ap.is_fabricated(ap.PARAM_BY_NAME[name], 'default') is False
    # a user or patch value is never marked, whatever its truth class
    for src in ('user', 'patch'):
        assert ap.is_fabricated(ap.PARAM_BY_NAME['amp_decay'], src) is False
    # FX defs share names with PARAMS ('level'), so the marker keys on the DEF
    assert all(d['truth'] == ap.TRUTH_DECK for d in ap.fx_defs())
    assert not any(ap.is_fabricated(d, 'default') for d in ap.fx_defs())


def test_pan_matches_the_pan_amy_actually_implements():
    import amyparams as ap
    # AMY's pan is 0..1 and CLAMPED (lgain_of_pan/rgain_of_pan floor it at 0),
    # centre 0.5 (reset_osc_params). The slider used to run -1..1 default 0.0:
    # its entire left half sent values AMY clamps to hard-left, and the "0.00"
    # it printed for an untouched pan was neither centre nor reachable.
    d = ap.PARAM_BY_NAME['pan']
    assert (d['min'], d['max'], d['default']) == (0.0, 1.0, 0.5)
    # an old stored value still displays and still sends ITS OWN number -- the
    # range change must not rewrite what a user saved
    assert ap.param_value_source({'pan': -0.3}, {}, 'pan') == (-0.3, 'user')
    assert {'pan': -0.3} in ap.synth_send_calls({'pan': -0.3})


def test_seeding_a_juno_display_still_sends_nothing():
    import amyparams as ap
    ppv = ap.patch_params({'type': 'juno6', 'patch': 11})
    penv = ap.patch_env({'type': 'juno6', 'patch': 11})
    assert ppv['filter_freq'] == 4678.2         # we DO know a great deal now...
    assert penv['amp']['decay'] == 142057
    # ...and knowing it must still send NOTHING while the user has touched
    # nothing. Seeding is display-only; only stored params are ever sent.
    assert ap.synth_send_calls({}, penv) == []
    assert ap.synth_send_calls(None, penv) == []
    # a touched attack restates bp0's siblings from the PATCH (142057 ms), not
    # from the schema's 100 ms -- the juno bank now gets the fix GM got
    calls = ap.synth_send_calls({'amp_attack': 10}, penv)
    assert {'osc': ap.OSC_CTL, 'bp0': '10,1,142057,1,22,0'} in calls
    # and a touched NON-envelope param drags nothing along: coef strings leave
    # unset slots empty, which AMY reads as "keep the patch's value"
    calls = ap.synth_send_calls({'filter_env': 3}, penv)
    assert not any('bp0' in c for c in calls)
    assert {'osc': ap.OSC_CTL, 'filter_freq': ',,,,3'} in calls


def test_patchparams_table_is_in_sync_with_patches_h():
    # The table is generated; a stale one is a lie with a build step in front
    # of it. Regenerate with: python tools/gen_patchparams.py
    import subprocess
    root = os.path.dirname(_HERE)
    gen = os.path.join(root, 'tools', 'gen_patchparams.py')
    if not os.path.exists(os.path.join(root, 'amy', 'src', 'patches.h')):
        pytest.skip('amy submodule not checked out')
    r = subprocess.run([sys.executable, gen, '--check'], cwd=root,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_patchparams_table_covers_the_juno_bank_and_nothing_it_cannot_read():
    import patchparams
    import amyparams as ap
    # every juno patch carries a real envelope + cutoff
    for p in range(128):
        row = patchparams.PARAMS[p]
        assert row['filter_freq'] > 0, p
        assert {'amp_attack', 'amp_decay', 'amp_sustain',
                'amp_release'} <= set(row), p
    # the divergence, quantified: this is the evidence the marker rests on
    assert sum(1 for p in range(128)
               if patchparams.PARAMS[p]['filter_freq'] != 1000) == 128
    assert sum(1 for p in range(128)
               if patchparams.PARAMS[p].get('resonance') is not None) == 86
    # the DX7 bank is absent: nothing it bakes is both readable and news
    assert not any(p in patchparams.PARAMS for p in range(128, 256))
    assert patchparams.patch_params(9999) == {}
    # every name in the table is a real param this schema can show
    for row in patchparams.PARAMS.values():
        for name in row:
            assert name in ap.PARAM_BY_NAME, name
    # and every value the table seeds is INSIDE its slider's range, so no
    # seeded control can present a pinned knob for a stray tap to knock off
    for p, row in patchparams.PARAMS.items():
        for name, v in row.items():
            d = ap.PARAM_BY_NAME[name]
            if d['type'] != 'slider':
                continue
            lo = int(round(d['min'] * d.get('scale', 1)))
            hi = int(round(d['max'] * d.get('scale', 1)))
            assert lo <= round(v * d.get('scale', 1)) <= hi, (p, name, v)


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


def test_forwarder_reapply_params_board_instrument_is_noop(deck):
    # A board instrument never gets a local synth (_state['synths']), so the
    # old code fell through to a full start() -- an ~80-200ms router rebuild
    # -- on EVERY parameditor slider tick during a drag, for zero benefit
    # (board params aren't pushed here; that's a separate feature). It must
    # now no-op instead of rebuilding.
    deckcfg, forwarder = deck
    b = deckcfg.add_instrument(device=0, channel=2)['id']
    forwarder.start()
    assert b not in forwarder._state['synths']
    calls = []
    orig_start = forwarder.start
    forwarder.start = lambda: calls.append(1)
    try:
        forwarder.reapply_params(b)
        assert calls == []                      # no rebuild triggered
    finally:
        forwarder.start = orig_start


def test_forwarder_reapply_params_internal_missing_synth_still_rebuilds(deck):
    # Regression guard: an INTERNAL instrument with no synth yet (e.g. before
    # the first start()) must still fall back to a full start() -- only
    # board instruments get the no-op short-circuit.
    deckcfg, forwarder = deck
    iid = deckcfg.instruments()[0]['id']
    assert iid not in forwarder._state['synths']
    calls = []
    orig_start = forwarder.start
    forwarder.start = lambda: calls.append(1)
    try:
        forwarder.reapply_params(iid)
        assert calls == [1]
    finally:
        forwarder.start = orig_start


def test_forwarder_reapply_fx(deck):
    deckcfg, forwarder = deck
    forwarder.start()
    deckcfg.set_device_fx('internal', 'reverb', 'level', 0.4)
    amy = sys.modules['amy']
    amy._sends.clear()
    forwarder.reapply_fx()
    assert any(k.get('reverb') == '0.4,0.85,0.5' for k in amy._sends)


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
    # Reverb is the shared device ROOM sent as a wire string (the old
    # amy.reverb(level) API is gone from the apply path)
    deckcfg, forwarder = deck
    deckcfg.set_device_fx('internal', 'reverb', 'level', 0.4)
    amy = sys.modules['amy']
    amy._sends.clear()
    forwarder.start()
    assert any(k.get('reverb') == '0.4,0.85,0.5' for k in amy._sends)


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


# ---------------------------------------------------------------------------
# E-4: RAM-patch slot map bounds (pure invariants + forwarder refusal)
# ---------------------------------------------------------------------------

def test_slot_map_fits_amy_pool():
    import synthkits
    # melodic block ends exactly where the kit block begins
    assert (synthkits.SLOT_MELODIC + synthkits.MAX_MELODIC_SLOTS
            == synthkits.SLOT_KITS)
    # worst-case kit map stays inside AMY's max_memory_patches window
    top = (synthkits.SLOT_KITS
           + synthkits.MAX_KIT_SLOTS * synthkits.SLOT_KIT_STRIDE)
    assert top <= synthkits.SLOT_LIMIT == 1024 + 128
    # container model: a kit needs its base slot (the whole-kit container
    # patch) plus a little headroom for fallback pads (classify_hit None)
    assert synthkits.SLOT_KIT_STRIDE >= 2    # container + >=1 fallback slot
    # the migration's point: the kit cap is no longer 5
    assert synthkits.MAX_KIT_SLOTS >= 20


def test_forwarder_refuses_sixth_melodic_instrument(deck):
    deckcfg, forwarder = deck
    import synthkits
    iid0 = deckcfg.instruments()[0]['id']
    deckcfg.set_instrument(iid0, 'type', 'gm')
    for ch in range(2, 7):                       # 5 more gm instruments
        deckcfg.add_instrument(device='internal', channel=ch, type='gm')
    forwarder.start()
    synths = forwarder._state['synths']
    built = [i for i, s in synths.items() if s is not None]
    # exactly MAX_MELODIC_SLOTS built; the 6th refused and recorded
    assert len(built) == synthkits.MAX_MELODIC_SLOTS
    assert len(forwarder._state['err_iids']) == 1


# ---------------------------------------------------------------------------
# E-6: deckcfg.load() aliasing canary -- read paths must not mutate the cfg
# ---------------------------------------------------------------------------

def test_read_paths_do_not_mutate_config(deck):
    deckcfg, forwarder = deck
    import copy
    import shellmodel
    deckcfg.add_instrument(device='internal', channel=2, type='drums')
    before = copy.deepcopy(deckcfg.load())
    # panel-build-ish read paths
    shellmodel.chip_specs(deckcfg.instruments(), deckcfg.active_instrument())
    deckcfg.device_list()
    deckcfg.device_load('internal')
    for i in deckcfg.instruments():
        shellmodel.instrument_sound(i)
    forwarder.start()                    # the router is read-only over cfg
    assert deckcfg.load() == before, \
        "a read path mutated the live config dict (E-6 contract)"


# ---------------------------------------------------------------------------
# O-2: C-side MIDI route table upload + Python board-forwarding handoff
# ---------------------------------------------------------------------------

def test_c_router_upload_and_board_handoff(deck):
    deckcfg, forwarder = deck
    tulip = sys.modules['tulip']
    calls = {}
    tulip.midi_routes = lambda masks, py_mask, tap: calls.update(
        masks=list(masks), py=py_mask, tap=tap)
    tulip.midi_activity = lambda: 0
    try:
        deckcfg.add_instrument(device=0, channel=2)             # board ch2
        deckcfg.add_instrument(device='internal', channel=1)    # layer ch1
        sent = _install_and_reset_sent()
        forwarder.start()
        assert calls['masks'][1] == 1      # ch2 -> board device 0 in C
        assert calls['py'] & 1             # ch1 layered: Python still routes
        assert not (calls['py'] & 2)       # ch2 fully C-owned
        assert calls['tap'] is False
        # Python must NOT double-forward what C already sent
        sent.clear()
        forwarder._route(bytes([0x91, 60, 100]))   # ch2 note on
        assert sent == []                  # handed off to the C router
        # tap toggle re-uploads with notify_all
        forwarder.set_midi_tap(True)
        assert calls['tap'] is True
    finally:
        del tulip.midi_routes
        del tulip.midi_activity
        forwarder._state['c_router'] = False
        forwarder._state['py_tap'] = False


# ---------------------------------------------------------------------------
# O-5: rebuild_one -- one synth rebuilt in place, others untouched
# ---------------------------------------------------------------------------

def test_rebuild_one_reuses_slot_and_skips_others(deck):
    deckcfg, forwarder = deck
    import synthkits
    iid0 = deckcfg.instruments()[0]['id']
    deckcfg.set_instrument(iid0, 'type', 'gm')
    deckcfg.add_instrument(device='internal', channel=2, type='gm')
    forwarder.start()
    other = [i['id'] for i in deckcfg.instruments() if i['id'] != iid0][0]
    other_syn = forwarder._state['synths'][other]
    old_syn = forwarder._state['synths'][iid0]
    slot = forwarder._state['built'][iid0]['slot']
    assert slot == synthkits.SLOT_MELODIC
    deckcfg.set_instrument(iid0, 'patch', 12)
    forwarder.rebuild_one(iid0)
    assert forwarder._state['synths'][other] is other_syn      # untouched
    assert not other_syn.released
    new_syn = forwarder._state['synths'][iid0]
    assert new_syn is not old_syn and old_syn.released
    assert new_syn.patch == slot           # recorded slot reused in place
    # a topology change (channel move) falls back to the full rebuild
    deckcfg.set_instrument(iid0, 'channel', 5)
    forwarder.rebuild_one(iid0)
    assert forwarder._state['synths'][other] is not other_syn  # rebuilt all


def test_real_synth_free_list_recycles_auto_numbers(deck):
    """F-1 (round 2): the REAL tulip/shared/py/synth.py allocator. The stub
    PatchSynth above never modeled numbering, so the free-list fix had no
    coverage: auto numbers must start at 18 (above channels 1-16 + the
    audition scratch 17, F-4/F-8), release() must recycle them (repeated
    rebuild_one calls used to walk the counter past AMY's 64-instrument
    cap), channel-pinned numbers must never enter the pool, and
    set_channel must retire the old auto number into it."""
    import importlib.util
    pydir = os.path.abspath(os.path.join(_HERE, '..', 'tulip', 'shared', 'py'))
    added = pydir not in sys.path
    if added:
        sys.path.insert(0, pydir)      # for `from patches import drumkit` etc.
    try:
        spec = importlib.util.spec_from_file_location(
            'real_synth', os.path.join(pydir, 'synth.py'))
        rs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rs)
        PS = rs.PatchSynth
        PS.amy_synth_allocated = set()
        PS.amy_synth_next = 18
        PS.amy_synth_free = []
        a = PS(patch=0)
        b = PS(patch=1)
        assert (a.synth, b.synth) == (18, 19)   # base clears ch 1-16 + scratch 17
        a.release()
        assert a.synth is None
        c = PS(patch=2)
        assert c.synth == 18                    # recycled, not a fresh 20
        assert PS.amy_synth_next == 20          # counter did not advance
        # release/realloc cycles are counter-stable after the first alloc
        # primes the pool (the F-1 leak shape: +1 per cycle, forever)
        prime = PS(patch=0)
        prime.release()
        before = PS.amy_synth_next
        for _ in range(10):
            s = PS(patch=0)
            s.release()
        assert PS.amy_synth_next == before
        # channel-pinned numbers belong to their channel, never the pool
        d = PS(patch=0, channel=5)
        d.release()
        assert 5 not in PS.amy_synth_free
        # set_channel retires the auto number into the pool exactly once
        e = PS(patch=0)
        n = e.synth
        e.set_channel(7)
        assert n in PS.amy_synth_free
        e.release()
        assert 7 not in PS.amy_synth_free
    finally:
        if added:
            sys.path.remove(pydir)
        sys.modules.pop('real_synth', None)


# ---------------------------------------------------------------------------
# P-ENG-1: gmbig.py dead-table eviction -- emu4 runtime arrays must still
# reproduce the old FONTS['emu4'] dict outputs byte-for-byte.
# ---------------------------------------------------------------------------

# Reference sample taken from the OLD FONTS['emu4'] dict (program, preset,
# root, nzones), spanning first..last incl. the two non-monotonic-preset rows
# (program 90->2038 sits above 93->2036). Any transcription slip in the derived
# arrays changes at least one patch_string / has_program below.
_EMU4_REFERENCE = [
    (0, 1903, 74, 2),     # first program
    (2, 1905, 61, 2),
    (24, 1936, 58, 2),
    (46, 1975, 69, 2),
    (90, 2038, 75, 1),    # preset non-monotonic vs. neighbour
    (93, 2036, 68, 2),
    (112, 2053, 40, 1),
    (119, 2060, 56, 1),   # last program
]

# Program count of the OLD emu4 table (len(FONTS['emu4']) before the rewrite).
_EMU4_PROGRAM_COUNT = 92


def test_gmbig_api_matches_reference():
    import gmbig
    assert gmbig.PRESET_BASE == 1024           # matches GM_BIG_PRESET_BASE
    assert gmbig.FONT == 'emu4'
    assert len(gmbig.programs()) == _EMU4_PROGRAM_COUNT
    assert gmbig.programs() == sorted(gmbig.programs())   # sorted contract
    for program, preset, root, nzones in _EMU4_REFERENCE:
        assert gmbig.has_program(program) is True
        # wire format must be identical to the old FONTS[emu4][program] recipe
        expected = "v0w7p%db2A5,1,60000,0.85,220,0Z" % preset
        assert gmbig.patch_string(program) == expected


def test_gmbig_missing_program_falls_back_like_old_code():
    import gmbig
    # program 1 is not covered by emu4; old code did FONTS['emu4'][1] -> KeyError
    assert 1 not in gmbig.programs()
    assert gmbig.has_program(1) is False
    with pytest.raises(KeyError):
        gmbig.patch_string(1)


# ---------------------------------------------------------------------------
# _AmyBatch: a message >= 1000 chars must bypass the batch line buffer
# (tulip.amy_send_batch silently truncates at MAX_MESSAGE_LEN=1023) and arrive
# complete and in order.
# ---------------------------------------------------------------------------

def test_amy_batch_long_line_sent_individually(deck):
    _deckcfg, forwarder = deck
    tulip = sys.modules['tulip']
    amy = sys.modules['amy']
    stream = []                         # reconstructed ordered wire stream
    # amy_send_batch receives a '\n'-joined batch; override_send is the plain
    # single-message path (both patched onto the fakes, cleaned up in finally).
    tulip.amy_send_batch = lambda s: stream.extend(s.split('\n'))
    amy.override_send = lambda m: stream.append(m)      # the saved _orig path
    long_msg = 'u0' + 'a' * 1500        # 1502 chars: past MAX_MESSAGE_LEN
    try:
        with forwarder._AmyBatch():
            amy.override_send('m1')     # collected into the batch
            amy.override_send('m2')
            amy.override_send(long_msg)  # must be sent alone, complete
            amy.override_send('m3')
        # complete + in order: the batch-so-far flushed, then the long one
        # alone, then the rest.
        assert stream == ['m1', 'm2', long_msg, 'm3']
        assert len(long_msg) >= 1000    # the whole thing, untruncated
    finally:
        del tulip.amy_send_batch
        del amy.override_send


# ---------------------------------------------------------------------------
# rebuild_one must batch its ~60 sends into exactly ONE amy_send_batch call.
# ---------------------------------------------------------------------------

def test_rebuild_one_batches_into_single_call(deck):
    deckcfg, forwarder = deck
    iid0 = deckcfg.instruments()[0]['id']
    deckcfg.set_instrument(iid0, 'type', 'gm')
    forwarder.start()
    tulip = sys.modules['tulip']
    amy = sys.modules['amy']
    batch_calls = []
    tulip.amy_send_batch = lambda s: batch_calls.append(s)
    amy.override_send = None
    orig_send = amy.send
    # route the fake amy.send through override_send (like real amy) so the
    # batch actually collects the rebuild's messages.
    def _send(**k):
        orig_send(**k)
        if amy.override_send is not None:
            amy.override_send(repr(k))
    amy.send = _send
    try:
        deckcfg.set_instrument(iid0, 'patch', 12)     # non-topology edit
        forwarder.rebuild_one(iid0)
        assert forwarder._state['synths'][iid0] is not None
        assert len(batch_calls) == 1                  # one MP->C call, not ~60
    finally:
        amy.send = orig_send
        del tulip.amy_send_batch
        del amy.override_send


# ===========================================================================
# Group-A correctness fixes (PyA): CFG-1/6, KITS-1/2/3, MIDI-2/5/6, E-1 UAF
# ===========================================================================

# --- CFG-1: DEFAULTS['fx'] must not be mutated in place -------------------
def test_device_fx_does_not_pollute_defaults(deck):
    """set_device_fx mutates cfg['fx'] in place; load() must deep-copy the
    mutable defaults so that mutation never reaches module-level DEFAULTS and
    bleeds into a later fresh/reset config in the same session (CFG-1)."""
    deckcfg, _ = deck
    deckcfg.set_device_fx('internal', 'reverb', 'level', 0.4)
    assert deckcfg.DEFAULTS['fx'] == {}, "set_device_fx polluted DEFAULTS['fx']"
    # simulate a factory reset / fresh config produced in the same process
    deckcfg._state.clear()
    try:
        os.remove(deckcfg.PATH)
    except OSError:
        pass
    fresh = deckcfg.load()
    assert fresh['fx'] == {}, "a fresh config inherited a prior device's FX"
    assert fresh['fx'] is not deckcfg.DEFAULTS['fx']   # not the aliased object


# --- CFG-6: apply() volume fallback aligns with DEFAULTS['volume'] ---------
def test_apply_volume_fallback_matches_default(deck):
    deckcfg, _ = deck
    amy = sys.modules['amy']
    calls = []
    orig = amy.volume
    amy.volume = lambda *a, **k: calls.append(a)
    try:
        deckcfg.apply({})          # degraded path (boot cfg-load failure): no keys
    finally:
        amy.volume = orig
    assert calls and calls[0][0] == deckcfg.DEFAULTS['volume'] == 1


# --- MIDI-2: RPN/NRPN data-entry CCs must not coalesce ---------------------
def test_no_coalesce_covers_rpn_nrpn(uipatch):
    _ui, ui_patch = uipatch
    nc = ui_patch._NO_COALESCE
    for cc in (6, 38, 96, 97, 98, 99, 100, 101):   # data-entry / RPN / NRPN
        assert cc in nc
    for cc in (0, 32, 64, 120, 127):               # existing guards retained
        assert cc in nc
    assert 1 not in nc and 74 not in nc            # continuous streams coalesce


# --- MIDI-5: layered retrigger must not strand voices ----------------------
def test_layered_retrigger_does_not_strand_voices(deck):
    deckcfg, forwarder = deck
    deckcfg.add_instrument(device='internal', channel=1)   # 2 internals: layered
    forwarder.start()
    assert 1 not in forwarder._state['c_channels']
    forwarder._route((0x90, 60, 100))              # first note-on
    forwarder._route((0x90, 60, 100))              # re-trigger, no note-off
    syns = list(forwarder._state['synths'].values())
    assert all(s.on.count(60) == 2 for s in syns)  # two voices per synth
    forwarder._route((0x80, 60, 0))                # single note-off
    assert all(60 not in s.on for s in syns), "a stale voice was stranded"
    assert forwarder._state['notes'] == {}


# --- MIDI-6: activity() must not double-count layered traffic --------------
def test_activity_does_not_double_count(deck):
    deckcfg, forwarder = deck
    tulip = sys.modules['tulip']
    tulip.midi_activity = lambda: 7
    try:
        forwarder._state['seen'] = 5
        forwarder._state['c_router'] = True        # C counts everything pre-route
        assert forwarder.activity() == 7           # C counter alone (no +seen)
        forwarder._state['c_router'] = False        # Python sees everything
        assert forwarder.activity() == 5
    finally:
        del tulip.midi_activity
        forwarder._state['c_router'] = False


# --- KITS-2: gm2 uncovered program degrades to sound, not silence ----------
def test_gm2_uncovered_program_still_sounds(deck):
    deckcfg, forwarder = deck
    import gmbig
    assert not gmbig.has_program(1)                # program 1 absent from emu4
    iid = deckcfg.instruments()[0]['id']
    deckcfg.set_instrument(iid, 'type', 'gm2')
    deckcfg.set_instrument(iid, 'patch', 1)
    amy = sys.modules['amy']
    amy._sends.clear()
    forwarder.start()
    assert forwarder._state['synths'].get(iid) is not None   # not KeyError-muted
    assert iid not in forwarder._state.get('err_iids', ())
    assert any('patch_string' in k for k in amy._sends)      # a covered patch stored
    # the in-place patch-swap path (rebuild_one) shares the same fallback
    assert not gmbig.has_program(3)
    deckcfg.set_instrument(iid, 'patch', 3)
    forwarder.rebuild_one(iid)
    assert forwarder._state['synths'].get(iid) is not None   # still sounds
    assert iid not in forwarder._state.get('err_iids', ())


# --- KITS-3: hit_name strips only the 6-char dedup hash --------------------
def test_hit_name_strips_only_six_char_hash():
    import synthkits
    synthkits._state['index'] = {'kits': {}, 'packs': {}, 'names': {}}
    # the generator's 6-char all-hex dedup hash IS stripped (hex-word + digits)
    assert synthkits.hit_name('kick_7dbbaa') == 'kick'
    assert synthkits.hit_name('brush1_139732') == 'brush1'
    # legit numeric / hex-word suffixes are PRESERVED (were wrongly collapsed)
    assert synthkits.hit_name('kick_1200') == 'kick_1200'
    assert synthkits.hit_name('snare_9090') == 'snare_9090'
    assert synthkits.hit_name('x_face') == 'x_face'
    assert synthkits.hit_name('x_beef') == 'x_beef'


# --- KITS-1: one unresolvable hit key skips its pad, keeps the kit, no leak -
def test_synthkit_skips_unresolvable_hit_no_leak():
    _install_hw_mocks()
    for m in ('synthkits', 'drums_kit'):
        sys.modules.pop(m, None)
    import synthkits
    import drums_kit
    synth = sys.modules['synth']
    notes = {36: 'p/kick', 38: 'p/missing', 40: 'p/snare'}
    synthkits.kit_notes = lambda k: dict(notes)
    synthkits.store_patch = lambda slot, ps: slot
    # fake corpus: 'p/missing' resolves to nothing (partial deploy / index
    # drift) -> its pad must be skipped without silencing the kit
    synthkits._state['packs']['p'] = {
        'p/kick': {'oscs': [{'wave': 0, 'amp': '1'}]},
        'p/snare': {'oscs': [{'wave': 0, 'amp': '1'}]},
    }

    kit = drums_kit.SynthKit('somekit')
    assert set(kit.pads) == {36, 40}           # bad pad skipped, rest audible
    assert 38 not in kit.note_hits
    # container model: the whole kit is exactly ONE synth, no per-hit synths
    assert len(synth.PatchSynth.instances) == 1
    assert kit.hit_synths == {}
    kit.release()
    assert all(s.released for s in synth.PatchSynth.instances)   # no leaked number


# --- P0: resync one-voice sampled kits -- a sampled kit patch (384-390, and
# amy's own 258) is now ONE voice with N voice-specific oscs baked in
# (patches.h patch_oscs[]: 38/42/32), not N identical voices. make_synth()
# must force num_voices=1 for these regardless of what a saved config or the
# rack.py voices slider asks for -- 6-10 voices x 38-42 oscs blew past AMY's
# 250-osc pool and starved allocation for every drum instrument.
def test_make_synth_clamps_sampled_kit_to_one_voice():
    _install_hw_mocks()
    for m in ('synthkits', 'drums_kit'):
        sys.modules.pop(m, None)
    import drums_kit
    # every id drums_kit knows as a sampled kit (384-390) plus amy's own
    # MIDI-channel-10 default kit (258), which bakes the identical one-voice
    # format even though the deck's kit selector never offers it
    for kit in sorted(drums_kit.SAMPLED_KIT_IDS):
        s = drums_kit.make_synth(kit, num_voices=10)
        assert s.num_voices == 1, "kit %r got %r voices" % (kit, s.num_voices)
    # a stale saved config's num_voices (6, 10, or the slider's 1..32) must
    # never survive to the synth -- confirm the clamp actually overrides a
    # non-default input rather than coincidentally matching a default
    s = drums_kit.make_synth(384, num_voices=6)
    assert s.num_voices == 1
    s = drums_kit.make_synth(390, num_voices=32)
    assert s.num_voices == 1


def test_make_synth_synth_kit_path_unaffected_by_clamp():
    """Our SynthKit path (patch RAM 1030+, 'synth:<key>' ids) is its own
    one-voice container synth (same model as the sampled kits) -- confirm it
    still builds normally and num_voices= passed to make_synth is simply not
    read there."""
    _install_hw_mocks()
    for m in ('synthkits', 'drums_kit'):
        sys.modules.pop(m, None)
    import synthkits
    import drums_kit
    notes = {36: 'p/kick', 38: 'p/snare'}
    synthkits.kit_notes = lambda k: dict(notes)
    synthkits.store_patch = lambda slot, ps: slot
    synthkits._state['packs']['p'] = {
        'p/kick': {'oscs': [{'wave': 0, 'amp': '1'}]},
        'p/snare': {'oscs': [{'wave': 0, 'amp': '1'}]},
    }

    kit = drums_kit.make_synth('synth:somekit', num_voices=10)
    assert isinstance(kit, drums_kit.SynthKit)
    assert set(kit.pads) == {36, 38}
    # the container synth is always built with 1 voice (its oscs ARE the
    # kit) -- unrelated to and unaffected by the sampled-kit clamp above
    assert kit.kit_synth.num_voices == 1
    kit.release()


# --- E-1: patch-picker search debounce must not UAF a freed panel ----------
def _import_instrument_with_fakes():
    """Import instrument.py against lightweight LVGL/deckui fakes so the
    search-debounce UAF guard is exercisable on the host (the real UI stack is
    not importable here). tulip.defer runs callbacks INLINE so the debounced
    _do fires synchronously."""
    _install_hw_mocks()
    tulip = sys.modules['tulip']
    tulip.defer = lambda fn, arg, ms: fn(arg)

    class _NS:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    lv = types.ModuleType('lvgl')
    lv.EVENT = _NS(DELETE=100, VALUE_CHANGED=2, FOCUSED=3, DEFOCUSED=4,
                   CLICKED=1)
    sys.modules['lvgl'] = lv

    dk = types.ModuleType('deckui')
    for c in ('ACCENT', 'SURFACE', 'SURFACE2', 'TEXT', 'MUTED', 'WHITE', 'BG',
              'ORANGE', 'FONT_S', 'FONT_M', 'FONT_L'):
        setattr(dk, c, object())
    dk.c = lambda x: x
    dk.label = lambda *a, **k: object()
    sys.modules['deckui'] = dk

    patches = types.ModuleType('patches')
    patches.patches = ['patch%d' % i for i in range(257)]
    sys.modules['patches'] = patches

    catalog = types.ModuleType('catalog')
    catalog.JUNO_END = 128
    catalog.DX_END = 256
    catalog.engine_of = lambda p: 'juno6'
    sys.modules['catalog'] = catalog

    sys.modules.pop('instrument', None)
    return importlib.import_module('instrument')


def test_search_debounce_guard_after_teardown():
    instrument = _import_instrument_with_fakes()
    calls = []
    instrument._build_list = lambda: calls.append(1)

    class _TA:
        def get_text(self):
            return 'ab'

    instrument._s.clear()
    instrument._s.update({'alive': True, 'listbody': object(),
                          'searchta': _TA(), 'search_gen': 0})
    instrument._search_changed(None)              # defer -> _do inline
    assert calls == [1], "live panel: debounce should rebuild the list"

    # panel torn down: LVGL DELETE fires -> _mark_dead flips the alive token
    calls.clear()
    instrument._mark_dead()
    assert instrument._s.get('alive') is False
    instrument._search_changed(None)
    assert calls == [], "freed panel: debounce must not rebuild against it"


def test_build_list_bails_on_freed_body():
    instrument = _import_instrument_with_fakes()

    class _Freed:
        def clean(self):
            raise RuntimeError("poked a deleted widget")

    instrument._s.clear()
    instrument._s.update({'alive': True, 'listbody': _Freed(), 'rows': []})
    instrument._build_list()          # must swallow, not propagate a hard crash
    instrument._s['alive'] = False
    instrument._build_list()          # alive gate: never even calls clean()


# ---------------------------------------------------------------------------
# ParamEditor against a fake LVGL: the two claims that only the RENDER path can
# make good on -- a seeded patch value lands on a real, settable position (not
# pinned at the stop where a stray tap knocks it off), and building the panel
# writes nothing.
# ---------------------------------------------------------------------------

class _RecSlider:
    def __init__(self, value, vmin, vmax, cb, on_release):
        self.value, self.vmin, self.vmax = value, vmin, vmax
        self.cb, self.on_release = cb, on_release

    def align(self, *a):
        pass

    def get_value(self):
        return self.value

    def _drag_to(self, pos):
        """Simulate a real touch: LVGL sets the position, then fires."""
        self.value = pos

        class _E:
            def __init__(s, obj):
                s._o = obj

            def get_target_obj(s):
                return s._o
        if self.cb:
            self.cb(_E(self))
        if self.on_release:
            self.on_release(_E(self))


class _RecLabel:
    def __init__(self, text=''):
        self.text = text

    def align(self, *a):
        pass

    def set_text(self, t):
        self.text = t


def _import_parameditor_with_fakes(tmp_path):
    """parameditor over a permissive LVGL fake + a real deckcfg on a temp file,
    so the seeding path can be driven exactly as the panel drives it."""
    _install_hw_mocks()
    sys.modules['lvgl'] = _LvModuleAuto('lvgl')

    dk = types.ModuleType('deckui')
    for c in ('ACCENT', 'SURFACE', 'SURFACE2', 'TEXT', 'MUTED', 'TEAL', 'WHITE',
              'BG', 'PLACEHOLDER', 'FONT_S', 'FONT_M', 'FONT_MONO'):
        setattr(dk, c, c)
    dk.c = lambda x: x
    dk._labels = []
    dk._sliders = []

    def _label(parent, text='', **k):
        lb = _RecLabel(text)
        dk._labels.append(lb)
        return lb
    dk.label = _label

    def _slider(parent, value, vmin, vmax, w=None, cb=None, color=None, h=None,
                on_release=None):
        s = _RecSlider(value, vmin, vmax, cb, on_release)
        dk._sliders.append(s)
        return s
    dk.slider = _slider
    dk._flat = lambda *a, **k: None
    dk.row = lambda parent: parent
    dk.stepper = lambda *a, **k: None
    dk.switch = lambda *a, **k: None
    dk.style_dropdown = lambda dd: None
    sys.modules['deckui'] = dk

    sys.modules.pop('deckcfg', None)
    deckcfg = importlib.import_module('deckcfg')
    deckcfg.PATH = str(tmp_path / 'deck_config.json')
    deckcfg._state.clear()

    for m in ('amyparams', 'parameditor'):
        sys.modules.pop(m, None)
    return importlib.import_module('parameditor'), dk, deckcfg


def _slider_for(pe, dk, deckcfg, iid, name):
    """Build just one param's control and hand back (def, slider, readout)."""
    import amyparams as ap
    d = ap.PARAM_BY_NAME[name]
    dk._labels[:] = []
    dk._sliders[:] = []
    # group_headers off -> the card's labels are exactly [name, value, min, max]
    ed = pe.ParamEditor(iid, defs=[d], show_advanced=True, group_headers=False)
    ed.build(object())
    return d, dk._sliders[-1], dk._labels[1]


def test_editor_seeds_a_juno_onto_a_settable_position(tmp_path):
    pe, dk, deckcfg = _import_parameditor_with_fakes(tmp_path)
    import amyparams as ap
    iid = deckcfg.instruments()[0]['id']
    deckcfg.set_instrument(iid, 'type', 'juno6')
    deckcfg.set_instrument(iid, 'patch', 11)      # decay 142057 ms, cutoff 4678

    d, s, val = _slider_for(pe, dk, deckcfg, iid, 'amp_decay')
    # the readout tells the truth: the patch's real 142057 ms
    assert val.text == '142057 ms'
    # and the knob is on a REAL position, ~70% along -- not pinned at the stop
    # where the old linear 0..2000 slider put it, and where the tap below
    # would have silently rewritten a 142-second decay to 2 seconds
    assert 0 < s.value < s.vmax
    assert (s.vmin, s.vmax) == (0, ap.LOG_STEPS)
    # THE pinned-knob case: a stray tap that does not move the knob. It is a
    # real touch event, so it stores -- but it now stores essentially what was
    # already there, instead of the range's stop.
    s._drag_to(s.value)
    stored = deckcfg.get_instrument(iid)['params']['amp_decay']
    assert abs(stored - 142057) / 142057.0 < 0.025
    assert stored > 100000, "a tap on the knob dropped a 142 s decay to %r" % stored


def test_editor_build_seeds_the_display_and_stores_nothing(tmp_path):
    pe, dk, deckcfg = _import_parameditor_with_fakes(tmp_path)
    iid = deckcfg.instruments()[0]['id']
    deckcfg.set_instrument(iid, 'type', 'juno6')
    deckcfg.set_instrument(iid, 'patch', 0)

    ed = pe.ParamEditor(iid, show_advanced=True)
    ed.build(object())
    # Building the whole panel reads a great deal off the patch and writes NONE
    # of it. An untouched param stays unstored, so it stays unsent -- the
    # invariant the seeding path lives or dies by.
    assert not (deckcfg.get_instrument(iid).get('params') or {})
    import amyparams as ap
    assert ap.synth_send_calls(deckcfg.get_instrument(iid).get('params'),
                               ap.patch_env(deckcfg.get_instrument(iid))) == []


def test_editor_rerender_never_moves_a_stored_value(tmp_path):
    pe, dk, deckcfg = _import_parameditor_with_fakes(tmp_path)
    iid = deckcfg.instruments()[0]['id']
    deckcfg.set_instrument(iid, 'type', 'juno6')
    deckcfg.set_instrument(iid, 'patch', 0)

    # every value the slider can produce must survive an open/close/open cycle
    d, s, _ = _slider_for(pe, dk, deckcfg, iid, 'amp_decay')
    for pos in (0, 1, 37, 228, 300, 462, 500):
        s._drag_to(pos)
        first = deckcfg.get_instrument(iid)['params']['amp_decay']
        for _ in range(3):                       # re-open the panel repeatedly
            d2, s2, val = _slider_for(pe, dk, deckcfg, iid, 'amp_decay')
            assert s2.value == pos, "re-render moved the knob off %d" % pos
            assert deckcfg.get_instrument(iid)['params']['amp_decay'] == first
            assert val.text == '%d ms' % first   # readout is real ms, not units


def test_editor_marks_only_what_it_cannot_know(tmp_path):
    pe, dk, deckcfg = _import_parameditor_with_fakes(tmp_path)
    iid = deckcfg.instruments()[0]['id']
    # a DX7: its 5-segment EG is unreadable and it bakes no filter at all
    deckcfg.set_instrument(iid, 'type', 'dx7')
    deckcfg.set_instrument(iid, 'patch', 128)
    for name in ('amp_decay', 'filter_freq', 'lfo_freq'):
        _, _, val = _slider_for(pe, dk, deckcfg, iid, name)
        assert val.text == 'patch default', name
    # ...but params AMY's own defaults settle keep printing their real number
    for name, want in (('resonance', '0.7'), ('pan', '0.50'),
                       ('filter_kbd', '0.00')):
        _, _, val = _slider_for(pe, dk, deckcfg, iid, name)
        assert val.text == want, (name, val.text)

    # the same juno controls are all KNOWN, so nothing is marked -- Juno A11
    # really is 1355 ms / 180 Hz / 0.9 Hz / 0.9, and never the 100 ms /
    # 1000 Hz / 4 Hz / 0.7 the editor used to draw over it.
    # (The readouts round to each control's own grid -- whole Hz for cutoff,
    # tenths for the scale-10 params -- which is the same grid a touch stores
    # onto, so what is shown and what would be saved agree.)
    deckcfg.set_instrument(iid, 'type', 'juno6')
    deckcfg.set_instrument(iid, 'patch', 0)
    for name, want in (('amp_decay', '1355 ms'), ('filter_freq', '180 Hz'),
                       ('lfo_freq', '0.9 Hz'), ('resonance', '0.9')):
        _, _, val = _slider_for(pe, dk, deckcfg, iid, name)
        assert val.text == want, (name, val.text)


# --- soft keyboard: styling must not plant a NULL LVGL style transition -----
class _LvAuto(int):
    """Auto-vivifying LVGL constant: any attribute is another int, so the
    PART/STATE/OPA selectors deckui composes with `|` all work."""

    def __getattr__(self, k):
        if k.startswith('__'):
            raise AttributeError(k)
        v = _LvAuto(abs(hash(k)) & 0xffff)
        object.__setattr__(self, k, v)
        return v

    def __call__(self, *a, **kw):
        return _LvAuto(0)


class _LvModuleAuto(types.ModuleType):
    def __getattr__(self, k):
        if k.startswith('__'):
            raise AttributeError(k)
        v = _LvAuto(abs(hash(k)) & 0xffff)
        setattr(self, k, v)
        return v


def _import_deckui_with_fakes():
    """Import deckui.py against a permissive LVGL/ui fake so the soft-keyboard
    styling path is exercisable on the host (the real UI stack is not)."""
    _install_hw_mocks()
    tulip = sys.modules['tulip']
    tulip.color = lambda r, g, b: (r, g, b)
    tulip.keyboard = lambda: None
    tulip.lv = _LvModuleAuto('lvgl')

    ui = types.ModuleType('ui')
    ui.lv_soft_kb = None
    ui.keyboard = lambda: None
    ui.pal_to_lv = lambda pal: pal
    sys.modules['ui'] = ui

    sys.modules['lvgl'] = _LvModuleAuto('lvgl')
    sys.modules.pop('deckui', None)
    return importlib.import_module('deckui'), ui


class _RecordingKb:
    """Stands in for ui.lv_soft_kb, recording every style call made on it."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def _rec(*a):
            self.calls.append((name, a))
        return _rec


def test_style_keyboard_never_sets_a_null_transition():
    # A NULL LV_STYLE_TRANSITION is stored, not ignored: lv_obj_set_state()
    # scans every style on the object for LV_STYLE_TRANSITION and dereferences
    # the pointer WITHOUT a NULL check. The first key press adds
    # LV_STATE_PRESSED, so the very first keystroke on the soft keyboard --
    # from ANY deck text field -- panicked the device (no Python traceback,
    # reset_cause=HARD). Reported live as "renamed an instrument, hit delete
    # first, it crashed"; delete was simply the first key pressed.
    dk, ui = _import_deckui_with_fakes()
    kb = _RecordingKb()
    ui.lv_soft_kb = kb
    dk.style_keyboard()

    assert kb.calls, "style_keyboard styled nothing -- fake is wrong, not the code"
    nulls = [c for c in kb.calls
             if c[0] == 'set_style_transition' and c[1] and c[1][0] is None]
    assert nulls == [], (
        "style_keyboard passed None to set_style_transition %d time(s): that "
        "stores a NULL transition pointer and hard-faults LVGL on the first "
        "keyboard state change" % len(nulls))


def test_style_keyboard_keeps_pressed_key_feedback():
    # The press tint is the whole point of the styling pass -- it must survive
    # the removal of the transition-stripping loop.
    dk, ui = _import_deckui_with_fakes()
    kb = _RecordingKb()
    ui.lv_soft_kb = kb
    dk.style_keyboard()
    assert any(c[0] == 'set_style_bg_color' for c in kb.calls)


def test_style_keyboard_noop_without_a_keyboard():
    dk, ui = _import_deckui_with_fakes()
    ui.lv_soft_kb = None
    dk.style_keyboard()               # must not raise when the kb isn't up


# --- soft keyboard: the close key must work in EVERY keymap ----------------
class _FakeKbEvent:
    """An lv event from a soft keyboard whose close key reports `text`.

    get_textarea() returns a truthy stand-in, i.e. the deck's own mode, in
    which ui_patch's filter swallows ordinary keys (LVGL already typed them)
    and only forwards the close key to the original ui.lv_soft_kb_cb.
    """
    def __init__(self, text):
        self._text = text

    def get_target_obj(self):
        return self

    def get_selected_button(self):
        return 0

    def get_button_text(self, btn):
        return self._text

    def get_textarea(self):
        return object()


# LVGL labels the close key with a DIFFERENT glyph per keymap (lv_keyboard.c):
# lowercase/special/number use SYMBOL.KEYBOARD, uppercase (and Arabic) use
# SYMBOL.CLOSE. lv_keyboard_def_event_cb accepts both; so must the deck.
@pytest.mark.parametrize('symbol_name', ['KEYBOARD', 'CLOSE'])
def test_keyboard_close_key_recognised_in_every_mode(uipatch, symbol_name):
    ui, _ = uipatch
    lv = sys.modules['lvgl']
    _ORIG_KB_CB_CALLS.clear()
    e = _FakeKbEvent(getattr(lv.SYMBOL, symbol_name))
    ui.lv_soft_kb_cb(e)               # the filtered cb ui_patch installed
    assert _ORIG_KB_CB_CALLS == [e], (
        "the lv.SYMBOL.%s close key was swallowed by the keyboard filter: in "
        "the keymaps that label it that way the keyboard cannot be dismissed"
        % symbol_name)


def test_keyboard_ordinary_key_still_filtered(uipatch):
    # The whole point of the filter: with a textarea attached LVGL already
    # inserted the character, so the original cb must NOT also key_send() it.
    ui, _ = uipatch
    _ORIG_KB_CB_CALLS.clear()
    ui.lv_soft_kb_cb(_FakeKbEvent('q'))
    assert _ORIG_KB_CB_CALLS == []


# --- kbmgr: the keyboard-lifecycle state machine ---------------------------
# UI geometry (scroll-to-clear, echo positioning) is device-only; these cover
# the crash-relevant STATE machine: open/close idempotence, the textarea-DELETE
# teardown hook that auto-closes a popped panel, deferred overlay teardown when
# the close originates from a keyboard event, and password-echo masking.

class _KbEvt:
    def __init__(self, obj):
        self._obj = obj

    def get_target_obj(self):
        return self._obj


class _KbFake:
    """Stands in for ui.lv_soft_kb: records attached callbacks so a test can
    fire VALUE_CHANGED / DELETE, and no-ops every styling call."""
    def __init__(self):
        self.textarea = 'UNSET'
        self.cbs = {}
        self.height = 240

    def add_event_cb(self, fn, ev, ud):
        self.cbs.setdefault(ev, []).append(fn)

    def remove_event_cb(self, fn):
        for ev in list(self.cbs):
            self.cbs[ev] = [c for c in self.cbs[ev] if c is not fn]

    def set_textarea(self, ta):
        self.textarea = ta

    def get_height(self):
        return self.height

    def update_layout(self):
        pass

    def fire(self, ev):
        for fn in list(self.cbs.get(ev, [])):
            fn(_KbEvt(self))

    def __getattr__(self, name):
        return lambda *a, **k: None


class _TaFake:
    """A textarea whose DELETE event can be fired (panel teardown) and whose
    password mode / text are readable for the echo path."""
    def __init__(self, text='', password=False):
        self._text = text
        self._pw = password
        self.cbs = {}

    def add_event_cb(self, fn, ev, ud):
        self.cbs.setdefault(ev, []).append(fn)

    def get_text(self):
        return self._text

    def get_password_mode(self):
        return self._pw

    def get_parent(self):
        return None

    def get_coords(self, area):
        pass

    def fire(self, ev):
        for fn in list(self.cbs.get(ev, [])):
            fn(_KbEvt(self))

    def __getattr__(self, name):
        return lambda *a, **k: None


class _EchoLabel:
    def __init__(self):
        self.text = ''

    def set_text(self, t=None, *a):
        self.text = t if t is not None else ''

    def __getattr__(self, name):
        return lambda *a, **k: None


def _import_kbmgr_with_fakes():
    """kbmgr + deckui imported against permissive LVGL/ui fakes, with a
    firmware-like tulip.keyboard() toggle and a defer that records (never runs)
    so scheduling is observable."""
    _install_hw_mocks()
    tulip = sys.modules['tulip']
    tulip.color = lambda r, g, b: (r, g, b)
    tulip._deferred = []
    tulip.defer = lambda fn, arg, ms: tulip._deferred.append((fn, arg, ms))

    lv = _LvModuleAuto('lvgl')
    lv.label = lambda parent=None: _EchoLabel()      # echo text inspectable
    sys.modules['lvgl'] = lv

    ui = types.ModuleType('ui')
    ui.lv_soft_kb = None
    ui.pal_to_lv = lambda pal: pal

    def _toggle():
        # Mirror the firmware: delete the overlay (firing DELETE) or create it.
        if ui.lv_soft_kb is not None:
            kb = ui.lv_soft_kb
            ui.lv_soft_kb = None
            try:
                kb.fire(lv.EVENT.DELETE)
            except Exception:
                pass
        else:
            ui.lv_soft_kb = _KbFake()
    ui.keyboard = _toggle
    tulip.keyboard = _toggle
    sys.modules['ui'] = ui

    for m in ('deckui', 'kbmgr'):
        sys.modules.pop(m, None)
    importlib.import_module('deckui')
    kbmgr = importlib.import_module('kbmgr')
    kbmgr._reset_state()
    return kbmgr, ui, tulip, lv


def test_kbmgr_open_is_idempotent_and_does_not_recreate_the_overlay():
    kbmgr, ui, tulip, lv = _import_kbmgr_with_fakes()
    ta = _TaFake()
    kbmgr.open(ta)
    assert ui.lv_soft_kb is not None
    kb = ui.lv_soft_kb
    assert kb.textarea is ta and kbmgr.bound_textarea() is ta
    kbmgr.open(ta)                        # re-focus the SAME field
    assert ui.lv_soft_kb is kb            # not torn down and rebuilt
    assert kbmgr.is_open()


def test_kbmgr_close_is_idempotent():
    kbmgr, ui, tulip, lv = _import_kbmgr_with_fakes()
    ta = _TaFake()
    kbmgr.open(ta)
    kbmgr.close()
    assert ui.lv_soft_kb is None
    assert not kbmgr.is_open() and kbmgr.bound_textarea() is None
    kbmgr.close()                         # already gone: must be a safe no-op
    assert ui.lv_soft_kb is None


def test_kbmgr_textarea_delete_auto_closes_the_keyboard():
    # The structural close-crash guard: a panel popped out from under the
    # keyboard deletes its textarea, which must auto-close so the outliving
    # keyboard can't poke the freed field (the historic use-after-free).
    kbmgr, ui, tulip, lv = _import_kbmgr_with_fakes()
    ta = _TaFake()
    kbmgr.open(ta)
    assert ui.lv_soft_kb is not None
    ta.fire(lv.EVENT.DELETE)              # panel teardown deletes the field
    assert ui.lv_soft_kb is None
    assert not kbmgr.is_open() and kbmgr.bound_textarea() is None


def test_kbmgr_close_from_kb_event_defers_overlay_teardown():
    # Deleting the keyboard from inside its own event handler is the crash
    # class. close(from_kb_event=True) must detach the binding IMMEDIATELY but
    # DEFER the overlay teardown to the next tick.
    kbmgr, ui, tulip, lv = _import_kbmgr_with_fakes()
    ta = _TaFake()
    kbmgr.open(ta)
    kb = ui.lv_soft_kb
    tulip._deferred[:] = []
    kbmgr.close(from_kb_event=True)
    # binding detached now...
    assert not kbmgr.is_open() and kbmgr.bound_textarea() is None
    # ...but the overlay is still up, teardown scheduled for the next tick
    assert ui.lv_soft_kb is kb
    assert len(tulip._deferred) == 1
    fn, arg, ms = tulip._deferred[0]
    fn(arg)                               # run the deferred hide
    assert ui.lv_soft_kb is None


def test_kbmgr_keyboard_self_delete_syncs_state():
    # The keyboard's own close key deletes the overlay without routing through
    # kbmgr.close(); the DELETE hook must still clear our binding state.
    kbmgr, ui, tulip, lv = _import_kbmgr_with_fakes()
    ta = _TaFake()
    kbmgr.open(ta)
    kb = ui.lv_soft_kb
    ui.lv_soft_kb = None                  # frozen ui.py: delete then null
    kb.fire(lv.EVENT.DELETE)
    assert not kbmgr.is_open() and kbmgr.bound_textarea() is None


def test_kbmgr_password_field_forces_the_echo_strip():
    # Echo is always on for password fields (fallback + confirmation), even
    # when the caller did not ask for it.
    kbmgr, ui, tulip, lv = _import_kbmgr_with_fakes()
    ta = _TaFake(password=True)
    kbmgr.open(ta)                        # echo defaults off
    assert kbmgr._s.get('echo') is not None


def test_kbmgr_non_password_echo_is_opt_in():
    kbmgr, ui, tulip, lv = _import_kbmgr_with_fakes()
    ta = _TaFake(password=False)
    kbmgr.open(ta)                        # echo defaults off
    assert kbmgr._s.get('echo') is None
    kbmgr.close()
    kbmgr.open(ta, echo=True)
    assert kbmgr._s.get('echo') is not None


def test_kbmgr_echo_masks_password_with_last_char_reveal():
    kbmgr, ui, tulip, lv = _import_kbmgr_with_fakes()
    ta = _TaFake(text='secret', password=True)
    kbmgr.open(ta)
    lbl = kbmgr._s['echo_lbl']
    kbmgr._s['reveal_last'] = False
    kbmgr._update_echo()
    assert lbl.text == '•' * 6       # fully masked
    kbmgr._s['reveal_last'] = True
    kbmgr._update_echo()
    assert lbl.text == '•' * 5 + 't'  # last char briefly revealed


def test_kbmgr_echo_shows_plaintext_for_non_password():
    kbmgr, ui, tulip, lv = _import_kbmgr_with_fakes()
    ta = _TaFake(text='hello', password=False)
    kbmgr.open(ta, echo=True)
    kbmgr._update_echo()
    assert kbmgr._s['echo_lbl'].text == 'hello'


def test_kbmgr_height_zero_when_no_keyboard():
    kbmgr, ui, tulip, lv = _import_kbmgr_with_fakes()
    assert kbmgr.height() == 0
    kbmgr.open(_TaFake())
    assert kbmgr.height() == 240          # _KbFake.get_height()


# --- rename: deleting down to (and past) an empty name stays harmless -------
def test_empty_instrument_name_shortens_to_a_placeholder():
    # Backspacing the rename field down to empty writes name='' through
    # deckcfg.set_instrument, and chip_label feeds that straight to
    # name_short: '' must degrade to the '?' placeholder, not an empty chip.
    # (chip_label itself is covered by the chip_specs tests above, which run
    # before this file's module fakes replace `catalog`.)
    import shellmodel as sm
    assert sm.name_short('') == '?'
    assert sm.name_short(None) == '?'


def test_rename_to_empty_then_delete_again_is_stable(deck):
    # delete-to-empty, then one more delete: the field is already '' and the
    # handler just rewrites '' -- idempotent, no underflow, no flush churn.
    deckcfg, _ = deck
    iid = deckcfg.active_instrument()
    deckcfg.set_instrument(iid, 'name', '', flush=False)
    assert deckcfg.get_instrument(iid)['name'] == ''
    deckcfg.set_instrument(iid, 'name', '', flush=False)
    assert deckcfg.get_instrument(iid)['name'] == ''
    deckcfg.flush()
    assert deckcfg.get_instrument(iid)['name'] == ''


# ---------------------------------------------------------------------------
# flashmode.py / flashlib.py -- ping-pong dual-frequency flash update (pure
# logic, faked esp32/NVS/Partition/machine). No hardware, no device calls.
# ---------------------------------------------------------------------------
def _install_flash_mocks(running='ota_1', slots=('ota_0', 'ota_1'),
                         nvs_seed=None):
    """Fake esp32 (NVS + Partition) and machine so flashmode runs on a host.

    Returns a dict of handles the tests assert against: the NVS store, and the
    recorded set_boot / reset targets.
    """
    rec = {'nvs': dict(nvs_seed or {}), 'boot': None, 'reset': 0}

    esp32 = types.ModuleType('esp32')

    class NVS:
        def __init__(self, ns):
            self.ns = ns

        def get_i32(self, k):
            key = (self.ns, k)
            if key not in rec['nvs']:
                raise OSError('NVS key not found')     # MicroPython behaviour
            return rec['nvs'][key]

        def set_i32(self, k, v):
            rec['nvs'][(self.ns, k)] = int(v)

        def erase_key(self, k):
            rec['nvs'].pop((self.ns, k), None)

        def commit(self):
            pass

    class Partition:
        TYPE_APP = 0
        RUNNING = 1

        def __init__(self, which=None, label=None):
            self.label = running if which == Partition.RUNNING else label

        def info(self):
            return (0, 0, 0x10000, 0x390000, self.label, False)

        def get_next_update(self):
            other = [s for s in slots if s != self.label][0]
            return Partition(label=other)

        def set_boot(self):
            rec['boot'] = self.label

        @staticmethod
        def find(ptype, label=None):
            return [Partition(label=label)] if label in slots else []

    esp32.NVS = NVS
    esp32.Partition = Partition
    sys.modules['esp32'] = esp32

    machine = types.ModuleType('machine')

    def _reset():
        rec['reset'] += 1
    machine.reset = _reset
    sys.modules['machine'] = machine
    return rec


def _fresh_flashmode(**mock_kw):
    _install_hw_mocks()                 # provides a 'tulip' module
    # a build-identity default: no compiled binding, no stamped constant
    sys.modules['tulip'].__dict__.pop('flash_freq', None)
    sys.modules.pop('flashbuild', None)
    rec = _install_flash_mocks(**mock_kw)
    sys.modules.pop('flashmode', None)
    fm = importlib.import_module('flashmode')
    return fm, rec


def test_flashmode_imports_on_host_without_esp32():
    # module must import even with NO esp32/machine present (host test env)
    for m in ('esp32', 'machine', 'flashbuild'):
        sys.modules.pop(m, None)
    _install_hw_mocks()
    sys.modules['tulip'].__dict__.pop('flash_freq', None)
    sys.modules.pop('flashmode', None)
    fm = importlib.import_module('flashmode')
    assert fm.get_flash_pending() == 0            # no NVS -> not pending
    assert fm.set_flash_pending(1) is False       # no NVS -> fail-soft
    assert fm.clear_flash_pending() is False
    assert fm.find_partition('ota_0') is None      # no Partition -> None
    assert fm.should_enter_flash_mode() is False   # never hijacks


def test_is_flasher_build_via_tulip_binding():
    fm, _ = _fresh_flashmode()
    sys.modules['tulip'].flash_freq = lambda: '80m'   # compiled binding wins
    assert fm.flash_freq() == '80m'
    assert fm.is_flasher_build() is True
    sys.modules['tulip'].flash_freq = lambda: '120m'
    assert fm.is_flasher_build() is False


def test_is_flasher_build_via_flashbuild_constant():
    fm, _ = _fresh_flashmode()
    fb = types.ModuleType('flashbuild')
    fb.FLASH_FREQ = '80m'
    sys.modules['flashbuild'] = fb
    assert fm.is_flasher_build() is True
    fb.FLASH_FREQ = '120m'
    assert fm.is_flasher_build() is False


def test_is_flasher_build_defaults_to_play():
    # neither a binding nor a stamped constant -> unknown -> play (never flash)
    fm, _ = _fresh_flashmode()
    assert fm.flash_freq() == '120m'
    assert fm.is_flasher_build() is False


def test_flashmode_nvs_roundtrip():
    fm, rec = _fresh_flashmode()
    assert fm.get_flash_pending() == 0
    assert fm.set_flash_pending(1) is True
    assert fm.get_flash_pending() == 1
    assert rec['nvs'][('deckboot', 'flash_pending')] == 1
    assert fm.clear_flash_pending() is True
    assert fm.get_flash_pending() == 0


def test_flashmode_slot_helpers_by_label():
    fm, _ = _fresh_flashmode(running='ota_0')
    assert fm.running_label() == 'ota_0'
    assert fm.flasher_partition().label == 'ota_0'
    assert fm.play_partition().label == 'ota_1'
    assert fm.find_partition('nope') is None


def test_should_enter_flash_mode_gating():
    # flasher build + pending -> yes
    fm, _ = _fresh_flashmode(nvs_seed={('deckboot', 'flash_pending'): 1})
    sys.modules['tulip'].flash_freq = lambda: '80m'
    assert fm.should_enter_flash_mode() is True
    # flasher build but NOT pending -> no (functional fallback deck)
    fm, _ = _fresh_flashmode()
    sys.modules['tulip'].flash_freq = lambda: '80m'
    assert fm.should_enter_flash_mode() is False
    # play build with a stray pending flag -> no (normal play untouched)
    fm, _ = _fresh_flashmode(nvs_seed={('deckboot', 'flash_pending'): 1})
    sys.modules['tulip'].flash_freq = lambda: '120m'
    assert fm.should_enter_flash_mode() is False


def test_request_update_arms_flasher():
    fm, rec = _fresh_flashmode(running='ota_1')     # on the play slot
    assert fm.request_update() is True
    assert rec['nvs'][('deckboot', 'flash_pending')] == 1
    assert rec['boot'] == 'ota_0'                    # set_boot -> flasher
    assert rec['reset'] == 0                         # request_update never resets


def test_arm_and_reboot_resets_after_arming():
    fm, rec = _fresh_flashmode(running='ota_1')
    assert fm.arm_and_reboot() is True
    assert rec['boot'] == 'ota_0'
    assert rec['reset'] == 1


def test_finalize_to_play_clears_and_boots_play():
    fm, rec = _fresh_flashmode(running='ota_0',       # booted in the flasher
                              nvs_seed={('deckboot', 'flash_pending'): 1})
    assert fm.finalize_to_play() is True
    assert fm.get_flash_pending() == 0                # flag cleared
    assert rec['boot'] == 'ota_1'                     # set_boot -> play
    assert rec['reset'] == 1


def test_request_update_fail_soft_without_flasher_slot():
    # no matching slots -> cannot arm; must NOT set the flag or reboot
    fm, rec = _fresh_flashmode(running='ota_1', slots=('ota_1',))
    assert fm.request_update() is False
    assert ('deckboot', 'flash_pending') not in rec['nvs']
    assert rec['boot'] is None


# --- flashlib.py: device-code builder is shared + parametrised ---
def test_build_ota_code_default_matches_flash_ota():
    import flashlib
    code = flashlib.build_ota_code('http://h:1', 'a' * 64, 4096)
    assert '__' not in code                            # every placeholder filled
    # flash_ota's historical target + modal title, byte-for-byte
    assert 'ota = Partition(Partition.RUNNING).get_next_update()' in code
    assert "title='Firmware update (OTA)'" in code
    assert "SIZE = 4096" in code
    assert "ur.get('http://h:1/fw.bin')" in code


def test_build_ota_code_pingpong_targets_play_by_label():
    import flashlib
    import flashmode as fm
    target = "Partition.find(Partition.TYPE_APP, label=%r)[0]" % fm.PLAY_LABEL
    code = flashlib.build_ota_code('http://h:2', 'b' * 64, 8192,
                                   target=target, title='Safe update (80MHz)')
    assert '__' not in code
    assert ("ota = Partition.find(Partition.TYPE_APP, label='ota_1')[0]"
            in code)
    assert "title='Safe update (80MHz)'" in code
    # the proven write-verify-retry loop is intact in the shared copy
    assert 'def wr(blk, w):' in code and "print('OTA:BOOTSET')" in code


# ---------------------------------------------------------------------------
# KITS-4: synth kits map the whole GM percussion range (nearest-pad aliases)
# ---------------------------------------------------------------------------

def _fresh_synthkits():
    """synthkits reloaded against the REPO's data dir (the module's relative
    'synthkits_data' entry only resolves when cwd happens to be deck/)."""
    sys.modules.pop('synthkits', None)
    synthkits = importlib.import_module('synthkits')
    synthkits._DIRS = (os.path.join(_HERE, 'synthkits_data'),)
    synthkits._state.update({'index': None, 'dir': None, 'packs': {}})
    return synthkits


def test_gm_fill_covers_full_percussion_range():
    synthkits = _fresh_synthkits()
    kits = synthkits.kits()
    assert kits                                   # repo data actually loaded
    for key in kits:
        notes = synthkits.kit_notes(key)
        fill = synthkits.gm_fill(notes)
        # every GM percussion key plays something (the sampled kits' contract)
        assert set(fill) == set(range(35, 82)), key
        for n, base in fill.items():
            assert base in notes, (key, n)        # alias targets are real pads
        for n in notes:
            assert fill[n] == n, (key, n)         # real pads keep themselves


def test_gm_fill_nearest_and_ties():
    synthkits = _fresh_synthkits()
    fill = synthkits.gm_fill({36, 38, 39, 42, 46})   # the real tr909 layout
    assert fill[35] == 36
    assert fill[37] == 36        # tie 36/38 -> lower
    assert fill[40] == 39        # 1 away from 39, 2 from 38/42
    assert fill[44] == 42        # tie 42/46 -> lower
    assert fill[45] == 46
    assert fill[81] == 46        # top of range falls back to the last pad
    assert synthkits.gm_fill([]) == {}


def test_every_kit_has_core_pads():
    synthkits = _fresh_synthkits()
    # kick/snare/closed-hat/open-hat at canonical GM slots -- aliasing spreads
    # the kit over the range, but only if these anchors exist (guards future
    # data regenerations)
    for key in synthkits.kits():
        assert {36, 38, 42, 46} <= set(synthkits.kit_notes(key)), key


def test_kit_notes_variant_fallback():
    synthkits = _fresh_synthkits()
    # 'tr909_d' is in saved configs but was deduped out of the data
    base = synthkits.kit_notes('tr909')
    assert base
    assert synthkits.kit_notes('tr909_d') == base
    assert synthkits.kit_notes('nokit_z') == {}
    assert synthkits.kit_notes('garbage') == {}


def test_synthkit_registers_contiguous_note_maps():
    _install_hw_mocks()
    _fresh_synthkits()
    sys.modules.pop('drums_kit', None)
    import drums_kit
    amy = sys.modules['amy']
    amy._sends.clear()
    kit = drums_kit.SynthKit('tr909')
    home = sorted(kit.pads)                        # [36, 38, 39, 42, 46]
    assert home == [36, 38, 39, 42, 46]
    assert kit.hit_synths == {}                   # tr909 is fully container
    anchor = home[0]
    maps = [k['midi_note_cmd'] for k in amy._sends if 'midi_note_cmd' in k]
    # pack_fill: N maps for N hits, contiguous keys anchor..anchor+N-1
    assert len(maps) == len(home)                 # one map per DISTINCT hit
    keys = sorted(int(m.split(',', 1)[0]) for m in maps)
    assert keys == list(range(anchor, anchor + len(home)))   # contiguous, anchored
    # each key anchor+i fires the i-th sorted home-note's container osc(s)
    # via osc-targeted fragments (v<osc>n60l%vi%iiM%i), per-hit gain in the
    # velocity-scale (max) field -- the sampled kits' baked io format
    key_to_oscs = {}
    for m in maps:
        fields = m.split(',', 5)
        assert fields[1] == '0' and fields[2] == '0' and fields[4] == '0'
        assert float(fields[3]) == pytest.approx(2.5)     # KIT_GAIN, level 1
        frags = fields[5].split('Z')
        for t in frags:
            assert t.startswith('v') and t.endswith('n60l%vi%iiM%i')
        key_to_oscs[int(fields[0])] = [int(t[1:t.index('n')]) for t in frags]
    for i, hn in enumerate(home):
        assert key_to_oscs[anchor + i] == kit.pads[hn]['trig'], (i, hn)
    # Python-routed path packs the same way: key anchor+1 (37) -> 2nd hit
    # (pad 38), fired as direct osc-targeted note-ons (one per trigger osc)
    # with the map's gain applied to the velocity and the already-mapped
    # marker set
    amy._sends.clear()
    kit.note_on(anchor + 1, 1.0)
    on = [k for k in amy._sends if 'vel' in k]
    assert [k['osc'] for k in on] == kit.pads[home[1]]['trig']
    for k in on:
        assert k['note'] == 60
        assert k['vel'] == pytest.approx(2.5)     # vel 1.0 * velscale
        assert k['note_source_channel'] == kit.synth
    # keys outside [anchor, anchor+len) are unmapped -> silent (no nearest-pad grab)
    off = anchor + len(home) + 2                   # 43: beyond the packed block
    assert off not in kit.note_alias
    amy._sends.clear()
    kit.note_on(off, 1.0)
    assert [k for k in amy._sends if 'vel' in k] == []


# ---------------------------------------------------------------------------
# Container kits: one patch per kit, osc-targeted note maps, gain in the
# velocity-scale field (the upstream 384-390 drum-kit model).
# ---------------------------------------------------------------------------
def _container_fixture():
    """Fresh synthkits with a small fake corpus: a 1-osc tone, a 2-osc
    tone+noise, and a 3-osc monster that must fall back."""
    _install_hw_mocks()
    for m in ('synthkits', 'drums_kit'):
        sys.modules.pop(m, None)
    import synthkits
    synthkits._state['packs']['p'] = {
        'p/one': {'oscs': [{'wave': 0, 'freq': '80,0,0,0,1', 'amp': '1.2',
                            'bp0': '0,1,90,0'}]},
        'p/two': {'oscs': [
            {'wave': 0, 'freq': '100', 'amp': '0.8', 'bp0': '0,1,50,0'},
            {'wave': 5, 'amp': '0.5', 'bp0': '0,1,30,0',
             'filter_type': 1, 'filter_freq': 4000}]},
        'p/big': {'oscs': [{'wave': 0, 'amp': '1'}, {'wave': 5, 'amp': '1'},
                           {'wave': 0, 'amp': '1'}]},
    }
    return synthkits


def test_kit_container_shape_and_triggers():
    s = _container_fixture()
    s.kit_notes = lambda k: {36: 'p/two', 38: 'p/one'}
    patch, pads, fallback = s.kit_container('anykit')
    assert fallback == {}
    # note order: pad 36 (2 oscs) at osc 0, pad 38 (1 osc) at osc 2; a wire
    # hit's note map must fire EVERY osc (trig) -- chained pairs are NOT
    # equivalent (AMY stacks a chained osc into its parent's render buffer
    # and filters the sum)
    assert pads[36] == {'osc': 0, 'nosc': 2, 'trig': [0, 1],
                        'velscale': 2.5, 'hit': 'p/two'}
    assert pads[38] == {'osc': 2, 'nosc': 1, 'trig': [2],
                        'velscale': 2.5, 'hit': 'p/one'}
    # design amps preserved (no KIT_GAIN bake -- gain rides in velscale),
    # except p/one's 1.2 which trims to _AMP_CAP/velscale = 1.0 (reproducing
    # the legacy min(2.5, 1.2*2.5) render-time cap)
    assert patch == ('v0w0f100A0,1,50,0a0.8G0F0R0.7Z'
                     'v1w5A0,1,30,0a0.5G1F4000R0.7Z'
                     'v2w0f80,0,0,0,1A0,1,90,0a1G0F0R0.7Z')
    # the 2-osc template is two Z-separated osc-targeted fragments, each
    # tagged i%i (this synth) + iM%i (already-mapped marker)
    assert s.container_note_cmd(36, pads[36]['trig'], 2.5) == \
        '36,0,0,2.5,0,v0n60l%vi%iiM%iZv1n60l%vi%iiM%i'
    assert s.container_note_cmd(38, pads[38]['trig'], 2.5) == \
        '38,0,0,2.5,0,v2n60l%vi%iiM%i'


def test_kit_container_gain_in_velscale_and_cap():
    s = _container_fixture()
    s.kit_notes = lambda k: {36: 'p/one'}
    # level 2 -> velscale 5.0; the stored amp is trimmed to _AMP_CAP/G so the
    # render-time amp*velscale product lands exactly on the legacy 2.5 cap
    patch, pads, _ = s.kit_container('k', hit_overrides={36: {'level': 2.0}})
    assert pads[36]['velscale'] == pytest.approx(5.0)
    assert 'a0.5' in patch                        # min(1.2, 2.5/5.0)
    # level 0.4 -> velscale exactly 1.0; amp under the cap stays the design amp
    patch, pads, _ = s.kit_container('k', hit_overrides={36: {'level': 0.4}})
    assert pads[36]['velscale'] == pytest.approx(1.0)
    assert 'a1.2' in patch
    # default: velscale KIT_GAIN, design amp 1.2 <= 2.5/2.5 is FALSE -> trims
    # to 1.0 (2.5/2.5), reproducing legacy min(2.5, 1.2*2.5)=2.5 at render
    patch, pads, _ = s.kit_container('k')
    assert pads[36]['velscale'] == pytest.approx(2.5)
    assert 'a1' in patch and 'a1.2' not in patch
    # snap stays an amp bake on the noise osc only (it's per-osc, velscale
    # is per-hit)
    s.kit_notes = lambda k: {36: 'p/two'}
    patch, pads, _ = s.kit_container('k', hit_overrides={36: {'snap': 2.0}})
    assert 'a0.8' in patch                        # tone untouched
    assert 'a1' in patch.split('Z')[1]            # noise 0.5*2

    # tune/decay transforms match the legacy per-hit path
    patch, _, _ = s.kit_container('k', hit_overrides={36: {'tune': 12}})
    assert 'f200' in patch and 'F8000' in patch   # freq + filter_freq doubled
    patch, _, _ = s.kit_container('k', hit_overrides={36: {'decay': 2}})
    assert 'A0,1,100,0' in patch and 'A0,1,60,0' in patch


def test_classify_hit_shapes():
    s = _container_fixture()
    assert s.classify_hit({'oscs': [{'wave': 0}]}) == 'wire'
    assert s.classify_hit({'oscs': [{'wave': 0}, {'wave': 5}]}) == 'wire'
    # >2 wire oscs -> fallback
    assert s.classify_hit(s._state['packs']['p']['p/big']) is None
    # strict partials: w10 parent at v0 whose preset == child count, w9 kids
    good = 'v0w10p2a1,0,1,1Zv1w9f100A0,1,20,0Zv2w9f200A0,1,20,0Z'
    assert s.classify_hit({'patch_string': good}) == 'partials'
    bad_preset = 'v0w10p3a1Zv1w9f100Zv2w9f200Z'
    assert s.classify_hit({'patch_string': bad_preset}) is None
    bad_child = 'v0w10p2a1Zv1w9f100Zv2w0f200Z'
    assert s.classify_hit({'patch_string': bad_child}) is None
    bad_parent = 'v0w9p2a1Zv1w9f100Zv2w9f200Z'
    assert s.classify_hit({'patch_string': bad_parent}) is None
    assert s.classify_hit(None) is None


def test_kit_container_partials_block():
    s = _container_fixture()
    s._state['packs']['p']['p/part'] = {'patch_string':
        'v0w10p2a1,0,1,1Zv1w9f100A0,1,20,0Zv2w9f200A0,1,20,0Z'}
    s.kit_notes = lambda k: {36: 'p/one', 49: 'p/part'}
    patch, pads, fallback = s.kit_container('k')
    assert fallback == {}
    # partials hit renumbered to its base (osc 1..3 after the 1-osc pad 36);
    # trigger target is ONLY the w10 parent -- its children ride behind it
    assert pads[49] == {'osc': 1, 'nosc': 3, 'trig': [1],
                        'velscale': 2.5, 'hit': 'p/part'}
    frags = {f[:2]: f for f in patch.split('Z') if f}
    assert 'w10' in frags['v1'] and 'p2' in frags['v1']
    assert 'w9' in frags['v2'] and 'f100' in frags['v2']
    assert 'w9' in frags['v3'] and 'f200' in frags['v3']
    # parent amp survives the cap (min(1, 2.5/2.5) == 1), children untouched
    assert 'a1,0,1,1' in frags['v1']


def test_whole_corpus_classifies_container():
    """Every hit of every kit in the shipped data must place in the
    container (the fallback path is defensive headroom, not a dependency);
    also pins each kit's container osc budget."""
    import json
    s = _fresh_synthkits()
    idx = s._load()
    assert idx['kits']
    packs = {}
    worst = 0
    for kit_key, kit in idx['kits'].items():
        total = 0
        for n, hit_key in kit['notes'].items():
            pack = hit_key.split('/', 1)[0]
            if pack not in packs:
                with open(os.path.join(_HERE, 'synthkits_data',
                                       pack + '.json')) as f:
                    packs[pack] = json.load(f)
            hit = packs[pack].get(hit_key)
            kind = s.classify_hit(hit)
            assert kind in ('wire', 'partials'), (kit_key, hit_key)
            if kind == 'wire':
                total += len(hit['oscs'][:2])
            else:
                total += len([x for x in hit['patch_string'].split('Z') if x])
        worst = max(worst, total)
    # a kit's container must fit comfortably in AMY's 250-osc pool alongside
    # other instruments (partials808 is the largest at 44 oscs today)
    assert worst <= 60


def test_synthkit_fallback_pad_coexists_with_container():
    s = _container_fixture()
    s.kit_notes = lambda k: {36: 'p/one', 38: 'p/big'}
    s.store_patch = lambda slot, ps: slot
    sys.modules.pop('drums_kit', None)
    import drums_kit
    synth = sys.modules['synth']
    amy = sys.modules['amy']
    amy._sends.clear()
    kit = drums_kit.SynthKit('k', slot_base=1030)
    # container synth + ONE fallback synth for the unclassifiable hit
    assert set(kit.pads) == {36}
    assert set(kit.hit_synths) == {38}
    assert kit.hit_slots[38] == 1031              # base+1: first fallback slot
    assert len(synth.PatchSynth.instances) == 2
    maps = {int(m['midi_note_cmd'].split(',', 1)[0]): m['midi_note_cmd']
            for m in amy._sends if 'midi_note_cmd' in m}
    # pack_fill lays both pads out; 36 -> container osc, 37 -> hit synth
    assert maps[36].endswith('v0n60l%vi%iiM%i')
    assert maps[37].endswith('i%dn60l%%v' % kit.hit_synths[38].synth)
    assert ',1,0,i' in maps[37]                   # fallback velscale stays 1
    # python path: container pad direct-osc, fallback pad via its synth
    amy._sends.clear()
    kit.note_on(37, 0.5)
    assert kit.hit_synths[38].on == [60]
    kit.release()
    assert all(x.released for x in synth.PatchSynth.instances)


def test_synthkit_retweak_inplace_vs_swap():
    s = _container_fixture()
    s.kit_notes = lambda k: {36: 'p/two', 38: 'p/one'}
    s.store_patch = lambda slot, ps: slot
    sys.modules.pop('drums_kit', None)
    import drums_kit
    synth = sys.modules['synth']
    amy = sys.modules['amy']
    kit = drums_kit.SynthKit('k')
    n_synths = len(synth.PatchSynth.instances)
    assert n_synths == 1
    # ---- slider hot path: same hit, new overrides -> live osc params only,
    # no synth churn, other pads' tails keep ringing
    amy._sends.clear()
    kit.retweak(36, {'decay': 2.0})
    assert len(synth.PatchSynth.instances) == n_synths        # no rebuild
    osc_sends = [k for k in amy._sends if 'osc' in k and 'bp0' in k]
    assert [k['osc'] for k in osc_sends] == [0, 1]            # both hit oscs
    assert osc_sends[0]['bp0'] == '0,1,100,0'                 # 50 * 2
    assert all(k['synth'] == kit.synth for k in osc_sends)
    # velscale unchanged -> no map re-registration
    assert not any('midi_note_cmd' in k for k in amy._sends)
    # ---- level change -> same-osc update PLUS map refresh with new velscale
    amy._sends.clear()
    kit.retweak(36, {'level': 2.0})
    maps = [k['midi_note_cmd'] for k in amy._sends if 'midi_note_cmd' in k]
    assert maps and all(
        ',5,0,v0n60l%vi%iiM%iZv1n60l%vi%iiM%i' in m for m in maps)
    assert kit.pads[36]['velscale'] == pytest.approx(5.0)
    # ---- swap (different hit key) -> full rebuild: maps cleared + relaid
    amy._sends.clear()
    kit.retweak(38, None, hit_key='p/two')
    clears = [k for k in amy._sends if k.get('midi_note_cmd') == '255']
    assert clears                                             # old maps gone
    assert kit.note_hits[38] == 'p/two'
    assert kit.pads[38]['nosc'] == 2                          # new shape live
    assert kit.hit_swaps[38] == 'p/two'                       # survives rebuild
    kit.release()


def test_piano_gets_no_special_level_injection():
    # A fresh piano must NOT get any deck-injected level/amp: it uses its natural
    # schema default (full volume). The old -6 dB crackle default was reverted --
    # on device the crackle is CPU-bound, not master clipping, so lowering volume
    # only made the piano too quiet. No engine gets a level injected here.
    _install_hw_mocks()
    for m in ('deckcfg', 'forwarder'):
        sys.modules.pop(m, None)
    import forwarder
    amy = sys.modules['amy']
    assert not hasattr(forwarder, 'PIANO_DEFAULT_LEVEL')

    class _Syn:
        synth = 42

    def _amp_sent():
        for k in reversed(amy._sends):
            if 'amp' in k:
                return float(str(k['amp']).split(',')[0])
        return None

    # no engine (piano included) has an amp injected when none is stored
    for t in ('piano', 'juno6', 'dx7'):
        amy._sends.clear()
        forwarder._apply_params(_Syn(), {}, {'type': t, 'patch': 256 if t == 'piano' else 0})
        assert _amp_sent() is None, t


# --- deckcfg: per-pad drum edits survive a reload ---
def test_instrument_hits_survive_reload(deck):
    deckcfg, _ = deck
    iid = deckcfg.load()['instruments'][0]['id']
    deckcfg.set_instrument(iid, 'type', 'drums')
    deckcfg.set_instrument(iid, 'kit', 'synth:tr808syn')
    deckcfg.set_instrument(iid, 'hits', {'36': {'tune': -5}})
    deckcfg.set_instrument(iid, 'hit_swaps', {'38': 'x/y'})
    deckcfg.set_instrument(iid, 'reverb_send', 0.5)
    deckcfg.invalidate()                      # simulated reboot: re-read + merge
    instr = deckcfg.get_instrument(iid)
    assert instr['kit'] == 'synth:tr808syn'
    assert instr['hits'] == {'36': {'tune': -5}}
    assert instr['hit_swaps'] == {'38': 'x/y'}
    assert instr['reverb_send'] == 0.5


# --- home: _DECK_MODULES must list every deck module ---
def _deck_modules_from_source():
    """Read _DECK_MODULES out of home.py WITHOUT importing it (needs LVGL)."""
    import ast
    with open(os.path.join(_HERE, 'home.py')) as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == '_DECK_MODULES':
                    return set(e.value for e in node.value.elts
                               if isinstance(e, ast.Constant))
    raise AssertionError("_DECK_MODULES not found in home.py")


def test_deck_modules_covers_every_deck_module():
    # The list has drifted twice: Apps then re-reads the missing files in full
    # on every scan, and files.py's delete guard stops protecting them.
    listed = _deck_modules_from_source()
    on_disk = set(n[:-3] for n in os.listdir(_HERE)
                  if n.endswith('.py') and not n.startswith('test_'))
    assert not (on_disk - listed)


# --- ui_patch: the safe MIDI drain ---
def _drain_harness(queue):
    """Install the mocks, preload `queue` into tulip.midi_in, return the drain."""
    _install_hw_mocks()
    _install_ui_mocks()
    tulip = sys.modules['tulip']
    holder = []
    tulip.midi_callback = lambda fn: holder.append(fn)
    pending = list(queue)
    tulip.midi_in = lambda: pending.pop(0) if pending else None
    tulip.sysex_in = lambda: b''
    sys.modules.pop('ui_patch', None)
    ui_patch = importlib.import_module('ui_patch')
    ui_patch._install_safe_midi_drain()
    assert holder, "drain was not registered"
    return holder[0]


def test_safe_drain_coalesces_bend_and_isolates_faults():
    drain = _drain_harness([
        bytes((0x90, 60, 100)),
        bytes((0xE0, 0x00, 0x10)),
        bytes((0xE0, 0x00, 0x20)),
        bytes((0xE0, 0x00, 0x40)),      # only this one survives coalescing
        bytes((0x80, 60, 0)),
    ])
    midi = sys.modules['midi']
    seen = []

    def _boom(m):
        raise RuntimeError("callback fault")

    midi.MIDI_CALLBACKS = set((seen.append, _boom))
    drain(False)
    # notes never coalesce, bends collapse to the LAST value per channel, and
    # the raising callback does not strand the rest of the batch
    assert seen == [bytes((0x90, 60, 100)), bytes((0xE0, 0x00, 0x40)),
                    bytes((0x80, 60, 0))]


# --- forwarder: a layered MPE zone silently mutes its partner ---
def test_mpe_zone_layered_partner_is_warned(deck):
    deckcfg, forwarder = deck
    deckcfg.set_value('mpe_enabled', True)
    a = deckcfg.load()['instruments'][0]['id']
    deckcfg.set_instrument_mpe(a, 'enabled', True)
    b = deckcfg.add_instrument(device='internal', channel=1)['id']
    logs = []
    fake = types.ModuleType('decklog')
    fake.log = lambda m: logs.append(m)
    fake.dbg = lambda *a, **k: None
    fake.err = lambda *a, **k: None
    prev = sys.modules.get('decklog')
    sys.modules['decklog'] = fake
    try:
        forwarder.start()
        forwarder._route(bytes((0x90, 60, 100)))
        # pinned CURRENT behaviour: the zone C-owns ch1, so the layered
        # partner never sounds -- but the mute is now logged, not silent
        assert forwarder._state['synths'][b].on == []
        assert any('ch1' in m and 'MPE zone' in m for m in logs)
    finally:
        if prev is None:
            sys.modules.pop('decklog', None)
        else:
            sys.modules['decklog'] = prev


# --- forwarder: a BOARD instrument on an MPE member channel is silently
# muted (_route returns early for member channels before it ever looks at
# `boards`) -- extend the layered-internal warning to cover this case too.
def test_board_instrument_on_mpe_member_channel_is_warned(deck):
    deckcfg, forwarder = deck
    deckcfg.set_value('mpe_enabled', True)
    a = deckcfg.load()['instruments'][0]['id']     # master on channel 1
    deckcfg.set_instrument_mpe(a, 'enabled', True)
    # board instrument sitting on ch2 -- a member channel of the ch1 zone
    # (default members=15 spans channels 2-16)
    b = deckcfg.add_instrument(device=0, channel=2)['id']
    logs = []
    fake = types.ModuleType('decklog')
    fake.log = lambda m: logs.append(m)
    fake.dbg = lambda *a, **k: None
    fake.err = lambda *a, **k: None
    prev = sys.modules.get('decklog')
    sys.modules['decklog'] = fake
    try:
        forwarder.start()
        assert 2 in forwarder._state['mpe_members']
        # pinned CURRENT behaviour: _route mutes ch2 before it ever forwards
        # to a board, but the mute is now logged.
        assert any('ch2' in m and 'board' in m for m in logs)
    finally:
        if prev is None:
            sys.modules.pop('decklog', None)
        else:
            sys.modules['decklog'] = prev


def test_board_instrument_not_on_member_channel_no_warning(deck):
    # A board instrument OUTSIDE the MPE zone's member-channel range must
    # not trigger the warning.
    deckcfg, forwarder = deck
    a = deckcfg.load()['instruments'][0]['id']     # master on channel 1
    deckcfg.set_value('mpe_enabled', True)
    deckcfg.set_instrument_mpe(a, 'enabled', True)
    deckcfg.set_instrument_mpe(a, 'members', 2)     # zone spans ch2-3 only
    deckcfg.add_instrument(device=0, channel=10)    # well outside the zone
    logs = []
    fake = types.ModuleType('decklog')
    fake.log = lambda m: logs.append(m)
    fake.dbg = lambda *a, **k: None
    fake.err = lambda *a, **k: None
    prev = sys.modules.get('decklog')
    sys.modules['decklog'] = fake
    try:
        forwarder.start()
        assert 10 not in forwarder._state['mpe_members']
        assert not any('board' in m for m in logs)
    finally:
        if prev is None:
            sys.modules.pop('decklog', None)
        else:
            sys.modules['decklog'] = prev


# --- amyfleet: enroll_from_config dedups by device -- a conflicting 2nd
# instrument on the same board is silently never enrolled (its notes never
# sound); that drop must be logged, not silent.
def test_amyfleet_enroll_conflict_channel_is_warned(deck):
    deckcfg, _ = deck
    sys.modules.pop('amyfleet', None)
    amyfleet = importlib.import_module('amyfleet')
    # two instruments routed to the SAME board (device 0) on DIFFERENT
    # channels -- enroll_from_config dedups by device, so only the first is
    # ever enrolled; the conflict must be logged, not dropped silently.
    deckcfg.add_instrument(device=0, channel=2)
    deckcfg.add_instrument(device=0, channel=5)
    logs = []
    fake = types.ModuleType('decklog')
    fake.log = lambda m: logs.append(m)
    fake.dbg = lambda *a, **k: None
    fake.err = lambda *a, **k: None
    prev = sys.modules.get('decklog')
    sys.modules['decklog'] = fake
    try:
        n = amyfleet.enroll_from_config()
        assert n == 1                              # dedup behaviour unchanged
        assert any('ch2' in m and 'ch5' in m for m in logs)
    finally:
        if prev is None:
            sys.modules.pop('decklog', None)
        else:
            sys.modules['decklog'] = prev


def test_amyfleet_enroll_no_warning_when_channels_match(deck):
    deckcfg, _ = deck
    sys.modules.pop('amyfleet', None)
    amyfleet = importlib.import_module('amyfleet')
    deckcfg.add_instrument(device=0, channel=2)
    deckcfg.add_instrument(device=0, channel=2)   # same device+channel: no conflict
    logs = []
    fake = types.ModuleType('decklog')
    fake.log = lambda m: logs.append(m)
    fake.dbg = lambda *a, **k: None
    fake.err = lambda *a, **k: None
    prev = sys.modules.get('decklog')
    sys.modules['decklog'] = fake
    try:
        n = amyfleet.enroll_from_config()
        assert n == 1
        assert logs == []
    finally:
        if prev is None:
            sys.modules.pop('decklog', None)
        else:
            sys.modules['decklog'] = prev


# --- update engine: manifest-driven /user apply (deck/UPGRADE.md Phase 1) ---
#
# The engine is pure stdlib (no tulip/lvgl/deckcfg), so these run straight on
# CPython. They pin the load-bearing guarantees: the format guard, sha256
# verification (a corrupt file is NEVER written), verify-all-before-apply (one
# bad file leaves /user untouched), the make_update_bundle -> apply round-trip,
# and that /var is never written.

import json as _json
import hashlib as _hashlib

_TOOLS = os.path.join(os.path.dirname(_HERE), 'tools')
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)


def _mk_update():
    sys.modules.pop('update', None)
    return importlib.import_module('update')


def _capture_writer():
    """A writer that records (dest, data) instead of touching flash. The
    engine now hands writers a SRC PATH (not bytes) so it never holds a
    whole bundle file in RAM; this test writer reads the src itself just to
    let assertions compare bytes -- that read is host-side test plumbing,
    not part of the production memory-safety path."""
    calls = []

    def w(dest, src):
        with open(src, 'rb') as f:
            calls.append((dest, f.read()))
    return calls, w


def _fs_writer(dest, src):
    """A writer that actually lays bytes down (for the round-trip test),
    streaming src -> dest in chunks like the real default writer does."""
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(src, 'rb') as fin, open(dest, 'wb') as fout:
        while True:
            chunk = fin.read(4096)
            if not chunk:
                break
            fout.write(chunk)


def _write_bundle(bundle_dir, files, fmt=1, min_engine=1, fw='2026.07.17',
                  corrupt=None):
    """Write a bundle by hand. files: {relpath: bytes}. The manifest hash/size
    always describe the ORIGINAL bytes; a relpath in `corrupt` is written to
    disk with the same length but flipped bits, so only its sha256 mismatches
    (isolating the hash check from the size check)."""
    os.makedirs(bundle_dir, exist_ok=True)
    corrupt = corrupt or set()
    entries = []
    for rel in sorted(files):
        body = files[rel]
        entries.append({'path': rel,
                        'sha256': _hashlib.sha256(body).hexdigest(),
                        'size': len(body), 'merge': 'deck-code'})
        out = os.path.join(bundle_dir, rel.replace('/', os.sep))
        parent = os.path.dirname(out)
        if parent:
            os.makedirs(parent, exist_ok=True)
        disk = bytes(b ^ 0xFF for b in body) if rel in corrupt else body
        with open(out, 'wb') as f:
            f.write(disk)
    manifest = {'format': fmt, 'min_engine_format': min_engine,
                'fw_version': fw, 'files': entries}
    with open(os.path.join(bundle_dir, 'manifest.json'), 'w') as f:
        _json.dump(manifest, f)
    return manifest


def test_update_format_guard_refuses_newer_bundle(tmp_path):
    update = _mk_update()
    bdir = str(tmp_path / 'b')
    _write_bundle(bdir, {'deckui.py': b'print(1)\n'}, fmt=2)  # > ENGINE_FORMAT
    calls, w = _capture_writer()
    res = update.apply_bundle(bdir, writer=w, user_root=str(tmp_path / 'user'))
    assert res['ok'] is False
    assert res['applied'] == []
    assert 'USB' in res['reason']            # clear refusal, not a half-apply
    assert calls == []                       # nothing written


def test_update_min_engine_guard_refuses(tmp_path):
    update = _mk_update()
    bdir = str(tmp_path / 'b')
    _write_bundle(bdir, {'deckui.py': b'x=1\n'}, min_engine=2)  # needs newer engine
    calls, w = _capture_writer()
    res = update.apply_bundle(bdir, writer=w, user_root=str(tmp_path / 'user'))
    assert res['ok'] is False and res['applied'] == [] and calls == []


def test_update_verify_pass_applies_all(tmp_path):
    update = _mk_update()
    bdir = str(tmp_path / 'b')
    files = {'deckui.py': b'A' * 5000, 'home.py': b'home\n'}
    _write_bundle(bdir, files)
    calls, w = _capture_writer()
    root = str(tmp_path / 'user')
    res = update.apply_bundle(bdir, writer=w, user_root=root)
    assert res['ok'] is True
    assert set(res['applied']) == {'deckui.py', 'home.py'}
    assert res['failed'] == []
    # writer got each file at its /user path with the correct bytes
    got = {dest: data for dest, data in calls}
    assert got[update._join(root, 'deckui.py')] == files['deckui.py']
    assert got[update._join(root, 'home.py')] == files['home.py']


def test_update_corrupted_file_is_rejected_and_not_written(tmp_path):
    # THE load-bearing check: a file whose bytes don't match its manifest
    # sha256 is never written.
    update = _mk_update()
    bdir = str(tmp_path / 'b')
    _write_bundle(bdir, {'deckui.py': b'good' * 100}, corrupt={'deckui.py'})
    calls, w = _capture_writer()
    res = update.apply_bundle(bdir, writer=w, user_root=str(tmp_path / 'user'))
    assert res['ok'] is False
    assert res['applied'] == []
    assert calls == []                        # corrupt file NEVER written
    assert res['failed'] and res['failed'][0]['path'] == 'deckui.py'
    assert 'sha256' in res['failed'][0]['reason']


def test_update_verify_all_before_apply_one_bad_writes_nothing(tmp_path):
    # A bundle with several good files and ONE bad one must write NOTHING --
    # /user is never left half-updated.
    update = _mk_update()
    bdir = str(tmp_path / 'b')
    _write_bundle(bdir, {'a.py': b'aaa\n', 'b.py': b'bbb\n', 'c.py': b'ccc\n'},
                  corrupt={'b.py'})
    calls, w = _capture_writer()
    res = update.apply_bundle(bdir, writer=w, user_root=str(tmp_path / 'user'))
    assert res['ok'] is False
    assert res['applied'] == []               # not even the good files
    assert calls == []
    assert [f['path'] for f in res['failed']] == ['b.py']


def test_update_never_writes_under_var(tmp_path):
    # /var is user data (config, Wi-Fi, logs). A bundle listing it is malformed;
    # guard anyway -- refuse the whole bundle, write nothing to /var or elsewhere.
    update = _mk_update()
    bdir = str(tmp_path / 'b')
    _write_bundle(bdir, {'deckui.py': b'ok\n',
                         'var/deck_config.json': b'{"secret":1}\n'})
    calls, w = _capture_writer()
    res = update.apply_bundle(bdir, writer=w, user_root=str(tmp_path / 'user'))
    assert res['ok'] is False
    assert calls == []                        # nothing written at all
    assert any('var' in (f['path'] or '') for f in res['failed'])
    # and definitely no write ever targeted a /var path
    assert not any('/var/' in dest or dest.endswith('/var') for dest, _ in calls)


def test_update_safe_relpath_rejects_traversal_and_absolute(tmp_path):
    update = _mk_update()
    for bad in ('/etc/passwd', '../escape.py', 'a/../../b.py', 'var/x', ''):
        with pytest.raises(ValueError):
            update._safe_relpath(bad)
    assert update._safe_relpath('deckui.py') == 'deckui.py'
    assert update._safe_relpath('sub/foo.py') == 'sub/foo.py'


def test_make_update_bundle_roundtrip_reproduces_files(tmp_path):
    # make_update_bundle -> apply_bundle reproduces the exact source files.
    update = _mk_update()
    sys.modules.pop('make_update_bundle', None)
    mub = importlib.import_module('make_update_bundle')

    srcdir = tmp_path / 'src'
    srcdir.mkdir()
    sources = {'deckui.py': b'# deckui\nprint("hi")\n',
               'update.py': b'# engine\nx = ' + b'y' * 3000 + b'\n'}
    for name, body in sources.items():
        (srcdir / name).write_bytes(body)

    bundle = str(tmp_path / 'bundle')
    manifest = mub.build_bundle(
        [str(srcdir / 'deckui.py'), str(srcdir / 'update.py')],
        bundle, fw_version='2026.07.17', label='roundtrip test')

    # manifest shape
    assert manifest['format'] == update.ENGINE_FORMAT
    assert manifest['fw_version'] == '2026.07.17'
    assert all(e['merge'] == 'deck-code' for e in manifest['files'])

    # apply into a fake /user and compare bytes
    root = tmp_path / 'user'
    res = update.apply_bundle(bundle, writer=_fs_writer, user_root=str(root))
    assert res['ok'] is True
    assert set(res['applied']) == set(sources)
    for name, body in sources.items():
        assert (root / name).read_bytes() == body


def test_make_update_bundle_is_deterministic(tmp_path):
    _mk_update()
    sys.modules.pop('make_update_bundle', None)
    mub = importlib.import_module('make_update_bundle')
    src = tmp_path / 'f.py'
    src.write_bytes(b'content\n')
    b1, b2 = str(tmp_path / 'b1'), str(tmp_path / 'b2')
    mub.build_bundle([str(src)], b1, fw_version='v1')
    mub.build_bundle([str(src)], b2, fw_version='v1')
    m1 = open(os.path.join(b1, 'manifest.json'), 'rb').read()
    m2 = open(os.path.join(b2, 'manifest.json'), 'rb').read()
    assert m1 == m2                            # byte-identical, re-runnable


def test_update_progress_callback_reports_two_tiers(tmp_path):
    # The engine feeds the two-tier UI: overall (byte-weighted) + per-file.
    update = _mk_update()
    bdir = str(tmp_path / 'b')
    _write_bundle(bdir, {'a.py': b'a' * 1000, 'b.py': b'b' * 3000})
    events = []
    update.apply_bundle(bdir, progress=lambda i: events.append(dict(i)),
                        writer=lambda d, x: None, user_root=str(tmp_path / 'u'))
    stages = {e['stage'] for e in events}
    assert {'start', 'verifying', 'writing', 'done'} <= stages
    # overall_total is byte-weighted (verify pass + write pass = 2 * 4000)
    done = [e for e in events if e['stage'] == 'done'][0]
    assert done['overall_total'] == 2 * 4000
    assert done['overall_done'] == done['overall_total']
    # sub-bar carries per-file identity + counts
    v = [e for e in events if e['stage'] == 'writing' and e.get('path') == 'b.py']
    assert v and v[0]['item_total'] == 3000 and v[0]['file_count'] == 2


def test_apply_bundle_never_reads_a_whole_file_into_one_buffer(tmp_path):
    # apply_bundle used to do `data = f.read()` (whole file) then hand it to
    # the writer -- a real OOM risk for a large bundle file on tight-RAM
    # boards. It must now hand the writer the SRC PATH so streaming is
    # possible, never a full-file bytes object.
    update = _mk_update()
    bdir = str(tmp_path / 'b')
    _write_bundle(bdir, {'deckui.py': b'Z' * 50000})
    seen = []

    def w(dest, arg):
        seen.append(arg)
        assert isinstance(arg, str)             # a path, never a bytes blob
        assert not isinstance(arg, (bytes, bytearray))

    res = update.apply_bundle(bdir, writer=w, user_root=str(tmp_path / 'user'))
    assert res['ok'] is True
    assert len(seen) == 1
    # the path handed to the writer is readable and matches the source bytes
    with open(seen[0], 'rb') as f:
        assert f.read() == b'Z' * 50000


def test_default_writer_streams_source_in_chunks(tmp_path):
    # Exercise the real on-device writer (update._default_writer): it must
    # read+write src -> dest in _CHUNK-sized pieces (matching
    # _sha256_file's memory safety), never the whole file in one read, and
    # still land the exact bytes atomically.
    update = _mk_update()
    _install_hw_mocks()
    sys.modules.pop('deckcfg', None)
    deckcfg = importlib.import_module('deckcfg')
    deckcfg.PATH = str(tmp_path / 'deck_config.json')
    sys.modules['deckcfg'] = deckcfg

    body = (b'X' * update._CHUNK) + (b'Y' * 1234)   # > 1 chunk, uneven remainder
    src = tmp_path / 'src.py'
    src.write_bytes(body)
    # forward slashes -- like every real dest_path (built via update._join),
    # so _default_writer's '/'-based _mkdirs split actually fires on Windows
    root = str(tmp_path).replace(os.sep, '/')
    dest = root + '/user/deckui.py'

    real_open = open
    read_sizes = []

    class _TrackedFile(object):
        def __init__(self, f):
            self._f = f

        def read(self, n=-1):
            data = self._f.read(n)
            read_sizes.append(len(data))
            return data

        def __getattr__(self, name):
            return getattr(self._f, name)

    def tracking_open(path, mode='r', *a, **k):
        f = real_open(path, mode, *a, **k)
        if path == str(src) and 'r' in mode:
            return _TrackedFile(f)
        return f

    import builtins
    builtins.open = tracking_open
    try:
        update._default_writer(dest, str(src))
    finally:
        builtins.open = real_open

    assert os.path.exists(dest)
    with open(dest, 'rb') as f:
        assert f.read() == body
    assert not os.path.exists(dest + '.new')          # tmp renamed away
    # streamed: more than one read, none of them the whole file at once
    assert len(read_sizes) > 1
    assert all(n <= update._CHUNK for n in read_sizes)


# ===========================================================================
# E-16: host-side coverage for the synthkits transform math, the deckcfg
# write-retry chain, and decklog rollover -- functions that shipped without a
# single test reference. Every assertion below pins REAL transform output for
# a known input (values hand-verified against the functions), not a tautology.
# ===========================================================================


# --- synthkits: pure coef-string helpers ---
def test_synthkits_scale_helpers_pin_transform_math():
    import synthkits as s
    # _scale_times scales ONLY the even (time) slots; odd (level) slots and
    # non-numeric time slots pass through untouched.
    assert s._scale_times('0,1,50,0', 2.0) == '0,1,100,0'
    assert s._scale_times('4,1,abc,0', 2.0) == '8,1,abc,0'
    # _scale_first scales only slot 0 (the const), leaving the rest; a
    # non-numeric first slot is left as-is.
    assert s._scale_first('100,0', 2.0) == '200,0'
    assert s._scale_first('abc,5', 2.0) == 'abc,5'
    # _capped_gain scales slot 0 by g but never past _AMP_CAP (2.5).
    assert s._AMP_CAP == 2.5
    assert s._capped_gain('1', 5.0) == '2.5'      # 5.0 -> clamped to the cap
    assert s._capped_gain('1', 2.0) == '2'        # under the cap: exact
    assert s._capped_gain('0.5', 2.0) == '1'
    assert s._capped_gain('x,2', 3.0) == 'x,2'    # non-numeric const: passthrough
    # _fmt: near-integers render as ints, fractions trim trailing zeros,
    # strings pass through.
    assert s._fmt(200.0) == '200'
    assert s._fmt(2.5) == '2.5'
    assert s._fmt(0) == '0'
    assert s._fmt('str') == 'str'


# --- synthkits: hit_patch_string osc-hit transforms ---
def _inject_hit(s, key, hit):
    pack = key.split('/', 1)[0]
    s._state['packs'][pack] = {key: hit}


def test_hit_patch_string_tune_decay_level_snap():
    import synthkits as s
    _inject_hit(s, 't16/k', {'oscs': [
        {'wave': 0, 'freq': '100', 'amp': '1', 'bp0': '0,1,50,0',
         'filter_freq': '200'},
        {'wave': 5, 'amp': '0.5', 'bp0': '0,1,30,0'}]})   # wave 5 == NOISE
    unity = 1.0 / s.KIT_GAIN            # cancels KIT_GAIN -> net level 1.0

    # identity: net gain 1.0, no tune/decay -> osc params wired verbatim,
    # bp0 -> 'A', filter_freq -> 'F', in _WIRE order. The pad-picker fix makes
    # each osc self-contained: every osc gains a filter reset (G0 filter_type,
    # F0 filter_freq when omitted, R0.7 resonance); osc0 keeps its own F200 and
    # so emits G0F200R0.7, osc1 (no filter) emits G0F0R0.7.
    assert s.hit_patch_string('t16/k', {'level': unity}) == \
        'v0w0f100A0,1,50,0a1G0F200R0.7Zv1w5A0,1,30,0a0.5G0F0R0.7Z'
    # tune +12 semitones doubles freq AND filter_freq (2**(12/12) == 2.0);
    # envelope times and amps are untouched.
    assert s.hit_patch_string('t16/k', {'level': unity, 'tune': 12}) == \
        'v0w0f200A0,1,50,0a1G0F400R0.7Zv1w5A0,1,30,0a0.5G0F0R0.7Z'
    # decay 2.0 scales bp0 TIME slots x2 (50->100, 30->60); freq/amp untouched.
    assert s.hit_patch_string('t16/k', {'level': unity, 'decay': 2}) == \
        'v0w0f100A0,1,100,0a1G0F200R0.7Zv1w5A0,1,60,0a0.5G0F0R0.7Z'
    # level 2.0 -> net 2.0*2.5 == 5.0: both amps drive PAST the cap, so both
    # land exactly on 2.5 (this pins the _capped_gain 2.5 ceiling).
    assert s.hit_patch_string('t16/k', {'level': 2.0}) == \
        'v0w0f100A0,1,50,0a2.5G0F200R0.7Zv1w5A0,1,30,0a2.5G0F0R0.7Z'
    # default overrides -> net KIT_GAIN (2.5): amp '1' -> 2.5, and the noise
    # osc amp '0.5' -> 1.25 (under the cap).
    assert s.hit_patch_string('t16/k') == \
        'v0w0f100A0,1,50,0a2.5G0F200R0.7Zv1w5A0,1,30,0a1.25G0F0R0.7Z'


def test_hit_patch_string_snap_only_scales_the_noise_osc():
    import synthkits as s
    _inject_hit(s, 't16b/k', {'oscs': [
        {'wave': 0, 'amp': '1'},        # tone
        {'wave': 5, 'amp': '0.5'}]})    # noise/transient (wave 5)
    unity = 1.0 / s.KIT_GAIN
    # snap 2.0 with net level 1.0: the tone osc (wave 0) is untouched, only the
    # noise osc gets the extra x2 (0.5*2 == 1.0). Both oscs still carry the
    # self-contained filter reset (G0F0R0.7) from the pad-picker fix.
    assert s.hit_patch_string('t16b/k', {'level': unity, 'snap': 2}) == \
        'v0w0a1G0F0R0.7Zv1w5a1G0F0R0.7Z'


def test_hit_patch_string_clamps_overrides():
    import synthkits as s
    _inject_hit(s, 't16c/k', {'oscs': [
        {'wave': 0, 'freq': '100', 'bp0': '0,1,40,0', 'amp': '1'}]})
    unity = 1.0 / s.KIT_GAIN
    # tune clamps to +/-24 semitones: 9999 -> +24 -> x4 (100->400);
    # -9999 -> -24 -> x0.25 (100->25).
    assert 'f400' in s.hit_patch_string('t16c/k', {'level': unity, 'tune': 9999})
    assert 'f25' in s.hit_patch_string('t16c/k', {'level': unity, 'tune': -9999})
    # decay clamps to [0.25, 4.0]: 9999 -> 4.0 (40->160); 0 -> 0.25 (40->10).
    assert 'A0,1,160,0' in \
        s.hit_patch_string('t16c/k', {'level': unity, 'decay': 9999})
    assert 'A0,1,10,0' in \
        s.hit_patch_string('t16c/k', {'level': unity, 'decay': 0})


def test_xform_partials_parent_only_level_rule():
    import synthkits as s
    # child = partial (w9), parent = summing carrier (w10). Both carry an amp.
    ps = 'v0w9f100,0,0a3Zv1w10a1A0,1,100,0Z'
    # identity transform via hit_patch_string (default level -> KIT_GAIN 2.5):
    # ONLY the parent (w10) amp is scaled (1 -> 2.5, capped); the child amp
    # 'a3' is left ALONE (scaling both would square the gain). Each osc also
    # gains the self-contained filter reset (G0F0R0.7) from the pad-picker fix.
    _inject_hit(s, 'p16/x', {'patch_string': ps})
    assert s.hit_patch_string('p16/x', {'level': 1.0}) == \
        'v0w9f100,0,0a3G0F0R0.7Zv1w10a2.5A0,1,100,0G0F0R0.7Z'
    # direct call, tune/decay/level all != 1: child freq first-slot scaled by
    # tune (100->200), parent amp by level (1->2, under cap), parent bp times
    # by decay (100->200); the child amp 'a3' stays put -> parent-only rule.
    assert s._xform_partials(ps, 2.0, 2.0, 2.0) == \
        'v0w9f200,0,0a3G0F0R0.7Zv1w10a2A0,1,200,0G0F0R0.7Z'


# ---------------------------------------------------------------------------
# deckcfg._write: the deferred flash-write retry chain (coalescing, the
# 60-retry cap, and the 5-second self-heal reclaim).
# ---------------------------------------------------------------------------
def _install_fake_ticks(start=1000):
    """Swap in a `time` module exposing MicroPython's ticks_ms/ticks_diff (both
    absent on CPython) so deckcfg's chain-age logic runs deterministically.
    Returns (real_time_module, clock_dict); caller restores in finally."""
    import time as rt
    ft = types.ModuleType('time')
    ft.__dict__.update(rt.__dict__)
    clock = {'now': start}
    ft.ticks_ms = lambda: clock['now']
    ft.ticks_diff = lambda a, b: a - b
    sys.modules['time'] = ft
    return rt, clock


def test_deckcfg_write_coalesces_one_chain_and_writes_latest(deck):
    import json
    deckcfg, _ = deck
    tulip = sys.modules['tulip']
    rt, clock = _install_fake_ticks(1000)
    deferred = []
    try:
        # force the deferral path: fenced_write reports "not safe to write yet"
        deckcfg.fenced_write = lambda fn: False
        tulip.defer = lambda fn, arg, ms: deferred.append(fn)
        deckcfg.save({'volume': 1, 'tag': 'first'})
        assert len(deferred) == 1                 # one retry chain armed
        assert deckcfg._state['write_chain'] is True
        # a second save while the chain is live (< 5s old) does NOT spawn a
        # second chain -- it coalesces, updating only the cached cfg.
        deckcfg.save({'volume': 2, 'tag': 'second'})
        assert len(deferred) == 1                 # still ONE chain
        assert deckcfg._state['cfg']['tag'] == 'second'
        # when the retry finally lands, it writes the LATEST cache, not the
        # stale value the chain started with.
        deckcfg.fenced_write = lambda fn: (fn(), True)[1]
        deferred[0](None)
        with open(deckcfg.PATH) as f:
            assert json.load(f)['tag'] == 'second'
        assert deckcfg._state['write_chain'] is False
    finally:
        sys.modules['time'] = rt


def test_deckcfg_write_retry_cap_writes_after_60(deck):
    import json, os
    deckcfg, _ = deck
    tulip = sys.modules['tulip']
    rt, clock = _install_fake_ticks(1000)
    deferred = []
    try:
        deckcfg.fenced_write = lambda fn: False   # never "safe" -> always defer
        tulip.defer = lambda fn, arg, ms: deferred.append(fn)
        deckcfg.save({'tag': 'capped'})
        fires = 0
        while deferred and fires < 500:
            deferred.pop()(None)
            fires += 1
        # the chain deferred exactly 60 times, then wrote anyway (cap reached)
        assert fires == 60
        assert os.path.exists(deckcfg.PATH)
        with open(deckcfg.PATH) as f:
            assert json.load(f)['tag'] == 'capped'
        assert deckcfg._state['write_chain'] is False
    finally:
        sys.modules['time'] = rt


def test_deckcfg_write_self_heals_stale_chain_after_5s(deck):
    import json, os
    deckcfg, _ = deck
    rt, clock = _install_fake_ticks(1000)
    try:
        deckcfg.fenced_write = lambda fn: (fn(), True)[1]   # writes if reached
        # pretend a chain is live and started at t=1000.
        deckcfg._state['write_chain'] = True
        deckcfg._state['write_chain_since'] = 1000
        # a save 0s later coalesces into the (young) live chain -> NO write.
        clock['now'] = 1000
        deckcfg.save({'tag': 'young'})
        assert not os.path.exists(deckcfg.PATH)
        # a save 6s later: the chain is implausibly old, so it is RECLAIMED and
        # the write goes through.
        clock['now'] = 7000
        deckcfg.save({'tag': 'old'})
        with open(deckcfg.PATH) as f:
            assert json.load(f)['tag'] == 'old'
        assert deckcfg._state['write_chain'] is False
    finally:
        sys.modules['time'] = rt


# ---------------------------------------------------------------------------
# decklog: log rollover -- one previous generation kept, size counter reset.
# ---------------------------------------------------------------------------
@pytest.fixture
def decklog_mod(tmp_path):
    _install_hw_mocks()
    for m in ('decklog', 'deckcfg'):
        sys.modules.pop(m, None)
    import decklog
    decklog._LOGFILE = str(tmp_path / 'deck.log')   # skip _logfile() probing
    decklog._state.update({'size': None, 'pend': 0, 'armed': False})
    del decklog._PENDING[:]
    return decklog


def test_decklog_write_pending_rolls_over_keeping_one_generation(decklog_mod):
    import os
    decklog = decklog_mod
    p = decklog._LOGFILE
    decklog._MAX = 100                    # tiny cap so a couple lines roll over
    with open(p, 'w') as f:
        f.write('X' * 90)
    decklog._state['size'] = 90

    # under the cap: appends in place, no rollover, buffer + counter cleared.
    decklog._PENDING[:] = ['hello']
    decklog._state['pend'] = 6
    decklog._write_pending()
    assert decklog._PENDING == [] and decklog._state['pend'] == 0
    assert decklog._state['size'] == 96
    assert not os.path.exists(p + '.old')
    assert open(p).read() == 'X' * 90 + 'hello\n'

    # crossing the cap: current log renamed to .old, a fresh log holds the new
    # line, and the size counter resets to just that line.
    decklog._PENDING[:] = ['world']
    decklog._write_pending()
    assert open(p).read() == 'world\n'
    assert open(p + '.old').read() == 'X' * 90 + 'hello\n'
    assert decklog._state['size'] == 6

    # a SECOND rollover drops the oldest generation (only one .old kept).
    decklog._PENDING[:] = ['Z' * 200]
    decklog._write_pending()
    assert open(p + '.old').read() == 'world\n'      # previous .old overwritten
    assert 'hello' not in open(p + '.old').read()


def test_decklog_flush_triggers_rollover_end_to_end(decklog_mod):
    import os
    decklog = decklog_mod
    p = decklog._LOGFILE
    decklog._MAX = 200
    with open(p, 'w') as f:
        f.write('x' * 180)
    decklog._state['size'] = 180
    # log() buffers; flush() commits through fenced_write and rolls over.
    decklog.log('hello world this is a log line')
    decklog.flush()
    assert os.path.exists(p + '.old')
    assert open(p + '.old').read() == 'x' * 180
    body = open(p).read()
    assert 'hello world this is a log line' in body
    assert decklog._state['size'] == len(body)


# --- Phase 1: user PARAM presets (presets.py; pure + storage) ---------------
@pytest.fixture
def presets(tmp_path):
    """Fresh deckcfg + presets with hardware mocked, a temp config file, and a
    temp preset library dir. Returns (deckcfg, presets_module)."""
    _install_hw_mocks()
    for m in ('deckcfg', 'forwarder', 'presets'):
        sys.modules.pop(m, None)
    deckcfg = importlib.import_module('deckcfg')
    deckcfg.PATH = str(tmp_path / 'deck_config.json')
    deckcfg._state.clear()
    # forwarder is imported by deckcfg.apply_instrument (recall); give it a
    # clean state so rebuild_one is a no-op-safe path under the mocks.
    forwarder = importlib.import_module('forwarder')
    forwarder._state.update({'on': False, 'synths': {}, 'routes': {},
                             'notes': {}, 'registered': False})
    presets = importlib.import_module('presets')
    presets.PRESETS_DIR = str(tmp_path / 'presets')
    return deckcfg, presets


def test_slug_is_filesystem_safe():
    import presets as P
    assert P.slug('Warm Bass') == 'warm-bass'
    assert P.slug('  Deep   Pad!! ') == 'deep-pad'
    assert P.slug('A/B\\C:D') == 'a-b-c-d'
    assert P.slug('***') == 'preset'          # all-punctuation -> fallback
    assert P.slug('') == 'preset'
    assert len(P.slug('x' * 200)) <= 48


def test_capture_grabs_exactly_the_reset_patch_set_plus_identity(presets):
    deckcfg, P = presets
    iid = deckcfg.instruments()[0]['id']
    deckcfg.set_instrument(iid, 'type', 'dx7')
    deckcfg.set_instrument(iid, 'patch', 130)
    deckcfg.set_instrument(iid, 'params', {'filter_freq': 800})
    deckcfg.set_instrument(iid, 'reverb_send', 0.4)
    instr = deckcfg.get_instrument(iid)
    rec = P.capture(instr, name='My Sound')
    assert rec['v'] == P.RECORD_VERSION
    assert rec['name'] == 'My Sound'
    assert rec['type'] == 'dx7' and rec['patch'] == 130
    assert rec['params'] == {'filter_freq': 800}
    assert rec['reverb_send'] == 0.4
    assert rec['hits'] == {} and rec['hit_swaps'] == {}
    assert 'created' in rec
    # a non-drums instrument captures NO kit (it is meaningless there)
    assert 'kit' not in rec
    # capture must not alias the instrument's mutable dicts
    rec['params']['filter_freq'] = 1
    assert deckcfg.get_instrument(iid)['params']['filter_freq'] == 800


def test_capture_includes_kit_and_pad_edits_for_drums(presets):
    deckcfg, P = presets
    iid = deckcfg.instruments()[0]['id']
    deckcfg.set_instrument(iid, 'type', 'drums')
    deckcfg.set_instrument(iid, 'kit', 'synth:house')
    deckcfg.set_instrument(iid, 'hits', {'38': {'tune': 2}})
    deckcfg.set_instrument(iid, 'hit_swaps', {'36': 'corpus:kick2'})
    rec = P.capture(deckcfg.get_instrument(iid), name='Kit A')
    assert rec['kit'] == 'synth:house'
    assert rec['hits'] == {'38': {'tune': 2}}
    assert rec['hit_swaps'] == {'36': 'corpus:kick2'}


def test_save_and_load_round_trip_through_json(presets):
    deckcfg, P = presets
    iid = deckcfg.instruments()[0]['id']
    deckcfg.set_instrument(iid, 'type', 'piano')
    deckcfg.set_instrument(iid, 'patch', 5)
    deckcfg.set_instrument(iid, 'params', {'pan': 0.25, 'resonance': 1.5})
    deckcfg.set_instrument(iid, 'reverb_send', 0.3)
    rec = P.save('Grand', deckcfg.get_instrument(iid))
    assert rec['slug'] == 'grand'
    # it really hit disk as one file per preset
    import os
    assert os.path.exists(str(P._path('grand')))
    got = P.load('grand')
    assert got is not None
    assert got['name'] == 'Grand' and got['type'] == 'piano'
    assert got['patch'] == 5
    assert got['params'] == {'pan': 0.25, 'resonance': 1.5}
    assert got['reverb_send'] == 0.3
    assert got['slug'] == 'grand'
    # list_presets surfaces it
    names = [p['name'] for p in P.list_presets()]
    assert names == ['Grand']


def test_fx_is_excluded_from_presets(presets):
    deckcfg, P = presets
    iid = deckcfg.instruments()[0]['id']
    # device FX lives on the shared bus, NOT the instrument -- a preset must
    # never carry it (restoring it would stomp other instruments on the device)
    deckcfg.set_device_fx('internal', 'reverb', 'level', 0.9)
    rec = P.save('NoFX', deckcfg.get_instrument(iid))
    assert 'fx' not in rec
    assert 'reverb' not in rec            # only the per-instrument reverb_send


def test_collision_overwrite_vs_auto_suffix(presets):
    deckcfg, P = presets
    iid = deckcfg.instruments()[0]['id']
    instr = deckcfg.get_instrument(iid)
    P.save('Bass', instr)
    assert P.exists('Bass') is True
    assert P.exists('bass') is True       # collision is by slug, case-folded
    assert P.exists('Lead') is False
    # auto-suffix picks the next free name (reusing deckcfg._unique_name)
    assert P.unique_name('Bass') == 'Bass 2'
    P.save(P.unique_name('Bass'), instr)
    assert P.exists('Bass 2') is True
    assert sorted(p['name'] for p in P.list_presets()) == ['Bass', 'Bass 2']
    # overwrite: saving the SAME name again does not multiply files
    P.save('Bass', instr)
    assert len(P.list_presets()) == 2


def test_recall_applies_every_captured_field(presets):
    deckcfg, P = presets
    src = deckcfg.instruments()[0]['id']
    deckcfg.set_instrument(src, 'type', 'gm')
    deckcfg.set_instrument(src, 'patch', 42)
    deckcfg.set_instrument(src, 'params', {'filter_freq': 1200})
    deckcfg.set_instrument(src, 'reverb_send', 0.5)
    rec = P.save('Organ', deckcfg.get_instrument(src))
    # a DIFFERENT, differently-configured instrument
    dst = deckcfg.add_instrument(device=0, channel=2)['id']
    deckcfg.set_instrument(dst, 'type', 'juno6')
    deckcfg.set_instrument(dst, 'patch', 0)
    deckcfg.set_instrument(dst, 'params', {'pan': 0.1})
    assert P.recall(dst, P.load('Organ')) is True
    got = deckcfg.get_instrument(dst)
    assert got['type'] == 'gm' and got['patch'] == 42
    assert got['params'] == {'filter_freq': 1200}   # overlay REPLACED, not merged
    assert got['reverb_send'] == 0.5
    # recall persisted (survives a cache drop + reload)
    deckcfg.invalidate()
    assert deckcfg.get_instrument(dst)['patch'] == 42
    # the source instrument was untouched
    assert deckcfg.get_instrument(src)['patch'] == 42


def test_recall_resets_overlay_deterministically(presets):
    deckcfg, P = presets
    src = deckcfg.instruments()[0]['id']
    # a CLEAN melodic instrument (no params/hits) saved as a preset
    rec = P.save('Clean', deckcfg.get_instrument(src))
    dst = deckcfg.add_instrument(device='internal', channel=3)['id']
    deckcfg.set_instrument(dst, 'params', {'pan': 0.9})
    deckcfg.set_instrument(dst, 'reverb_send', 0.8)
    P.recall(dst, P.load('Clean'))
    got = deckcfg.get_instrument(dst)
    # recalling a clean preset CLEARS the target's stray overlay (deterministic)
    assert got['params'] == {}
    assert got['reverb_send'] == 0.0


def test_delete_and_rename(presets):
    deckcfg, P = presets
    instr = deckcfg.instruments()[0]
    P.save('Temp', instr)
    P.save('Keep', instr)
    assert P.delete('temp') is True
    assert P.exists('Temp') is False
    assert [p['name'] for p in P.list_presets()] == ['Keep']
    # rename moves the file to the new slug and drops the old one
    out = P.rename('keep', 'Kept')
    assert out['name'] == 'Kept' and out['slug'] == 'kept'
    assert P.exists('Keep') is False and P.exists('Kept') is True
    assert P.load('keep') is None


def test_missing_library_dir_is_empty_not_an_error(presets):
    deckcfg, P = presets
    # nothing saved yet: the dir does not exist -- must read as empty, not raise
    assert P.list_presets() == []
    assert P.load('nope') is None
    assert P.exists('nope') is False


# --- profilerdata: pure logic behind the Debug > Profiler screen ---
def test_pct_of_budget_and_format():
    import profilerdata as pfd
    assert pfd.format_pct(pfd.pct_of_budget(pfd.CYCLES_PER_BLOCK)) == '100%'
    assert pfd.format_pct(pfd.pct_of_budget(pfd.CYCLES_PER_BLOCK // 2)) == '50%'
    assert pfd.pct_of_budget(0) == 0.0


def test_pct_of_budget_never_negative():
    import profilerdata as pfd
    # a bogus/negative cycle reading must not read as a negative percent
    assert pfd.pct_of_budget(-100) == 0.0


def test_pct_of_budget_over_100_is_real_signal():
    import profilerdata as pfd
    # a core that missed its deadline must show > 100%, not get clamped away
    assert pfd.pct_of_budget(pfd.CYCLES_PER_BLOCK * 2) > 100.0


def test_pct_of_budget_guards_zero_budget():
    import profilerdata as pfd
    assert pfd.pct_of_budget(1000, budget=0) == 0.0


def test_block_budget_cycles_from_clock():
    import profilerdata as pfd
    # 100 MHz * 256 / 44100 -- a different clock must change the budget,
    # not silently fall back to the 240 MHz constant
    b = pfd.block_budget_cycles(cpu_hz=100_000_000)
    assert b == 100_000_000 * 256 // 44100
    assert b != pfd.CYCLES_PER_BLOCK


def test_block_budget_cycles_falls_back_without_clock():
    import profilerdata as pfd
    assert pfd.block_budget_cycles(cpu_hz=None) == pfd.CYCLES_PER_BLOCK
    assert pfd.block_budget_cycles(cpu_hz=0) == pfd.CYCLES_PER_BLOCK


def test_core_load_lines_formats_all_four_fields():
    import profilerdata as pfd
    budget = pfd.CYCLES_PER_BLOCK
    rc = (budget, budget // 4, budget // 2, budget // 10)  # (c0w, c1w, c0l, c1l)
    lines = pfd.core_load_lines(rc, budget=budget)
    assert lines == {'core0_worst': '100%', 'core1_worst': '25%',
                      'core0_last': '50%', 'core1_last': '10%'}


def test_read_render_cyc_present():
    import profilerdata as pfd
    calls = []

    class _T:
        def render_cyc(self, *a):
            calls.append(a)
            return (1, 2, 3, 4)
    assert pfd.read_render_cyc(_T()) == (1, 2, 3, 4)


def test_read_render_cyc_absent_is_graceful_fallback():
    """The currently-installed firmware may not have tulip.render_cyc at
    all (it ships with an upcoming build) -- the Profiler screen must show
    'n/a', not raise, in that case."""
    import profilerdata as pfd

    class _NoRenderCyc:
        pass
    assert pfd.read_render_cyc(_NoRenderCyc()) is None


def test_read_render_cyc_swallows_exceptions():
    import profilerdata as pfd

    class _T:
        def render_cyc(self, *a):
            raise RuntimeError('not ready')
    assert pfd.read_render_cyc(_T()) is None


def test_reset_worst_calls_render_cyc_with_one():
    import profilerdata as pfd
    calls = []

    class _T:
        def render_cyc(self, *a):
            calls.append(a)
    pfd.reset_worst(_T())
    assert calls == [(1,)]


def test_reset_worst_absent_is_noop():
    import profilerdata as pfd

    class _NoRenderCyc:
        pass
    pfd.reset_worst(_NoRenderCyc())   # must not raise


def test_internal_sram_summary_excludes_psram_region():
    import profilerdata as pfd
    # (total, free, largest_free, min_free) -- matches mem_probe.py's
    # confirmed esp32.idf_heap_info() tuple shape
    regions = [
        (8 * 1024 * 1024, 6 * 1024 * 1024, 5 * 1024 * 1024, 4 * 1024 * 1024),  # PSRAM
        (100 * 1024, 40 * 1024, 31 * 1024, 20 * 1024),                        # internal A
        (30 * 1024, 10 * 1024, 8 * 1024, 5 * 1024),                           # internal B
    ]
    free_total, largest = pfd.internal_sram_summary(regions)
    assert free_total == 50 * 1024               # 40K + 10K internal only
    assert largest == 31 * 1024                   # the larger of 31K/8K


def test_internal_sram_summary_empty_regions():
    import profilerdata as pfd
    assert pfd.internal_sram_summary([]) == (0, 0)


# --- profilerdata: Debug > Profiler BAR helpers (threshold/percent math
# behind the new horizontal bars, kept host-testable like the rest of this
# module) ---
def test_bar_fill_pct_clamps_over_100_but_not_below():
    import profilerdata as pfd
    assert pfd.bar_fill_pct(45.0) == 45.0
    assert pfd.bar_fill_pct(100.0) == 100.0
    # an over-budget core (>100%) must still draw a FULL bar, not overflow it
    assert pfd.bar_fill_pct(310.0) == 100.0
    assert pfd.bar_fill_pct(0.0) == 0.0
    assert pfd.bar_fill_pct(-5.0) == 0.0


def test_load_bar_color_thresholds():
    import profilerdata as pfd
    assert pfd.load_bar_color(0.0) == pfd.BAR_GREEN
    assert pfd.load_bar_color(79.9) == pfd.BAR_GREEN
    assert pfd.load_bar_color(80.0) == pfd.BAR_AMBER
    assert pfd.load_bar_color(100.0) == pfd.BAR_AMBER
    # over budget (missed the render deadline) must be unmistakably red
    assert pfd.load_bar_color(100.1) == pfd.BAR_RED
    assert pfd.load_bar_color(300.0) == pfd.BAR_RED


def test_mem_pct_free_basic():
    import profilerdata as pfd
    assert pfd.mem_pct_free(50, 200) == 25.0
    assert pfd.mem_pct_free(200, 200) == 100.0


def test_mem_pct_free_guards_missing_or_zero_total():
    import profilerdata as pfd
    # a source this build doesn't have (None) or a zero/unknown total must
    # draw an empty bar, not raise
    assert pfd.mem_pct_free(None, 1000) == 0.0
    assert pfd.mem_pct_free(500, 0) == 0.0
    assert pfd.mem_pct_free(500, None) == 0.0


def test_mem_pct_free_never_exceeds_100():
    import profilerdata as pfd
    # free > total shouldn't happen, but a bar can't physically draw >100%
    assert pfd.mem_pct_free(300, 200) == 100.0


def test_internal_sram_total_excludes_psram_region():
    import profilerdata as pfd
    regions = [
        (8 * 1024 * 1024, 6 * 1024 * 1024, 5 * 1024 * 1024, 4 * 1024 * 1024),  # PSRAM
        (100 * 1024, 40 * 1024, 31 * 1024, 20 * 1024),                        # internal A
        (30 * 1024, 10 * 1024, 8 * 1024, 5 * 1024),                           # internal B
    ]
    assert pfd.internal_sram_total(regions) == 130 * 1024   # 100K + 30K, no PSRAM


def test_internal_sram_total_empty_regions():
    import profilerdata as pfd
    assert pfd.internal_sram_total([]) == 0


# --- logtail: pure tail-reading logic behind the Debug > Logs screen ---
def test_read_tail_bytes_missing_file(tmp_path):
    import logtail
    data, truncated = logtail.read_tail_bytes(str(tmp_path / 'nope.log'))
    assert data == b''
    assert truncated is False


def test_read_tail_bytes_short_file_not_truncated(tmp_path):
    import logtail
    p = tmp_path / 'deck.log'
    # write_bytes (not write_text/'\n') -- write_text's universal-newline
    # translation turns '\n' into '\r\n' on Windows, which would corrupt
    # the line-splitting assertions below
    p.write_bytes(b"[1] one\n[2] two\n[3] three\n")
    data, truncated = logtail.read_tail_bytes(str(p), max_bytes=8192)
    assert truncated is False
    assert logtail.tail_lines(data, n=40, truncated=truncated) == [
        "[1] one", "[2] two", "[3] three"]


def test_read_tail_bytes_seeks_near_end_when_large(tmp_path):
    import logtail
    p = tmp_path / 'deck.log'
    lines = ["[%d] line %d" % (i, i) for i in range(1, 201)]
    p.write_bytes(("\n".join(lines) + "\n").encode('utf-8'))
    data, truncated = logtail.read_tail_bytes(str(p), max_bytes=200)
    assert truncated is True
    out = logtail.tail_lines(data, n=40, truncated=truncated)
    # the last line is always intact; a leading partial fragment is dropped
    assert out[-1] == "[200] line 200"
    assert all(l.startswith('[') for l in out)


def test_tail_lines_truncated_single_fragment_is_dropped():
    import logtail
    # The whole tail window landed inside one long line (no '\n' anywhere in
    # the read) -- text.split('\n') then has exactly ONE element. A
    # truncated read always starts mid-line, so that lone fragment is a
    # cut-off partial, not a complete line, and must be dropped -- the old
    # `len(lines) > 1` guard let it through and displayed it as if whole.
    data = b"...cut off in the middle of one very long unterminated line"
    assert logtail.tail_lines(data, n=40, truncated=True) == []


def test_tail_lines_not_truncated_single_line_is_kept():
    import logtail
    # Same shape (one fragment, no '\n') but truncated=False -- a short file
    # read from byte 0, so the single line IS complete and must be kept.
    data = b"only line, no trailing newline"
    assert logtail.tail_lines(data, n=40, truncated=False) == [
        "only line, no trailing newline"]


def test_tail_lines_newest_last_and_capped_to_n():
    import logtail
    data = ("\n".join("[%d] msg" % i for i in range(1, 11))).encode('utf-8')
    out = logtail.tail_lines(data, n=3, truncated=False)
    assert out == ["[8] msg", "[9] msg", "[10] msg"]


def test_tail_lines_empty_data():
    import logtail
    assert logtail.tail_lines(b'', n=40, truncated=False) == []
    assert logtail.tail_lines(None, n=40, truncated=False) == []


def test_tail_lines_drops_blank_lines():
    import logtail
    data = b"[1] a\n\n[2] b\n"
    assert logtail.tail_lines(data, n=40, truncated=False) == ["[1] a", "[2] b"]


def test_decklog_logfile_accessor(decklog_mod):
    """decklog.logfile() is the public path the Logs screen reads -- it
    must return exactly the path decklog itself writes to (the
    decklog_mod fixture pins _LOGFILE to a temp path so this doesn't probe
    the real /sd, /user/var of the HOST running the test)."""
    decklog = decklog_mod
    assert decklog.logfile() == decklog._LOGFILE
    assert decklog.logfile() == decklog._lf()


# ---------------------------------------------------------------------------
# boardlink: deck <-> AMYboard transport (pure logic -- codec + framing only;
# UsbMidiLink/UartLink touch tulip/machine and aren't exercised on the host).
# ---------------------------------------------------------------------------

def test_encode7_decode7_round_trip_empty_and_one_byte():
    import boardlink as bl
    assert bl.decode7(bl.encode7(b'')) == b''
    for b in (0x00, 0x01, 0x7f, 0x80, 0xff):
        one = bytes([b])
        assert bl.decode7(bl.encode7(one)) == one


def test_encode7_decode7_round_trip_all_256_byte_values():
    import boardlink as bl
    data = bytes(range(256))
    encoded = bl.encode7(data)
    assert bl.decode7(encoded) == data


def test_encode7_output_is_7bit_sysex_safe():
    import boardlink as bl
    # every byte of a base64 encoding must be a legal SysEx data byte
    # (0x00-0x7F) since encode7's whole job is getting 8-bit data across a
    # link that reserves 0xF0/0xF7 as framing bytes.
    data = bytes(range(256)) * 4
    encoded = bl.encode7(data)
    assert all(0x00 <= b <= 0x7F for b in encoded)
    # and it must not itself contain a literal SysEx delimiter
    assert 0xF0 not in encoded and 0xF7 not in encoded


def test_encode7_decode7_round_trip_a_few_kb_random():
    import boardlink as bl
    import os
    for size in (0, 1, 3, 4096, 5000):
        data = os.urandom(size) if size else b''
        assert bl.decode7(bl.encode7(data)) == data


def test_encode7_has_no_trailing_newline():
    import boardlink as bl
    # binascii.b2a_base64 appends a trailing newline by default on both
    # MicroPython and CPython; encode7 must strip it (matches the
    # binascii.b2a_base64(...).rstrip() idiom already used in
    # tulip/shared/amyboard-py/amyboard.py for the same reason).
    assert not bl.encode7(b'hello world').endswith(b'\n')


def test_pack_unpack_frame_round_trip():
    import boardlink as bl
    for opcode, payload in ((bl.OP_PING, b''),
                             (bl.OP_VERSION, b'ping'),
                             (bl.OP_OTA_BEGIN, bytes(range(50))),
                             (0x7f, b'x')):
        frame = bl.pack_frame(opcode, payload)
        assert bl.unpack_frame(frame) == (opcode, payload)


def test_pack_frame_default_empty_payload():
    import boardlink as bl
    frame = bl.pack_frame(bl.OP_STATE_PUSH)
    assert frame == bytes([bl.OP_STATE_PUSH])
    assert bl.unpack_frame(frame) == (bl.OP_STATE_PUSH, b'')


def test_pack_frame_rejects_opcode_out_of_range():
    import boardlink as bl
    import pytest as _pytest
    with _pytest.raises(ValueError):
        bl.pack_frame(-1, b'')
    with _pytest.raises(ValueError):
        bl.pack_frame(0x80, b'')


def test_unpack_frame_empty_is_none():
    import boardlink as bl
    assert bl.unpack_frame(b'') is None
    assert bl.unpack_frame(None) is None


def test_build_parse_envelope_round_trip():
    import boardlink as bl
    for payload in (b'', b'zIZ', bytes(range(0, 0x80))):
        raw = bl.build_envelope(payload)
        assert raw[0] == 0xF0 and raw[-1] == 0xF7
        assert raw[1:4] == bytes(bl.MFR[1:4])
        assert bl.parse_envelope(raw) == payload


def test_parse_envelope_rejects_malformed_or_foreign_frames():
    import boardlink as bl
    assert bl.parse_envelope(None) is None
    assert bl.parse_envelope(b'') is None
    assert bl.parse_envelope(bytes([0xF0, 0x00, 0x03, 0x45, 0xF7])) == b''
    # too short to even hold the manufacturer id
    assert bl.parse_envelope(bytes([0xF0, 0xF7])) is None
    # missing F0/F7 delimiters
    assert bl.parse_envelope(bytes([0x00, 0x03, 0x45, 0x41, 0x4B])) is None
    # right length, wrong manufacturer id (some other device's SysEx)
    other = bytes([0xF0, 0x00, 0x00, 0x0E, 0x01, 0xF7])
    assert bl.parse_envelope(other) is None


def test_open_factory_selects_transport_class():
    import boardlink as bl
    usb = bl.open(0)
    assert isinstance(usb, bl.UsbMidiLink) and usb.device == 0
    uart = bl.open(0, transport='uart')
    assert isinstance(uart, bl.UartLink) and uart.device == 0
    with pytest.raises(ValueError):
        bl.open(0, transport='carrier-pigeon')


def test_usbmidi_send_wraps_envelope_and_uses_tulip_midi_out(deck):
    # `deck` fixture installs the tulip mock (records midi_out calls).
    import boardlink as bl
    link = bl.open(3)   # USB device index 3
    link.send(b'zIZ')
    sent = sys.modules['tulip']._sent
    assert len(sent) == 1
    data, device = sent[0]
    assert device == 3
    assert data == bl.build_envelope(b'zIZ')


def test_uartlink_send_recv_are_unimplemented_stubs():
    import boardlink as bl
    uart = bl.UartLink(0)
    with pytest.raises(NotImplementedError):
        uart.send(b'zIZ')
    with pytest.raises(NotImplementedError):
        uart.recv()
