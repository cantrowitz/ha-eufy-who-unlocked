# NOTES — eufy Who Unlocked integration (v0.2.0)

## What this is
Read-only Home Assistant integration for the eufy FamiLock S3 Max:
who last unlocked + unlock method, from eufy FCM push delivered
directly into Home Assistant. No bridge, no Mac Mini, no extra
process. Scope is deliberately narrow: who-unlocks / last unlock +
method ONLY. No camera, motion, battery, lock entity, or
lock/unlock services, and Matter stays the lock entity.

## Architecture (v0.2.0 — HA-only, breaking change from v0.1.0)
- `runtime.py` — the single construction site: HA-Store-backed
  `SessionCache` (private store, keyed by sha256(email)[:16]) and
  `EufyCloudApi` from `eufy_home_security` 0.4.12. Password lives
  ONLY in the cache, never in the config entry.
- `__init__.py` — startup poll once (`async_login` +
  `async_get_devices(refresh=True)`) to confirm the lock serial is
  on the account, then push-only. `PushListener` starts in a
  background task with doubling-backoff retry; it supervises its
  own MCS socket afterwards. Push callback feeds the coordinator
  via `async_set_updated_data` (unlocks) or merges the `locked`
  flag (lock-only events). On reconnect, one catch-up device-list
  poll — it cannot recover WHO unlocked during a gap.
- `lock_push.py` — the ~100-line lock shim on the PushListener
  callback. The library does NO lock decoding (no 257/259/261/530,
  no short_user_id or params on SecurityEvent; T85V0 catalogued
  only as a camera), so the shim reads the inner payload out of
  `event.raw["payload"]` (base64/NUL-JSON on the wire, dict in
  captures), uses the INNER `event_time` (not `event_time_ms`,
  ~3s later), maps with the single event table, and dedupes by
  `event_type:event_time:short_user_id`. Stdlib-only, so the
  mapping and replay tests run without HA or the library.
- `config_flow.py` — email + password + lock serial + country
  (default US); login via `EufyCloudApi.async_login` with
  `LoginChallengeError` handling (verify-code step, captcha step —
  image via the HA log, since config flows cannot render images);
  serial confirmed in `async_get_devices()`; reauth flow via
  `async_reauthenticate`; options flow for the optional 10-minute
  reconciliation poll (off by default).
- `manifest.json` — `requirements: ["eufy-home-security==0.4.12"]`
  ONLY (exact pin), `iot_class: "cloud_push"`, version 0.2.0.
  Transitive deps need no separate listing; do NOT add exact
  protobuf/firebase-messaging pins (HA 2026.10 constrains
  protobuf==7.36.0; exact pins broke ecoFlow/comfoconnect).
- `bridge/` is reference / test harness only now, NOT the shipping
  path. `tests/test_bridge_contract.py` was deleted with it.

## What was verified (2026-11-05/06, VM)
- `python3 -m py_compile` passes on all integration + test files;
  `manifest.json`, `strings.json`, `translations/en.json` parse.
- `pytest tests/ -v`: **48 passed** (venv with pytest +
  voluptuous + aiohttp; HA and the eufy library are not installed
  on this VM). Covers: verified event table (Python mapper +
  Node replay), lock shim vs synthetic pushes shaped like the
  real capture, replay of the real capture through the Python
  mapper (5 distinct unlocks: app adam x2, manual, palm,
  passcode Emma), 10s correlation both orders, sensor/event
  behaviour and config-flow login/challenge/reauth-error paths
  via stubs mirroring real HA 2026.10 / library 0.4.12 API names,
  `async_setup_entry` with a fake push feeding the coordinator,
  and a no-PII scan of the tree (`tests/test_no_pii.py`).
- `node --check` passes on bridge files; `node
  bridge/test-replay.mjs <capture>` prints ALL CHECKS PASSED.

## NOT verified — do not claim otherwise
- **No live Home Assistant run.** HA is not installed on this VM
  (`ModuleNotFoundError: No module named 'homeassistant'`), so
  the integration has never loaded in a real HA 2026.10. Before
  first use: copy `custom_components/eufy_who_unlocked/` into HA,
  restart, add the integration with eufy email + password +
  lock serial, and confirm `sensor.front_door_last_unlock`
  appears and updates on the next unlock. In particular,
  `EventEntity._trigger_event`, `ConfigEntry.async_create_background_task`,
  `ConfigFlow._get_reauth_entry` and `async_update_reload_and_abort`
  are used from the documented HA API but have not been exercised
  against real HA 2026.10.
- **No live FCM login/push from this VM.** The egress proxy
  blocks `android.clients.google.com/checkin`, so no live login
  or push test is possible here — do not attempt it.
- **#1 live-test risk — single-endpoint push registration.**
  The Node bridge registered the FCM token in three places; this
  integration (via the library) does only `POST push
  /app/push/register_push_token`. Alarun push is verified for
  cameras/HomeBase, but whether that one call suffices for T85V0
  lock pushes is UNPROVEN until a real HA + real lock test. If
  pushes never arrive, the next step is the signed
  `securityAppPost` calls (`/v1/apppush/register_push_token`,
  `/v1/app/review/app_push_check`) — do NOT invent the postSigned
  signing now.

## Open questions / decisions for Adam
1. **Manual unlocks are anonymous.** Event 257 reports the account
   holder's name, not who turned the thumbturn. The sensor
   docstring says this; do not build arrival automations on manual
   unlocks.
2. **App unlocks can be remote.** Pair an app unlock with the
   Matter lock-unlock trigger within 10s before treating it as an
   arrival.
3. **Captcha in the config flow.** HA config flows cannot render
   images, so the captcha image is logged (data URI) and the flow
   asks for the answer against the captcha id. Signing in once in
   the eufy app also clears the challenge.
4. **No scan-interval option.** The integration is push-only; the
   only tunable is the optional 10-minute reconciliation poll in
   the options flow.
5. **v0.1.0 bridge entries are not migrated.** v0.2.0 is a breaking
   config change (bridge URL → eufy account): delete the old entry
   and re-add. There is no migration path, by design — the old
   entry held no credentials worth carrying over.
