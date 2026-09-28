"""A simulator for agent traffic.

The claims in ``docs/DESIGN.md`` are falsifiable only if they can be run, so
every number in that document comes from this file.  What is modelled:

**The upstream** - ``c`` workers, mean service time ``S``, and a thrash term.
The thrash term is the part that makes overload interesting: an inference
endpoint under a deep queue does not degrade gracefully, it degrades
*super-linearly*, because batching windows fill, KV cache spills and requests
that would have taken 500 ms take four seconds.  Without that term overload is
merely slow and every admission strategy looks equally good.

**The callers** - agents that issue a request, wait, and retry on refusal.  A
``compliant`` agent honours whatever backoff it is given.  A ``noncompliant``
one retries on a short fixed delay no matter what it is told: not malice, just
a retry decorator somebody left at its defaults.

The comparison is deliberately fair in the way that matters: *the same caller
population runs against both routers*.  The claim under test is not "weir is
good because its clients are polite".  It is that weir's outcome is far less
sensitive to how polite its clients are, because the mechanisms do not depend
on client cooperation.

This is a model, not a benchmark.  It says nothing about what any real endpoint
does, and the absolute numbers mean nothing outside its own assumptions.  What
it does support is the *shape* of the comparison, and every assumption is a
named parameter in this file so the shape can be argued with.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass, field

from .clock import VirtualClock
from .damper import Appointment
from .errors import Reason, Refused
from .fib import Route
from .intent import CREDIT, Intent, digest_of, new_root
from .router import Router, RouterConfig


# ---------------------------------------------------------------------------
# Modelled upstream
# ---------------------------------------------------------------------------

@dataclass
class Upstream:
    clock: VirtualClock
    workers: int = 8
    service: float = 0.5
    thrash: float = 1.5
    rng: random.Random = field(default_factory=lambda: random.Random(7))

    busy: int = 0
    queue: list = field(default_factory=list)
    served: int = 0
    wasted: int = 0            # completed work nobody was still waiting for
    busy_seconds: float = 0.0
    wasted_seconds: float = 0.0

    def submit(self, on_done, still_wanted) -> None:
        self.queue.append((on_done, still_wanted))
        self._pump()

    def _pump(self) -> None:
        while self.queue and self.busy < self.workers:
            on_done, still_wanted = self.queue.pop(0)
            self.busy += 1
            depth = len(self.queue) / max(1, self.workers)
            svc = self.service * (1.0 + self.thrash * depth) * self.rng.uniform(0.8, 1.2)

            def finish(on_done=on_done, still_wanted=still_wanted, svc=svc):
                self.busy -= 1
                self.busy_seconds += svc
                self.served += 1
                if not still_wanted():
                    # The caller gave up while this was in flight.  The work
                    # was done anyway, billed anyway, and discarded.  Today
                    # nothing on the path can see this; a router holding the
                    # deadline can.
                    self.wasted += 1
                    self.wasted_seconds += svc
                on_done(svc)
                self._pump()

            self.clock.after(svc, finish)

    @property
    def saturated(self) -> bool:
        return self.busy >= self.workers


# ---------------------------------------------------------------------------
# Baseline: a conventional gateway
# ---------------------------------------------------------------------------

class ClassicGateway:
    """What is deployed today: a concurrency limit and HTTP 429.

    No budget, no deadline, no loop detection, no metering.  Refusals are free
    to ignore and carry only a Retry-After hint, which is a *suggestion about a
    duration*, not a reservation of a time - so every refused caller computes
    its own comeback and they all collide again.
    """

    def __init__(self, clock, upstream: Upstream, limit: int = 32,
                 retry_after: float = 1.0) -> None:
        self.clock = clock
        self.upstream = upstream
        self.limit = limit
        self.retry_after = retry_after
        self.inflight = 0
        self.admitted = 0
        self.refused = 0

    def submit(self, on_ok, on_refused, still_wanted) -> None:
        if self.inflight >= self.limit:
            self.refused += 1
            on_refused(self.retry_after)
            return
        self.inflight += 1
        self.admitted += 1

        def done(svc):
            self.inflight -= 1
            on_ok(svc)

        self.upstream.submit(done, still_wanted)


# ---------------------------------------------------------------------------
# Caller population
# ---------------------------------------------------------------------------

@dataclass
class AgentSpec:
    n: int = 200
    noncompliant: float = 0.3
    deadline: float = 20.0
    max_attempts: int = 40
    #: What a non-compliant agent does instead of honouring backoff.
    stubborn_delay: float = 0.2
    human_fraction: float = 0.5


@dataclass
class Outcome:
    label: str
    succeeded: int = 0
    gave_up: int = 0
    attempts: int = 0
    upstream_calls: int = 0
    wasted_calls: int = 0
    wasted_seconds: float = 0.0
    credits_burned: int = 0
    latencies_human: list[float] = field(default_factory=list)
    latencies_all: list[float] = field(default_factory=list)

    def p(self, pct: float, human: bool = False) -> float:
        xs = sorted(self.latencies_human if human else self.latencies_all)
        if not xs:
            return float("nan")
        k = min(len(xs) - 1, int(round((pct / 100.0) * (len(xs) - 1))))
        return xs[k]

    def row(self) -> dict:
        return {
            "succeeded": self.succeeded,
            "gave_up": self.gave_up,
            "attempts": self.attempts,
            "upstream_calls": self.upstream_calls,
            "wasted_calls": self.wasted_calls,
            "wasted_upstream_s": round(self.wasted_seconds, 1),
            "p50_human_s": round(self.p(50, True), 2),
            "p95_human_s": round(self.p(95, True), 2),
            "credits_burned": self.credits_burned,
        }


def run_storm(spec: AgentSpec, *, weir: bool, seed: int = 11,
              workers: int = 8, service: float = 0.5,
              limit: int = 12, grant: int = 2 * CREDIT) -> Outcome:
    """One storm run against one router.  Deterministic for a given seed."""
    clock = VirtualClock()
    rng = random.Random(seed)
    up = Upstream(clock, workers=workers, service=service, rng=random.Random(seed + 1))
    out = Outcome("weir" if weir else "classic")

    router = None
    gw = None
    if weir:
        router = Router(RouterConfig(limit=limit, default_grant=grant,
                                     max_fanout=10_000), clock=clock)
        router.route(Route("agent.task", "upstream", price=100, latency=service))
    else:
        gw = ClassicGateway(clock, up, limit=limit)

    def launch(idx: int) -> None:
        stubborn = rng.random() < spec.noncompliant
        human = rng.random() < spec.human_fraction
        start_t = clock.now()
        deadline = start_t + spec.deadline
        root = new_root()
        state = {"attempts": 0, "done": False, "ticket": None}

        def still_wanted() -> bool:
            return not state["done"]

        def give_up() -> None:
            # The caller's own timeout fires.  Anything still in flight
            # upstream is now work that nobody will read.
            if not state["done"]:
                state["done"] = True
                out.gave_up += 1

        def succeed() -> None:
            if state["done"]:
                return
            state["done"] = True
            out.succeeded += 1
            lat = clock.now() - start_t
            out.latencies_all.append(lat)
            if human:
                out.latencies_human.append(lat)

        clock.at(deadline, give_up)

        def attempt() -> None:
            if state["done"] or state["attempts"] >= spec.max_attempts:
                return
            state["attempts"] += 1
            out.attempts += 1

            if weir:
                intent = Intent(
                    principal=f"did:web:lab#p{idx}",
                    agent=f"aas:64512/agent-{idx}",
                    capability="agent.task",
                    root=root,
                    deadline=deadline,
                    coupling="human" if human else "batch",
                    declared_cost=100,
                    budget=grant,
                    intent_digest=f"b2:unique-{idx}",   # coalescing off for this test
                )
                try:
                    adm = router.begin(intent, state["ticket"])
                except Refused as exc:
                    charged = exc.charged or 0
                    if charged:
                        router.ledger.charge_refusal(intent.root, intent.principal, charged)
                    out.credits_burned += charged
                    if exc.retry_not_before is not None:
                        state["ticket"] = router.damper.issue(
                            intent, exc.retry_not_before,
                            (Appointment.decode(state["ticket"]).attempt + 1)
                            if state["ticket"] and Appointment.decode(state["ticket"]) else 1
                        ).encode()
                    if exc.reason in Reason.RETRYABLE and exc.retry_not_before is not None:
                        wait = (exc.retry_not_before - clock.now()) if not stubborn \
                            else spec.stubborn_delay
                        clock.after(max(wait, 0.001), attempt)
                    else:
                        give_up()
                    return

                def done(svc, adm=adm):
                    adm.settle(observed=100)
                    succeed()

                up.submit(done, still_wanted)
            else:
                def on_ok(svc):
                    succeed()

                def on_refused(retry_after):
                    wait = retry_after * rng.uniform(0.5, 1.5) if not stubborn \
                        else spec.stubborn_delay
                    clock.after(wait, attempt)

                gw.submit(on_ok, on_refused, still_wanted)

        clock.after(rng.uniform(0.0, 0.5), attempt)

    for i in range(spec.n):
        launch(i)

    clock.run(until=clock.now() + spec.deadline * 4)

    out.upstream_calls = up.served
    out.wasted_calls = up.wasted
    out.wasted_seconds = up.wasted_seconds
    return out
