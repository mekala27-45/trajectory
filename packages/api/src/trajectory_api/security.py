"""Write authentication and rate limiting.

Read routes are public: the whole point of the project is that a stranger can look at the
results without signing up for anything. Write routes take a bearer token, which is enough
for a service whose writers are a laptop and a CI job. There are no user accounts, no
OAuth and no sessions, because none of those would protect anything that this does not.

The rate limiter is a token bucket held in process memory. That is the correct shape here
and not a shortcut: the service runs as a single small machine, an in-process limiter has
no failure mode of its own, and adding Redis to rate limit a handful of writers per day
would be infrastructure that exists to look serious. If this ever runs on more than one
machine the limiter becomes per machine, which is documented rather than silently wrong.
"""

from __future__ import annotations

import hmac
import threading
import time
from dataclasses import dataclass, field

import structlog
from fastapi import Header, HTTPException, Request, status

from trajectory_api.settings import get_settings

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class _Bucket:
    """One client's token bucket."""

    tokens: float
    updated_at: float


@dataclass
class RateLimiter:
    """A token bucket per client, refilling continuously.

    Continuous refill rather than fixed windows, so a client is not able to spend a whole
    minute's allowance in the last second of one window and again in the first second of
    the next.
    """

    per_minute: int
    burst: int = 0
    _buckets: dict[str, _Bucket] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        """Default the burst allowance to one minute's worth."""
        if self.burst <= 0:
            self.burst = self.per_minute

    def allow(self, client: str, *, now: float | None = None) -> tuple[bool, float]:
        """Consume a token.

        Args:
            client: Identity to limit on.
            now: Current monotonic time, injectable for tests.

        Returns:
            Whether the request is allowed, and the seconds until the next token.
        """
        moment = now if now is not None else time.monotonic()
        rate = self.per_minute / 60.0
        with self._lock:
            bucket = self._buckets.get(client)
            if bucket is None:
                bucket = _Bucket(tokens=float(self.burst), updated_at=moment)
                self._buckets[client] = bucket
            elapsed = max(0.0, moment - bucket.updated_at)
            bucket.tokens = min(float(self.burst), bucket.tokens + elapsed * rate)
            bucket.updated_at = moment
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0.0
            return False, round((1.0 - bucket.tokens) / rate, 3)

    def reset(self) -> None:
        """Forget every bucket."""
        with self._lock:
            self._buckets.clear()


_limiter: RateLimiter | None = None


def limiter() -> RateLimiter:
    """Return the process wide limiter."""
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(per_minute=get_settings().trajectory_rate_limit_writes_per_minute)
    return _limiter


def set_limiter(new_limiter: RateLimiter | None) -> None:
    """Replace the process wide limiter. Used by the tests."""
    global _limiter
    _limiter = new_limiter


def client_identity(request: Request) -> str:
    """Identify the caller for rate limiting.

    Uses the forwarded address when a proxy set one, because behind Fly every request
    arrives from the same internal peer and limiting on that would throttle everyone at
    once.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def bearer_token(authorization: str | None) -> str:
    """Extract the token from an Authorization header, or return an empty string."""
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def token_matches(supplied: str, expected: str) -> bool:
    """Compare tokens in constant time.

    Constant time so the response latency does not leak the key one byte at a time. Cheap
    insurance on a route that is otherwise trivial to brute force.
    """
    if not supplied or not expected:
        return False
    return hmac.compare_digest(supplied, expected)


def require_api_key(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    """Authorise a write request, then charge it against the caller's rate limit.

    The signature takes nothing but the request and the header on purpose. FastAPI
    inspects a dependency's parameters and treats any Pydantic model it finds as a body
    field, so a `settings: Settings` parameter here silently made every write route expect
    its payload wrapped under a key. Settings are read inside the function instead.

    Raises:
        HTTPException: 401 when the token is missing or wrong, 429 when the caller is over
            their limit.
    """
    expected = get_settings().trajectory_api_key
    supplied = bearer_token(authorization)

    if not token_matches(supplied, expected):
        log.warning("auth.rejected", client=client_identity(request), path=request.url.path)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="this route needs a bearer token. Read routes are public.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    allowed, retry_after = limiter().allow(client_identity(request))
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"rate limit exceeded, retry in {retry_after:.1f}s",
            headers={"Retry-After": str(max(1, int(retry_after) + 1))},
        )
