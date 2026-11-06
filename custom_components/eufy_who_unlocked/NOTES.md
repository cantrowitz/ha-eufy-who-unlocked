# NOTES — eufy Who Unlocked integration (v0.2.5)

## v0.2.5 — unlock method as an ENUM sensor, per-model options (2026-11-05)

New sensor `sensor.front_door_unlock_method`: state is the
unlock method, device class enum, options from the lock model.
The model is the serial's 5-character prefix ("T85V0…") — the
same value the startup poll's device object reports as
`CloudDevice.model_id` (alarun `cloud/models.py`: `model_id`
is `serial_prefix(device_sn)`, `model_name` "Lock 85V0" for a
catalogued model) — looked up in
`lock_push.MODEL_UNLOCK_METHODS`. T85V0 is the only listed
model, with the full observed set [manual, auto, passcode,
fingerprint, app, palm]; an unlisted model falls back to that
full set rather than a guessed narrower one. A method outside
the model's options never becomes state (unknown instead).
Who cannot be an enum: names are dynamic, from eufy per
enrolled user. The who sensor and its method attribute and the
event entity are unchanged.

### Can the API tell us which methods this lock supports? — NO

Checked 2026-11-05, from the actual sources, not memory:
- alarun 0.4.12: `async_get_devices` (`app/house/get_devs_list`)
  returns `CloudDevice` with device_sn, device_type (203),
  name, station_sn, channel, firmware versions, region — and
  `model_id`/`model_name` derived from the serial prefix only.
  No capabilities / unlock-methods / credential field. The
  T85V0 model file (`devices/data/models/T85V0.json`) is
  camera-oriented (detection/arming settings); its only
  lock-flavoured entries are static defaults like
  `smart_lock_is_support_temporary_pw: false` and
  `smart_lock_setting_manage_access: false` (both `read: null`,
  i.e. not a live per-device query) — no palm/fingerprint/
  passcode list anywhere (0 hits for palm/vein/fingerprint/
  passcode in the file).
- Node `@mega-yfue/eufy-sdk` 0.5.0: the lock capability is
  lock()/unlock(), locked state (param 6000), battery, RSSI,
  auto-lock — no unlock-method enumeration, no enrolled-
  credential listing. `classify.ts` excludes T85V0 from lock
  types entirely.
- mscodemonkey/eufy-mega-security: its T85V0 catalogue
  (`device_catalogue/devices/t85v0-familock-s3-s3-max.yaml`)
  lists battery, state, `lock_event_origin` (a static per-event
  origin enum: 0 None, 1 Automatic, 2 Manual, 3 App, 4 Password,
  5 Fingerprint, 6 Temporary Password, 7 Keypad — notably NO
  palm entry, even though palm 530 is live-verified) and
  user-management *actions* (`add_user`, `delete_user`,
  `update_user_passcode`) — but no endpoint or field that lists
  which methods the lock supports or which credentials are
  enrolled. Its T85V0 status provider queries only locked state
  + battery (opcode 34). Compatibility matrix: T85V0 "Discovery
  and main battery confirmed… lock controls remain unverified
  or unsupported."
- fuatakgun/eufy_security + bropat: the lock entity only
  checks for a `LOCKED` property and exposes lock/unlock
  (+ PIN for safe locks). No method/capability discovery.
- alaraun/homeassistant-eufy-home-security (the library this
  integration uses): treats T85V0 as a camera only — no lock
  entity at all, no method list.
- The enrolled-credential side (which users have a palm/PIN/
  fingerprint actually enrolled) is per-user data the eufy app
  manages; no library or integration above exposes an endpoint
  for it, and eufy keeps biometric templates in local storage
  on the lock. Hardware capability vs enrolled credentials are
  different questions, and neither is queryable from the API.
So the enum options stay hard-coded from the event table
(the observed set), keyed by model so other locks can extend
it. No fake API query was added.

