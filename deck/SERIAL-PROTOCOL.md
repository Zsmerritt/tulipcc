# SERIAL-PROTOCOL — typed messages on the shared console

The deck's serial link (COM11, USB-CDC) is a SHARED stream: the MicroPython
REPL, C-side prints (AMY warnings, littlefs errors, boot logs), decklog
echoes, and our tools' command/response traffic all interleave on it.
Untyped parsing of that stream caused real failures (corrupted file
transfers, a connected device reported as off-Wi-Fi, hash reads polluted by
keypress warnings).

**The rule: every serial output we control carries a prepended type, and
every receiver listens ONLY for its type.** Multi-message flows add a
per-invocation nonce so a stale line from a previous attempt can never
satisfy a new request. Chatter then costs nothing: it simply doesn't match.

## Audit (2026-07-16) — every sender/receiver we control

| Channel | Sender -> Receiver | Type tag | Nonce | Status |
|---|---|---|---|---|
| flash_ota IP query | device -> host | `IP:` | single-flight | OK |
| flash_ota reach check | device -> host | `PING:` | single-flight | OK |
| flash_ota OTA result | device -> host | `OTA:` (`OK`, `BOOTSET`) | single-flight | OK (tightened to tagged parse 2026-07-16) |
| flash_stream framed transfer | both directions | `FWD:` / `FWA:` | lockstep seq | OK (by design) |
| flash_fw chunk hashes | device -> host | `HASH:` | per-chunk name | OK |
| deploy_verify sha check | device -> host | `SHA:` | retry-loop tolerant | OK |
| decklog console echo | device -> host | `DECKLOG ` | n/a (log) | OK |
| qget file fetch | device -> host | `B64:<nonce>:` + `B64END:<nonce>:<sha8>` | **yes** | FIXED 2026-07-16 (was untagged base64 — any chatter line corrupted the decode; now filtered + checksummed, fails loudly) |
| qexec script results | device -> host | caller-chosen `TAG:` via the new filter arg | caller-chosen | ADDED 2026-07-16 (optional; untagged mode still passes everything through for interactive use) |
| raw-REPL command channel | host -> device | MicroPython `\x01/\x04` framing | protocol-level | inherent (not ours to tag); qexec's no-reset transport + bounded reads mitigate |
| mpremote `fs cp` deploys | host -> device | none (mpremote internal) | n/a | KNOWN GAP — mitigated by deploy_verify's sha retry loop; long-term, deploys can move to a `FWD:`-style framed push or Wi-Fi |

Conventions for new code:
- Pick a short unique UPPERCASE type ending in `:` (`KIT:`, `CAL:`...).
- Multi-line or retry-prone flows: embed a nonce — `TYPE:<nonce>:payload` —
  generated per invocation; verify an end-marker with a checksum where the
  payload matters (see qget).
- One in-flight request per port (serial is exclusive); nonces are for
  RETRIES and stale-line rejection, not concurrency.

## Architecture decision: where the router lives

Owner suggestion considered: a C-side router that demultiplexes inbound
serial and calls up to Python. Decision (open to revision by the
engineering review): **the typed-message convention lives host-side in
Python for now; no C console router.** Reasons:

1. Outbound (device->host) is where every real failure happened, and tags
   fix it completely at zero runtime cost — the fixes above close the audit.
2. Inbound traffic is the raw-REPL protocol itself; a C router would have to
   sit inside the USB-CDC/REPL core, the exact channel used to recover the
   device when everything else is broken. High regression risk on the
   recovery path for a problem we no longer observe (qexec's no-reset
   transport removed the inbound flakes).
3. A C router earns its keep the day the deck needs device-initiated
   messages to a persistent host session (push telemetry, live meters over
   serial). If that arrives, the right shape is a tiny mux at the CDC layer
   framing REPL vs typed channels -- revisit then.
