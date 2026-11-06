# NOTES — eufy Who Unlocked integration (v0.2.9)

## v0.2.9 — auth review fixes (Watts / Frenck patterns, 2026-11-06)

Preemptive audit (no live failure drove these): the happy-path
auth handling was already sound; the gaps were all in the
unhappy paths Frenck probes. No refresh token exists in this
integration — eufy "refresh" is a silent re-login with the
cached password, and the session lives in the private HA Store,
not in entry data, so Watts #1's stale-token-on-restart failure
cannot occur here.

- **A2** — `config.abort.reauth_successful` ("Re-authentication
  was successful.") added to strings.json + translations/en.json
  (exact Watts #4 repeat: a successful reauth showed an empty
  abort dialog without it).
- **A1** — config-entry unique ID (`email:serial`): set in
  `async_step_user` before login, duplicate aborts
  `already_configured`; reauth refuses a different account
  (`wrong_account`). Entries created before v0.2.9 have
  `unique_id=None`: setup backfills it, and reauth backfills
  instead of flagging a legitimate reauth as the wrong account.
- **A3** — `_attempt_login` no longer pre-saves the typed
  password. The library caches the password on a successful
  login itself, and on a refusal drops the cached password
  only when the attempted password equals it — so pre-saving
  a typo'd reauth password destroyed the working one. A
  failed reauth now leaves the old cached password intact.
- **A4** — `SessionReplacedError` is now explicit: setup raises
  `ConfigEntryAuthFailed` (reauth prompt, not infinite
  `ConfigEntryNotReady` retries against the library's latch),
  the flow shows a distinct `session_replaced` error, and
  reauth passes `take_over=True` so an explicit user re-sign-in
  can take the session back. A plain setup login never takes
  over (it would silently kick the phone app's session).
- **A5** — auth vs transient split in the three background
  paths (reconcile poll, push-start retry, `_on_listening`):
  `AuthenticationError`/`SessionReplacedError` surface reauth
  once (`entry.async_start_reauth`, guarded against repeats);
  only transients retry with backoff.
- **B1** — rate-limit errors in the flow are `rate_limited`,
  not `cannot_connect`. **B2** — a fresh challenge raised
  while answering one is stored and shown, not discarded as
  `invalid_code`. **B4** — a reauth challenge answer with no
  password goes back to the start instead of `invalid_auth`.
  **B5** — the captcha data-URI stays logged at WARNING on
  purpose: the log is the only channel a config flow has to
  show the user the image. **B3** — setup also catches
  non-library network/parse errors as `ConfigEntryNotReady`.
- Tests: 15 new `test_v029_*` tests (90 total); 14 fail
  against v0.2.8 code, the 15th is the guard proving the A3
  fix did not lose the success path (a successful reauth
  still caches the new password).

## v0.2.8 — event entity state is the NEW unlock, in the same update (2026-11-06)

## v0.2.8 — event entity state is the NEW unlock, in the same update (2026-11-06)

**The problem.** Live HA Nov 6, 00:00 EST, v0.2.7 installed:
the sensors showed the 12:00 AM app unlock (cantrowitz / app /
261) but the event entity's state was still the 11:39 PM unlock
and its `event_type` attribute still the OLD type — the Events
card / Activity log showed the previous unlock, 24 minutes stale.

**Root cause, verified against HA core source
(`/tmp/event_entity.py`):** `EventEntity._trigger_event()` only
sets internal fields (`__last_event_triggered` /
`__last_event_type` / `__last_event_attributes`) — it does NOT
write state. The write is `CoordinatorEntity.
_handle_coordinator_update()` (`async_write_ha_state()`).
v0.2.7's handler called `super()` FIRST, so the write snapshot
the previous unlock, and the trigger that followed stayed
invisible until the next update.

**The fix:** trigger FIRST, then call `super()`, so the single
write that follows carries the new event. No second write, no
double-fire. Test `test_v028_event_state_is_new_unlock_in_same_update`
models the ordering honestly (stub coordinator snapshots
state/attributes at the moment `super()` runs; stub event entity
mirrors real `_trigger_event` — internal fields only, no write)
and FAILS against v0.2.7 code.

## v0.2.7 — Events card shows a short event type; logbook reads as a sentence (2026-11-05)

**The problem.** Live HA 2026-11-05 23:40, v0.2.6 installed:
the Events card showed "Unlock — 58 seconds ago —
eufy_who_unlocked_unlock". The v0.2.6 rename had fixed the
bare 530, but the remaining string was still internal /
technical. That is HA's event-entity display: it always shows
the last HA event type that fired.

**The fix, two parts.**
1. The HA event type is shortened: `eufy_who_unlocked_unlock`
   -> `eufy_unlock` (const `EVENT_TYPE_UNLOCK`). The Events
   card should now read "Unlock — eufy_unlock".
2. A new `logbook.py` registers a renderer
   (`async_describe_events`, the HA-native mechanism used by
   core integrations such as doorbird, lutron_caseta and
   assist_pipeline) so the logbook / Activity-list entry for
   the same unlock reads as a sentence: "Unlocked by
   cantrowitz via Palm / vein", with fallbacks ("Unlocked by
   …", "Unlocked via …", "Unlocked") and a never-raise guard
   on malformed events.

**Why not the bare "unlock"?** Checked, not guessed: HA's
developer docs say "Custom events are prefixed with the
domain of your integration, like
`my_integration_button_pressed`", and the event bus is one
global namespace — a bare "unlock" could collide with any
other integration, a future core event, or an automation
listening for that word. "eufy_unlock" is the shortest safe
namespaced form (manufacturer prefix, still distinct). A
better HA-native alternative was also considered and used
alongside, not instead: the logbook renderer above — it
makes the Activity *list* readable, but it cannot change
what the Events *card* shows, because the card displays the
event-type string itself. Hence both changes.

**Breaking change:** automations triggering on
`event_type: eufy_who_unlocked_unlock` must switch to
`event_type: eufy_unlock`. Filtering on `event_data.method` /
`who` / `eufy_event_type` is unchanged. No lock events were
added. Everything else from v0.2.6 is unchanged.

**What the displays should now show.** Palm unlock: Events
card "Unlock — eufy_unlock"; Activity list "Unlocked by
cantrowitz via Palm / vein". App unlock: "Unlock —
eufy_unlock" and "Unlocked by cantrowitz via App".
Honest caveat: the logbook renderer is unit-tested here
(4 new tests) but NOT live-verified — this VM has no HA
install, so confirm the Activity list shows the sentence
after installing. The shortened Events-card string is the
part certain to change, since the card displays the
event-type string itself.

**Tests.** 74 passed (was 70; +4 new in
`tests/test_logbook.py`), verified to FAIL against v0.2.6
code and pass with the fix. The updated event-type
assertions in `tests/test_integration_stubs.py` pass with
the fix.

## v0.2.6 — readable Activity log: `eufy_event_type` + short names (2026-11-05)

## v0.2.6 — readable Activity log: `eufy_event_type` + short names (2026-11-05)

**The bug.** Live HA 2026-11-05 23:24: the device Activity log
showed "Front door unlock — 530" next to "Front door unlock
method — palm" and "Front door last unlock — cantrowitz". 530 is
eufy's numeric event code for palm, and it read as nonsense.
Root cause, confirmed against HA core source
(`components/event/entity.py`, dev branch): `EventEntity`
builds its state attributes as
`{"event_type": last_event_type} | last_event_attributes` —
HA's own `event_type` is the HA event-type string
(`eufy_who_unlocked_unlock`), and our `_trigger_event` data
dict (plus our `extra_state_attributes`) carried a key also
named `event_type` with the bare number, so the dict union
overwrote HA's value with 530. The device Activity / logbook
rendering surfaces that attribute, hence "530".

**The fix.** The numeric code is renamed to `eufy_event_type`
everywhere it is exposed: both sensors' attributes, the event
entity's attributes, and the data dict passed to
`_trigger_event` (and the drop-reason diagnostic string). The
mapped dict in `lock_push.py` now carries `eufy_event_type`
instead of `event_type`. The wire/library field is untouched:
the inner payload key and `SecurityEvent.event_type` are still
`event_type` — only the exposed shape was renamed. The HA
event type string `eufy_who_unlocked_unlock` (const
`EVENT_TYPE_UNLOCK`) is unchanged; automations trigger on it
as before.
**Breaking change:** anything filtering on
`event_data.event_type` or `state_attr(…, 'event_type')` must
switch to `eufy_event_type`. Filtering on `method` / `who` is
unchanged. Regression test
`test_v026_names_icons_and_no_numeric_event_type_exposed`
asserts no exposed attribute or event-data key is named
`event_type` with a numeric value; it fails on v0.2.5 code.

**What the Activity log should now show.** For a palm unlock:
"Last unlocked by — cantrowitz", "Unlock method — palm", and
"Unlock — eufy_who_unlocked_unlock" (the event row shows HA's
event-type string, as every event entity does; the method/who
are the two sensor rows above it, and the numeric code is in
the `eufy_event_type` attribute). For an app unlock: "Last
unlocked by — cantrowitz", "Unlock method — app", "Unlock —
eufy_who_unlocked_unlock". No lock events were added — Adam
said hold off on locks; lock pushes still only update the
`locked` attribute.

**Short names, no repeated "Front door".** With
`_attr_has_entity_name=True` the device name ("eufy FamiLock
S3 Max (who-unlocked)") already provides the context, so
entity names are subordinate to it (HA convention):
- Who sensor: "Last unlocked by", icon `mdi:account-outline`
  (person outline), translation key `last_unlocked_by`.
- Method sensor: "Unlock method", icon `mdi:lock-open-variant`
  (the padlock icon, moved off the who sensor), key
  `unlock_method`.
- Event entity: "Unlock", icon `mdi:door-open`, key `unlock`.
**Entity IDs — old -> new (new installs only):**
- `sensor.eufy_familock_s3_max_who_unlocked_front_door_last_unlock`
  -> `sensor.eufy_familock_s3_max_who_unlocked_last_unlocked_by`
- `sensor.eufy_familock_s3_max_who_unlocked_front_door_unlock_method`
  -> `sensor.eufy_familock_s3_max_who_unlocked_unlock_method`
- `event.eufy_familock_s3_max_who_unlocked_front_door_unlock`
  -> `event.eufy_familock_s3_max_who_unlocked_unlock`
Unique IDs are UNCHANGED (`{entry_id}_front_door_last_unlock`,
`{entry_id}_front_door_unlock_method`, `{entry_id}_front_door_unlock`),
so an EXISTING install keeps its old entity IDs — HA stores
entity IDs in the registry keyed by unique ID, and a rename of
the friendly name does not move them; nothing orphans and
automations/dashboards using the old IDs keep working. To get
the new form on an existing install, rename the entity ID in
the HA UI (entity → gear icon). New installs get the new IDs
automatically (suggested object ID derives from the new names).

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
