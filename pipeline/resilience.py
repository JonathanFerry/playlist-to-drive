"""
resilience.py - retry, backoff, and a circuit breaker.

A long run fails in two very different ways, and conflating them ruins
the run. A video that is private or deleted will never succeed, and
retrying wastes time. A quota error or a 503 says nothing about the
video at all, and marking it failed is simply wrong.

Worse, quota exhaustion is not a single failure: it fails every
remaining item in quick succession. Without a circuit breaker, one
exhausted quota at item 200 marks the next 780 as failed and the
manifest becomes useless for resuming.
"""

from __future__ import annotations

import random
import time

from googleapiclient.errors import HttpError

# Worth retrying: rate limits and transient server faults.
TRANSIENT_STATUS = {429, 500, 502, 503, 504}

MAX_ATTEMPTS = 5
BASE_DELAY = 2.0
MAX_DELAY = 64.0


class QuotaExhausted(Exception):
    """Retries exhausted on a rate-limit error. The run should stop."""


def _status_of(exc: Exception) -> int | None:
    if isinstance(exc, HttpError):
        return getattr(exc.resp, "status", None)
    return None


def is_transient(exc: Exception) -> bool:
    return _status_of(exc) in TRANSIENT_STATUS


def with_retry(fn, *args, label: str = "", **kwargs):
    """Call fn, retrying transient failures with exponential backoff.

    Jitter is added so that concurrent retries do not resynchronise into
    a thundering herd against the same quota window.
    """
    last = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            # Broad on purpose: this decides whether an error is worth
            # retrying, and anything not transient is re-raised below
            # rather than swallowed.
            last = exc
            if not is_transient(exc):
                raise
            if attempt == MAX_ATTEMPTS:
                break
            delay = min(BASE_DELAY * (2 ** (attempt - 1)), MAX_DELAY)
            delay += random.uniform(0, delay * 0.25)
            status = _status_of(exc)
            print(f"        retry {attempt}/{MAX_ATTEMPTS - 1} after {delay:.0f}s "
                  f"(HTTP {status}{' ' + label if label else ''})")
            time.sleep(delay)

    if _status_of(last) == 429:
        raise QuotaExhausted(
            f"rate limited after {MAX_ATTEMPTS} attempts; stopping so the "
            f"manifest stays accurate and the run can resume"
        ) from last
    raise last


class CircuitBreaker:
    """Stop a long run when failures stop looking like bad luck.

    Two triggers. Consecutive failures catch a systemic break — expired
    credentials, network loss, exhausted quota. A total-failure ceiling
    catches slow bleeds that never trip the consecutive count.

    Videos that are legitimately unprocessable — no captions, private,
    deleted — are NOT failures and never move the needle. Otherwise a
    playlist with many dead entries would trip the breaker spuriously.
    """

    def __init__(self, consecutive_limit: int = 5, total_limit: int = 50):
        self.consecutive_limit = consecutive_limit
        self.total_limit = total_limit
        self.consecutive = 0
        self.total = 0
        self.reason = ""

    def record_success(self) -> None:
        self.consecutive = 0

    def record_failure(self, note: str = "") -> None:
        self.consecutive += 1
        self.total += 1
        if self.consecutive >= self.consecutive_limit:
            self.reason = (
                f"{self.consecutive} consecutive failures — this looks "
                f"systemic rather than per-video. Last: {note[:120]}"
            )
        elif self.total >= self.total_limit:
            self.reason = f"{self.total} total failures in this run"

    @property
    def tripped(self) -> bool:
        return bool(self.reason)
