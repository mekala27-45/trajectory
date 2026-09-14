#!/usr/bin/env python3
"""Generate the incident log for log-forensics-01.

The log is 40,000 lines across five services and contains one real incident plus three
red herrings. Everything is driven by a fixed seed, so the file is byte identical on every
machine and the expected answers can be derived rather than guessed.

This script lives outside `workspace/` on purpose. An agent that could read the generator
would be solving a different task.

Run from the task directory:

    python3 vendor/generate_log.py

It writes `workspace/app.log.gz` and `verify/expected.json`.
"""

from __future__ import annotations

import gzip
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED = 20260412
START = datetime(2026, 4, 12, 13, 45, 0, tzinfo=UTC)

SERVICES = ["api-gateway", "checkout", "inventory", "payments", "postgres-proxy"]

TRIGGER_AT = timedelta(minutes=47, seconds=7)  # 14:32:07
REVERT_AT = timedelta(minutes=79, seconds=12)  # 15:04:12
END_AT = timedelta(minutes=95)

NORMAL_PATHS = [
    "/v1/cart",
    "/v1/cart/items",
    "/v1/checkout",
    "/v1/inventory/availability",
    "/v1/payments/authorize",
    "/v1/orders",
    "/healthz",
]

rng = random.Random(SEED)


def event_id(prefix: str, index: int) -> str:
    """Deterministic event identifier."""
    return f"{prefix}-{index:06d}"


def line(ts: datetime, service: str, level: str, message: str, **fields: object) -> str:
    """Render one log line in the format the fleet uses."""
    extra = " ".join(f"{k}={v}" for k, v in fields.items())
    stamp = ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"
    body = f"{stamp} {level:<5} [{service}] {message}"
    return f"{body} {extra}".rstrip()


def build() -> tuple[list[str], dict[str, object]]:
    """Produce the log lines and the expected findings."""
    lines: list[str] = []
    counter = 0
    five_xx = 0
    impacted: set[str] = set()

    trigger_id = event_id("cfg", 4821)
    revert_id = event_id("cfg", 4822)
    trigger_ts = START + TRIGGER_AT
    revert_ts = START + REVERT_AT

    # Red herring one: a certificate warning that fires every ten minutes from the start.
    cert_times = [START + timedelta(minutes=m) for m in range(0, 96, 10)]
    # Red herring two: a scanner producing 404s in a burst well before the incident.
    scan_start = START + timedelta(minutes=21)
    # Red herring three: an unrelated payments restart during the incident.
    restart_ts = START + timedelta(minutes=66, seconds=30)

    cursor = START
    while cursor - START < END_AT:
        counter += 1
        in_incident = trigger_ts <= cursor < revert_ts
        service = rng.choice(SERVICES)
        request_id = event_id("req", counter)
        path = rng.choice(NORMAL_PATHS)

        if in_incident and service == "inventory" and rng.random() < 0.55:
            lines.append(
                line(
                    cursor,
                    "inventory",
                    "ERROR",
                    "pool exhausted, waiting for connection",
                    request_id=request_id,
                    path=path,
                    waited_ms=rng.randint(2500, 9800),
                    pool_in_use=4,
                    pool_max=4,
                    status=503,
                )
            )
            five_xx += 1
            impacted.add("inventory")
        elif in_incident and service == "checkout" and rng.random() < 0.4:
            lines.append(
                line(
                    cursor,
                    "checkout",
                    "ERROR",
                    "upstream call failed",
                    request_id=request_id,
                    upstream="inventory",
                    path="/v1/checkout",
                    error="deadline exceeded after 5000ms",
                    status=502,
                )
            )
            five_xx += 1
            impacted.add("checkout")
        elif in_incident and service == "api-gateway" and rng.random() < 0.35:
            lines.append(
                line(
                    cursor,
                    "api-gateway",
                    "ERROR",
                    "request failed",
                    request_id=request_id,
                    path="/v1/checkout",
                    upstream="checkout",
                    status=503,
                    duration_ms=rng.randint(5000, 9000),
                )
            )
            five_xx += 1
            impacted.add("api-gateway")
        elif service == "payments" and rng.random() < 0.06:
            lines.append(
                line(
                    cursor,
                    "payments",
                    "WARN",
                    "retrying idempotent authorize",
                    request_id=request_id,
                    attempt=rng.randint(2, 3),
                    provider="acquirer-eu",
                    status=200,
                )
            )
        elif scan_start <= cursor < scan_start + timedelta(minutes=6) and rng.random() < 0.5:
            lines.append(
                line(
                    cursor,
                    "api-gateway",
                    "WARN",
                    "no route matched",
                    request_id=request_id,
                    path=rng.choice(["/wp-login.php", "/.env", "/admin.php", "/.git/config"]),
                    remote="203.0.113." + str(rng.randint(2, 250)),
                    status=404,
                )
            )
        else:
            lines.append(
                line(
                    cursor,
                    service,
                    "INFO",
                    "request completed",
                    request_id=request_id,
                    path=path,
                    status=200,
                    duration_ms=rng.randint(4, 180),
                )
            )

        cursor += timedelta(milliseconds=rng.randint(60, 225))

    # Splice in the fixed events at their exact timestamps.
    fixed = [
        (
            trigger_ts,
            line(
                trigger_ts,
                "inventory",
                "INFO",
                "config.reload applied",
                event_id=trigger_id,
                key="db.pool.max_size",
                old=40,
                new=4,
                source="config-service",
                actor="deploy-bot",
            ),
        ),
        (
            revert_ts,
            line(
                revert_ts,
                "inventory",
                "INFO",
                "config.reload applied",
                event_id=revert_id,
                key="db.pool.max_size",
                old=4,
                new=40,
                source="config-service",
                actor="oncall",
            ),
        ),
        (
            restart_ts,
            line(
                restart_ts,
                "payments",
                "INFO",
                "process restarted by operator",
                event_id=event_id("ops", 771),
                reason="suspected memory leak",
                actor="oncall",
            ),
        ),
    ]
    for ts in cert_times:
        fixed.append(
            (
                ts,
                line(
                    ts,
                    "postgres-proxy",
                    "WARN",
                    "server certificate expires soon",
                    event_id=event_id("tls", int((ts - START).total_seconds())),
                    days_remaining=29,
                    subject="CN=postgres-proxy.internal",
                ),
            )
        )

    merged = lines + [text for _, text in fixed]
    merged.sort(key=lambda text: text[:24])

    expected = {
        "root_cause_service": "inventory",
        "trigger_event_id": trigger_id,
        "trigger_timestamp": trigger_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "resolution_event_id": revert_id,
        "error_signature": "pool exhausted",
        "impacted_services": sorted(impacted),
        "total_5xx": five_xx,
        "line_count": len(merged),
    }
    return merged, expected


def main() -> None:
    """Write the compressed log and the expected findings."""
    lines, expected = build()
    target = ROOT / "workspace" / "app.log.gz"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = ("\n".join(lines) + "\n").encode()
    # mtime=0 so the archive is byte identical between runs.
    with gzip.GzipFile(filename="", mode="wb", fileobj=target.open("wb"), mtime=0) as handle:
        handle.write(payload)
    (ROOT / "verify" / "expected.json").write_text(json.dumps(expected, indent=2) + "\n")
    print(f"wrote {target} ({target.stat().st_size // 1024} KiB, {len(lines)} lines)")
    print(json.dumps(expected, indent=2))


if __name__ == "__main__":
    main()
