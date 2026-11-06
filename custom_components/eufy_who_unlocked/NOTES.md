# NOTES — eufy Who Unlocked integration (v0.1.0)

## What this is
Read-only Home Assistant integration for the eufy FamiLock S3 Max
who-unlocked bridge (`~/workspace/eufy-who-unlocked/bridge.mjs`).
Scope is deliberately narrow: who last unlocked + unlock method.
No camera, motion, battery, lock entity, or lock/unlock services,
and no eufy credentials anywhere in HA — the config flow asks only
for the bridge URL.

## What was verified (2026-11-05, VM)
- `python3 -m py_compile` passes on all six `.py` files.
- `manifest.json`, `strings.json`, `translations/en.json` parse as JSON.
- Stub-import test (`/tmp/test_eufy_stubs.py`, tempur-conftest style,
  stubs mirror real HA 2026.10 API names only): all modules import;
  sensor returns `None` ("unknown") with no data and `"Emma"` with a
  real 259/passcode bridge payload, attributes complete; event entity
  seeds from existing coordinator data (no spurious fire on restart)
  and fires `eufy_who_unlocked_unlock` exactly once per new
  `at_epoch_ms`; config-flow URL validation/normalisation helpers behave.

## NOT verified — do not claim otherwise
- **No live Home Assistant run.** HA is not installed on this VM
  (`ModuleNotFoundError: No module named 'homeassistant'`), so the
  integration has never loaded in a real HA core. Before first use:
  copy `custom_components/eufy_who_unlocked/` into HA, restart, add
  the integration with the bridge URL, and confirm
  `sensor.front_door_last_unlock` appears and the event entity fires
  on the next unlock. In particular, `EventEntity._trigger_event`
  and `_attr_event_types` are used from the documented HA API but
  have not been exercised against real HA 2026.10.
- **No live bridge to test against.** The bridge runs on the Mac
  Mini, not this VM. The config flow's `/health` check and the
  coordinator's `/last-unlock` handling (including the 404 "no
  unlock yet" path) are untested against a real bridge.
- **No hacs.json / README / tests yet.** Only the integration
  folder exists. The coordinator will git-init the repo; hacs.json,
  README, and a pytest suite (mirroring tempur's `tests/`) are
  still to be added if wanted.

## Open questions / decisions for Adam
1. **Bridge reachability from HA — decided.** The bridge agent
   first bound `127.0.0.1` only, which HA (on a different host)
   could not reach. The coordinator fixed this before shipping:
   `bridge.mjs` now binds `0.0.0.0` by default (`BIND_HOST` env
   to override), LAN-only with no auth. Do not expose port 8787
   beyond the home LAN.
2. **Event latency.** The event entity fires on the next HA poll of
   the bridge (up to 5 min after the unlock), not at push time.
   Automations needing the unlock immediately should trigger on
   the Matter lock unlock (`lock.eufy_familock_s3_max`) and read
   this sensor, correlating within 10 s (`CORRELATION_WINDOW_S`).
   If true push-into-HA is wanted later, that needs a webhook or
   a bridge-initiated push into HA — out of scope for v0.1.0.
3. **No options flow.** Scan interval is a constant (300 s); there
   is no UI to change it. Add an options flow if Adam ever wants
   it tunable.
4. **Manual unlocks are anonymous.** Event 257 reports the account
   holder's name, not who turned the thumbturn. The sensor docstring
   says this; do not build arrival automations on manual unlocks.
5. **No reauth flow.** Deliberate: HA holds no eufy credentials,
   so there is nothing to re-authenticate. If the bridge URL
   changes, delete and re-add the entry (or edit it via the
   config-entry options if that is added later).
