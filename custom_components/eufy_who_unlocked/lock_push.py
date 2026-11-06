"""Lock-event mapping and push shim for eufy Who Unlocked.

This module is the ONE place the eufy lock-event mapping lives in
Python. It mirrors ``bridge/unlock-map.mjs`` (same event table,
same dedupe key, same ``locked`` derivation) so the Node reference
bridge and this integration can never drift apart silently.

It is stdlib-only on purpose: no Home Assistant and no
``eufy_home_security`` imports, so the mapping and the replay tests
run anywhere — including this VM, where neither HA nor the eufy
library is installed and no live login is possible.

What the library does NOT do (the gap this shim fills):
``eufy_home_security`` 0.4.12 decodes FCM pushes into
``SecurityEvent`` objects (``device_sn``, ``event_type``,
``person_name`` from ``person_name`` or ``nick_name``,
``event_time_ms``, full ``raw``), but it has no lock decoding: no
257/259/261/530 handling, no ``short_user_id`` or ``params`` on
``SecurityEvent``, and the T85V0 is catalogued only as a camera.
This module reads the inner payload back out of
``event.raw["payload"]`` (base64/NUL-JSON on the wire, already a
dict in captures) and maps it.

Two shapes, both real (v0.2.4 fix): the library's ``SecurityEvent.raw``
is a ``MappingProxyType``, NOT a ``dict`` — v0.2.3 rejected every
library-delivered push with "no inner payload" because
``isinstance(raw, dict)`` was False. And the library already lifts
``event_type`` / ``person_name`` / ``device_sn`` / ``event_time_ms``
onto the event itself, so when the inner payload is missing or
undecodable the shim now falls back to those lifted fields
(``nick_name`` / ``short_user_id`` / ``params`` live ONLY in the
inner payload; if they cannot be recovered the unlock still maps,
with method/time/exterior and ``who`` None — never invented).

Verified live 2026-11-05 (Mac capture):
  261 app      'adam'       000A  exterior
  257 manual   'cantrowitz' 0000  interior (thumbturn; anonymous)
  530 palm     per enrolled user — 'cantrowitz' 0000 / 'Yvonne ' 0003
  259 passcode 'Emma '      0002  exterior
Param 6000: '3' = unlocked, '4' = locked.
Event 530 is NEW: not in the SDK's LockPushEvent enum (257-269).

Security note: the full ``user_id`` is deliberately NEVER copied
through. It is in the raw payload; nothing downstream needs it.
"""
from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

# A Matter unlock matches the stored push only if the two timestamps
# are within this many seconds. Never assume arrival order — Matter
# and eufy push are independent paths, either can arrive first.
CORRELATION_WINDOW_S = 10

# event_type -> { method, label, exterior, kind }
# Unlocks name the person; locks update the locked flag only and
# never overwrite the last unlock.
EVENT_MAP: dict[int, dict[str, Any]] = {
    # Unlocks
    257: {"method": "manual", "label": "Manual / thumbturn", "exterior": False, "kind": "unlock"},
    258: {"method": "auto", "label": "Auto unlock", "exterior": True, "kind": "unlock"},
    259: {"method": "passcode", "label": "Passcode / PIN", "exterior": True, "kind": "unlock"},
    260: {"method": "fingerprint", "label": "Fingerprint", "exterior": True, "kind": "unlock"},
    261: {"method": "app", "label": "App", "exterior": True, "kind": "unlock"},
    269: {"method": "passcode", "label": "Temporary passcode", "exterior": True, "kind": "unlock"},
    # 530 is the palm/vein unlock. Not in the SDK enum, verified live.
    530: {"method": "palm", "label": "Palm / vein", "exterior": True, "kind": "unlock"},
    # Locks — update locked state only, never overwrite the last unlock.
    262: {"method": "manual_lock", "label": "Manual lock", "exterior": False, "kind": "lock"},
    263: {"method": "keypad_lock", "label": "Keypad lock", "exterior": False, "kind": "lock"},
    264: {"method": "app_lock", "label": "App lock", "exterior": False, "kind": "lock"},
    265: {"method": "auto_lock", "label": "Auto lock", "exterior": False, "kind": "lock"},
    266: {"method": "passcode_lock", "label": "Passcode lock", "exterior": False, "kind": "lock"},
    267: {"method": "fingerprint_lock", "label": "Fingerprint lock", "exterior": False, "kind": "lock"},
    268: {"method": "temporary_passcode_lock", "label": "Temporary passcode lock", "exterior": False, "kind": "lock"},
}

