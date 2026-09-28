"""Machine-readable terms, enforced at the hop.

``robots.txt`` is a text file at the application layer that asks nicely.  It
worked for twenty-five years because the crawlers that mattered were operated
by a handful of companies with reputations to protect.  That is not the shape
of the agent internet: the crawler is now a script somebody vibe-coded on a
Tuesday, and the site's only options are to serve it or to fight a fingerprint
war it cannot win.

The asymmetry is that enforcement lives with the party that has no leverage.
Weir moves it to the hop, which has both sides' traffic, and makes it work in
both directions:

*   for the origin - terms are enforced *before* the request is forwarded, so a
    disallowed crawl costs the origin nothing at all, not even a TLS handshake;
*   for the agent - compliance is attested in the receipt chain, so a
    well-behaved operator can *prove* it stayed within terms instead of being
    lumped in with the scrapers.

That second half is the part that makes this adoptable.  A pure enforcement
mechanism gets routed around; one that also gives compliant agents a
credential they can show is one they will voluntarily traverse.

Terms are deliberately simple - allow/deny by capability and purpose, a rate,
and an optional price.  A policy language nobody can read is a policy nobody
applies correctly.
"""

from __future__ import annotations

import fnmatch
import threading
from dataclasses import dataclass, field

from .errors import Reason, Refused
from .intent import Intent


@dataclass(slots=True)
class Terms:
    """One origin's published terms."""

    origin: str
    allow: tuple[str, ...] = ("*",)
    deny: tuple[str, ...] = ()
    #: Purposes the origin permits, e.g. {"search", "answer"} but not "train".
    purposes: tuple[str, ...] = ("*",)
    #: Requests per second per principal.  None means unlimited.
    rate: float | None = None
    #: Millicredits per request, if the origin prices access.
    price: int = 0

    def permits(self, capability: str, purpose: str) -> tuple[bool, str]:
        for pat in self.deny:
            if fnmatch.fnmatch(capability, pat):
                return False, f"capability {capability} denied by {self.origin} terms"
        if not any(fnmatch.fnmatch(capability, p) for p in self.allow):
            return False, f"capability {capability} not in {self.origin} allow list"
        if "*" not in self.purposes and purpose not in self.purposes:
            return False, f"purpose {purpose!r} not permitted by {self.origin} terms"
        return True, ""


class TermsRegistry:
    """Terms lookup plus per-principal rate enforcement."""

    def __init__(self, clock) -> None:
        self._clock = clock
        self._terms: dict[str, Terms] = {}
        self._buckets: dict[tuple[str, str], tuple[float, float]] = {}
        self._lock = threading.Lock()
        self.denied = 0
        self.throttled = 0

    def publish(self, terms: Terms) -> None:
        self._terms[terms.origin] = terms

    def get(self, origin: str) -> Terms | None:
        return self._terms.get(origin)

    def check(self, intent: Intent, origin: str, purpose: str = "answer") -> int:
        """Enforce terms.  Returns the price in millicredits."""
        terms = self._terms.get(origin)
        if terms is None:
            return 0

        ok, why = terms.permits(intent.capability, purpose)
        if not ok:
            self.denied += 1
            raise Refused(Reason.TERMS_DENIED, why)

        if terms.rate is not None:
            key = (origin, intent.principal)
            now = self._clock.now()
            with self._lock:
                tokens, last = self._buckets.get(key, (terms.rate, now))
                tokens = min(terms.rate, tokens + (now - last) * terms.rate)
                if tokens < 1.0:
                    self.throttled += 1
                    wait = (1.0 - tokens) / terms.rate
                    self._buckets[key] = (tokens, now)
                    raise Refused(Reason.CONGESTED,
                                  f"{origin} terms allow {terms.rate}/s for this principal",
                                  retry_not_before=now + wait,
                                  charged=0)
                self._buckets[key] = (tokens - 1.0, now)
        return terms.price
