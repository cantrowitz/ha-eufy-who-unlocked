"""Tests for the lock push shim (lock_push.py) against synthetic pushes.

The synthetic pushes are shaped like the real capture
(~/workspace/user/files/t85v0-capture-2026-11-06T02-26-13-636Z.jsonl):
an inner payload dict with event_type, event_time (ms epoch),
nick_name, short_user_id and params (param 6000 '3' unlocked /
'4' locked), wrapped the way the library delivers it — a
SecurityEvent-like object whose ``raw["payload"]`` is either the
already-decoded dict (capture "push" shape) or the base64
NUL-terminated JSON of the true FCM wire form.

No live network, no HA, no eufy library: the shim is stdlib-only.
"""
import base64
import json
from types import SimpleNamespace

import pytest

LOCK_SERIAL = "T85V0K0000000000"  # clearly fake serial, same shape as a real one
OTHER_SERIAL = "T85V0K9999999999"


def inner_payload(event_type, event_time, nick, short, param_6000="3", extra=None):
    """One inner lock-event payload, as the capture carries it."""
    payload = {
        "event_type": event_type,
        "event_time": event_time,
        "short_user_id": short,
        "nick_name": nick,
        "user_id": "0000000000000000000000000000000000000000",  # must NOT leak through
        "device_sn": LOCK_SERIAL,
        "params": [{"param_type": 6000, "param_value": param_6000}],
    }
    if extra:
        payload.update(extra)
    return payload


def fake_event(payload, *, device_sn=LOCK_SERIAL, wire_base64=False):
    """A SecurityEvent-like object: .device_sn, .event_time_ms, .raw.

    event_time_ms is deliberately ~3s AFTER the inner event_time —
    the library falls back to the outer push time — so a test that
    asserts at_epoch_ms == inner event_time proves the shim uses
    the inner time, not the event's.
    """
    inner = payload
    event_time_ms = inner.get("event_time", 0) + 3000
    if wire_base64:
        wire = json.dumps(inner).encode() + b"\x00" + b"trailing-junk"
        raw_payload = base64.b64encode(wire).decode()
    else:
        raw_payload = dict(inner)
    return SimpleNamespace(
        device_sn=device_sn,
        event_time_ms=event_time_ms,
        raw={"payload": raw_payload},
    )


# Verified live 2026-11-05: (event_type, event_time, nick, short, method, exterior)
VERIFIED = [
    (261, 1793931999000, "adam", "000A", "app", True),
    (261, 1793932038000, "adam", "000A", "app", True),
    (257, 1793932048000, "cantrowitz", "0000", "manual", False),
    (530, 1793932075000, "cantrowitz", "0000", "palm", True),
    (259, 1793932100000, "Emma ", "0002", "passcode", True),
]


@pytest.mark.parametrize(
    "event_type,event_time,nick,short,method,exterior", VERIFIED
)
def test_verified_unlocks_map(lock_push, event_type, event_time, nick, short, method, exterior):
    mapped = lock_push.map_lock_payload(
        inner_payload(event_type, event_time, nick, short)
    )
    assert mapped is not None
    assert mapped["event_type"] == event_type
    assert mapped["who"] == nick.strip()
    assert mapped["short_user_id"] == short
    assert mapped["method"] == method
    assert mapped["exterior"] is exterior
    assert mapped["kind"] == "unlock"
    assert mapped["at_epoch_ms"] == event_time
    assert mapped["locked"] is False
    # The full user_id is never copied through.
    assert "user_id" not in mapped
    assert "0000000000000000000000000000000000000000" not in json.dumps(mapped)


def test_yvonne_palm_names_the_person(lock_push):
    mapped = lock_push.map_lock_payload(
        inner_payload(530, 1793932286000, "Yvonne ", "0003")
    )
    assert mapped["who"] == "Yvonne"
    assert mapped["method"] == "palm"
    assert mapped["exterior"] is True
    assert mapped["short_user_id"] == "0003"


def test_fingerprint_and_temporary_passcode(lock_push):
    assert lock_push.map_lock_payload(
        inner_payload(260, 1793932300000, "adam", "000A")
    )["method"] == "fingerprint"
    assert lock_push.map_lock_payload(
        inner_payload(269, 1793932301000, "adam", "000A")
    )["method"] == "passcode"


