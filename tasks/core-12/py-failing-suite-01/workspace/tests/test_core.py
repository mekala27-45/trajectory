"""The visible test suite. The hidden suite used for scoring is a superset of this."""

from datetime import date

from daterange import business_days, date_range, days_between, overlaps, split_months


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


def test_reversed_range_is_empty():
    assert date_range(date(2026, 3, 13), date(2026, 3, 10)) == []


def test_overlaps_detects_a_shared_day():
    assert overlaps(date(2026, 1, 1), date(2026, 1, 10), date(2026, 1, 10), date(2026, 1, 20))
    assert not overlaps(date(2026, 1, 1), date(2026, 1, 9), date(2026, 1, 10), date(2026, 1, 20))


def test_split_months_covers_the_whole_range():
    chunks = split_months(date(2026, 1, 20), date(2026, 3, 5))
    assert chunks == [
        (date(2026, 1, 20), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 28)),
        (date(2026, 3, 1), date(2026, 3, 5)),
    ]
