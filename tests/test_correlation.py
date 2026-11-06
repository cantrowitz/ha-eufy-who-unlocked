"""Tests for the Matter/push correlation rule and exterior classification.

The rule, agreed with the user: a Matter lock unlock matches the
stored eufy push only if |matter_time - push_time| <= 10 seconds.
Never assume arrival order — Matter and eufy push are independent
paths, either can arrive first. The eufy side is push; the HA side
polls the bridge, so automations that need the unlock immediately
trigger on Matter and read the sensor, correlating within this window.
"""
CORRELATION_WINDOW_S = 10

# event_type -> (method, exterior). Mirrors bridge/unlock-map.mjs.
EVENT_MAP = {
    257: ("manual", False),
    258: ("auto", True),
    259: ("passcode", True),
    260: ("fingerprint", True),
    261: ("app", True),
    269: ("passcode", True),
    530: ("palm", True),
}


def correlates(matter_epoch_ms: int, push_epoch_ms: int, window_s: int = CORRELATION_WINDOW_S) -> bool:
    return abs(matter_epoch_ms - push_epoch_ms) <= window_s * 1000


def test_correlates_within_10s_either_order():
    assert correlates(100_000, 102_000)  # push 2s after Matter
    assert correlates(102_000, 100_000)  # push 2s before Matter
    assert correlates(100_000, 110_000)  # exactly 10s
    assert correlates(100_000, 90_000)


def test_does_not_correlate_outside_window():
    assert not correlates(100_000, 111_000)
    assert not correlates(100_000, 89_000)
    assert not correlates(100_000, 160_000)  # a minute later: a different unlock


def test_exterior_classification():
    assert EVENT_MAP[530] == ("palm", True)
    assert EVENT_MAP[259] == ("passcode", True)
    assert EVENT_MAP[261] == ("app", True)
    assert EVENT_MAP[260] == ("fingerprint", True)
    assert EVENT_MAP[257] == ("manual", False)


def test_verified_unlocks_correlate_with_matter():
    # Emma's PIN: push event_time 21:28:20 EST. A Matter unlock at
    # 21:28:22 EST (2s later) correlates; one at 21:29:00 does not.
    push_ms = 1793932100000
    assert correlates(push_ms + 2_000, push_ms)
    assert not correlates(push_ms + 40_000, push_ms)


def test_stale_push_must_not_match_a_later_unlock():
    # Two app unlocks 39s apart (21:26:39 and 21:27:18): the first
    # push must not be matched to the second Matter unlock.
    assert not correlates(1793932038000, 1793931999000)
