# Bridge — eufy who-unlocked push listener (REFERENCE ONLY)

> **Not the shipping path.** Since v0.2.0 the Home Assistant
> integration listens to eufy push directly (`PushListener` from
> `eufy_home_security`, in-process — see the repo README). This
> bridge is kept as the reference implementation and test harness:
> it proved the design live on 2026-11-05, and its `unlock-map.mjs`
> is the mapping the Python shim (`lock_push.py`) mirrors. Do not
> run it in production; do not point the v0.2.0 integration at it
> (it takes no bridge URL any more).

Long-lived listener for eufy FCM push, scoped ONLY to who unlocked /
last unlock + unlock method. Read-only: it listens, it never locks or
unlocks anything.

## Run it (Mac Mini)

The bridge must run where Google's FCM checkin is reachable. On the
author's VM the egress proxy blocks `android.clients.google.com/checkin`,
so FCM registration fails there — on a Mac Mini it works.

```sh
npm install
EUFY_EMAIL=… EUFY_PASSWORD=… EUFY_LOCK_SN=T85V0… \
  EUFY_COUNTRY=US PORT=8787 node bridge.mjs
```

- Login is interactive the first time (captcha / 2FA asked in the
  terminal); the session is then cached in `.eufy-session.json`, so
  re-runs skip login. Use a separate eufy account shared to the lock,
  not your primary account.
- Polls eufy ONCE at startup to seed the locked state, then push-only.
  No 60-second poll loop. `RECONCILE_MS=600000` enables the SDK's
  10-minute reconciliation poll; it is off by default.
- On push disconnect/reconnect it does ONE catch-up poll, then back
  to push-only. A poll can only re-seed locked/unlocked — it cannot
  recover WHO unlocked during a gap. Missed unlocks stay missed.

## HTTP API (LAN-only, no auth — do not expose beyond your home LAN)

The bridge listens on all interfaces (`BIND_HOST`, default `0.0.0.0`,
port `PORT`, default 8787) so Home Assistant, on a different host,
can reach it. Set `BIND_HOST=127.0.0.1` to restrict it to this machine.

- `GET /last-unlock` → latest unlock, or 404 if none seen yet:
  `{who, nick_name, short_user_id, method, method_label, event_type,
  exterior, at_iso, at_epoch_ms, locked}`
- `GET /health` → `{status, push_connected, last_unlock_at,
  last_push_at, locked, uptime_s, reconcile_ms}`

Latest unlock is also persisted to `last-unlock.json` across restarts.

## Verified event mapping

From a live capture on 2026-11-05 (all pushes arrived ~2s after the
unlock, param 6000 = `3` unlocked):

| event_type | method | who (nick_name) | short_user_id | exterior |
|---|---|---|---|---|
| 261 | app | user1 | 000A | true |
| 257 | manual / thumbturn | user1 (account holder — anonymous in practice) | 0000 | false |
| 530 | palm / vein | per enrolled user — user1 0000, user3 0003 | — | true |
| 259 | passcode / PIN | user2 | 0002 | true |

Event 530 is new: it is not in the SDK's `LockPushEvent` enum
(257–269), and the SDK's `lockState` decode drops it, so the bridge
derives `locked` from param 6000 instead. Lock events 262–269 update
the locked flag only and never overwrite the last unlock.

Exterior means someone proved who they are from outside. App unlocks
can also be remote, so automations should pair an app unlock with the
Matter lock-unlock trigger within 10 seconds.

An exterior unlock means that person ARRIVED. It does not prove they
are still home — leaving produces no unlock event, and there is no
presence entity here. Do not invent one.

## Replay test

```sh
node test-replay.mjs <capture-file.jsonl>
```

Replays a `.jsonl` capture through the exact mapping the bridge uses
live and asserts the five verified unlocks above, plus a synthetic
user3 palm and auto-lock. The capture file is NOT committed to this
repo. Exits 0 when all checks pass.
