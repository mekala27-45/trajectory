"""UUIDv7 generation with within-millisecond monotonicity.

Run identifiers are UUIDv7 (RFC 9562) rather than UUIDv4 because they sort by creation
time. That single property is worth the code below: run listings come back in
chronological order straight from the primary key index, and a directory of run files
sorts correctly in a shell without anyone parsing a timestamp out of a filename.

The standard library does not ship a UUIDv7 generator as of Python 3.12, and the
alternative is a native dependency for something this small.

RFC 9562 section 6.2 method 1 is used for monotonicity: the 12 bit `rand_a` field is a
counter that increments for every identifier minted inside the same millisecond, so a
batch of runs created in a tight loop still sorts correctly.
"""

from __future__ import annotations

import os
import threading
import time
from uuid import UUID

_VERSION_7 = 0x7
_VARIANT_RFC4122 = 0b10
_COUNTER_BITS = 12
_COUNTER_MAX = (1 << _COUNTER_BITS) - 1

_lock = threading.Lock()
_last_ms = -1
_counter = 0


def _next_state() -> tuple[int, int]:
    """Return the millisecond and counter to use for the next identifier.

    Returns:
        A tuple of Unix epoch milliseconds and a 12 bit counter that increases for
        every identifier minted within the same millisecond.
    """
    global _last_ms, _counter
    with _lock:
        now_ms = time.time_ns() // 1_000_000
        if now_ms > _last_ms:
            _last_ms = now_ms
            # Seed low so a burst inside one millisecond has room to count up.
            _counter = int.from_bytes(os.urandom(2), "big") & 0x0FF
        else:
            _counter += 1
            if _counter > _COUNTER_MAX:
                # Counter exhausted inside one millisecond. Wait for the clock.
                while now_ms <= _last_ms:
                    time.sleep(0.0002)
                    now_ms = time.time_ns() // 1_000_000
                _last_ms = now_ms
                _counter = 0
        return _last_ms, _counter


def uuid7() -> UUID:
    """Return a time ordered UUIDv7.

    Layout, most significant bit first:

    * 48 bits: Unix epoch milliseconds
    * 4 bits: version (7)
    * 12 bits: monotonic counter within the millisecond
    * 2 bits: variant (0b10)
    * 62 bits: random

    Returns:
        A UUID whose lexical order matches its creation order.
    """
    unix_ms, counter = _next_state()
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)

    value = (unix_ms & ((1 << 48) - 1)) << 80
    value |= _VERSION_7 << 76
    value |= counter << 64
    value |= _VARIANT_RFC4122 << 62
    value |= rand_b

    return UUID(int=value)


def uuid7_str() -> str:
    """Return a time ordered UUIDv7 formatted as a string."""
    return str(uuid7())


def timestamp_ms_of(value: UUID | str) -> int:
    """Extract the embedded millisecond timestamp from a UUIDv7.

    Args:
        value: A UUIDv7, as a UUID or its string form.

    Returns:
        Unix epoch milliseconds taken from the leading 48 bits.

    Raises:
        ValueError: If the UUID is not version 7.
    """
    uuid_value = UUID(value) if isinstance(value, str) else value
    if uuid_value.version != _VERSION_7:
        raise ValueError(f"not a UUIDv7: {uuid_value} has version {uuid_value.version}")
    return uuid_value.int >> 80