def test_lock_events_update_locked_only(lock_push):
    tracker = lock_push.UnlockTracker()
    unlock = tracker.apply(inner_payload(259, 1793932100000, "Emma ", "0002"))
    assert unlock["kind"] == "unlock"
    for event_type in (262, 263, 264, 265, 266, 267, 268):
        mapped = tracker.apply(
            inner_payload(event_type, 1793932300000, "cantrowitz", "0000", param_6000="4")
        )
        assert mapped is not None
        assert mapped["kind"] == "lock"
        assert mapped["locked"] is True
    # The last unlock is still Emma's PIN, not a lock.
    assert tracker.last_unlock["who"] == "Emma"
    assert tracker.locked is True


def test_unknown_event_type_is_ignored(lock_push):
    assert lock_push.map_lock_payload(
        inner_payload(999, 1793932100000, "adam", "000A")
    ) is None
    assert lock_push.map_lock_payload(
        inner_payload(203, 1793932100000, "adam", "000A")  # a camera event_type
    ) is None


def test_missing_params_means_locked_unknown(lock_push):
    payload = inner_payload(261, 1793932100000, "adam", "000A")
    del payload["params"]
    assert lock_push.map_lock_payload(payload)["locked"] is None
    # A boolean `locked` field on the payload is the fallback.
    payload["locked"] = True
    assert lock_push.map_lock_payload(payload)["locked"] is True


def test_shim_uses_inner_event_time_not_event_time_ms(lock_push):
    tracker = lock_push.UnlockTracker()
    event = fake_event(inner_payload(259, 1793932100000, "Emma ", "0002"))
    assert event.event_time_ms == 1793932103000  # the trap: ~3s later
    mapped = lock_push.handle_push_event(event, lock_serial=LOCK_SERIAL, tracker=tracker)
    assert mapped is not None
    assert mapped["at_epoch_ms"] == 1793932100000
    assert mapped["at_iso"] == "2026-11-06T02:28:20.000Z"


def test_shim_ignores_other_devices_and_unattributable_events(lock_push):
    tracker = lock_push.UnlockTracker()
    other = fake_event(
        inner_payload(259, 1793932100000, "Emma ", "0002"),
        device_sn=OTHER_SERIAL,
    )
    assert lock_push.handle_push_event(other, lock_serial=LOCK_SERIAL, tracker=tracker) is None
    anonymous = fake_event(
        inner_payload(259, 1793932100000, "Emma ", "0002"), device_sn=None
    )
    anonymous.raw = {"payload": {"event_type": 259, "event_time": 1793932100000}}
    assert lock_push.handle_push_event(anonymous, lock_serial=LOCK_SERIAL, tracker=tracker) is None
    assert tracker.last_unlock is None


def test_shim_dedupes_repeated_pushes(lock_push):
    tracker = lock_push.UnlockTracker()
    payload = inner_payload(259, 1793932100000, "Emma ", "0002")
    first = lock_push.handle_push_event(fake_event(payload), lock_serial=LOCK_SERIAL, tracker=tracker)
    assert first is not None
    # Same occurrence again (pushRaw / push / lockState / event copies).
    for _ in range(3):
        assert lock_push.handle_push_event(fake_event(payload), lock_serial=LOCK_SERIAL, tracker=tracker) is None
    # A different unlock at a different time is NOT a duplicate.
    second = lock_push.handle_push_event(
        fake_event(inner_payload(261, 1793932200000, "adam", "000A")),
        lock_serial=LOCK_SERIAL, tracker=tracker,
    )
    assert second is not None and second["who"] == "adam"


def test_shim_decodes_base64_wire_form(lock_push):
    tracker = lock_push.UnlockTracker()
    event = fake_event(
        inner_payload(530, 1793932075000, "cantrowitz", "0000"), wire_base64=True
    )
    mapped = lock_push.handle_push_event(event, lock_serial=LOCK_SERIAL, tracker=tracker)
    assert mapped is not None
    assert mapped["method"] == "palm"
    assert mapped["at_epoch_ms"] == 1793932075000


def test_decode_push_payload_short_keys_and_garbage(lock_push):
    # Old-firmware short keys expand to the long names.
    decoded = lock_push.decode_push_payload({"t": 1793932100000, "f": "Emma "})
    assert decoded["event_time"] == 1793932100000
    assert decoded["person_name"] == "Emma "
    # Garbage is {} — never an exception.
    assert lock_push.decode_push_payload("!!!not-base64!!!") == {}
    assert lock_push.decode_push_payload(None) == {}
    assert lock_push.decode_push_payload(42) == {}


def test_correlation_window_both_orders(lock_push):
    assert lock_push.correlates(100_000, 102_000)
    assert lock_push.correlates(102_000, 100_000)
    assert lock_push.correlates(100_000, 110_000)
    assert not lock_push.correlates(100_000, 111_000)
    assert not lock_push.correlates(100_000, 160_000)
