# eufy Who Unlocked for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Matter tells Home Assistant that the eufy FamiLock S3 Max was unlocked —
but not **who** unlocked it. This project fills that gap, read-only:
**who last unlocked the front door, and by which method** — palm, PIN,
app, or manual thumbturn.

Home Assistant now listens to eufy push notifications **directly** —
there is no bridge to run, no Mac Mini, no extra process:

```
eufy cloud (FCM push) ──► Home Assistant (PushListener, in this integration)
Matter (local push)   ──► lock.eufy_familock_s3_max (unchanged)
```

## What it is

- `sensor.front_door_last_unlock` — state is the person's name as eufy
  reports it, with method, event type, exterior flag, time, and
  short user id as attributes.
- `sensor.front_door_unlock_method` — state is HOW the door was last
  unlocked, as an enum: one of `manual`, `auto`, `passcode`,
  `fingerprint`, `app`, `palm`. The options come from the lock model
  (the serial's 5-character prefix, e.g. `T85V0` — the same value the
  startup poll reports as `CloudDevice.model_id`), looked up in
  `lock_push.MODEL_UNLOCK_METHODS`; an unlisted model falls back to
  the full observed set. Who cannot be an enum: names are dynamic,
  from eufy per enrolled user.
- `event.front_door_unlock` — fires on each new unlock, ~2 seconds
  after it happens (at push time, not on a poll).
- Push-only after one startup device-list poll. An optional
  10-minute reconciliation poll exists in the options flow and is
  off by default.

## What it is NOT

- Not official, not affiliated with, endorsed by, or supported by
  Anker or eufy. It uses alarun's `python-eufy-home-security`
  library, which speaks the protocol the eufy app speaks. eufy or
  Google could change it and break push delivery.
- Not a lock. There is **no lock entity and no lock/unlock control**
  here — Matter already does that locally.
- Not presence. An exterior unlock means that person **arrived**. It
  does not prove they are still home — leaving produces no unlock
  event, and there is no presence entity here.
- Not a camera integration. No video, motion, or battery entities.
  Scope is deliberately who-unlocks / last unlock + method only.

## How it works

The integration signs in to the eufy cloud with the account you
give it (email + password + lock serial + country) and runs
`PushListener` from `eufy-home-security==0.4.12` — the push stack
only: Android checkin → Firebase Installations → GCM register →
MCS socket → push decode. The FCM token is re-uploaded to eufy on
every start, and the listener supervises its own socket (30s
checks, doubling backoff). The eufy session, password and FCM
credentials live in a private Home Assistant store
(`Store(private=True, atomic_writes=True)`, keyed by a sha256
prefix of the e-mail) — never in the config entry.

The library decodes pushes into `SecurityEvent`s but does **no
lock decoding** — no 257/259/261/530 handling, no `short_user_id`
or `params`. The integration's `lock_push.py` shim (about 100
lines, the single source for the event table, shared with the
tests) reads the inner payload back out of `event.raw["payload"]`
and maps it. The inner payload's `event_time` is used, not the
event's `event_time_ms` (~3s later).

## Verified live — 2026-11-05

All pushes arrived ~2 seconds after the unlock (param 6000 = `3`
unlocked). Proved by the Node reference bridge on a Mac Mini; the
Python mapper replays the same capture to the same five unlocks
(see Tests).

| event_type | method | who | short_user_id | exterior |
|---|---|---|---|---|
| 261 | app | adam | 000A | yes |
| 257 | manual / thumbturn | cantrowitz (account holder — anonymous in practice) | 0000 | no |
| 530 | palm / vein | per enrolled user — cantrowitz 0000, Yvonne 0003 | — | yes |
| 259 | passcode / PIN | Emma | 0002 | yes |

Event **530 is new**: it is not in the SDK's `LockPushEvent` enum
(257–269). Palm names the person per enrolled user — Adam's own palm
reads as `cantrowitz` only because that is his name in the app.
Only the manual thumbturn is truly anonymous. Lock events 262–269
update the locked flag only and never overwrite the last unlock.

## Architecture — why two triggers

- **Matter is the fast trigger.** `lock.eufy_familock_s3_max` changes
  over local Matter push the moment the lock moves. Automations that
  need the unlock immediately should trigger on Matter and read this
  sensor, correlating the two within **10 seconds** — never assume
  arrival order, Matter and eufy push are independent paths.
- **eufy push is the who.** The push arrives ~2s after the unlock
  and feeds the sensor and the event entity directly.
- **If eufy push dies**, Matter still works and the existing
  10-second timer fallback in `automation.unlock_lights` still works.
  Nothing here is load-bearing for locking the door.
