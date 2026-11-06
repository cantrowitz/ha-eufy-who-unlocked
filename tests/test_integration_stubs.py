"""Stub-import tests for the HA integration modules.

Home Assistant and ``eufy_home_security`` are NOT installed on
this VM, so minimal stubs mirroring the real HA 2026.10 /
library 0.4.12 API names are installed in ``sys.modules``, then
the REAL integration modules are imported (by file path, under a
fake ``eufy_who_unlocked`` package) and exercised: sensor/event
behaviour, config-flow login + challenge branching, and
``async_setup_entry`` with a fake push feeding the coordinator.

Async tests run their coroutines with ``asyncio.run`` — no
pytest-asyncio plugin needed.
"""
import asyncio
import hashlib
import importlib.util
import json
import sys
import types
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
INTEGRATION_DIR = REPO / "custom_components" / "eufy_who_unlocked"
LOCK_SERIAL = "T85V0K0000000000"


# ---------------------------------------------------------------------------
# Stub packages
# ---------------------------------------------------------------------------

def _module(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


class _Platform(str, Enum):
    SENSOR = "sensor"
    EVENT = "event"


def _callback(func):
    return func


class _ConfigEntryAuthFailed(Exception):
    pass


class _ConfigEntryError(Exception):
    pass


class _ConfigEntryNotReady(Exception):
    pass


class _UpdateFailed(Exception):
    pass


class _DataUpdateCoordinator:
    """Minimal DataUpdateCoordinator: data + listeners + refresh."""

    def __init__(self, hass, logger, *, name, update_method=None, update_interval=None):
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_method = update_method
        self.update_interval = update_interval
        self.data = None
        self.last_update_success = True
        self._listeners = []

    def async_set_updated_data(self, value):
        self.data = value
        for listener in list(self._listeners):
            listener()

    async def async_refresh(self):
        try:
            self.data = await self.update_method()
            self.last_update_success = True
        except Exception:
            self.last_update_success = False
            raise

    async def async_config_entry_first_refresh(self):
        await self.async_refresh()

    def async_add_listener(self, fn):
        self._listeners.append(fn)
        return lambda: self._listeners.remove(fn)


class _CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        coordinator.async_add_listener(self._handle_coordinator_update)
        super().__init__()

    def _handle_coordinator_update(self):
        pass

    @property
    def available(self):
        return self.coordinator.last_update_success


class _DeviceInfo(dict):
    def __init__(self, **kwargs):
        super().__init__(kwargs)


class _Store:
    def __init__(self, hass, version, key, *, private=False, atomic_writes=False):
        self.hass = hass
        self.version = version
        self.key = key
        self.private = private
        self.atomic_writes = atomic_writes
        self._data = None

    async def async_load(self):
        return self._data

    async def async_save(self, data):
        self._data = data


class _TextSelectorType(str, Enum):
    EMAIL = "email"
    PASSWORD = "password"


class _TextSelectorConfig:
    def __init__(self, *, type=None):
        self.type = type


class _TextSelector:
    def __init__(self, config=None):
        self.config = config

    def __voluptuous_compile__(self, schema):
        # Real HA selectors validate/convert via this hook; the stub
        # just passes the value through.
        return lambda _path, value: value


class _CountrySelector:
    def __init__(self, config=None):
        self.config = config

    def __voluptuous_compile__(self, schema):
        return lambda _path, value: value


class _SensorEntity:
    pass


class _SensorDeviceClass(str, Enum):
    """Mirror of HA's SensorDeviceClass: only ENUM is used here."""

    ENUM = "enum"


class _EventEntity:
    def __init__(self, *args, **kwargs):
        self._triggered = []

    def _trigger_event(self, event_type, data):
        self._triggered.append((event_type, data))


class _ConfigEntry:
    def __init__(self, *, entry_id="test-entry", data=None, options=None):
        self.entry_id = entry_id
        self.data = data or {}
        self.options = options or {}
        self.runtime_data = None
        self._on_unload = []

    def async_on_unload(self, fn):
        self._on_unload.append(fn)

    def add_update_listener(self, fn):
        return lambda: None

    def async_create_background_task(self, hass, target, name, eager_start=False):
        return asyncio.create_task(target)


class _ConfigFlow:
    def __init_subclass__(cls, *, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._flow_domain = domain

    def __init__(self):
        self.hass = None
        self.context = {}
        self._reauth_entry = None

    def async_show_form(self, *, step_id, data_schema=None, errors=None,
                        description_placeholders=None):
        return {
            "type": "form",
            "step_id": step_id,
            "errors": errors or {},
            "data_schema": data_schema,
            "description_placeholders": description_placeholders or {},
        }

    def async_create_entry(self, *, title, data, options=None):
        return {"type": "create_entry", "title": title, "data": data}

    async def async_set_unique_id(self, unique_id):
        self._unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        pass

    def async_abort(self, *, reason):
        return {"type": "abort", "reason": reason}

    def async_update_reload_and_abort(self, entry, *, data=None, options=None):
        return {"type": "abort", "reason": "reauth_successful", "data": data}

    def _get_reauth_entry(self):
        return self._reauth_entry


class _OptionsFlow:
    def __init__(self):
        self.hass = None
        self.config_entry = None

    def async_show_form(self, *, step_id, data_schema=None, errors=None,
                        description_placeholders=None):
        return {
            "type": "form",
            "step_id": step_id,
            "errors": errors or {},
            "data_schema": data_schema,
        }

    def async_create_entry(self, *, title, data):
        return {"type": "create_entry", "title": title, "data": data}


# --- fake eufy library ----------------------------------------------------

class _LoginChallengeError(Exception):
    def __init__(self, kind, *, login_id="", captcha_id="", captcha_image="",
                 code=0, region="", code_requested=False):
        super().__init__(f"login requires {kind}")
        self.kind = kind
        self.login_id = login_id
        self.captcha_id = captcha_id
        self.captcha_image = captcha_image
        self.code = code
        self.region = region
        self.code_requested = code_requested


class _AuthenticationError(Exception):
    pass


class _EufySecurityError(Exception):
    pass


class _LoginVerificationError(_AuthenticationError):
    pass


class _FakeSessionCache:
    def __init__(self, store, account):
        self._store = store
        self.account = account.strip().lower()
        self._doc = {}
        self._password = None

    async def async_load(self):
        self._doc = await self._store.async_load() or {}
        self._password = self._doc.get("password")

    async def async_save(self):
        self._doc.setdefault("account", self.account)
        if self._password is not None:
            self._doc["password"] = self._password
        await self._store.async_save(self._doc)

    def set_password(self, password):
        self._password = password

    @property
    def password(self):
        return self._password

    def section(self, name):
        return self._doc.setdefault(name, {})


class _FakeCloudDevice:
    def __init__(self, device_sn, name="Front Door"):
        self.device_sn = device_sn
        self.name = name


class _FakeCloudApi:
    """Controllable fake: behaviour is set per test via .behavior."""

    instances = []
    behavior = {"devices": []}
    security_calls = []  # (host, path, body) — security-app registration

    def __init__(self, session, cache, email, password, *, country="",
                 timezone="", region=None, scan_regions=False, install=None):
        self.session = session
        self.cache = cache
        self.email = email
        self.password = password
        self.country = country
        self.region = "us"
        type(self).instances.append(self)

    def device_region(self, device_sn):
        return "us"

    # -- security-realm plumbing (mirrors the real library's private API) --
    async def _security_identity(self, base):
        return SimpleNamespace(
            auth_token="fake-sec-token", user_id="fake-user", region="us"
        )

    async def _call(self, host, path, payload, identity, *, category=False):
        type(self).security_calls.append((host, path, dict(payload)))
        if path in type(self).behavior.get("security_fail_paths", set()):
            raise _EufySecurityError(f"cloud refused {path}")
        return 0, {"code": 0}, {}

    async def _with_session(self, operation, region, *, login=True):
        base = SimpleNamespace(
            auth_token="fake-auth-token", user_id="fake-user", region=region
        )
        return await operation(base)

    async def async_login(self, *, verify_code=None, captcha_id=None,
                          captcha_answer=None, login_id=None, force=False):
        behavior = type(self).behavior
        if behavior.get("auth_error"):
            raise _AuthenticationError("bad credentials")
        challenge = behavior.get("challenge")
        if challenge and verify_code is None and captcha_id is None:
            kind, kwargs = challenge
            raise _LoginChallengeError(kind, **kwargs)
        if verify_code is not None and behavior.get("code_ok") is False:
            raise _LoginVerificationError("bad code")
        if captcha_answer is not None and behavior.get("captcha_ok") is False:
            raise _LoginChallengeError("captcha", captcha_id=captcha_id or "")
        self._logged_in = True

    async def async_reauthenticate(self, password, *, verify_code=None,
                                   captcha_id=None, captcha_answer=None,
                                   login_id=None, take_over=False):
        await self.async_login(verify_code=verify_code, captcha_id=captcha_id,
                               captcha_answer=captcha_answer, login_id=login_id)

    async def async_get_devices(self, *, refresh=False, rescan_regions=False):
        return list(type(self).behavior.get("devices", []))

    async def async_register_push_token(self, token):
        return None


class _FakePushListener:
    instances = []

    def __init__(self, cloud, cache, callback, *, session,
                 on_token_upload=None, on_listening=None):
        self.cloud = cloud
        self.cache = cache
        self.callback = callback
        self.session = session
        self.on_token_upload = on_token_upload
        self.on_listening = on_listening
        self.started = False
        self.token = "fake-fcm-token"
        type(self).instances.append(self)

    def emit_token_upload(self, error=None):
        """Simulate the library reporting a token upload outcome."""
        if self.on_token_upload is not None:
            self.on_token_upload(error)

    @property
    def running(self):
        return self.started

    async def async_start(self):
        self.started = True
        if self.on_listening is not None:
            self.on_listening(True, None)

    async def async_stop(self):
        self.started = False
        if self.on_listening is not None:
            self.on_listening(False, None)

    def emit(self, event):
        """Deliver a fake SecurityEvent to the integration callback."""
        self.callback(event)


class _FakeConfigEntries:
    def __init__(self):
        self.forwarded = []

    async def async_forward_entry_setups(self, entry, platforms):
        self.forwarded.append((entry, list(platforms)))

    async def async_unload_platforms(self, entry, platforms):
        return True

    async def async_reload(self, entry_id):
        return None


class _FakeHass:
    def __init__(self):
        self.config = SimpleNamespace(country="US", time_zone="America/New_York")
        self.data = {}
        self.config_entries = _FakeConfigEntries()
        self._session = object()

    def async_create_task(self, coro):
        return asyncio.create_task(coro)


def _install_stubs():
    """Install fake homeassistant + eufy_home_security modules."""
    _module("homeassistant", __path__=[])
    _module("homeassistant.const", Platform=_Platform)
    _module("homeassistant.core", HomeAssistant=_FakeHass, callback=_callback)
    _module(
        "homeassistant.config_entries",
        ConfigEntry=_ConfigEntry,
        ConfigFlow=_ConfigFlow,
        OptionsFlow=_OptionsFlow,
        ConfigFlowResult=dict,
    )
    _module(
        "homeassistant.exceptions",
        ConfigEntryAuthFailed=_ConfigEntryAuthFailed,
        ConfigEntryError=_ConfigEntryError,
        ConfigEntryNotReady=_ConfigEntryNotReady,
        HomeAssistantError=Exception,
    )
    _module("homeassistant.helpers", __path__=[])
    _module(
        "homeassistant.helpers.aiohttp_client",
        async_get_clientsession=lambda hass: hass._session,
    )
    _module("homeassistant.helpers.storage", Store=_Store)
    _module(
        "homeassistant.helpers.update_coordinator",
        DataUpdateCoordinator=_DataUpdateCoordinator,
        UpdateFailed=_UpdateFailed,
        CoordinatorEntity=_CoordinatorEntity,
    )
    _module("homeassistant.helpers.device_registry", DeviceInfo=_DeviceInfo)
    _module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    _module(
        "homeassistant.helpers.selector",
        TextSelector=_TextSelector,
        TextSelectorConfig=_TextSelectorConfig,
        TextSelectorType=_TextSelectorType,
        CountrySelector=_CountrySelector,
    )
    _module("homeassistant.components", __path__=[])
    _module(
        "homeassistant.components.sensor",
        SensorEntity=_SensorEntity, SensorDeviceClass=_SensorDeviceClass,
    )
    _module("homeassistant.components.event", EventEntity=_EventEntity)

    _module("eufy_home_security", __path__=[])
    _module("eufy_home_security.cloud", __path__=[])
    _module("eufy_home_security.cloud.api", EufyCloudApi=_FakeCloudApi)
    _module("eufy_home_security.storage", SessionCache=_FakeSessionCache)
    _module("eufy_home_security.push", __path__=[])
    _module("eufy_home_security.push.fcm", PushListener=_FakePushListener)
    _module(
        "eufy_home_security.exceptions",
        AuthenticationError=_AuthenticationError,
        EufySecurityError=_EufySecurityError,
        LoginChallengeError=_LoginChallengeError,
        LoginVerificationError=_LoginVerificationError,
    )


def _load_integration_module(name):
    """Load one real integration module by file path."""
    if "eufy_who_unlocked" not in sys.modules:
        package = types.ModuleType("eufy_who_unlocked")
        package.__path__ = [str(INTEGRATION_DIR)]
        sys.modules["eufy_who_unlocked"] = package
    spec = importlib.util.spec_from_file_location(
        f"eufy_who_unlocked.{name}", INTEGRATION_DIR / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def integration():
    """The real integration modules, loaded once with stubs installed."""
    _install_stubs()
    modules = {}
    for name in ("lock_push", "const", "runtime", "entity", "sensor", "event",
                 "config_flow"):
        modules[name] = _load_integration_module(name)
    # The package __init__ is loaded under the key "init": attribute
    # access on the namespace would otherwise collide with __init__.
    modules["init"] = _load_integration_module("__init__")
    # Reset fake-library behaviour between tests.
    _FakeCloudApi.instances.clear()
    _FakePushListener.instances.clear()
    return SimpleNamespace(**modules)


@pytest.fixture(autouse=True)
def _reset_fake_library():
    _FakeCloudApi.behavior = {"devices": [_FakeCloudDevice(LOCK_SERIAL)]}
    _FakeCloudApi.instances.clear()
    _FakeCloudApi.security_calls.clear()
    _FakePushListener.instances.clear()
    yield
    _FakeCloudApi.behavior = {"devices": [_FakeCloudDevice(LOCK_SERIAL)]}
    _FakeCloudApi.security_calls.clear()


def _mapped_unlock():
    return {
        "who": "Emma",
        "nick_name": "Emma",
        "short_user_id": "0002",
        "method": "passcode",
        "method_label": "Passcode / PIN",
        "eufy_event_type": 259,
        "exterior": True,
        "at_iso": "2026-11-06T02:28:20.000Z",
        "at_epoch_ms": 1793932100000,
        "locked": False,
        "kind": "unlock",
    }


def _fake_push_event(event_type, event_time, nick, short, param_6000="3"):
    return SimpleNamespace(
        device_sn=LOCK_SERIAL,
        event_time_ms=event_time + 3000,
        raw={"payload": {
            "event_type": event_type,
            "event_time": event_time,
            "short_user_id": short,
            "nick_name": nick,
            "device_sn": LOCK_SERIAL,
            "params": [{"param_type": 6000, "param_value": param_6000}],
        }},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_manifest_pins_library_and_push_class(integration):
    manifest = json.loads((INTEGRATION_DIR / "manifest.json").read_text())
    assert manifest["requirements"] == ["eufy-home-security==0.4.12"]
    assert manifest["iot_class"] == "cloud_push"
    assert manifest["version"] == "0.2.7"
    assert manifest["domain"] == "eufy_who_unlocked"


def test_runtime_store_key_is_hashed_private(integration):
    runtime = integration.runtime
    key = runtime.store_key("Adam@Example.com")
    assert key.startswith("eufy_who_unlocked.")
    assert "adam" not in key and "example" not in key
    assert key == "eufy_who_unlocked." + hashlib.sha256(
        b"adam@example.com").hexdigest()[:16]
    store = runtime.cache_store(_FakeHass(), "adam@example.com")
    assert store.private is True
    assert store.atomic_writes is True


def test_sensor_reports_who_and_attributes(integration):
    hass = _FakeHass()
    coordinator = _DataUpdateCoordinator(
        hass, __import__("logging").getLogger("test"),
        name="test", update_method=lambda: None,
    )
    entry = _ConfigEntry(entry_id="entry-1")
    sensor = integration.sensor.FrontDoorLastUnlockSensor(coordinator, entry)
    assert sensor.native_value is None  # unknown before the first unlock
    coordinator.async_set_updated_data(_mapped_unlock())
    assert sensor.native_value == "Emma"
    attrs = sensor.extra_state_attributes
    assert attrs["method"] == "passcode"
    assert attrs["eufy_event_type"] == 259
    assert "event_type" not in attrs
    assert attrs["exterior"] is True
    assert attrs["at_epoch_ms"] == 1793932100000
    assert attrs["short_user_id"] == "0002"
    assert attrs["locked"] is False


def test_v026_names_icons_and_no_numeric_event_type_exposed(integration):
    """v0.2.6: readable names/icons + the numeric eufy code renamed.

    - Who sensor: "Last unlocked by", mdi:account-outline.
    - Method sensor: "Unlock method", mdi:lock-open-variant
      (the padlock icon, moved off the who sensor).
    - Event entity: "Unlock", mdi:door-open.
    - "Front door" no longer repeats in any entity name: with
      _attr_has_entity_name=True the device name ("eufy FamiLock
      S3 Max (who-unlocked)") already provides that context.
    - Translation keys changed with the names (last_unlocked_by /
      unlock_method / unlock), so NEW installs get matching
      entity_ids; unique_ids are UNCHANGED, so existing entities
      keep their registry entry (and entity_id) — no orphans.
    - Regression for the 530-in-the-Activity-log bug: no exposed
      attribute or event-data key is named "event_type" with a
      bare number. HA's EventEntity sets its own "event_type"
      attribute to the HA event-type string
      ("eufy_unlock" since v0.2.7, "eufy_who_unlocked_unlock"
      before); our numeric code used to
      overwrite it with 530, which is what the device Activity
      log displayed. The code now lives only under
      "eufy_event_type".
    These assertions FAIL against v0.2.5 code (old names/icons
    and "event_type" in the exposed dicts).
    """
    hass = _FakeHass()
    coordinator = _DataUpdateCoordinator(
        hass, __import__("logging").getLogger("test"),
        name="test", update_method=lambda: None,
    )
    entry = _ConfigEntry(
        entry_id="entry-1", data={"lock_serial": LOCK_SERIAL},
    )
    who = integration.sensor.FrontDoorLastUnlockSensor(coordinator, entry)
    assert who._attr_name == "Last unlocked by"
    assert who._attr_translation_key == "last_unlocked_by"
    assert who._attr_icon == "mdi:account-outline"
    assert who._attr_unique_id == "entry-1_front_door_last_unlock"
    method = integration.sensor.FrontDoorUnlockMethodSensor(coordinator, entry)
    assert method._attr_name == "Unlock method"
    assert method._attr_translation_key == "unlock_method"
    assert method._attr_icon == "mdi:lock-open-variant"
    assert method._attr_unique_id == "entry-1_front_door_unlock_method"

    strings = json.loads((INTEGRATION_DIR / "strings.json").read_text())
    assert strings["entity"]["sensor"]["last_unlocked_by"]["name"] == "Last unlocked by"
    assert strings["entity"]["sensor"]["unlock_method"]["name"] == "Unlock method"
    assert strings["entity"]["event"]["unlock"]["name"] == "Unlock"
    assert "front_door_last_unlock" not in strings["entity"]["sensor"]
    assert "front_door_unlock_method" not in strings["entity"]["sensor"]
    assert "front_door_unlock" not in strings["entity"]["event"]

    palm = _mapped_unlock()
    palm.update(method="palm", method_label="Palm / vein",
                eufy_event_type=530, who="cantrowitz", short_user_id="0000")
    coordinator.async_set_updated_data(palm)
    for attrs in (who.extra_state_attributes, method.extra_state_attributes):
        assert attrs["eufy_event_type"] == 530
        assert "event_type" not in attrs
    # Create the event entity after the palm data is set, so it
    # seeds that epoch and does not re-fire it; a new epoch fires once.
    event = integration.event.FrontDoorUnlockEvent(coordinator, entry)
    assert event._attr_name == "Unlock"
    assert event._attr_translation_key == "unlock"
    assert event._attr_icon == "mdi:door-open"
    assert event._attr_unique_id == "entry-1_front_door_unlock"
    assert event._triggered == []
    second = dict(palm)
    second["at_epoch_ms"] = 1793932400000
    coordinator.async_set_updated_data(second)
    assert len(event._triggered) == 1
    ha_event_type, data = event._triggered[0]
    assert ha_event_type == "eufy_unlock"
    assert data["eufy_event_type"] == 530
    assert data["method"] == "palm"
    assert "event_type" not in data


def test_method_sensor_is_enum_with_model_options(integration):
    """v0.2.5: the unlock method is its own ENUM sensor.

    Options come from the lock model (serial prefix T85V0 ->
    MODEL_UNLOCK_METHODS), not a second hard-coded list; the
    state tracks coordinator data, is unknown before the first
    unlock, and never states a method outside its options.
    These assertions FAIL against v0.2.4 code (no method sensor
    class at all).
    """
    hass = _FakeHass()
    coordinator = _DataUpdateCoordinator(
        hass, __import__("logging").getLogger("test"),
        name="test", update_method=lambda: None,
    )
    entry = _ConfigEntry(
        entry_id="entry-1", data={"lock_serial": LOCK_SERIAL},
    )
    sensor = integration.sensor.FrontDoorUnlockMethodSensor(coordinator, entry)
    assert sensor._attr_device_class == "enum"
    assert sensor._attr_options == [
        "manual", "auto", "passcode", "fingerprint", "app", "palm",
    ]
    assert sensor.native_value is None  # unknown before the first unlock

    coordinator.async_set_updated_data(_mapped_unlock())  # passcode
    assert sensor.native_value == "passcode"
    assert sensor.extra_state_attributes["who"] == "Emma"
    assert sensor.extra_state_attributes["method_label"] == "Passcode / PIN"

    palm = _mapped_unlock()
    palm.update(method="palm", method_label="Palm / vein",
                eufy_event_type=530, who="cantrowitz", short_user_id="0000")
    coordinator.async_set_updated_data(palm)
    assert sensor.native_value == "palm"

    # A method outside the model's options never becomes state.
    bogus = _mapped_unlock()
    bogus["method"] = "retinal_scan"
    coordinator.async_set_updated_data(bogus)
    assert sensor.native_value is None


def test_method_sensor_options_come_from_model(integration):
    """An unlisted model falls back to the full observed set."""
    hass = _FakeHass()
    coordinator = _DataUpdateCoordinator(
        hass, __import__("logging").getLogger("test"),
        name="test", update_method=lambda: None,
    )
    other = _ConfigEntry(
        entry_id="entry-2", data={"lock_serial": "T8531K0000000000"},
    )
    sensor = integration.sensor.FrontDoorUnlockMethodSensor(coordinator, other)
    assert sensor._attr_options == [
        "manual", "auto", "passcode", "fingerprint", "app", "palm",
    ]
    # And a missing serial does the same, not crash.
    missing = _ConfigEntry(entry_id="entry-3", data={})
    assert integration.sensor.FrontDoorUnlockMethodSensor(
        coordinator, missing
    )._attr_options == sensor._attr_options


def test_sensor_platform_adds_both_sensors(integration):
    """async_setup_entry registers the who sensor AND the method sensor."""
    added = []

    def add_entities(entities):
        added.extend(entities)

    async def scenario():
        hass = _FakeHass()
        coordinator = _DataUpdateCoordinator(
            hass, __import__("logging").getLogger("test"),
            name="test", update_method=lambda: None,
        )
        entry = _ConfigEntry(
            entry_id="entry-1", data={"lock_serial": LOCK_SERIAL},
        )
        entry.runtime_data = SimpleNamespace(coordinator=coordinator)
        await integration.sensor.async_setup_entry(hass, entry, add_entities)

    _run(scenario())
    assert [type(e).__name__ for e in added] == [
        "FrontDoorLastUnlockSensor", "FrontDoorUnlockMethodSensor",
    ]
    assert added[1]._attr_unique_id == "entry-1_front_door_unlock_method"


def test_event_fires_once_per_new_epoch_and_seeds(integration):
    hass = _FakeHass()
    coordinator = _DataUpdateCoordinator(
        hass, __import__("logging").getLogger("test"),
        name="test", update_method=lambda: None,
    )
    coordinator.async_set_updated_data(_mapped_unlock())
    entry = _ConfigEntry(entry_id="entry-1")
    event = integration.event.FrontDoorUnlockEvent(coordinator, entry)
    assert event._triggered == []  # seeded: no re-fire on restart
    # Same epoch again: no fire.
    coordinator.async_set_updated_data(_mapped_unlock())
    assert event._triggered == []
    # A genuinely new unlock fires exactly once.
    second = _mapped_unlock()
    second["at_epoch_ms"] = 1793932200000
    second["who"] = "adam"
    second["method"] = "app"
    second["eufy_event_type"] = 261
    coordinator.async_set_updated_data(second)
    assert len(event._triggered) == 1
    event_type, data = event._triggered[0]
    assert event_type == "eufy_unlock"
    assert data["who"] == "adam"
    assert data["at_epoch_ms"] == 1793932200000


def _run(coro):
    return asyncio.run(coro)


def test_config_flow_user_success_creates_entry_without_password(integration):
    flow = integration.config_flow.EufyWhoUnlockedConfigFlow()
    flow.hass = _FakeHass()
    result = _run(flow.async_step_user({
        "email": "adam@example.com",
        "password": "test-password",
        "lock_serial": LOCK_SERIAL,
        "country": "US",
    }))
    assert result["type"] == "create_entry", result
    assert result["data"]["email"] == "adam@example.com"
    assert result["data"]["lock_serial"] == LOCK_SERIAL
    assert "password" not in result["data"]  # password lives in the cache only


def test_config_flow_verify_code_challenge_then_success(integration):
    _FakeCloudApi.behavior["challenge"] = (
        "verify_code", {"login_id": "L1", "code_requested": True}
    )
    flow = integration.config_flow.EufyWhoUnlockedConfigFlow()
    flow.hass = _FakeHass()
    first = _run(flow.async_step_user({
        "email": "adam@example.com",
        "password": "test-password",
        "lock_serial": LOCK_SERIAL,
        "country": "US",
    }))
    assert first["type"] == "form" and first["step_id"] == "verify_code", first
    result = _run(flow.async_step_verify_code({"verification_code": "123456"}))
    assert result["type"] == "create_entry", result
    assert result["data"]["lock_serial"] == LOCK_SERIAL


def test_config_flow_wrong_code_is_an_error(integration):
    _FakeCloudApi.behavior["challenge"] = (
        "verify_code", {"login_id": "L1", "code_requested": True}
    )
    _FakeCloudApi.behavior["code_ok"] = False
    flow = integration.config_flow.EufyWhoUnlockedConfigFlow()
    flow.hass = _FakeHass()
    _run(flow.async_step_user({
        "email": "adam@example.com",
        "password": "test-password",
        "lock_serial": LOCK_SERIAL,
        "country": "US",
    }))
    result = _run(flow.async_step_verify_code({"verification_code": "000000"}))
    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_code"}


def test_config_flow_captcha_challenge_then_success(integration):
    _FakeCloudApi.behavior["challenge"] = (
        "captcha",
        {"login_id": "L1", "captcha_id": "C1",
         "captcha_image": "data:image/png;base64,xxxx"},
    )
    flow = integration.config_flow.EufyWhoUnlockedConfigFlow()
    flow.hass = _FakeHass()
    first = _run(flow.async_step_user({
        "email": "adam@example.com",
        "password": "test-password",
        "lock_serial": LOCK_SERIAL,
        "country": "US",
    }))
    assert first["type"] == "form" and first["step_id"] == "captcha", first
    assert first["description_placeholders"]["captcha_id"] == "C1"
    result = _run(flow.async_step_captcha({"captcha_answer": "abcd"}))
    assert result["type"] == "create_entry", result


def test_config_flow_invalid_auth_and_lock_not_found(integration):
    flow = integration.config_flow.EufyWhoUnlockedConfigFlow()
    flow.hass = _FakeHass()
    _FakeCloudApi.behavior["auth_error"] = True
    result = _run(flow.async_step_user({
        "email": "adam@example.com",
        "password": "wrong",
        "lock_serial": LOCK_SERIAL,
        "country": "US",
    }))
    assert result["errors"] == {"base": "invalid_auth"}

    _FakeCloudApi.behavior = {"devices": []}
    flow2 = integration.config_flow.EufyWhoUnlockedConfigFlow()
    flow2.hass = _FakeHass()
    result = _run(flow2.async_step_user({
        "email": "adam@example.com",
        "password": "test-password",
        "lock_serial": LOCK_SERIAL,
        "country": "US",
    }))
    assert result["errors"] == {"base": "lock_not_found"}


def test_config_flow_invalid_email_is_an_error(integration):
    flow = integration.config_flow.EufyWhoUnlockedConfigFlow()
    flow.hass = _FakeHass()
    result = _run(flow.async_step_user({
        "email": "not-an-email",
        "password": "test-password",
        "lock_serial": LOCK_SERIAL,
        "country": "US",
    }))
    assert result["errors"] == {"base": "invalid_email"}


def test_setup_entry_push_feeds_coordinator(integration):
    async def scenario():
        hass = _FakeHass()
        entry = _ConfigEntry(
            entry_id="entry-1",
            data={"email": "adam@example.com", "lock_serial": LOCK_SERIAL,
                  "country": "US"},
        )
        assert await integration.init.async_setup_entry(hass, entry) is True
        assert entry.runtime_data is not None
        assert hass.config_entries.forwarded[0][1] == ["sensor", "event"]
        # The background push start runs: let it complete.
        await asyncio.sleep(0.05)
        assert len(_FakePushListener.instances) == 1
        listener = _FakePushListener.instances[0]
        assert listener.started is True
        coordinator = entry.runtime_data.coordinator
        assert coordinator.data is None  # no unlock yet
        assert coordinator.update_interval is None  # push-only by default

        listener.emit(_fake_push_event(259, 1793932100000, "Emma ", "0002"))
        assert coordinator.data["who"] == "Emma"
        assert coordinator.data["method"] == "passcode"
        assert coordinator.data["at_epoch_ms"] == 1793932100000

        # A lock-only push updates `locked` without touching the last unlock.
        listener.emit(_fake_push_event(265, 1793932300000, "cantrowitz", "0000", "4"))
        assert coordinator.data["who"] == "Emma"
        assert coordinator.data["locked"] is True

        # A push for another device is ignored.
        other = _fake_push_event(261, 1793932400000, "adam", "000A")
        other.device_sn = "T85V0K9999999999"
        other.raw["payload"]["device_sn"] = "T85V0K9999999999"
        listener.emit(other)
        assert coordinator.data["who"] == "Emma"

        # Unload stops the listener.
        assert await integration.init.async_unload_entry(hass, entry) is True
        assert listener.started is False

    _run(scenario())


def test_listening_triggers_security_registration_once_and_dedupes(integration):
    """The listening callback — not just token-upload — registers.

    Regression test for the live failure: with only the mega
    ``register_push_token`` the sensor stayed unknown after real
    thumbturn (257) and palm (530) unlocks. The Node bridge, which
    also calls the security-app endpoints, received both live.

    The listening callback is the primary trigger because the
    library fires ``on_token_upload`` BEFORE assigning
    ``listener.token`` (see the timing test below), while
    ``on_listening(True)`` fires after ``_start_client`` returns,
    token set. A repeat upload for the same token is deduplicated;
    a listening drop resets the dedupe, so a supervisor restart
    re-registers.
    """
    async def scenario():
        hass = _FakeHass()
        entry = _ConfigEntry(
            entry_id="entry-1",
            data={"email": "adam@example.com", "lock_serial": LOCK_SERIAL,
                  "country": "US"},
        )
        assert await integration.init.async_setup_entry(hass, entry) is True
        await asyncio.sleep(0.05)
        listener = _FakePushListener.instances[0]
        assert listener.started is True
        # Registration already ran via the listening callback at setup.
        assert [c[1] for c in _FakeCloudApi.security_calls] == [
            "/v1/apppush/register_push_token",
            "/v1/app/review/app_push_check",
        ]
        host, _, register_body = _FakeCloudApi.security_calls[0]
        assert host == "security-app.eufylife.com"
        assert register_body["token"] == "fake-fcm-token"
        assert register_body["is_notification_enable"] is True
        _, _, check_body = _FakeCloudApi.security_calls[1]
        assert check_body["app_type"] == "eufySecurity"

        # A repeat token upload for the same token does NOT re-register.
        listener.emit_token_upload()
        await asyncio.sleep(0.05)
        assert len(_FakeCloudApi.security_calls) == 2

        # A listening drop resets the dedupe; the next upload
        # (supervisor restart) re-registers, same as the Node SDK
        # registering on every start.
        listener.on_listening(False, None)
        listener.emit_token_upload()
        await asyncio.sleep(0.05)
        assert len(_FakeCloudApi.security_calls) == 4

        # And the following listening callback for the same token
        # does not register a third time.
        listener.on_listening(True, None)
        await asyncio.sleep(0.05)
        assert len(_FakeCloudApi.security_calls) == 4

    _run(scenario())


def test_token_upload_before_token_assigned_registers_once_token_appears(integration):
    """v0.2.1 timing bug: token-upload fired before ``token`` was set.

    The real library's ``PushListener._start_client`` calls
    ``_report_token_upload(None)`` and only THEN assigns
    ``self._token = token``. v0.2.1 read ``listener.token`` inside
    the callback, got ``None``, logged "no FCM token is
    available", and the security-app calls never ran — the exact
    WARNING in the user's live HA logs (10:34:46 PM). The fix
    waits for the token to appear instead of giving up.

    This test models the real ordering in the fake: the upload
    callback fires while ``token`` is ``None``, the token is
    assigned afterwards, and the registration must still run —
    exactly once.
    """
    async def scenario():
        hass = _FakeHass()
        entry = _ConfigEntry(
            entry_id="entry-1",
            data={"email": "adam@example.com", "lock_serial": LOCK_SERIAL,
                  "country": "US"},
        )
        assert await integration.init.async_setup_entry(hass, entry) is True
        await asyncio.sleep(0.05)
        listener = _FakePushListener.instances[0]
        assert listener.started is True
        # Registration already ran once at setup via listening; reset
        # the record and the dedupe so this scenario starts clean.
        _FakeCloudApi.security_calls.clear()
        listener.on_listening(False, None)

        # Model the real library ordering: callback fires, token
        # assigned only afterwards.
        listener.token = None
        listener.emit_token_upload()
        await asyncio.sleep(0.05)
        # Nothing yet — the wait task is polling, not giving up.
        assert _FakeCloudApi.security_calls == []
        listener.token = "fake-fcm-token"
        await asyncio.sleep(0.7)  # wait polls every 0.5s
        assert [c[1] for c in _FakeCloudApi.security_calls] == [
            "/v1/apppush/register_push_token",
            "/v1/app/review/app_push_check",
        ]
        assert _FakeCloudApi.security_calls[0][2]["token"] == "fake-fcm-token"
        # The wait task is done; no duplicate registration follows.
        await asyncio.sleep(0.6)
        assert len(_FakeCloudApi.security_calls) == 2

    _run(scenario())


def test_token_upload_never_available_logs_and_skips(integration):
    """If the token never appears, the wait gives up after 10s.

    The wait is shortened here by pre-setting the token to None
    and checking the early state only — the full 10s timeout is
    exercised by the wait loop's bound, not by sleeping 10s in
    the suite. This test asserts the registration is NOT attempted
    while the token is missing.
    """
    async def scenario():
        hass = _FakeHass()
        entry = _ConfigEntry(
            entry_id="entry-1",
            data={"email": "adam@example.com", "lock_serial": LOCK_SERIAL,
                  "country": "US"},
        )
        assert await integration.init.async_setup_entry(hass, entry) is True
        await asyncio.sleep(0.05)
        listener = _FakePushListener.instances[0]
        _FakeCloudApi.security_calls.clear()
        listener.on_listening(False, None)
        listener.token = None
        listener.emit_token_upload()
        await asyncio.sleep(0.05)
        assert _FakeCloudApi.security_calls == []
        # Restore the token so the pending wait task can finish
        # cleanly instead of running past the test.
        listener.token = "fake-fcm-token"
        await asyncio.sleep(0.7)
        assert len(_FakeCloudApi.security_calls) == 2

    _run(scenario())


def test_failed_security_registration_is_degraded_not_fatal(integration):
    """A refused security-app call must not break setup or pushes."""
    async def scenario():
        _FakeCloudApi.behavior["security_fail_paths"] = {
            "/v1/apppush/register_push_token",
            "/v1/app/review/app_push_check",
        }
        hass = _FakeHass()
        entry = _ConfigEntry(
            entry_id="entry-1",
            data={"email": "adam@example.com", "lock_serial": LOCK_SERIAL,
                  "country": "US"},
        )
        assert await integration.init.async_setup_entry(hass, entry) is True
        await asyncio.sleep(0.05)
        listener = _FakePushListener.instances[0]
        listener.emit_token_upload()
        await asyncio.sleep(0.05)
        # Both calls were attempted and failed; setup and the
        # listener are unaffected.
        assert len(_FakeCloudApi.security_calls) == 2
        assert listener.started is True
        coordinator = entry.runtime_data.coordinator
        listener.emit(_fake_push_event(530, 1793932075000, "cantrowitz", "0000"))
        assert coordinator.data["who"] == "cantrowitz"
        assert coordinator.data["method"] == "palm"

    _run(scenario())


def test_setup_entry_auth_failure_raises(integration):
    async def scenario():
        _FakeCloudApi.behavior["auth_error"] = True
        hass = _FakeHass()
        entry = _ConfigEntry(
            entry_id="entry-1",
            data={"email": "adam@example.com", "lock_serial": LOCK_SERIAL,
                  "country": "US"},
        )
        with pytest.raises(_ConfigEntryAuthFailed):
            await integration.init.async_setup_entry(hass, entry)

    _run(scenario())


def test_setup_entry_missing_lock_raises(integration):
    async def scenario():
        _FakeCloudApi.behavior = {"devices": []}
        hass = _FakeHass()
        entry = _ConfigEntry(
            entry_id="entry-1",
            data={"email": "adam@example.com", "lock_serial": LOCK_SERIAL,
                  "country": "US"},
        )
        with pytest.raises(_ConfigEntryError):
            await integration.init.async_setup_entry(hass, entry)

    _run(scenario())


def test_push_diagnostics_count_seen_mapped_dropped(integration):
    """v0.2.3 diagnostic: every callback is counted, drops are named.

    Regression for the live v0.2.2 failure: registration was ok,
    the sensor stayed unknown, and there were NO log lines at all
    — _on_push only logged mapped unlocks at DEBUG, so a dropped
    push was invisible. With the diagnostic counters, seen=0
    means the library never delivered a push (MCS down / eufy
    never sent to this token); seen>0 + dropped>0 names the
    mapper's reason. These counters must FAIL against v0.2.2
    code (no push_diag attribute, no drop logging).
    """
    async def scenario():
        hass = _FakeHass()
        entry = _ConfigEntry(
            entry_id="entry-1",
            data={"email": "adam@example.com", "lock_serial": LOCK_SERIAL,
                  "country": "US"},
        )
        assert await integration.init.async_setup_entry(hass, entry) is True
        await asyncio.sleep(0.05)
        listener = _FakePushListener.instances[0]
        coordinator = entry.runtime_data.coordinator
        diag = coordinator.push_diag
        assert diag["seen"] == 0 and diag["mapped"] == 0 and diag["dropped"] == 0
        assert diag["listening"] is True

        # A real palm push: seen + mapped, sensor shows it.
        listener.emit(_fake_push_event(530, 1793932075000, "cantrowitz", "0000"))
        assert diag["seen"] == 1 and diag["mapped"] == 1 and diag["dropped"] == 0
        assert coordinator.data["who"] == "cantrowitz"
        sensor = integration.sensor.FrontDoorLastUnlockSensor(coordinator, entry)
        assert sensor.extra_state_attributes["push_events_seen"] == 1
        assert sensor.extra_state_attributes["push_listening"] is True

        # A push for another device: seen, dropped, reason named.
        other = _fake_push_event(261, 1793932400000, "adam", "000A")
        other.device_sn = "T85V0K9999999999"
        other.raw["payload"]["device_sn"] = "T85V0K9999999999"
        listener.emit(other)
        assert diag["seen"] == 2 and diag["mapped"] == 1 and diag["dropped"] == 1
        assert "wrong device serial" in (diag["last_drop_reason"] or "")
        assert coordinator.data["who"] == "cantrowitz"  # unchanged

        # A push with no inner payload and no lifted fields at all:
        # dropped, reason named. (v0.2.4: the fallback now fills
        # device_sn into the inner dict, so the reason is the
        # unknown eufy_event_type, not "no inner payload".)
        listener.emit(SimpleNamespace(device_sn=LOCK_SERIAL, event_time_ms=None, raw={}))
        assert diag["seen"] == 3 and diag["dropped"] == 2
        assert diag["last_drop_reason"] == "unknown eufy_event_type=None or no event_time"

    _run(scenario())
