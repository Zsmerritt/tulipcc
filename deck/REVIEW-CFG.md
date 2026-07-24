# SLICE C — Config / persistence / timing review (slice id: CFG)

## Exec summary

The config/persistence/timing layer is mature and heavily hardened: the atomic
tmp+rename, the single write-chain with self-heal, the auto/manual/quiet fence
ladder, the batched log flush, the shared 100ms ticker, and the GC-rooted defer
pool are each well-reasoned and, on the tracing I did, correct under their
stated failure modes. The write-chain "stuck flag" and "duplicate chain" hazards
I chased both turn out to be closed (chained retries refresh `write_chain_since`,
and every defer-failure path falls through to an immediate `do()` + flag reset).
I found **one genuine state-corruption defect** — the module-level
`DEFAULTS['fx']` dict is mutated in place, so once a user edits any FX value the
default is polluted for the entire session and later fresh/factory-reset configs
inherit stale FX (reproduced below; the host tests miss it only because the
`deck` fixture re-imports `deckcfg` every test, resetting `DEFAULTS`). The rest
are LOW: three of them only bite on old no-fence firmware, one is an
import-ordering issue that silently defeats the documented `/sd` log channel, and
one is a ticker fragility where a single transient exception permanently unhooks
a subscriber. `python -m pytest deck/test_deck.py -q` -> 93 passed at baseline.

Severity counts: CRIT 0, HIGH 0, MED 1, LOW 5.

---

## Findings (most severe first)

### 1. [MED] `DEFAULTS['fx']` is mutated in place — FX bleeds across fresh/reset configs
`deck/deckcfg.py:184` (`cfg = dict(DEFAULTS)`) + `deck/deckcfg.py:547`
(`fx.setdefault(...).setdefault(...)[name] = value`)

`load()` does a **shallow** `dict(DEFAULTS)`, so `cfg['fx']` is the *same object*
as the module-level `DEFAULTS['fx']` whenever the config file has no `'fx'` key
(exactly fresh installs and any pre-FX config — `cfg.update(...)` on line 185
only overwrites `'fx'` if the file already carries it). `set_device_fx` then does
`fx = cfg.setdefault('fx', {})` (returns the aliased dict) and mutates it in
place. That permanently writes the user's FX into `DEFAULTS['fx']`.

On the device the module is imported once at boot and lives for the whole
session, so after the first FX edit every subsequently created default-derived
config aliases the now-polluted `DEFAULTS['fx']`. Concrete failure: a factory
reset (or any path that produces a fresh `data={}` config in the same process)
comes back up with the *old* device's reverb/echo/EQ still present, and
`device_fx()` reports non-`{}` for a brand-new device. Reproduced against the
exact code path:

```
DEFAULTS fx after first edit -> {'internal': {'reverb': {'level': 0.4}}}
brand-new fresh cfg[fx]      -> {'internal': {'reverb': {'level': 0.4}}}   # should be {}
is same object as DEFAULTS[fx]: True
```

The host suite passes only because the `deck` fixture (`test_deck.py:236-238`)
pops+re-imports `deckcfg` per test, resurrecting a clean `DEFAULTS`. That masks a
real on-device bug.

**Fix:** deep-copy the mutable defaults in `load()`, e.g.
`cfg = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v) for k, v in DEFAULTS.items()}`
(or explicitly `cfg['fx'] = dict(cfg['fx'])` before the aliasing can leak).
`favorites` is already safe because `toggle_favorite` copy-then-reassigns; `fx`
is the only default mutated in place.

---

### 2. [LOW] `decklog._LOGFILE` is frozen at import, before SD is mounted — `/sd` channel is dead
`deck/decklog.py:36` (`_LOGFILE = _logfile()`) vs `deck/boot.py:29` (import
decklog) and `deck/boot.py:76-80` (SD mount)

`_logfile()` prefers `/sd/deck.log` when a card is mounted, and the whole point
of that branch (per its own comment) is that SD is a separate peripheral whose
writes never race the flash cache / need the fence. But `decklog` is imported and
used for the boot banner at `boot.py:29`, which runs `_LOGFILE = _logfile()`
*before* the SD mount at `boot.py:76-80`. So `_LOGFILE` is always resolved to
`/user/var/deck.log`; even when SD is present the log keeps going to flash and
keeps paying the fence/quiet penalty the SD path was meant to avoid. (SD pins are
hardware-gated and inert on v4r9, so this is latent, not currently observable —
noting it because it's a logic/ordering defect in my slice, independent of the
pin question.)

**Fix:** resolve the logfile lazily (compute `_LOGFILE` on first write, or
re-resolve after mount), or expose a `decklog.use_sd()` that `boot.py` calls
right after a successful `uos.mount(..., '/sd')`.

---

### 3. [LOW] no-fence trim leaves `_state['pend']` inflated -> every later `log()` thrashes flush
`deck/decklog.py:100-102`

