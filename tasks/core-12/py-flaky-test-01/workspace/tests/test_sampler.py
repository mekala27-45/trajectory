"""Tests for the request auditor's sampling helpers.

The sampler is random by design, so these assert the properties the auditor depends on
rather than one exact sample.
"""

from sampler import Request, retry_delays, sample_requests, tenant_counts

TENANTS = ("acme", "globex", "initech", "umbrella")
PER_TENANT = 10
SAMPLE_SIZE = 8


def make_requests() -> list[Request]:
    """Build a fleet with an equal number of requests per tenant."""
    return [
        Request(request_id=f"req-{index:04d}", tenant=tenant, latency_ms=20 + index)
        for index, tenant in enumerate(TENANTS * PER_TENANT)
    ]


def test_sample_size_matches_k():
    assert len(sample_requests(make_requests(), SAMPLE_SIZE)) == SAMPLE_SIZE


def test_sample_is_drawn_without_replacement():
    picked = sample_requests(make_requests(), SAMPLE_SIZE)
    assert len({request.request_id for request in picked}) == SAMPLE_SIZE


def test_sample_covers_every_tenant():
    # The auditor's whole point is that a reviewer sees traffic from every tenant, so a
    # sample that leaves one out is not usable.
    picked = sample_requests(make_requests(), SAMPLE_SIZE)
    assert set(tenant_counts(picked)) == set(TENANTS)


def test_retry_delays_stay_inside_the_jitter_envelope():
    delays = retry_delays(5, base_delay=0.5, factor=2.0, jitter=0.25)
    assert len(delays) == 5
    for attempt, delay in enumerate(delays):
        nominal = 0.5 * 2.0**attempt
        assert 0.75 * nominal <= delay <= 1.25 * nominal


def test_retry_delays_fit_the_upload_budget():
    # The uploader gives up after 20 seconds, so five jittered attempts have to fit.
    assert sum(retry_delays(5, base_delay=0.5, factor=2.0, jitter=0.25)) <= 20.0


def test_tenant_counts_are_keyed_in_first_seen_order():
    counts = tenant_counts(make_requests())
    assert list(counts) == list(TENANTS)
    assert set(counts.values()) == {PER_TENANT}
