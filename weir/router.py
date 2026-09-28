"""The forwarding pipeline.

Stage order is not arbitrary.  It follows the same principle as an ACL fast
path: **do the cheapest, most terminal check first**, so that the traffic you
are going to refuse anyway is refused before it has cost you anything.  On an
agent network this matters more than it does on a packet network, because the
expensive thing is not the forwarding, it is the upstream inference, and a
refusal that happens after the upstream call has saved nothing at all.

    1  parse         - a few hundred bytes of ASCII, no body decode
    2  attest        - one keyed hash; everything downstream trusts these fields
    3  loop / depth  - pure header arithmetic, terminal, no shared state
    4  deadline      - one comparison; drops work that is already worthless
    5  terms         - origin policy, before the origin is contacted at all
    6  route         - capability lookup, needed to price anything
    7  budget        - ledger reservation, first contended lock
    8  damper        - admission and appointments
    9  coalesce      - dedupe, may avoid the upstream entirely
    10 forward       - the only expensive step
    11 settle        - reconcile declared against observed cost
    12 receipt       - record the decision either way

Stages 1-8 are all sub-millisecond and touch no network.  A request from a
looping agent with an expired deadline and an empty budget is refused at stage
3 having consumed roughly the cost of parsing an HTTP header - which, when the
alternative is a forty-second inference call, is the entire point.

Note that budget reservation (7) comes *before* admission (8): a request that
cannot be paid for should never occupy a queue slot, or a bankrupt swarm can
still deny service to a solvent one.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .attest import Keyring
from .clock import RealClock
from .coalesce import Coalescer
from .damper import Appointment, Damper
from .errors import Reason, Refused
from .fib import Fib, Route
from .intent import CREDIT, Hop, Intent
from .ledger import InsufficientBudget, Ledger
from .pathvec import PathGuard
from .receipts import ReceiptLog
from .sched import DeadlineScheduler
from .terms import TermsRegistry


@dataclass(slots=True)
class Decision:
    """The outcome of one forwarding decision."""

    ok: bool
    intent: Intent
    route: Route | None = None
    result: Any = None
    disposition: str = ""          # forwarded | coalesced | cached
    reason: str = ""
    detail: str = ""
    retry_not_before: float | None = None
    ticket: str = ""
    credits: int = 0
    status: int = 200

    @property
    def refused(self) -> bool:
        return not self.ok


@dataclass(slots=True)
class RouterConfig:
    aas: int = 64512
    name: str = "weir-0"
    limit: int = 32
    refusal_cost: int = 5
    max_fanout: int = 16
    default_grant: int = 100 * CREDIT
    require_attestation: bool = False
    coalesce_ttl: float = 30.0
    public_capabilities: frozenset[str] = frozenset()
    receipt_path: str | None = None


class Router:
    """A weir router.

    Transport-agnostic on purpose.  :mod:`weir.server` wraps this in HTTP/1.1
    and :mod:`weir.sim` drives it with synthetic swarms; the forwarding logic
    knows about neither.
    """

    def __init__(self, config: RouterConfig | None = None, *, clock=None,
                 key: bytes = b"weir-dev-key-not-for-production!!") -> None:
        self.config = config or RouterConfig()
        self.clock = clock or RealClock()
        self.key = key

        self.fib = Fib()
        self.keyring = Keyring(required=self.config.require_attestation)
        self.paths = PathGuard()
        self.ledger = Ledger(self.clock, default_grant=self.config.default_grant)
        self.damper = Damper(self.clock, key, limit=self.config.limit,
                             refusal_cost=self.config.refusal_cost)
        self.sched = DeadlineScheduler(self.clock)
        self.coalescer = Coalescer(self.clock, ttl=self.config.coalesce_ttl,
                                   public=self.config.public_capabilities)
        self.terms = TermsRegistry(self.clock)
        self.receipts = ReceiptLog(self.clock, path=self.config.receipt_path)

        self.here = Hop(self.config.aas, self.config.name)
        self._fanout: dict[tuple[str, str], int] = {}
        self._fanout_peak = 0
        self._fanout_lock = threading.Lock()
        self.counters = {
            "seen": 0, "forwarded": 0, "refused": 0,
            "coalesced": 0, "cached": 0, "credits_settled": 0,
        }

    # -- configuration -------------------------------------------------

    def route(self, *args, **kwargs) -> "Router":
        """Add a route; returns self so config reads as a chain."""
        self.fib.add(args[0] if args and isinstance(args[0], Route) else Route(*args, **kwargs))
        return self

    # -- the pipeline --------------------------------------------------

    def begin(self, intent: Intent, ticket: str | None = None) -> "Admission":
        """Run stages 1-8 and reserve capacity.  Raises :class:`Refused`.

        Split out from :meth:`forward` because occupancy must be held for the
        real lifetime of the upstream call.  A synchronous transport can let
        the call stack express that; an event-driven one cannot, and if the
        router settles as soon as it has handed the request off, its
        concurrency limit measures nothing and admits everything.  Any async
        data plane calls ``begin`` and then :meth:`Admission.settle` when the
        upstream actually answers.
        """
        self.counters["seen"] += 1

        if not self.keyring.verify(intent):
            raise Refused(Reason.BAD_ATTESTATION,
                          f"no valid attestation from AAS {intent.aas}")

        self.paths.check(intent, self.here)
        fanout_key = self._fanout_enter(intent)

        # Every stage past this point holds the fan-out slot, so each of them
        # has to give it back on the way out.
        try:
            self.sched.check_feasible(intent, queue_wait=self._queue_wait())

            route = self.fib.lookup(intent, self.clock.now())
            price = self.terms.check(intent, route.origin)

            cost = max(intent.declared_cost, route.price + price)
            try:
                reserved = self.ledger.reserve(intent.root, intent.principal, cost)
            except InsufficientBudget as exc:
                raise Refused(Reason.BUDGET_EXHAUSTED, str(exc)) from exc

            try:
                self.damper.admit(intent, ticket)
            except Refused:
                self.ledger.settle(intent.root, reserved, 0)
                raise
        except Refused:
            self._fanout_exit(fanout_key)
            raise

        return Admission(router=self, intent=intent, route=route,
                         reserved=reserved, observed=route.price + price,
                         started=self.clock.now(), fanout_key=fanout_key)

    def forward(self, intent: Intent, ticket: str | None = None, *,
                upstream: Callable[[Intent, Route], Any] | None = None) -> Decision:
        """Synchronous forwarding: admit, call upstream, settle.

        This is the path the HTTP data plane uses, where a thread per
        connection makes the call stack the natural place to hold occupancy.
        """
        try:
            adm = self.begin(intent, ticket)
        except Refused as exc:
            return self._refuse(intent, exc)

        failed = False
        try:
            def work():
                if upstream is None:
                    return {"ok": True, "upstream": adm.route.upstream}
                return upstream(intent.forwarded(self.here, adm.observed), adm.route)

            result, disposition = self.coalescer.run(intent, work)
        except Refused as exc:
            adm.settle(observed=0, failed=True)
            return self._refuse(intent, exc)
        except Exception:
            adm.settle(observed=0, failed=True)
            raise

        # A coalesced or cached answer cost no upstream work, so it is settled
        # at zero: charging for a call that was never made is how a meter
        # loses the right to be believed.
        return adm.settle(result=result,
                          observed=adm.observed if disposition == "miss" else 0,
                          disposition=disposition)

    # -- refusal path --------------------------------------------------

    def _refuse(self, intent: Intent, exc: Refused) -> Decision:
        charged = exc.charged if exc.charged else (
            self.config.refusal_cost if exc.retryable else 0)
        if charged:
            self.ledger.charge_refusal(intent.root, intent.principal, charged)
        self.counters["refused"] += 1
        self.receipts.record(intent, "refused", reason=exc.reason, credits=charged)

        ticket = ""
        if exc.retry_not_before is not None:
            attempt = 1
            ticket = self.damper.issue(intent, exc.retry_not_before, attempt).encode()

        return Decision(ok=False, intent=intent, reason=exc.reason, detail=exc.detail,
                        retry_not_before=exc.retry_not_before, ticket=ticket,
                        credits=charged, status=exc.status)

    # -- helpers -------------------------------------------------------

    def _fanout_enter(self, intent: Intent) -> tuple:
        """Bound the width of any single node's fan-out.

        Depth is bounded by ``depth`` and total spend by the ledger, but a
        single node issuing ten thousand siblings at once can still bury a
        downstream before the ledger notices.  Width needs its own bound.

        Width is a measure of **concurrency, not of cumulative volume**.  A
        counter that only ever goes up cannot tell a 40-wide fan-out from an
        agent that made forty calls over an afternoon, and throttling the
        second one is both wrong and the kind of wrong that shows up in
        production a week after everyone stopped watching.  So the count is of
        calls currently outstanding, and :meth:`_fanout_exit` releases it.

        Keyed on the *delegator*, not the executor: what needs bounding is how
        many calls one node issues, and keying on the callee instead counts a
        popular downstream's inbound traffic and throttles the wrong party.
        """
        issuer = str(intent.path[0]) if intent.path else intent.agent
        key = (intent.root, issuer)
        with self._fanout_lock:
            n = self._fanout.get(key, 0) + 1
            self._fanout[key] = n
            self._fanout_peak = max(self._fanout_peak, n)
        if n > self.config.max_fanout:
            self._fanout_exit(key)
            raise Refused(Reason.FANOUT_EXCEEDED,
                          f"{issuer} has {n} calls in flight under root "
                          f"{intent.root[:8]} (width limit {self.config.max_fanout})")
        return key

    def _fanout_exit(self, key: tuple) -> None:
        with self._fanout_lock:
            n = self._fanout.get(key, 0) - 1
            if n > 0:
                self._fanout[key] = n
            else:
                self._fanout.pop(key, None)

    def _queue_wait(self) -> float:
        d = self.damper
        over = max(0, d.inflight - int(d.limit))
        return over * (d._service_estimate / max(d.limit, 1.0))

    def stats(self) -> dict[str, Any]:
        return {
            "router": {"aas": self.config.aas, "name": self.config.name, **self.counters},
            "damper": self.damper.stats(),
            "ledger": self.ledger.stats(),
            "sched": self.sched.stats(),
            "coalesce": self.coalescer.stats(),
            "receipts": {"count": self.receipts._seq, "tip": self.receipts.tip[:14]},
        }


@dataclass(slots=True)
class Admission:
    """Capacity held for one in-flight request.

    Exactly one of :meth:`settle` must be called, or the router leaks both a
    damper slot and a ledger reservation.  The synchronous path guarantees this
    with try/finally; an async transport has to be careful, which is why the
    object exists rather than a pair of loose calls.
    """

    router: "Router"
    intent: Intent
    route: Route
    reserved: int
    observed: int
    started: float
    fanout_key: tuple | None = None
    done: bool = False

    def settle(self, *, result: Any = None, observed: int | None = None,
               failed: bool = False, disposition: str = "miss") -> Decision:
        if self.done:
            raise RuntimeError("admission settled twice")
        self.done = True
        r = self.router
        if self.fanout_key is not None:
            r._fanout_exit(self.fanout_key)

        latency = r.clock.now() - self.started
        r.damper.complete(latency, failed=failed)

        cost = self.observed if observed is None else observed
        r.ledger.settle(self.intent.root, self.reserved, cost)
        r.counters["credits_settled"] += cost

        name = {"miss": "forwarded", "joined": "coalesced", "hit": "cached"}[disposition]
        if failed:
            name = "forwarded"
        r.counters[name] += 1
        r.receipts.record(self.intent, name, credits=cost)
        return Decision(ok=True, intent=self.intent, route=self.route, result=result,
                        disposition=name, credits=cost)
