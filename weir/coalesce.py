"""Intent-digest coalescing.

A CDN caches on the URL, because for documents the URL *is* the identity of the
content.  Agents do not fetch documents, they ask questions, and a hundred
agents asking one question phrase it a hundred ways.  Keyed on bytes, those are
a hundred cache misses.  Keyed on intent digest, they are one upstream call and
ninety-nine waiters - and on this network the upstream call is the expensive
thing, so the difference is not a latency optimisation, it is the bill.

Two rules keep this from being a security hole, and they are not optional:

1.  **Only safe requests.**  Coalescing a side-effecting request means one agent
    gets another agent's write.  ``safe`` is checked, never inferred.
2.  **Only within an authorisation scope.**  Two callers share a result only if
    they share a principal, or the capability is declared public.  Without this
    rule the cache is an exfiltration primitive: ask the same question as your
    victim and receive their answer, computed under their credentials.  This is
    the single most dangerous idea in the whole design and the reason the scope
    key is mandatory rather than a configuration flag.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from .intent import Intent


@dataclass
class _Flight:
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: BaseException | None = None
    waiters: int = 0


class Coalescer:
    """Single-flight plus short-lived result cache, keyed on intent."""

    def __init__(self, clock, *, ttl: float = 30.0, public: frozenset[str] = frozenset()) -> None:
        self._clock = clock
        self._flights: dict[tuple, _Flight] = {}
        self._cache: dict[tuple, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self.ttl = ttl
        #: Capabilities whose results may be shared across principals.
        self.public = set(public)

        self.hits = 0
        self.joined = 0
        self.misses = 0

    def key(self, intent: Intent) -> tuple | None:
        if not intent.safe or not intent.intent_digest:
            return None
        scope = "public" if intent.capability in self.public else intent.principal
        return (scope, intent.capability, intent.intent_digest)

    def acquire(self, intent: Intent) -> tuple[str, tuple | None, Any]:
        """Decide this caller's role in one atomic step.

        Split out from :meth:`run` so the router can find out whether a request
        will actually reach the upstream *before* it spends an admission slot
        on it. Returns one of:

        ``("hit", key, value)``     cached; serve now, no upstream call
        ``("join", key, flight)``   someone else is already fetching this
        ``("lead", key, flight)``   we must fetch it; the flight is registered,
                                    so the caller MUST call :meth:`release`
        ``("bypass", None, None)``  not coalescable (unsafe, or no digest)
        """
        k = self.key(intent)
        if k is None:
            return "bypass", None, None

        now = self._clock.now()
        with self._lock:
            entry = self._cache.get(k)
            if entry and entry[0] > now:
                self.hits += 1
                return "hit", k, entry[1]

            flight = self._flights.get(k)
            if flight is not None:
                flight.waiters += 1
                self.joined += 1
                return "join", k, flight

            flight = _Flight()
            self._flights[k] = flight
            self.misses += 1
            return "lead", k, flight

    def join(self, flight: _Flight, intent: Intent) -> Any:
        """Wait for the leader's result. Raises whatever the leader raised."""
        flight.event.wait(timeout=max(0.0, intent.deadline - self._clock.now()))
        if flight.error is not None:
            raise flight.error
        return flight.result

    def release(self, key: tuple, flight: _Flight, *, result: Any = None,
                error: BaseException | None = None) -> None:
        """Publish the leader's outcome and wake every waiter.

        Must be called exactly once for every ``("lead", ...)`` acquisition,
        including the paths where the leader never got to make the call - a
        leader that is refused admission and returns without releasing leaves
        its waiters blocked until their deadlines expire.
        """
        with self._lock:
            self._flights.pop(key, None)
            if error is None:
                self._cache[key] = (self._clock.now() + self.ttl, result)
        flight.result = result
        flight.error = error
        flight.event.set()

    def run(self, intent: Intent, work: Callable[[], Any]) -> tuple[Any, str]:
        """Execute ``work``, or return an in-flight or cached result.

        Returns ``(result, disposition)`` where disposition is one of
        ``hit``, ``joined``, ``miss``.
        """
        role, key, obj = self.acquire(intent)
        if role == "hit":
            return obj, "hit"
        if role == "join":
            return self.join(obj, intent), "joined"
        if role == "bypass":
            return work(), "miss"

        try:
            value = work()
        except BaseException as exc:
            self.release(key, obj, error=exc)
            raise
        self.release(key, obj, result=value)
        return value, "miss"

    def sweep(self) -> int:
        now = self._clock.now()
        with self._lock:
            dead = [k for k, (exp, _) in self._cache.items() if exp <= now]
            for k in dead:
                del self._cache[k]
            return len(dead)

    def stats(self) -> dict[str, int]:
        return {"cache_hits": self.hits, "coalesced": self.joined,
                "upstream_calls": self.misses, "cached_keys": len(self._cache)}
