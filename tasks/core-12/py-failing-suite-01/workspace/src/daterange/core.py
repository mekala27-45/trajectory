"""Inclusive date range helpers.

Every range in this module includes both endpoints. A range whose start equals its end is
one day long, not zero.
"""

from __future__ import annotations

from datetime import date, timedelta

WEEKEND = (5, 6)


def date_range(start: date, end: date) -> list[date]:
    """Return every date from start to end, with both ends included.

    A range where start equals end contains that single date.

    Args:
        start: First date in the range.
        end: Last date in the range.

    Returns:
        The dates in ascending order, or an empty list when end precedes start.
    """
    if end < start:
        return []
    span = (end - start).days
    return [start + timedelta(days=offset) for offset in range(span)]


def days_between(start: date, end: date) -> int:
    """Return the number of days in the inclusive range from start to end.

    Args:
        start: First date in the range.
        end: Last date in the range.

    Returns:
        The count of dates in the range, which is zero when end precedes start.
    """
    return len(date_range(start, end))


def business_days(start: date, end: date) -> list[date]:
    """Return the weekdays in the inclusive range from start to end.

    Args:
        start: First date in the range.
        end: Last date in the range.

    Returns:
        Dates in the range that are not a Saturday or a Sunday.
    """
    return [day for day in date_range(start, end) if day.weekday() not in WEEKEND]


def overlaps(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    """Return whether two inclusive ranges share at least one date.

    Args:
        a_start: Start of the first range.
        a_end: End of the first range.
        b_start: Start of the second range.
        b_end: End of the second range.

    Returns:
        True when the ranges touch or overlap.
    """
    return a_start <= b_end and b_start <= a_end


def split_months(start: date, end: date) -> list[tuple[date, date]]:
    """Split an inclusive range into one inclusive range per calendar month.

    Args:
        start: First date in the range.
        end: Last date in the range.

    Returns:
        A list of start and end pairs, one per month the range touches.
    """
    if end < start:
        return []
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        if cursor.month == 12:
            next_month = date(cursor.year + 1, 1, 1)
        else:
            next_month = date(cursor.year, cursor.month + 1, 1)
        chunk_end = min(end, next_month - timedelta(days=1))
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks
