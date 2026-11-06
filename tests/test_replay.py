"""Replay the real Mac capture through the PYTHON mapper.

Feeds ~/workspace/user/files/t85v0-capture-2026-11-06T02-26-13-636Z.jsonl
through lock_push.UnlockTracker — the exact mapping the HA
integration runs live — and asserts the five verified unlocks.
Skipped when the capture file is not present: it is never committed
to this repo (it contains device/user identifiers).

Also covers, synthetically: user3's palm (chat-only, not in the
file), an auto-lock updating `locked` without overwriting the last
unlock, and duplicate suppression.
"""
import json
import os
from pathlib import Path

import pytest

_capture_env = os.environ.get("EUFY_CAPTURE_FILE", "").strip()
CAPTURE = Path(_capture_env) if _capture_env else (
    Path.home() / "workspace/user/files/t85v0-capture-2026-11-06T02-26-13-636Z.jsonl"
)

# The capture file is local-only and never committed: it carries the
# real nicknames eufy reports. Expected values use generic placeholders.
NAME_MAP = {"adam": "user1", "cantrowitz": "user1", "Emma": "user2", "Yvonne": "user3"}

EXPECTED = [
    {"at_epoch_ms": 1793931999000, "eufy_event_type": 261, "who": "user1",
     "short_user_id": "000A", "method": "app", "exterior": True, "locked": False},
    {"at_epoch_ms": 1793932038000, "eufy_event_type": 261, "who": "user1",
     "short_user_id": "000A", "method": "app", "exterior": True, "locked": False},
    {"at_epoch_ms": 1793932048000, "eufy_event_type": 257, "who": "user1",
     "short_user_id": "0000", "method": "manual", "exterior": False, "locked": False},
    {"at_epoch_ms": 1793932075000, "eufy_event_type": 530, "who": "user1",
     "short_user_id": "0000", "method": "palm", "exterior": True, "locked": False},
    {"at_epoch_ms": 1793932100000, "eufy_event_type": 259, "who": "user2",
     "short_user_id": "0002", "method": "passcode", "exterior": True, "locked": False},
]


def payload_of(line):
    """Extract the lock-event payload from one capture line, whatever its kind."""
    d = json.loads(line)
    kind = d.get("kind")
    if kind in ("push", "lockState", "event"):
        return d.get("payload")
    if kind == "pushRaw":
        return (d.get("raw") or {}).get("payload", {}).get("payload")
    return None  # "device" line etc.


def test_replay_real_capture_through_python_mapper(lock_push):
    """The real capture replays to exactly the five verified unlocks."""
    if not CAPTURE.exists():
        pytest.skip(f"capture file not present: {CAPTURE}")
    tracker = lock_push.UnlockTracker()
    unlocks = []
    for line in CAPTURE.read_text().splitlines():
        if not line.strip():
            continue
        mapped = tracker.apply(payload_of(line))
        if mapped is not None and mapped["kind"] == "unlock":
            unlocks.append(mapped)
    assert len(unlocks) == 5, [u["at_epoch_ms"] for u in unlocks]
    for got, exp in zip(unlocks, EXPECTED):
        got = dict(got, who=NAME_MAP.get(got["who"], got["who"]))
        for key, value in exp.items():
            assert got[key] == value, f"{key}: got {got[key]!r}, expected {value!r}"


def test_replay_synthetic_yvonne_palm_and_lock(lock_push):
    tracker = lock_push.UnlockTracker()
    yvonne = tracker.apply({
        "event_type": 530,
        "event_time": 1793932286000,
        "short_user_id": "0003",
        "nick_name": "user3 ",
        "params": [{"param_type": 6000, "param_value": "3"}],
    })
    assert yvonne is not None
    assert yvonne["who"] == "user3"
    assert yvonne["method"] == "palm"
    assert yvonne["exterior"] is True
    assert yvonne["locked"] is False

    lock_ev = tracker.apply({
        "event_type": 265,
        "event_time": 1793932300000,
        "short_user_id": "0000",
        "nick_name": "user1",
        "params": [{"param_type": 6000, "param_value": "4"}],
    })
    assert lock_ev is not None and lock_ev["kind"] == "lock"
    assert tracker.locked is True
    assert tracker.last_unlock["who"] == "user3"

    dup = tracker.apply({
        "event_type": 530,
        "event_time": 1793932286000,
        "short_user_id": "0003",
        "nick_name": "user3 ",
        "params": [{"param_type": 6000, "param_value": "3"}],
    })
    assert dup is None


def test_replay_capture_lines_are_all_lock_payloads_or_ignorable(lock_push):
    """Every push-kind line in the capture maps or is a known non-lock line."""
    if not CAPTURE.exists():
        pytest.skip(f"capture file not present: {CAPTURE}")
    kinds = set()
    for line in CAPTURE.read_text().splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        kinds.add(d.get("kind"))
        payload = payload_of(line)
        if payload is None:
            continue
        # Every payload-bearing line is a lock event the mapper knows.
        assert lock_push.map_lock_payload(payload) is not None, d.get("kind")
    assert kinds == {"device", "pushRaw", "push", "lockState", "event"}
