# eufy Who Unlocked for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Matter tells Home Assistant that the eufy FamiLock S3 Max was unlocked —
but not **who** unlocked it. This project fills that gap, read-only:
**who last unlocked the front door, and by which method** — palm, PIN,
app, or manual thumbturn.

Home Assistant listens to eufy push notifications **directly** —
there is no bridge to run, no Mac Mini, no extra process:

```
eufy cloud (FCM push) ──► Home Assistant (PushListener, in this integration)
Matter (local push)   ──► lock.eufy_familock_s3_max (unchanged)
```

## What it is

- `sensor.eufy_familock_s3_max_who_unlocked_last_unlocked_by`
  ("Last unlocked by") — state is the person's name as eufy
  reports it, with method, eufy event code, exterior flag, time, and
  short user id as attributes.
- `sensor.eufy_familock_s3_max_who_unlocked_unlock_method`
  ("Unlock method") — state is HOW the door was last unlocked, as
  an enum: one of `manual`, `auto`, `passcode`, `fingerprint`,
  `app`, `palm`. The options come from the lock model (the serial's
  5-character prefix, e.g. `T85V0` — the same value the startup
  poll reports as `CloudDevice.model_id`), looked up in
  `lock_push.MODEL_UNLOCK_METHODS`; an unlisted model falls back to
  the full observed set. Who cannot be an enum: names are dynamic,
  from eufy per enrolled user.
- `event.eufy_familock_s3_max_who_unlocked_unlock` ("Unlock") —
  fires `eufy_unlock` on each new unlock, ~2 seconds after it
  happens (at push time, not on a poll). The logbook entry reads
  as a sentence, e.g. "Unlocked by cantrowitz via Palm / vein".
- Push-only after one startup device-list poll. An optional
  10-minute reconciliation poll exists in the options flow and is
  off by default.

## Tested on

This is the only hardware / environment the integration has been
verified live on. Anything else is untested — the code is written
to fall back gracefully, but do not assume it works on another
lock or HA version until someone tests it there.

- **Lock:** eufy FamiLock S3 Max, model **T85V0** (device type 203).
  The only lock ever tested, live and in replay.
- **Home Assistant:** **2026.10.4**, timezone America/New_York.
  Requires HA 2026.10+ (see Setup).
- **Live in HA, 2026-11-05:** palm (530), app (261), passcode/PIN
  (259), and manual thumbturn (257) — each arrived as an FCM push
  ~2 seconds after the unlock and updated the sensor / fired the
  event. See "Verified live" below for the full table.
- **Live via the Node reference bridge, 2026-11-05 (Mac Mini):**
  the same four methods, plus a second app unlock — the capture
  the Python mapper replays in tests.
- **NOT tested live on this lock:** fingerprint (260) and
  auto-unlock (258). They are in the SDK event enum and in the
  method enum sensor's options, but were never observed on a
  T85V0, and eufy's specs do not list a fingerprint sensor on
  the S3 Max. An option that never occurs is harmless; dropping
  one the mapper can produce would hide a real unlock.
- **Tests (no live HA, no live login):** 74 pytest tests on this
  VM, including replay of the real Mac capture through the Python
  mapper. This VM cannot do a live test — its egress proxy blocks
  the FCM checkin (`android.clients.google.com`), so live
  verification was done on Adam's HA host and Mac Mini only.

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
every start (three registration calls: the library's push
registration plus two security-app calls), and the listener
supervises its own socket (30s checks, doubling backoff). The
eufy session, password and FCM credentials live in a private
Home Assistant store (`Store(private=True, atomic_writes=True)`,
keyed by a sha256 prefix of the e-mail) — never in the config entry.

The library decodes pushes into `SecurityEvent`s but does **no
lock decoding** — no 257/259/261/530 handling, no `short_user_id`
or `params`. The integration's `lock_push.py` shim (the single
source for the event table, shared with the tests) reads the inner
payload back out of `event.raw["payload"]` and maps it. The inner
payload's `event_time` is used, not the event's `event_time_ms`
(~3s later). The numeric eufy code is exposed as `eufy_event_type`
(in attributes and event data); the HA event type is `eufy_unlock`.

## Verified live — 2026-11-05

All pushes arrived ~2 seconds after the unlock (param 6000 = `3`
unlocked). Proved by the Node reference bridge on a Mac Mini; the
Python mapper replays the same capture to the same five unlocks
(see Tests).

| eufy_event_type | method | who | short_user_id | exterior |
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
`sensor.eufy_familock_s3_max_who_unlocked_last_unlocked_by`
updates within a few seconds and
`event.eufy_familock_s3_max_who_unlocked_unlock` fires.

### 4. Use it

```yaml
# Trigger on an app unlock
trigger:
  - platform: event
    event_type: eufy_unlock
    event_data:
      method: app

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
          {{ state_attr('sensor.eufy_familock_s3_max_who_unlocked_last_unlocked_by', 'exterior') is true
             and (now() - state_attr('sensor.eufy_familock_s3_max_who_unlocked_last_unlocked_by', 'at_iso') | as_datetime).total_seconds() < 10 }}
    action:
      - service: light.turn_on
        target:
          entity_id: light.foyer_mirror_sconces
```

## Limitations

- Push registration impersonates the eufy Android app via Firebase
  (the same approach as eufy-mega-security, Ring, and Bosch camera
  integrations). Google or eufy could change it; if push stops, the
  sensor goes stale and the Matter + timer fallback is what remains.
- A poll can only re-seed locked/unlocked — it cannot recover WHO
  unlocked during a push gap. Missed unlocks stay missed.
- App unlocks can be remote, so pair an app unlock with the Matter
  trigger before treating it as an arrival.

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
can extend the mapping.

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

The pytest suite (74 tests) covers the verified event table, the
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
