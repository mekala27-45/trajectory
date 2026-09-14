from uuid import UUID

import pytest
from trajectory_core.ids import timestamp_ms_of, uuid7, uuid7_str


def test_uuid7_has_version_and_variant():
    value = uuid7()
    assert value.version == 7
    assert (value.int >> 62) & 0b11 == 0b10


def test_uuid7_is_monotonic_within_a_millisecond():
    """A burst minted inside one millisecond still sorts by creation order.

    This is the whole reason for using v7 over v4, so it gets a test that actually
    creates a burst rather than two identifiers a sleep apart.
    """
    values = [uuid7_str() for _ in range(5000)]
    assert values == sorted(values)
    assert len(set(values)) == len(values)


def test_timestamp_round_trips():
    import time

    before = time.time_ns() // 1_000_000
    value = uuid7()
    after = time.time_ns() // 1_000_000
    assert before <= timestamp_ms_of(value) <= after


def test_timestamp_accepts_a_string():
    value = uuid7_str()
    assert timestamp_ms_of(value) == timestamp_ms_of(UUID(value))


def test_timestamp_rejects_a_v4_uuid():
    with pytest.raises(ValueError, match="not a UUIDv7"):
        timestamp_ms_of(UUID("f47ac10b-58cc-4372-a567-0e02b2c3d479"))