- **A poll can only re-seed locked/unlocked** — it cannot recover WHO
  unlocked during a push gap. Missed unlocks stay missed. The
  startup poll and the optional reconciliation poll exist only to
  confirm the lock is still listed and the cloud reachable.

## Setup

### Requirements

- Home Assistant 2026.10+, Python 3.14 (the HA host, not this repo).
- The HA host must reach `android.clients.google.com`,
  `firebaseinstallations.googleapis.com` and `mtalk.google.com:5228`
  direct TLS with SNI — no HTTP proxy fallback. A host behind an
  egress proxy that blocks the FCM checkin cannot register for push.

### 1. Install the integration (HACS custom repo or manual)

- HACS → Custom repositories → `https://github.com/cantrowitz/ha-eufy-who-unlocked`,
  category Integration — or copy `custom_components/eufy_who_unlocked/`
  into your Home Assistant `custom_components/` and restart.

### 2. Add the integration

- Settings → Devices & Services → Add Integration → **eufy Who Unlocked**.
- Enter the eufy account e-mail + password, the lock serial (find it
  in the eufy app, under the lock's device settings), and the login
  country (default US).
- Use a **separate eufy account** shared to the lock, not your primary
  account, if you can.
- First login may ask for a two-step verification code (e-mailed by
  eufy) or a captcha. The code step is in the UI; for a captcha, the
  image is written to the Home Assistant log (config flows cannot
  render images) — open its data-URI link in a browser and type the
  characters. Signing in once in the eufy app also clears the
  challenge.

### 3. Verify

Unlock the door (palm, PIN, app, thumbturn) and confirm
`sensor.front_door_last_unlock` updates within a few seconds and
`event.front_door_unlock` fires.

### 4. Use it

```yaml
# Example: foyer lights only for exterior arrivals, not thumbturns
automation:
  - alias: "Unlock lights for arrivals"
    trigger:
      - platform: state
        entity_id: lock.eufy_familock_s3_max
        to: unlocked
    condition:
      - condition: template
        value_template: >
          {{ state_attr('sensor.front_door_last_unlock', 'exterior') is true
             and (now() - state_attr('sensor.front_door_last_unlock', 'at_iso') | as_datetime).total_seconds() < 10 }}
    action:
      - service: light.turn_on
        target:
          entity_id: light.foyer_mirror_sconces
```

## Limitations

- **Fixed in v0.2.2 — the v0.2.1 hook read the token too early.**
  The library fires `on_token_upload` BEFORE assigning
  `listener.token`, so v0.2.1 always saw `None`, logged "no FCM
  token is available" (HA log, 10:34:46 PM), and the two
  security-app calls never ran. v0.2.2 triggers the registration
  from `on_listening` (token set by then) and, as a fallback,
  from `on_token_upload` via a task that waits up to 10s for the
  token to appear. Deduplicated per token; a listening drop
  re-registers on restart.
- **Fixed in v0.2.1 — push registration is now three calls, not one.**
  The Node reference bridge registered the FCM token with eufy in
  three places: `POST push /app/push/register_push_token`, plus
  `securityAppPost /v1/apppush/register_push_token` and
  `/v1/app/review/app_push_check` on `security-app.eufylife.com`.
  This integration (via the library) did only the first — and in
  live HA on 2026-11-05 the sensor stayed `unknown` after real
  thumbturn (257) and palm (530) unlocks, while the Node bridge
  received both live ~2s after the press. v0.2.1 adds the two
  security-app calls (`custom_components/eufy_who_unlocked/security_push.py`),
  made through the library's own security-realm session right
  after each token upload, with the same endpoints, bodies and
  signing as the Node SDK. A failed extra call is logged and
  degraded, never fatal. Whether this closes the gap is proven
  only by the next live unlock in HA — see below.
- Push registration impersonates the eufy Android app via Firebase
  (the same approach as eufy-mega-security, Ring, and Bosch camera
  integrations). Google or eufy could change it; if push stops, the
  sensor goes stale and the Matter + timer fallback is what remains.
- A poll can only re-seed locked/unlocked — it cannot recover WHO
  unlocked during a push gap. Missed unlocks stay missed.
- App unlocks can be remote, so pair an app unlock with the Matter
  trigger before treating it as an arrival.
- **Fixed in v0.2.4 — the mapper dropped every library-delivered push.**
  v0.2.3 proved pushes DO arrive: live HA 22:57:14, 2026-11-05,
  `eufy push delivered: event_type=261 device_sn=T85V0…`, then
  `eufy push dropped by mapper: no inner payload`. Root cause:
  the library freezes `SecurityEvent.raw` as a `MappingProxyType`,
  not a `dict`, and v0.2.3's `isinstance(raw, dict)` check rejected
  it outright. The fix accepts any `Mapping` for `raw` (and for
  decoded payloads), and falls back to the fields the library
  already lifted onto the event (`event_type`, `person_name`,
  `device_sn`, `event_time_ms`) when the inner payload is missing
  or undecodable. `nick_name` / `short_user_id` / `params` live
  only in the inner payload — if they cannot be recovered the
  unlock still maps (method/time/exterior, `who` None, never
  invented). Drops now also log the raw key names (names only).
- **v0.2.3 — diagnostic release (no push even with registration ok).**
  v0.2.2's registration succeeded in live HA (both security-app
  calls `ok` at 22:46:20, 2026-11-05) yet an app/Remote unlock at
  22:47:01 still left the sensor `unknown` — and there were NO
  other log lines, because `_on_push` only logged mapped unlocks
  at DEBUG and the library's MCS logs live under a different
  logger the user never saw. No concrete bug was found in the
  delivery path on code reading (decode, serial filter, and
  sensor wiring all match the real capture shape), so v0.2.3
  adds visibility instead of inventing a fix: every push the
  library delivers is logged at INFO (event type, redacted
  serial, time) and every mapper drop is logged with its reason;
  listening start/stop is logged at INFO; the library's
  `eufy_home_security.push.fcm` logger is raised to DEBUG so
  MCS connect/login and "push event:" lines appear in HA's log
  UI; and the sensor gains diagnostic attributes
  (`push_listening`, `push_events_seen/mapped/dropped`,
  `last_push_at`, `last_push_drop_reason`). Reading them
  distinguishes the remaining hypotheses: `push_events_seen=0`
  means the library never delivered a push (MCS socket down, or
  eufy never sent to this token); seen>0 + dropped>0 means a
  push arrived but was rejected (reason logged); seen>0 +
  mapped>0 means pushes work. Next live test: palm first
  (530, definitely produced pushes in the Node capture), then
  report the sensor's diagnostic attributes and the
  `eufy push delivered:` / `eufy push listening:` log lines.