On old no-fence firmware, when `fenced_write(_write_pending)` returns `False`
(audio playing) and `len(_PENDING) > 200`, the code trims with
`del _PENDING[:-200]` but never decrements `_state['pend']`. `_write_pending`
was never called (it clears the buffer at its own start), so the byte counter
stays at its pre-trim value. From then on `_state['pend']` is desynced high, so
`log()`'s `if _state['pend'] >= _FLUSH_BYTES` (line 115) is true on essentially
every call, firing a `flush()` per log line — the exact per-line flush storm the
batching (O-6) exists to avoid — until a real write finally succeeds and resets
`pend` to 0. Fence/auto-fence firmware never hits this.

**Fix:** recompute `_state['pend']` after trimming, e.g.
`_state['pend'] = sum(len(l) + 1 for l in _PENDING)`.

---

### 4. [LOW] deferred log flush can drop `armed` without re-arming -> buffered tail lingers
`deck/decklog.py:91` (`_state['armed'] = False`) with no re-arm on the
write-deferred branch

`flush()` clears `armed` at entry, then on no-fence firmware may fail to write
(quiet_now False) and return with lines still buffered and `armed` False. Nothing
re-schedules a flush; the tail only lands on the next `log()` call (which re-arms)
or an explicit pre-reboot `flush()`. If logging goes quiet right after a failed
flush, those diagnostic lines sit in RAM indefinitely. Minor for a diagnostic
log, and fence firmware writes immediately, hence LOW.

**Fix:** on the "couldn't write and buffer non-empty" branch, re-arm the deferred
flush (set `armed=True` + `tulip.defer(flush, 0, _FLUSH_MS)`).

---

### 5. [LOW] ticker drops a subscriber permanently on a single transient exception
`deck/ticker.py:43-46`

`_fire` runs each subscriber in `try/except` and `cancel(key)`s it on *any*
exception. This is correct for a genuinely dead panel, but a one-shot transient
(e.g. a clock/meter label touched during a concurrent panel rebuild) unhooks the
subscriber forever. A subscriber that is re-registered on every panel build
self-heals on the next build; a build-once persistent widget (top-bar clock/meter)
that raises once stays dead until reboot. Given those ticks live in homeshell
(slice UI), I'm flagging the interface risk, not prescribing the UI fix.

**Fix (ticker side):** only drop after N consecutive failures, or distinguish
"object deleted" (drop) from a transient (keep). At minimum, log the drop via
`decklog.dbg` so a silently-frozen clock is diagnosable.

---

### 6. [LOW] `apply()._volume` fallback default (4) disagrees with `DEFAULTS['volume']` (1)
`deck/deckcfg.py:687,689` use `cfg.get('volume', 4)`

Cosmetic/latent: `load()` always injects `DEFAULTS['volume']=1`, so the `4`
fallback is only reachable when `apply()` is handed a raw non-`load()` dict (e.g.
`boot.py`'s `cfg={}` after a config-load failure). In that degraded path the boot
volume would come up at `4` — the exact 4x-hot level the comment on line 55-58
says was removed for causing clipper crackle. Harmless in the normal path.

**Fix:** use `cfg.get('volume', 1)` (and likewise keep the fallbacks aligned with
`DEFAULTS`).

---

## Seam notes (not counted as findings)

- `tsequencer.c:29-31` (reader side of the defer pool, my slice) has **no read
  memory barrier** to pair with the writer's publish order; the writer's barrier
  in `modtulip.c:262` is a *compiler-only* `__asm__ volatile("" ::: "memory")`,
  not an Xtensa `MEMW`/full barrier. On the dual-core S3 a hardware store-buffer
  reordering could in principle let the AMY core observe `defer_callbacks[i]`
  before the matching `defer_args`/`defer_sysclock` writes land — the very hazard
  the comment claims to close. The write-side barrier lives in `modtulip.c`, which
  the brief assigns to slice MIDI ("the defer publish order"), so I'm flagging it
  for that slice rather than claiming it here.
- `tsequencer.c:39` schedules sequencer callbacks with
  `mp_sched_schedule(..., mp_obj_new_int(tick_count))` — unlike the defer path it
  does **not** check the return value (a full sched queue silently drops the tick)
  and it allocates an int object from the AMY render core. For a musical tick,
  drop-on-late is arguably more correct than the defer retry, and `mp_obj_new_int`
  is alloc-free for small ints; large-int allocation only after ~2^30 ticks
  (months of uptime) and only if the deck actually arms a sequencer slot, which it
  does not appear to. Pre-existing and effectively inert for the deck — noting for
  completeness.

## Perf note

Nothing in this slice is on AMY's per-block render path; the timing code (ticker
lv_timer, screensaver 300ms poll, decklog batched flush) already reflects the
O-3/O-6/O-7 optimizations and costs are negligible. The one perf-adjacent
regression is finding #3 (flush thrash on no-fence firmware); no additional
optimization list is warranted.
