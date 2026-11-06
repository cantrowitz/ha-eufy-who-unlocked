"""Tests for the eufy who-unlocked event mapping.

The mapping lives in exactly one Python place —
``custom_components/eufy_who_unlocked/lock_push.py`` — and in
``bridge/unlock-map.mjs`` (the reference bridge, kept in sync).
These tests verify both sides against the verified event table
from the live Mac capture of 2026-11-05, and replay the real
capture file through the Node mapping (skipped when the capture
file is not present — it is never committed to this repo).
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BRIDGE_DIR = REPO / "bridge"
_capture_env = os.environ.get("EUFY_CAPTURE_FILE", "").strip()
CAPTURE = Path(_capture_env) if _capture_env else (
    Path.home() / "workspace/user/files/t85v0-capture-2026-11-06T02-26-13-636Z.jsonl"
)

# Verified live 2026-11-05. No user_id UUIDs, no serials here.
# at_epoch_ms is the eufy event_time; who is the trimmed nick_name.
EXPECTED_UNLOCKS = [
    {"at_epoch_ms": 1793931999000, "event_type": 261, "who": "adam",
     "short_user_id": "000A", "method": "app", "exterior": True},
    {"at_epoch_ms": 1793932038000, "event_type": 261, "who": "adam",
     "short_user_id": "000A", "method": "app", "exterior": True},
    {"at_epoch_ms": 1793932048000, "event_type": 257, "who": "cantrowitz",
     "short_user_id": "0000", "method": "manual", "exterior": False},
    {"at_epoch_ms": 1793932075000, "event_type": 530, "who": "cantrowitz",
     "short_user_id": "0000", "method": "palm", "exterior": True},
    {"at_epoch_ms": 1793932100000, "event_type": 259, "who": "Emma",
     "short_user_id": "0002", "method": "passcode", "exterior": True},
    # Yvonne's palm, 21:31:26 EST, reported in chat (not in the capture file).
    {"at_epoch_ms": 1793932286000, "event_type": 530, "who": "Yvonne",
     "short_user_id": "0003", "method": "palm", "exterior": True},
]


def test_replay_against_real_capture():
    """Run bridge/test-replay.mjs against the real capture file."""
    if not CAPTURE.exists():
        pytest.skip(f"capture file not present: {CAPTURE}")
    result = subprocess.run(
        ["node", "test-replay.mjs", str(CAPTURE)],
        cwd=BRIDGE_DIR, capture_output=True, text=True, timeout=60,
    )
    assert "ALL CHECKS PASSED" in result.stdout, result.stdout + result.stderr
    assert result.returncode == 0


def test_python_mapper_matches_verified_table(lock_push):
    """lock_push maps every verified unlock exactly (incl. trimming)."""
    for exp in EXPECTED_UNLOCKS:
        # The capture carries trailing spaces on some nicks ('Emma ').
        nick = exp["who"] + (" " if exp["who"] in ("Emma", "Yvonne") else "")
        mapped = lock_push.map_lock_payload({
            "event_type": exp["event_type"],
            "event_time": exp["at_epoch_ms"],
            "nick_name": nick,
            "short_user_id": exp["short_user_id"],
            "params": [{"param_type": 6000, "param_value": "3"}],
        })
        assert mapped is not None, exp
        assert mapped["who"] == exp["who"]
        assert mapped["method"] == exp["method"]
        assert mapped["exterior"] is exp["exterior"]
        assert mapped["at_epoch_ms"] == exp["at_epoch_ms"]
        assert mapped["locked"] is False


def test_event_table_methods_are_known():
    methods = {u["method"] for u in EXPECTED_UNLOCKS}
    assert methods == {"app", "manual", "palm", "passcode"}


def test_palm_names_the_person_per_enrolled_user():
    palms = [u for u in EXPECTED_UNLOCKS if u["method"] == "palm"]
    assert {u["who"] for u in palms} == {"cantrowitz", "Yvonne"}
    assert {u["short_user_id"] for u in palms} == {"0000", "0003"}


def test_manual_is_interior_and_anonymous():
    manual = [u for u in EXPECTED_UNLOCKS if u["method"] == "manual"]
    assert len(manual) == 1
    assert manual[0]["exterior"] is False
    # Manual carries the account holder's name, not the turner's identity.
    assert manual[0]["who"] == "cantrowitz"


def test_passcode_names_the_person():
    pins = [u for u in EXPECTED_UNLOCKS if u["method"] == "passcode"]
    assert pins[0]["who"] == "Emma"
    assert pins[0]["short_user_id"] == "0002"


def test_capture_file_unlocks_match_table():
    """If the capture file is present, its push payloads match the table."""
    if not CAPTURE.exists():
        pytest.skip(f"capture file not present: {CAPTURE}")
    seen = []
    for line in CAPTURE.read_text().splitlines():
        d = json.loads(line)
        if d.get("kind") != "push":
            continue
        p = d["payload"]
        seen.append({
            "at_epoch_ms": p["event_time"],
            "event_type": p["event_type"],
            "who": p["nick_name"].strip(),
            "short_user_id": p["short_user_id"],
        })
    # The capture file holds the first five unlocks; Yvonne's is chat-only.
    assert len(seen) == 5
    for got, exp in zip(seen, EXPECTED_UNLOCKS[:5]):
        assert got == {k: exp[k] for k in got}