## v0.2.4 — mapper dropped every library push (2026-11-05, live, FIXED)
v0.2.3 proved pushes arrive: live HA 22:57:14,
`eufy push delivered: event_type=261 device_sn=T85V0…`,
then `eufy push dropped by mapper: no inner payload`.
Root cause, confirmed against the library source
(`push/decode.py`, `events.py`): the library freezes
`SecurityEvent.raw` as a `MappingProxyType`, NOT a `dict`, and
`extract_lock_payload` in v0.2.3 did `isinstance(raw, dict)` —
False for every library-delivered push, so it returned
`(None, {})` before ever looking at the payload. The library
itself had decoded the push fine (`event_type=261`,
`person_name='adam'`, `device_sn`, `event_time_ms` — the outer
time, ~1.5–3s after the inner `event_time`).
Fix: `raw` and decoded payloads accept any `Mapping`;
the inner payload is decoded from `raw["payload"]` (base64 wire
form or already-decoded dict) as before, and fields the library
lifted onto the event are merged in as fallback where the inner
payload lacks them (inner wins: its `event_time` is the true
unlock time). `nick_name` / `short_user_id` / `params` live only
in the inner payload — if they cannot be recovered the unlock
still maps with method/time/exterior and `who` None, never
invented. Drops now log raw key names (names only, never values).
Verified against the real library `decode_push` on the base64
wire form, the dict-payload form (where the library itself lifts
nothing — our decode of `raw["payload"]` is what saves it),
and lifted-fields-only / no-name shapes. New regression tests
fail on v0.2.3 code and pass on v0.2.4. Next live test: palm
(530) first, then app (261), then PIN (259) — expect the sensor
to show the person's name within seconds, and `unlock: … via …`
lines in the log.

## What this is
Read-only Home Assistant integration for the eufy FamiLock S3 Max:
who last unlocked + unlock method, from eufy FCM push delivered
directly into Home Assistant. No bridge, no Mac Mini, no extra
process. Scope is deliberately narrow: who-unlocks / last unlock +
method ONLY. No camera, motion, battery, lock entity, or
lock/unlock services, and Matter stays the lock entity.

## v0.2.2 — token-timing bug (2026-11-05, live failure)
v0.2.1 was installed in real HA and still showed no activity
after thumbturn and palm unlocks. HA log, 10:34:46 PM:
"eufy push token uploaded but no FCM token is available;
skipping security-app push registration"
(`__init__.py:231`). Root cause, confirmed in the library
source (`PushListener._start_client`, 0.4.12): it calls
`_report_token_upload(None)` and only THEN assigns
`self._token = token` (and writes the cache). Our v0.2.1 hook
read `listener.token` inside the callback, always got `None`,
and skipped — so the two security-app calls never ran at all.
Fix: the registration is now triggered primarily from
`on_listening(True)` (fires after `_start_client` returns,
token set) and, as a fallback, from `on_token_upload` via a
task that WAITS for the token (poll 0.5s, up to 10s) instead of
giving up. Deduplicated per token; a listening drop resets the
dedupe so a supervisor restart re-registers. The token itself
is never logged. Also in v0.2.2: `DeviceInfo.serial_number`
is set from the configured lock serial, so Device info shows
the serial like the Matter device does.
Tests: the fake `PushListener` now models the real ordering
(callback fires while `token` is `None`, token assigned
afterwards); new tests prove the registration still runs
exactly once when the token appears, the listening trigger
registers once and dedupes, and a drop + re-upload
re-registers. Still NOT live-tested — see below.

## v0.2.1 — security-app push registration (2026-11-05, live failure)
v0.2.0 loaded in real HA but the sensor stayed `unknown` after
real thumbturn (257) and palm (530) unlocks — no pushes arrived
at all — while the Node bridge received both live. Root cause:
the library registers the FCM token only on the mega push
service; the Node SDK registers it in three places. New module
`security_push.py` adds the other two, read from the SDK source
(not invented): `securityAppPost("/v1/apppush/register_push_token",
{is_notification_enable: true, token, transaction: ms-epoch})`
and `securityAppPost("/v1/app/review/app_push_check",
{app_type: "eufySecurity", transaction: ms-epoch})`, both
signed+encrypted POSTs to the security-app host
(`security-app.eufylife.com` US / `security-app-eu.eufylife.com`
EU), made through the library's own security-realm plumbing
(`_with_session` + `_security_identity` + `_call`, the same
pattern `async_list_security_devices` uses on that host).
Hooked to `PushListener`'s `on_token_upload` callback in
`__init__.py`, so it runs after every successful mega token
upload (first start and supervisor restarts), as a background
task; a failed call is logged per endpoint and never fails
setup. Still unproven live until the next unlock in HA after
re-install — that is the live-test step, not a claim.

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
  ONLY (exact pin), `iot_class: "cloud_push"`, version 0.2.2.
  Transitive deps need no separate listing; do NOT add exact
  protobuf/firebase-messaging pins (HA 2026.10 constrains
  protobuf==7.36.0; exact pins broke ecoFlow/comfoconnect).
