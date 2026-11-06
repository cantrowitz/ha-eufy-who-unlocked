"""Constants for the eufy Who Unlocked integration."""
from homeassistant.const import Platform

from .lock_push import CORRELATION_WINDOW_S

DOMAIN = "eufy_who_unlocked"

# Config keys (stored in the config entry).
CONF_EMAIL = "email"
CONF_LOCK_SERIAL = "lock_serial"
CONF_COUNTRY = "country"
# Password is NEVER stored in the entry: it lives in the private
# SessionCache store (see runtime.py), like the cloud session and
# the FCM credentials.
CONF_PASSWORD = "password"
CONF_VERIFICATION_CODE = "verification_code"
CONF_CAPTCHA_ANSWER = "captcha_answer"

# Options keys.
CONF_RECONCILE = "reconcile"

# Default login country (ISO 3166 alpha-2). The library falls back
# to the host IP's country when this is empty.
DEFAULT_COUNTRY = "US"

# A Matter unlock matches the stored push only if the two timestamps
# are within this many seconds. Never assume arrival order — Matter
# and eufy push are independent paths, either can arrive first.
# (Value lives in lock_push.py, the single source for the mapping.)

# HA event type fired by the event entity on a new unlock.
#
# Deliberately NOT the bare "unlock": HA's own docs require custom
# events to be prefixed with the integration's domain
# ("Custom events are prefixed with the domain of your
# integration"), and the event bus is one global namespace — a
# bare "unlock" could collide with any other integration, core
# event, or automation listening for that word. "eufy_unlock" is
# the shortest safe namespaced form: the manufacturer prefix
# keeps it distinct, and it is what the device-page Events card
# displays ("Unlock — eufy_unlock"), so it is kept short on
# purpose. v0.2.6 used the full "eufy_who_unlocked_unlock";
# v0.2.7 shortens it (breaking change for automations
# triggering on the old string — see README/NOTES).
# The logbook.py renderer turns the same bus event into a
# readable sentence ("Unlocked by cantrowitz via palm") in the
# logbook / Activity list.
EVENT_TYPE_UNLOCK = "eufy_unlock"

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.EVENT,
]

# Device info for the single device all entities attach to.
DEVICE_NAME = "eufy FamiLock S3 Max (who-unlocked)"
DEVICE_MANUFACTURER = "eufy (FCM push)"
DEVICE_MODEL = "FamiLock S3 Max T85V0"

# Optional reconciliation: re-ask the cloud device list this often
# (seconds) to confirm the lock is still listed and the cloud is
# reachable. OFF by default — the integration is push-only, and a
# device-list poll can only re-seed locked/unlocked, never recover
# WHO unlocked during a push gap. Missed unlocks stay missed.
RECONCILE_INTERVAL_S = 600

# Event mapping, verified live 2026-11-05 — lives in lock_push.py,
# the single source shared by the shim and the tests. Do not
# re-implement it here.
#   261 app      'adam'        000A  exterior
#   257 manual   'cantrowitz'  0000  interior (thumbturn; anonymous)
#   530 palm     per enrolled user, e.g. 'cantrowitz' 0000 / 'Yvonne ' 0003
#   259 passcode 'Emma '       0002  exterior
# Lock events 262-269 update the locked flag only, never the last unlock.
