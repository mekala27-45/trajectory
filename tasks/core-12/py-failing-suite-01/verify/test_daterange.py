"""Hidden verification suite for py-failing-suite-01.

Superset of the visible tests. Three of these fail on the unmodified workspace, all from
the same off by one, and the remaining five guard against a fix that trades one bug for
another (for example by making a reversed range return a single date).
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "src"))

from daterange import business_days, date_range, days_between, overlaps, split_months  # noqa: E402


def test_single_day_range_contains_one_day():
    day = date(2026, 3, 10)
    assert date_range(day, day) == [day]


def test_range_includes_both_ends():
    assert date_range(date(2026, 3, 10), date(2026, 3, 13)) == [
        date(2026, 3, 10),
        date(2026, 3, 11),
        date(2026, 3, 12),
        date(2026, 3, 13),
    ]


def test_days_between_counts_inclusively():
    assert days_between(date(2026, 3, 1), date(2026, 3, 31)) == 31
    assert days_between(date(2026, 3, 1), date(2026, 3, 1)) == 1


def test_reversed_range_is_still_empty():
    assert date_range(date(2026, 3, 13), date(2026, 3, 10)) == []
    assert days_between(date(2026, 3, 13), date(2026, 3, 10)) == 0


def test_business_days_excludes_the_weekend_and_includes_the_last_day():
    # 2026-03-09 is a Monday, 2026-03-15 is a Sunday.
    assert business_days(date(2026, 3, 9), date(2026, 3, 15)) == [
        date(2026, 3, 9),
        date(2026, 3, 10),
        date(2026, 3, 11),
        date(2026, 3, 12),
        date(2026, 3, 13),
    ]


def test_leap_day_is_included():
    assert date(2028, 2, 29) in date_range(date(2028, 2, 27), date(2028, 3, 1))
    assert days_between(date(2028, 2, 1), date(2028, 2, 29)) == 29


def test_overlaps_is_unchanged():
    assert overlaps(date(2026, 1, 1), date(2026, 1, 10), date(2026, 1, 10), date(2026, 1, 20))
    assert not overlaps(date(2026, 1, 1), date(2026, 1, 9), date(2026, 1, 10), date(2026, 1, 20))


def test_split_months_is_unchanged():
    assert split_months(date(2026, 1, 20), date(2026, 3, 5)) == [
        (date(2026, 1, 20), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 28)),
        (date(2026, 3, 1), date(2026, 3, 5)),
    ]