# Short key -> long name, parity with the library's decode.py: older
# firmware uses single-letter keys inside the inner payload, newer
# firmware the long names, and a single payload has been seen mixing
# both — so the short keys are mapped onto the long ones.
SHORT_KEYS: dict[str, str] = {
    "a": "msg_type",
    "s": "device_sn",
    "c": "channel",
    "n": "name",
    "p": "file_path",
    "t": "event_time",
    "m": "online",
    "e": "sensor_open",
    "k": "cipher",
    "f": "person_name",
    "i": "fetch_id",
    "j": "sense_id",
}


def _coerce_int(value: Any) -> int | None:
    """An int-ish value as an int, else None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return None


def _coerce_str(value: Any) -> str | None:
    """A string value, stripped, or None when absent/blank."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def decode_push_payload(value: Any) -> dict[str, Any]:
    """Decode the inner ``payload`` field to a dict; {} if it is not one.

    On the wire ``payload`` is base64 (either alphabet, padding
    optional) of NUL-terminated JSON, not encrypted — the same
    decoding the library's ``decode_payload`` performs. Captures and
    already-decoded copies carry a dict instead; those pass through
    with short keys expanded. Anything else yields {}.
    """
    if isinstance(value, Mapping):
        parsed = dict(value)
    elif isinstance(value, str) and value.strip():
        text = value.strip().replace("_", "/").replace("-", "+")
        try:
            raw = base64.b64decode(text + "=" * (-len(text) % 4), validate=True)
        except (binascii.Error, ValueError):
            return {}
        body = raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
        try:
            parsed = json.loads(body)
        except ValueError:
            return {}
        if not isinstance(parsed, dict):
            return {}
    else:
        return {}
    for short, long in SHORT_KEYS.items():
        if short in parsed and long not in parsed:
            parsed[long] = parsed[short]
    return parsed


def locked_from_payload(payload: dict[str, Any] | None) -> bool | None:
    """Derive locked/unlocked from the payload.

    Param 6000 is authoritative ('3' unlocked, '4' locked). Fall
    back to a boolean ``locked`` field on the payload (present on
    lockState for 257-269, absent for 530). Returns True/False, or
    None when neither is available.
    """
    if not isinstance(payload, Mapping):
        return None
    payload = dict(payload)
    params = payload.get("params")
    if isinstance(params, list):
        for param in params:
            if not isinstance(param, dict):
                continue
            if _coerce_int(param.get("param_type")) == 6000:
                value = str(param.get("param_value"))
                if value == "3":
                    return False
                if value == "4":
                    return True
    if isinstance(payload.get("locked"), bool):
        return payload["locked"]
    return None


def map_lock_payload(
    payload: dict[str, Any] | None,
    *,
    fallback_epoch_ms: int | None = None,
) -> dict[str, Any] | None:
    """Map one raw lock-event payload to a normalised unlock/lock dict.

    Returns None for event types we do not recognise (not a lock
    event at all) — the caller should ignore those, not log them as
    unlocks — and for payloads with no usable event time.

    The event time is the INNER ``event_time`` (ms epoch): the
    library's ``SecurityEvent.event_time_ms`` is ~3s later (it falls
    back to the outer push time), so only the inner time orders
    unlocks correctly. ``fallback_epoch_ms`` (the event's own time
    when the inner payload carries none) is used only when the inner
    payload has no ``event_time``.

    Returned shape (unlock):
      { who, nick_name, short_user_id, method, method_label,
        event_type, exterior, at_iso, at_epoch_ms, locked,
        kind: "unlock" }
    Returned shape (lock): same, but kind: "lock" and locked True.
    ``who`` is the trimmed nick_name as eufy reports it. For manual
    (257) that is the account holder's name — anonymous in practice.
    The full user_id is deliberately NOT copied through.
    """
    if not isinstance(payload, Mapping):
        return None
    payload = dict(payload)
    event_type = _coerce_int(payload.get("event_type") or payload.get("eventType"))
    if event_type is None:
        return None
    spec = EVENT_MAP.get(event_type)
    if spec is None:
        return None

    at_epoch_ms = _coerce_int(payload.get("event_time") or payload.get("eventTime"))
    if at_epoch_ms is None:
        at_epoch_ms = _coerce_int(fallback_epoch_ms)
    if at_epoch_ms is None:
        return None
    # A seconds-epoch (old/short-key payloads) converts to ms.
    if at_epoch_ms < 10_000_000_000:
        at_epoch_ms *= 1000

    nick = (
        _coerce_str(payload.get("nick_name"))
        or _coerce_str(payload.get("person_name"))
        or ""
    )
    short_id = _coerce_str(payload.get("short_user_id")) or ""
    locked = locked_from_payload(payload)

    return {
        "who": nick or None,
        "nick_name": nick or None,
        "short_user_id": short_id or None,
        "method": spec["method"],
        "method_label": spec["label"],
        "event_type": event_type,
        "exterior": spec["exterior"],
        "at_iso": datetime.fromtimestamp(at_epoch_ms / 1000, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "at_epoch_ms": at_epoch_ms,
        "locked": locked,
        "kind": spec["kind"],
    }


def correlates(
    matter_epoch_ms: int,
    push_epoch_ms: int,
    window_s: int = CORRELATION_WINDOW_S,
) -> bool:
    """Whether a Matter unlock and a push are the same physical unlock.

    True when the two timestamps are within ``window_s`` seconds of
    each other, either order — Matter and eufy push are independent
    paths, either can arrive first.
    """
    return abs(matter_epoch_ms - push_epoch_ms) <= window_s * 1000


def raw_key_names(event: Any) -> list[str]:
    """The key names of a push's raw message, names only — never values.

    Diagnostic use only: on a dropped push it reveals the push's
    shape (does it carry ``payload``? ``event_type``?) without
    leaking any content, token, or user id.
    """
    raw: Any = getattr(event, "raw", None)
    if raw is None and isinstance(event, Mapping):
        raw = event
    if not isinstance(raw, Mapping):
        return []
    try:
        return [str(k) for k in raw.keys()]
    except Exception:
        return []


def extract_lock_payload(event: Any) -> tuple[str | None, dict[str, Any]]:
    """Pull (device_sn, inner payload) out of a library ``SecurityEvent``.

    Accepts a ``SecurityEvent`` (``.device_sn``, ``.raw`` mapping
    whose ``payload`` is the base64 wire form), a raw FCM data-message
    dict, or an already-decoded inner payload dict. The device serial
    falls back to the inner payload's ``device_sn``/``station_sn``
    (for the T85V0 the two are the same). Returns ("", {})-style
    empties — (None, {}) — when nothing usable is there.

    ``raw`` is a ``Mapping``, not necessarily a ``dict``: the
    library freezes ``SecurityEvent.raw`` as a ``MappingProxyType``,
    and v0.2.3's ``isinstance(raw, dict)`` check rejected every
    library-delivered push on that alone.

    When the inner payload is missing or undecodable, fields the
    library already lifted onto the event itself are merged in as
    fallback (inner wins where both exist): ``event_type``,
    ``person_name`` -> ``nick_name``, ``device_sn``, and the outer
    ``event_time`` in ``raw``. ``nick_name`` / ``short_user_id`` /
    ``params`` live only in the inner payload — if they cannot be
    recovered the returned inner still maps (method/time/exterior
    from the lifted fields, ``who`` None), rather than dropping
    the unlock entirely.
    """
    raw: Any = getattr(event, "raw", None)
    if raw is None and isinstance(event, Mapping):
        raw = event
    if not isinstance(raw, Mapping):
        return None, {}

    device_sn = getattr(event, "device_sn", None)
    if not isinstance(device_sn, str) or not device_sn:
        device_sn = None

    inner: dict[str, Any] = {}
    if "payload" in raw:
        inner = decode_push_payload(raw.get("payload"))
    elif "event_type" in raw or "eventType" in raw:
        inner = decode_push_payload(raw)

    # Fallback: fields the library lifted onto the event itself.
    # Inner payload wins where both exist (its event_time is the
    # true unlock time; the event's event_time_ms is ~1.5-3s later).
    lifted_type = _coerce_int(getattr(event, "event_type", None))
    if lifted_type is not None and "event_type" not in inner:
        inner["event_type"] = lifted_type
    lifted_name = _coerce_str(getattr(event, "person_name", None))
    if lifted_name is not None and not _coerce_str(inner.get("nick_name")) and not _coerce_str(
        inner.get("person_name")
    ):
        inner["nick_name"] = lifted_name
    if "event_time" not in inner and "eventTime" not in inner:
        outer_time = _coerce_int(raw.get("event_time")) or _coerce_int(raw.get("eventTime"))
        if outer_time is not None:
            inner["event_time"] = outer_time
    if device_sn is None:
        device_sn = (
            _coerce_str(inner.get("device_sn"))
            or _coerce_str(inner.get("station_sn"))
            or _coerce_str(raw.get("device_sn"))
            or _coerce_str(raw.get("station_sn"))
        )
    if device_sn is not None and "device_sn" not in inner:
        inner["device_sn"] = device_sn
    return device_sn, inner


class UnlockTracker:
    """Deduplicating tracker: one physical unlock arrives several times.

    The same occurrence arrives as pushRaw, push, lockState and
    event — all with the same event_type, event_time and
    short_user_id. ``apply()`` returns the mapped object the first
    time it sees a key and None for repeats. Also holds the current
    state: last unlock (unlocks only) + locked flag.
    """

    def __init__(self, *, keep_keys: int = 200) -> None:
        # An ordered dict as an insertion-ordered set: a plain set
        # would not let "drop the oldest" mean anything.
        self._seen: dict[str, None] = {}
        self._keep_keys = keep_keys
        self._last_unlock: dict[str, Any] | None = None
        self._locked: bool | None = None

    def apply(
        self,
        payload: dict[str, Any] | None,
        *,
        fallback_epoch_ms: int | None = None,
    ) -> dict[str, Any] | None:
        """Feed one raw payload. Mapped object, or None if duplicate / not a lock event."""
        mapped = map_lock_payload(payload, fallback_epoch_ms=fallback_epoch_ms)
        if mapped is None:
            return None
        key = f"{mapped['event_type']}:{mapped['at_epoch_ms']}:{mapped['short_user_id'] or ''}"
        if key in self._seen:
            return None
        self._seen[key] = None
        while len(self._seen) > self._keep_keys:
            # Drop the oldest keys; a dict iterates in insertion order.
            del self._seen[next(iter(self._seen))]
        if mapped["locked"] is not None:
            self._locked = mapped["locked"]
        if mapped["kind"] == "unlock":
            self._last_unlock = mapped
        return mapped

    @property
    def last_unlock(self) -> dict[str, Any] | None:
        """The latest unlock seen (unlocks only)."""
        return self._last_unlock

    @property
    def locked(self) -> bool | None:
        """The latest known locked flag, or None before any event."""
        return self._locked

    @property
    def last_unlock_view(self) -> dict[str, Any] | None:
        """Latest unlock with the current locked flag merged in, or None."""
        if self._last_unlock is None:
            return None
        return {**self._last_unlock, "locked": self._locked}

    def set_seed(
        self,
        *,
        last_unlock: dict[str, Any] | None = None,
        locked: bool | None = None,
    ) -> None:
        """Seed state (e.g. from the startup poll); None arguments leave state alone."""
        if last_unlock is not None:
            self._last_unlock = last_unlock
        if locked is not None:
            self._locked = locked


def handle_push_event(
    event: Any,
    *,
    lock_serial: str,
    tracker: UnlockTracker,
) -> dict[str, Any] | None:
    """Map one library ``SecurityEvent`` to a normalised unlock/lock dict.

    Ignores events for any other device (``device_sn`` != the
    configured lock serial) and events that are not lock events.
    The inner payload's ``event_time`` is used, not the event's
    ``event_time_ms`` (~3s later); it is only the fallback when the
    inner payload carries no time. Dedupe runs through ``tracker``.
    Returns the mapped dict, or None when the event is ignored.
    """
    device_sn, inner = extract_lock_payload(event)
    if not inner:
        return None
    if device_sn is not None and lock_serial and device_sn != lock_serial:
        return None
    if device_sn is None:
        # Cannot attribute the event to the configured lock.
        return None
    fallback = getattr(event, "event_time_ms", None)
    if not isinstance(fallback, int):
        fallback = None
    return tracker.apply(inner, fallback_epoch_ms=fallback)
