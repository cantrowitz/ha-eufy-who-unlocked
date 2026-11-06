# eufy Who Unlocked for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Matter tells Home Assistant that the eufy FamiLock S3 Max was unlocked —
but not **who** unlocked it. This project fills that gap, read-only:
**who last unlocked the front door, and by which method** — palm, PIN,
app, or manual thumbturn.

It is two pieces that work together:

```
eufy cloud (FCM push) ──► bridge on your Mac Mini ──► Home Assistant
Matter (local push)   ──► lock.eufy_familock_s3_max (unchanged)
```

## What it is

- `sensor.front_door_last_unlock` — state is the person's name as eufy
  reports it, with method, event type, exterior flag, time, and
  short user id as attributes.
- `event.front_door_unlock` — fires on each new unlock.
- A small Node bridge (`bridge/`) that listens to eufy FCM push and
  serves the latest unlock over local HTTP. It polls eufy **once at
  startup** to seed state, then is push-only — no 60-second poll loop.

## What it is NOT

- Not official, not affiliated with, endorsed by, or supported by
  Anker or eufy. It reverse-engineers the protocol the eufy app
  speaks, the same way the community eufy integrations do. eufy or
  Google could change it and break push delivery.
- Not a lock. There is **no lock entity and no lock/unlock control**
  here — Matter already does that locally, and the SDK's lock
  actuation is live-verified only on the T8531/T85D0, not the T85V0.
- Not presence. An exterior unlock means that person **arrived**. It
  does not prove they are still home — leaving produces no unlock
  event, and there is no presence entity here.
- Not a camera integration. No video, motion, or battery entities.
  Scope is deliberately who-unlocks / last unlock + method only.

## Verified live — 2026-11-05

All pushes arrived ~2 seconds after the unlock (param 6000 = `3`
unlocked). The Mac capture proved the whole design; a run on this
VM was invalid because its egress proxy blocks Google's FCM checkin.

| event_type | method | who | short_user_id | exterior |
|---|---|---|---|---|
| 261 | app | adam | 000A | yes |
| 257 | manual / thumbturn | cantrowitz (account holder — anonymous in practice) | 0000 | no |
| 530 | palm / vein | per enrolled user — cantrowitz 0000, Yvonne 0003 | — | yes |
| 259 | passcode / PIN | Emma | 0002 | yes |

Event **530 is new**: it is not in the SDK's `LockPushEvent` enum
(257–269). Palm names the person per enrolled user — Adam's own palm
reads as `cantrowitz` only because that is his name in the app.
Only the manual thumbturn is truly anonymous.

## Architecture — why two triggers

- **Matter is the fast trigger.** `lock.eufy_familock_s3_max` changes
  over local Matter push the moment the lock moves. Automations that
  need the unlock immediately should trigger on Matter and read this
  sensor, correlating the two within **10 seconds** — never assume
  arrival order, Matter and eufy push are independent paths.
- **eufy push is the who.** The bridge holds the latest unlock from
  FCM push in memory. Home Assistant polls the bridge's
  `/last-unlock` every **5 minutes** (only to pick up an unlock HA
  missed while down, and to surface a dead bridge as unavailable).
  The event entity fires on the next poll, so it is not real-time —
  use the Matter trigger for that.
- **If eufy push dies**, Matter still works and the existing
  10-second timer fallback in `automation.unlock_lights` still works.
  Nothing here is load-bearing for locking the door.

## Setup

### 1. Run the bridge (Mac Mini)

The bridge must run where Google's FCM checkin is reachable — on the
author's VM it is blocked by the egress proxy, on a Mac Mini it works
(`curl -I https://android.clients.google.com/checkin` should return a
real HTTP response, e.g. 400 for a bare HEAD).

```sh
cd bridge
npm install
EUFY_EMAIL=… EUFY_PASSWORD=… EUFY_LOCK_SN=T85V0… \
  EUFY_COUNTRY=US PORT=8787 node bridge.mjs
```

- Use a **separate eufy account** shared to the lock, not your primary
  account. First login may ask for a captcha / 2FA code in the
  terminal; the session is then cached in `.eufy-session.json`.
- The bridge listens on all interfaces (`BIND_HOST`, default `0.0.0.0`)
  with **no auth, LAN-only**. Home Assistant, on a different host,
  must be able to reach the Mac Mini over the LAN. Do not expose
  port 8787 beyond your home LAN.
- `GET /last-unlock` → latest unlock (404 if none yet).
  `GET /health` → `{status, push_connected, last_unlock_at, …}`.

### 2. Install the integration (HACS custom repo or manual)

- HACS → Custom repositories → `https://github.com/cantrowitz/ha-eufy-who-unlocked`,
  category Integration — or copy `custom_components/eufy_who_unlocked/`
  into your Home Assistant `custom_components/` and restart.
- Settings → Devices & Services → Add Integration → **eufy Who Unlocked**.
- Enter the bridge URL only — e.g. `http://mac-mini.local:8787`.
  There are **no eufy credentials in Home Assistant**; the bridge
  owns auth, so there is nothing to re-authenticate.

### 3. Use it

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

- Push registration impersonates the eufy Android app via Firebase
  (the same approach as eufy-mega-security, Ring, and Bosch camera
  integrations). Google or eufy could change it; if push stops, the
  sensor goes stale and the Matter + timer fallback is what remains.
- A poll can only re-seed locked/unlocked — it cannot recover WHO
  unlocked during a push gap. Missed unlocks stay missed.
- App unlocks can be remote, so pair an app unlock with the Matter
  trigger before treating it as an arrival.
- The integration has not yet had a live run in real Home Assistant
  2026.10 (see `custom_components/eufy_who_unlocked/NOTES.md`) —
  first install should be verified by hand: confirm
  `sensor.front_door_last_unlock` appears and updates on the next
  unlock.

## Tests

```sh
python3 -m pytest tests/ -v
node bridge/test-replay.mjs <capture-file.jsonl>   # replay only; file not committed
```

The pytest suite covers the verified event table, the 10-second
correlation rule, and the bridge HTTP contract (via a stub server).
It does not run Home Assistant itself.

## Development

- Bridge: Node ≥ 24.5, `@mega-yfue/eufy-sdk@^0.5.0`.
- Integration: Home Assistant 2026.10+, stdlib + HA-provided `aiohttp` only.
- Commits are authored as Adam Cantrowitz <cantrowitz@gmail.com>.
