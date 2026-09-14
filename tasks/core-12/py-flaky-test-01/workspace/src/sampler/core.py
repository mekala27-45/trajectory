"""Sampling helpers for the request auditor.

The auditor keeps a small sample of production requests for a human to review, and it
retries its uploads with exponential backoff. Both are random on purpose. A sample that
always picked the same requests would keep missing whatever the last sample missed, and a
backoff without jitter puts every retrying client back on the wire in the same
millisecond.

Randomness that reaches production has to stay random. Randomness that reaches a test has
to be controlled by the test, which is not the same thing.
"""

from __future__ import annotations

import random
from collections.abc import Sequence


class Request:
    """One audited request.

    A plain class rather than a tuple, because the auditor grew two more fields last
    quarter and positional unpacking kept breaking.
    """

    __slots__ = ("request_id", "tenant", "latency_ms")

    def __init__(self, request_id: str, tenant: str, latency_ms: int) -> None:
        """Store the three fields the auditor reads."""
        self.request_id = request_id
        self.tenant = tenant
        self.latency_ms = latency_ms

    def __repr__(self) -> str:
        """Readable in a test failure, which is the only place this is printed."""
        return f"Request({self.request_id!r}, {self.tenant!r}, {self.latency_ms})"

    def __eq__(self, other: object) -> bool:
        """Value equality, so samples can be compared across calls."""
        if not isinstance(other, Request):
            return NotImplemented
        return (self.request_id, self.tenant, self.latency_ms) == (
            other.request_id,
            other.tenant,
            other.latency_ms,
        )

    def __hash__(self) -> int:
        """Hashable so a sample can go into a set."""
        return hash((self.request_id, self.tenant, self.latency_ms))


def _shuffled(items: Sequence[Request]) -> list[Request]:
    """Return a copy of items in a random order.

    Copies rather than shuffling in place. Callers hand us the auditor's live buffer and
    reordering it under them caused a duplicate upload once already.
    """
    pool = list(items)
    random.shuffle(pool)
    return pool


def sample_requests(items: Sequence[Request], k: int) -> list[Request]:
    """Return k requests drawn uniformly at random, without replacement.

    Args:
        items: Requests to draw from.
        k: How many to draw. A k larger than the input returns everything.

    Returns:
        The drawn requests. Every element of the result is an element of the input and no
        element appears twice.

    Raises:
        ValueError: If k is negative.
    """
    if k < 0:
        raise ValueError(f"k must not be negative, got {k}")
    return _shuffled(items)[:k]


def retry_delays(
    attempts: int,
    base_delay: float = 0.5,
    factor: float = 2.0,
    jitter: float = 0.25,
) -> list[float]:
    """Return the delay in seconds before each retry, with jitter applied.

    Delay n is `base_delay * factor ** n`, multiplied by a jitter factor drawn uniformly
    from `[1 - jitter, 1 + jitter]`.

    Args:
        attempts: How many retries to schedule.
        base_delay: Delay before the first retry, before jitter.
        factor: Growth per attempt.
        jitter: Fraction of the nominal delay the jitter may add or remove.

    Returns:
        One delay per attempt, in order.

    Raises:
        ValueError: If attempts is negative or jitter is outside [0, 1).
    """
    if attempts < 0:
        raise ValueError(f"attempts must not be negative, got {attempts}")
    if not 0.0 <= jitter < 1.0:
        raise ValueError(f"jitter must be in [0, 1), got {jitter}")

    delays: list[float] = []
    for attempt in range(attempts):
        nominal = base_delay * factor**attempt
        spread = random.uniform(1.0 - jitter, 1.0 + jitter)
        delays.append(round(nominal * spread, 6))
    return delays


def tenant_counts(items: Sequence[Request]) -> dict[str, int]:
    """Count requests per tenant, keyed in first seen order.

    Args:
        items: Requests to count.

    Returns:
        Tenant to count, ordered by the first appearance of each tenant.
    """
    counts: dict[str, int] = {}
    for item in items:
        counts[item.tenant] = counts.get(item.tenant, 0) + 1
    return counts
