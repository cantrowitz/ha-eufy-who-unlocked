"""Tests for the Matter/push correlation rule and exterior classification.

The rule, agreed with the user: a Matter lock unlock matches the
stored eufy push only if |matter_time - push_time| <= 10 seconds.
Never assume arrival order — Matter and eufy push are independent
paths, either can arrive first. The mapping and the rule live in
``lock_push.py``; these tests assert them there, not a copy here.
"""


def test_correlates_within_10s_either_order(lock_push):
    assert lock_push.correlates(100_000, 102_000)  # push 2s after Matter
    assert lock_push.correlates(102_000, 100_000)  # push 2s before Matter
    assert lock_push.correlates(100_000, 110_000)  # exactly 10s
    assert lock_push.correlates(100_000, 90_000)


def test_does_not_correlate_outside_window(lock_push):
    assert not lock_push.correlates(100_000, 111_000)
    assert not lock_push.correlates(100_000, 89_000)
    assert not lock_push.correlates(100_000, 160_000)  # a minute later: a different unlock


def test_exterior_classification(lock_push):
    event_map = lock_push.EVENT_MAP
    assert event_map[530]["method"] == "palm" and event_map[530]["exterior"] is True
    assert event_map[259]["method"] == "passcode" and event_map[259]["exterior"] is True
    assert event_map[261]["method"] == "app" and event_map[261]["exterior"] is True
    assert event_map[260]["method"] == "fingerprint" and event_map[260]["exterior"] is True
    assert event_map[257]["method"] == "manual" and event_map[257]["exterior"] is False


def test_lock_events_are_not_exterior_unlocks(lock_push):
    # Locks never count as arrivals, whatever the method.
    for event_type in (262, 263, 264, 265, 266, 267, 268):
        assert lock_push.EVENT_MAP[event_type]["kind"] == "lock"


def test_verified_unlocks_correlate_with_matter(lock_push):
    # user2's PIN: push event_time 21:28:20 EST. A Matter unlock at
    # 21:28:22 EST (2s later) correlates; one at 21:29:00 does not.
    push_ms = 1793932100000
    assert lock_push.correlates(push_ms + 2_000, push_ms)
    assert not lock_push.correlates(push_ms + 40_000, push_ms)


def test_stale_push_must_not_match_a_later_unlock(lock_push):
    # Two app unlocks 39s apart (21:26:39 and 21:27:18): the first
    # push must not be matched to the second Matter unlock.
    assert not lock_push.correlates(1793932038000, 1793931999000)