## Can the API tell us which methods this lock supports?

No — checked 2026-11-05 from the actual sources. The eufy cloud
device list (`app/house/get_devs_list`) returns serial, device
type (203), name, firmware — and a model derived from the serial
prefix only (`CloudDevice.model_id` = `T85V0`). It has no
capabilities / unlock-methods / enrolled-credentials field, and
the T85V0 model file in the alarun library is camera-oriented
with no palm/fingerprint/passcode list. The Node SDK's lock
capability is lock/unlock + locked state + battery only.
Other integrations agree: eufy-mega-security's T85V0 catalogue
lists a static per-event origin enum (Automatic/Manual/App/
Password/Fingerprint/Temporary Password/Keypad — no palm) and
user-management *actions*, but no supported-methods query;
fuatakgun's lock entity only checks for a `LOCKED` property;
alaraun treats the T85V0 as a camera only. Enrolled credentials
(which users actually have a palm/PIN/fingerprint) are per-user
app data, with biometric templates stored locally on the lock —
no endpoint exposes them. So the enum options are hard-coded
from the observed event table, keyed by model so other locks
can extend the mapping. Full evidence in `NOTES.md` (v0.2.5).

## The Node bridge (`bridge/`)

Reference / test harness only — **not the shipping path**. It
proved the design live on 2026-11-05 (Mac Mini) and its
`unlock-map.mjs` is the mapping the Python shim mirrors. It is kept
for replay testing and as a fallback experiment, not to run in
production. See `bridge/README.md`.

## Tests

```sh
python3 -m pytest tests/ -v
node bridge/test-replay.mjs <capture-file.jsonl>   # replay only; file not committed
```

The pytest suite (69 tests) covers the verified event table, the
Python lock shim against synthetic pushes shaped like the real
capture, replay of the real capture through the Python mapper,
the 10-second correlation rule, the HA sensor/event/config-flow
behaviour via stubs (HA and the eufy library are not installed on
this VM), and a no-PII scan of the tree. It does not run Home
Assistant itself, and it performs no live login — this VM's egress
proxy blocks the FCM checkin, so no live test is possible here.

## Development

- Integration: Home Assistant 2026.10+, `eufy-home-security==0.4.12`
  (exact pin; transitive deps need no separate listing).
- Tests need `pytest`, `voluptuous`, `aiohttp` (e.g. in a venv:
  `python3 -m venv /tmp/eufy-venv && /tmp/eufy-venv/bin/pip install pytest voluptuous aiohttp`).
- Bridge (reference only): Node ≥ 24.5, `@mega-yfue/eufy-sdk@^0.5.0`.
- Commits are authored as Adam Cantrowitz <cantrowitz@gmail.com>.
