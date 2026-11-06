"""Constants for the eufy Who Unlocked integration."""
from homeassistant.const import Platform

DOMAIN = "eufy_who_unlocked"

# Config keys
CONF_BRIDGE_URL = "bridge_url"

# Default bridge URL. The bridge runs on the user's Mac Mini (see
# bridge/bridge.mjs); edit the host if yours differs. The bridge
# listens on all interfaces (BIND_HOST, default 0.0.0.0) with no auth,
# LAN-only — that is how Home Assistant, on a different host, reaches
# it. Do not expose the bridge port beyond your home LAN.
DEFAULT_BRIDGE_URL = "http://mac-mini.local:8787"

# Poll interval for HA -> bridge, in seconds (5 minutes).
# This is deliberately slow, and it is NOT the event latency: the
# bridge receives unlocks via eufy FCM push in ~2s and holds the
# latest one in memory. HA polling the bridge only re-reads that
# stored latest unlock, so a slow poll costs nothing on the live path
# — it exists only to (a) pick up an unlock HA missed while it was
# down, and (b) surface a dead bridge as unavailable entities.
# Do NOT shorten this to 60s: it would just hammer a local HTTP
# endpoint for no new information. Automations that need the unlock
# the moment it happens should trigger on the Matter lock unlock
# (lock.eufy_familock_s3_max) and read this sensor, correlating the
# two within CORRELATION_WINDOW_S below.
DEFAULT_SCAN_INTERVAL = 300

# A Matter unlock matches the stored push only if the two timestamps
# are within this many seconds. Never assume arrival order — Matter
# and eufy push are independent paths, either can arrive first.
CORRELATION_WINDOW_S = 10

# HA event type fired by the event entity on a new unlock.
EVENT_TYPE_UNLOCK = "eufy_who_unlocked_unlock"

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.EVENT,
]

# Device info for the single device all entities attach to.
DEVICE_NAME = "eufy FamiLock S3 Max (who-unlocked bridge)"
DEVICE_MANUFACTURER = "eufy (via bridge)"
DEVICE_MODEL = "FamiLock S3 Max T85V0"

# Bridge HTTP endpoints (relative to the bridge URL).
ENDPOINT_LAST_UNLOCK = "/last-unlock"
ENDPOINT_HEALTH = "/health"

# Per-request timeout for bridge calls, in seconds.
BRIDGE_TIMEOUT_S = 10

# Event mapping, verified live 2026-11-05 (see bridge unlock-map.mjs).
# Kept here for reference only — the mapping lives in the bridge and
# HA just displays what the bridge reports. Do not re-implement it.
#   261 app      'adam'        000A  exterior
#   257 manual   'cantrowitz'  0000  interior (thumbturn; anonymous)
#   530 palm     per enrolled user, e.g. 'cantrowitz' 0000 / 'Yvonne ' 0003
#   259 passcode 'Emma '       0002  exterior
# Lock events 262-269 update the locked flag only, never the last unlock.
