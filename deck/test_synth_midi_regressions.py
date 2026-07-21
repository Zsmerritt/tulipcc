# Regression tests for two host-testable correctness fixes in the shared
# firmware Python layer (bugfix-deferred-guards):
#   - synth.PatchSynth.release() is idempotent (a 2nd call used to raise
#     ValueError because it sent amy.send(synth=None, num_voices=0)).
#   - midi.MidiConfig.remove_arpeggiator() clears .synth on dict[channel],
#     not on the dict object itself (was a guaranteed AttributeError that also
#     meant the arpeggiator entry was never deleted).
#
# The modules under test live in ../tulip/shared/py. We deliberately do NOT
# put this test file there: pytest prepends a test file's own directory to
# sys.path, and tulip/shared/py/patches.py would then shadow the `patches`
# that deck/shellmodel.py resolves, breaking unrelated deck tests in a
# combined run. Instead this file lives beside test_deck.py and runs the real
# synth/midi code in a FRESH subprocess interpreter, so nothing (sys.path,
# sys.modules, or PatchSynth class state) leaks into the parent pytest.

import os
import subprocess
import sys
import textwrap

_DECK_DIR = os.path.dirname(os.path.abspath(__file__))
_SHARED_PY = os.path.normpath(
    os.path.join(_DECK_DIR, '..', 'tulip', 'shared', 'py'))

# Runs in a clean interpreter. Installs minimal stubs for the real modules'
# dependencies, loads the REAL synth.py / midi.py, and exercises the fixed
# paths. Prints "OK" on success; raises (non-zero exit) on regression.
_CHILD = textwrap.dedent(r'''
    import sys, types

    SHARED = sys.argv[1]
    check = sys.argv[2]

    def permissive(name):
        m = types.ModuleType(name)
        m.__getattr__ = lambda attr: (lambda *a, **k: None)
        return m

    # amy stub reproduces the real None-arg rejection (amy/amy/__init__.py):
    # any non-time/sequence kwarg that is None -> ValueError. That is exactly
    # what makes an unguarded second release() blow up.
    amy = types.ModuleType('amy')
    amy.sent = []
    def _send(**kwargs):
        for key, arg in kwargs.items():
            if arg is None and key not in ('time', 'sequence'):
                raise ValueError('No arg for key ' + key)
        amy.sent.append(kwargs)
    amy.send = _send
    amy.reset = lambda *a, **k: None
    sys.modules['amy'] = amy
    for n in ('tulip', 'patches', 'tulip_queue', 'arpegg'):
        sys.modules[n] = permissive(n)

    sys.path.insert(0, SHARED)
    # Import midi FIRST: it resolves the midi<->synth cycle (its
    # `from synth import ...` fully loads synth while midi is partially
    # initialised) -- the same order the device uses.
    import midi
    import synth

    if check == 'release':
        s = synth.PatchSynth(num_voices=2, channel=5)
        assert s.synth == 5, s.synth
        s.release()
        assert s.synth is None
        assert {'synth': 5, 'num_voices': 0} in amy.sent
        before = len(amy.sent)
        s.release()                      # must be a no-op, not raise
        assert s.synth is None
        assert len(amy.sent) == before, 'second release() sent a message'

    elif check == 'arp':
        mc = midi.MidiConfig()
        class Arp: pass
        a = Arp(); a.synth = object()
        mc.arpeggiator_per_channel[3] = a
        mc.remove_arpeggiator(3)         # pre-fix: AttributeError on the dict
        assert 3 not in mc.arpeggiator_per_channel, 'entry not removed'
        assert a.synth is None, 'arp.synth not cleared'

    elif check == 'arp_absent':
        mc = midi.MidiConfig()
        mc.remove_arpeggiator(99)        # absent channel: must not raise
        assert 99 not in mc.arpeggiator_per_channel

    else:
        raise SystemExit('unknown check ' + check)

    print('OK')
''')


def _run_check(check):
    proc = subprocess.run(
        [sys.executable, '-c', _CHILD, _SHARED_PY, check],
        capture_output=True, text=True)
    assert proc.returncode == 0, (
        "check %r failed (rc=%s)\nstdout:\n%s\nstderr:\n%s"
        % (check, proc.returncode, proc.stdout, proc.stderr))
    assert 'OK' in proc.stdout


def test_release_is_idempotent():
    _run_check('release')


def test_remove_arpeggiator_targets_channel_entry():
    _run_check('arp')


def test_remove_arpeggiator_absent_channel_is_noop():
    _run_check('arp_absent')