- `bridge/` is reference / test harness only now, NOT the shipping
  path. `tests/test_bridge_contract.py` was deleted with it.

## What was verified (2026-11-05/06, VM)
- `python3 -m py_compile` passes on all integration + test files;
  `manifest.json`, `strings.json`, `translations/en.json` parse.
- `pytest tests/ -v`: **58 passed** (venv with pytest +
  voluptuous + aiohttp; HA and the eufy library are not installed
  on this VM). Covers: verified event table (Python mapper +
  Node replay), lock shim vs synthetic pushes shaped like the
  real capture, replay of the real capture through the Python
  mapper (5 distinct unlocks: app adam x2, manual, palm,
  passcode Emma), 10s correlation both orders, sensor/event
  behaviour and config-flow login/challenge/reauth-error paths
  via stubs mirroring real HA 2026.10 / library 0.4.12 API names,
  `async_setup_entry` with a fake push feeding the coordinator,
  security-app registration (both calls, order, bodies, hosts;
  first-call failure does not stop the second; failure is
  degraded, never fatal; token-upload hook in setup), the v0.2.2
  timing fix (upload-before-token-assign still registers once
  the token appears; listening trigger registers once and
  dedupes; drop + re-upload re-registers), and a no-PII scan of
  the tree (`tests/test_no_pii.py`).
- `node --check` passes on bridge files; `node
  bridge/test-replay.mjs <capture>` prints ALL CHECKS PASSED.

## NOT verified — do not claim otherwise
- **Live HA run, partial (2026-11-05).** v0.2.0 DID load in real
  HA 2026.10: the sensor appeared (`unknown`) and the config
  entry was created — but no push arrived after real thumbturn
  and palm unlocks. v0.2.1 added the security-app calls, but its
  hook read the token before the library assigned it, so the
  calls never ran (HA log 10:34:46 PM: "no FCM token is
  available"). That is what v0.2.2 fixes (see above); the fix
  itself has NOT been live-tested yet. Re-install v0.2.2,
  restart HA, unlock (palm is the best test), and confirm the
  sensor updates. In particular, `EventEntity._trigger_event`,
  `ConfigEntry.async_create_background_task`,
  `ConfigFlow._get_reauth_entry` and `async_update_reload_and_abort`
  are used from the documented HA API but have not been exercised
  against real HA 2026.10.
- **No live FCM login/push from this VM.** The egress proxy
  blocks `android.clients.google.com/checkin`, so no live login
  or push test is possible here — do not attempt it.
- **#1 live-test risk — security-app registration (v0.2.2).**
  The two extra calls are read from the Node SDK source and
  tested against fakes, but whether they are what makes T85V0
  lock pushes flow in real HA is UNPROVEN until the next live
  unlock after re-install. If pushes still never arrive, the
  HA log lines to collect are the "security-app push registration"
  results and any push-listener errors — and the next suspects
  are (a) the security-realm identity the library mints for
  `/v1/...` vs the SDK's per-host ECDH key, and (b) whether the
  listener's `on_token_upload` fires before the MCS socket
  starts (in the library it fires inside `_start_client`,
  before `client.start()`; our extra calls run as a concurrent
  background task, so an unlock in the first ~second after
  start could still be missed).

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

## v0.2.3 — diagnostic release (2026-11-05, live failure, no concrete bug found)
v0.2.2 registration succeeded live (both security-app calls `ok`
22:46:20) yet a Remote/app unlock 22:47:01 left the sensor
`unknown`, with no other log lines. Code reading found no bug in
the delivery path: library decode (app_tab eufy_security, type
203 < 10100, inner payload event_type/device_sn/params), serial
filter, and sensor wiring all match the real capture shape.
Hypotheses left, in order: (1) MCS socket (mtalk.google.com:5228)
never up / login rejected silently on the HA host — registration
is HTTPS and proves nothing about 5228, and library MCS logs are
DEBUG under a different logger the user never saw; (2) eufy
never delivered to this HA token (token shadowing / last-token
wins); (3) library dropped the push before our callback
(app_tab / span dedupe / decode) — least likely given the
capture shape; (4) our mapper dropped it — least likely, replay
tests prove the mapper on the real capture. v0.2.3 therefore
adds INFO logging for every push delivery + drop reason,
listening start/stop, raises the library push logger to DEBUG,
and exposes push counters as sensor attributes. Next live test
is palm (530) first, and the sensor's push_events_seen +
log lines decide between the hypotheses.
