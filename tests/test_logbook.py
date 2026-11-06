"""Tests for v0.2.7: short HA event type + logbook renderer.

Home Assistant and ``eufy_home_security`` are NOT installed on
this VM, so minimal stubs mirroring the real HA 2026.10 API
names are installed in ``sys.modules``, then the REAL
``logbook.py`` (and ``const.py`` it imports) are loaded by file
path under a fake ``eufy_who_unlocked`` package.

What is asserted:
- The HA event type is the short namespaced ``eufy_unlock`` —
  NOT the bare ``unlock`` (unsafe: HA docs require custom
  events to be prefixed with the integration's domain, and the
  event bus is one global namespace) and NOT the old long
  ``eufy_who_unlocked_unlock``.
- ``async_describe_events`` registers the renderer for that
  exact event type under this integration's domain.
- The renderer turns an unlock bus event into a readable
  sentence ("Unlocked by user1 via Palm / vein"), with
  graceful fallbacks when who/method are missing, and never
  raises on a malformed event.

These assertions FAIL against v0.2.6 code (old event-type
string, and no logbook.py at all).
"""
import importlib.util
import sys
import types
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
INTEGRATION_DIR = REPO / "custom_components" / "eufy_who_unlocked"


def _module(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


class _Platform(str, Enum):
    SENSOR = "sensor"
    EVENT = "event"


def _install_stubs():
    """Minimal HA stubs sufficient to import const.py + logbook.py."""
    _module("homeassistant", __path__=[])
    _module("homeassistant.const", Platform=_Platform, ATTR_ENTITY_ID="entity_id")
    _module(
        "homeassistant.components",
        __path__=[],
    )
    _module(
        "homeassistant.components.logbook",
        LOGBOOK_ENTRY_NAME="name",
        LOGBOOK_ENTRY_MESSAGE="message",
        LOGBOOK_ENTRY_ENTITY_ID="entity_id",
    )
    _module(
        "homeassistant.core",
        Event=SimpleNamespace,
        HomeAssistant=object,
        callback=lambda func: func,
    )


def _load(name):
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
def logbook_module():
    _install_stubs()
    _load("lock_push")
    const = _load("const")
    logbook = _load("logbook")
    return SimpleNamespace(const=const, logbook=logbook)


def _register(logbook_module):
    """Call async_describe_events with a fake registrar; return the callback."""
    registered = {}

    def fake_describe_event(domain, event_type, callback_fn):
        registered[(domain, event_type)] = callback_fn

    logbook_module.logbook.async_describe_events(None, fake_describe_event)
    return registered


def _event(data):
    return SimpleNamespace(data=data)


def test_event_type_is_short_namespaced_not_bare_or_old(logbook_module):
    assert logbook_module.const.EVENT_TYPE_UNLOCK == "eufy_unlock"
    assert logbook_module.const.EVENT_TYPE_UNLOCK != "unlock"
    assert logbook_module.const.EVENT_TYPE_UNLOCK != "eufy_who_unlocked_unlock"
    # Still namespaced (manufacturer prefix), not a bare word on
    # the global event bus.
    assert logbook_module.const.EVENT_TYPE_UNLOCK.startswith("eufy_")


def test_logbook_registers_renderer_for_short_event_type(logbook_module):
    registered = _register(logbook_module)
    assert ("eufy_who_unlocked", "eufy_unlock") in registered
    assert ("eufy_who_unlocked", "eufy_who_unlocked_unlock") not in registered
    assert ("eufy_who_unlocked", "unlock") not in registered


def test_logbook_renders_palm_unlock_as_sentence(logbook_module):
    registered = _register(logbook_module)
    describe = registered[("eufy_who_unlocked", "eufy_unlock")]
    entry = describe(
        _event(
            {
                "who": "user1",
                "method": "palm",
                "method_label": "Palm / vein",
                "eufy_event_type": 530,
                "entity_id": "event.eufy_familock_s3_max_who_unlocked_unlock",
            }
        )
    )
    assert entry["message"] == "Unlocked by user1 via Palm / vein"
    assert entry["name"] == "eufy Who Unlocked"
    assert entry["entity_id"] == "event.eufy_familock_s3_max_who_unlocked_unlock"
    assert "530" not in entry["message"]
    assert "eufy_unlock" not in entry["message"]


def test_logbook_fallbacks_and_never_raises(logbook_module):
    registered = _register(logbook_module)
    describe = registered[("eufy_who_unlocked", "eufy_unlock")]
    assert describe(_event({"who": "user2", "method": "passcode"}))["message"] == (
        "Unlocked by user2 via passcode"
    )
    assert describe(_event({"who": "user1"}))["message"] == (
        "Unlocked by user1"
    )
    assert describe(_event({"method_label": "App"}))["message"] == (
        "Unlocked via App"
    )
    assert describe(_event({}))["message"] == "Unlocked"
    # Malformed event data never raises.
    assert describe(SimpleNamespace(data=None))["message"] == "Unlocked"
    assert describe(SimpleNamespace(data="not-a-dict"))["message"] == "Unlocked"
