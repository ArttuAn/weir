"""Intent-addressed forwarding.

An IP forwarding table maps a destination prefix to a next hop, and longest
prefix match decides.  The address is the question, because on the classical
internet you already know *which machine* you want; naming it is the whole job.

An agent does not know which machine it wants, and should not.  It wants a
capability - ``search.web``, ``llm.completion.long-context``, ``payments.quote``
- and which endpoint provides that today is exactly the kind of decision a
network should be making on its behalf.  Hardcoding a provider hostname into a
thousand agents is how you get a fleet that cannot be repointed, repriced or
failed over without a redeploy.

So the FIB is keyed on dotted capability names with longest-*capability* match,
which is structurally the same lookup as longest-prefix match and inherits the
same useful property: specific routes override general ones, so
``llm.completion.long-context`` can go somewhere different from
``llm.completion`` without touching either route.

Route selection among equal-specificity candidates is a cost function rather
than pure preference, because on this network "best" genuinely varies per
request: a human-coupled request with 800 ms left wants the fastest endpoint at
almost any price, and a batch job with an hour wants the cheapest endpoint that
will eventually answer.  One FIB, two different next hops, decided per request
from fields the header already carries.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import Reason, Refused
from .intent import Intent


@dataclass(slots=True)
class Route:
    capability: str            # dotted prefix this route serves
    upstream: str              # base URL or simulator endpoint name
    origin: str = ""           # terms identity, defaults to upstream host
    price: int = 100           # millicredits per call
    latency: float = 0.4       # expected service time, seconds
    weight: float = 1.0        # admin preference, lower is better
    capacity: int = 32         # advertised concurrency
    healthy: bool = True

    def specificity(self) -> int:
        return len(self.capability.split("."))

    def matches(self, capability: str) -> bool:
        return capability == self.capability or capability.startswith(self.capability + ".")


class Fib:
    """Capability-keyed forwarding table."""

    def __init__(self) -> None:
        self._routes: list[Route] = []
        self.lookups = 0
        self.misses = 0

    def add(self, route: Route) -> None:
        if not route.origin:
            host = route.upstream.split("//")[-1].split("/")[0]
            route.origin = host or route.upstream
        self._routes.append(route)
        # Most specific first, so the scan is naturally longest-match.
        self._routes.sort(key=lambda r: (-r.specificity(), r.weight))

    def routes(self) -> list[Route]:
        return list(self._routes)

    def lookup(self, intent: Intent, now: float) -> Route:
        """Longest-capability match, then cost function over the tied set."""
        self.lookups += 1
        candidates = [r for r in self._routes if r.healthy and r.matches(intent.capability)]
        if not candidates:
            self.misses += 1
            raise Refused(Reason.NO_ROUTE, f"no route for capability {intent.capability!r}")

        best_spec = candidates[0].specificity()
        tied = [r for r in candidates if r.specificity() == best_spec]
        if len(tied) == 1:
            return tied[0]

        time_left = max(1e-3, intent.deadline - now)
        return min(tied, key=lambda r: self._cost(r, intent, time_left))

    @staticmethod
    def _cost(route: Route, intent: Intent, time_left: float) -> float:
        """Lower is better.

        Urgency is the ratio of expected service time to time remaining.  A
        human-coupled request weights latency heavily and price barely; a batch
        request with plenty of slack does the reverse.  The crossover is not
        tuned by hand, it falls out of the deadline the caller declared.
        """
        urgency = min(4.0, route.latency / time_left)
        if intent.human_coupled:
            urgency = max(urgency, 1.0)
        # The latency term must decay to near zero as slack grows, or a batch
        # job with an hour to spare still buys the premium endpoint.  The 0.05
        # floor keeps latency from being ignored entirely, since even a patient
        # caller prefers not to hold a connection open all day.
        latency_term = route.latency * (0.05 + 4.0 * urgency)
        price_term = (route.price / 1000.0) * max(0.0, 2.0 - urgency)
        return route.weight * (latency_term + price_term)
