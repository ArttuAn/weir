"""Agent-side SDK.

The point of this file is that being a well-behaved agent should be *less* code
than being a badly-behaved one, not more.  :meth:`Caller.call` honours
appointments, tracks remaining budget, derives children that cannot outspend or
outlive their parent, and carries the delegation path - and it is shorter than
the average hand-rolled retry decorator.

If the compliant path is harder than the greedy one, everyone writes the greedy
one, and the network mechanisms end up carrying load they were meant to
prevent.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .clock import RealClock
from .intent import CREDIT, Intent, digest_of, new_root, to_headers


class CallFailed(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass
class Caller:
    """An agent talking through a weir router."""

    router_url: str
    principal: str
    agent: str
    budget: int = 10 * CREDIT
    clock: object = field(default_factory=RealClock)
    max_waits: int = 8

    root: str = ""
    spent: int = 0
    #: Router clock minus our clock, learned from `weir-now` on any response.
    #: Deadlines are absolute and are compared against the *router's* clock, so
    #: a caller whose clock is off sends a deadline that means something other
    #: than it intended. See docs/PHYSICAL.md, "Time".
    clock_offset: float = 0.0

    def __post_init__(self) -> None:
        if not self.root:
            self.root = new_root()

    def _learn_clock(self, headers) -> None:
        """Update our estimate of the router's clock from `weir-now`.

        Crude on purpose: no round-trip compensation, because the correction we
        need is on the order of a deadline (seconds) and the flight time is on
        the order of milliseconds. Over-engineering this would buy accuracy
        nobody can use.
        """
        raw = headers.get("weir-now")
        if raw:
            try:
                self.clock_offset = float(raw) - self.clock.now()
            except ValueError:
                pass

    def call(self, capability: str, payload: dict, *, deadline: float | None = None,
             coupling: str = "batch", cost: int = 100, safe: bool = True,
             path: tuple = ()) -> dict:
        """Make one call, honouring whatever the router says.

        Waiting for an appointment is the whole retry policy.  There is no
        backoff curve to tune because the router already knows when it will
        have room, and guessing is what caused the storm.
        """
        if deadline is None:
            deadline = self.clock.now() + 30.0
        ticket = None
        waits = 0
        reclocked = False

        while True:
            intent = Intent(
                principal=self.principal, agent=self.agent, capability=capability,
                root=self.root, deadline=deadline + self.clock_offset,
                coupling=coupling,
                budget=max(0, self.budget - self.spent), declared_cost=cost,
                safe=safe, path=path,
                intent_digest=digest_of(capability, payload),
            )
            headers = {**to_headers(intent), "content-type": "application/json"}
            if ticket:
                headers["weir-appointment"] = ticket

            req = urllib.request.Request(self.router_url, method="POST",
                                         data=json.dumps(payload).encode(),
                                         headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    out = json.loads(resp.read())
                    self._learn_clock(resp.headers)
                    self.spent += int(resp.headers.get("weir-credits") or 0)
                    return out
            except urllib.error.HTTPError as exc:
                body = json.loads(exc.read() or b"{}")
                self._learn_clock(exc.headers)
                self.spent += int(exc.headers.get("weir-credits") or 0)
                nb = exc.headers.get("weir-retry-not-before")
                ticket = exc.headers.get("weir-appointment") or ticket

                reason = body.get("reason", "refused")

                # Cold start with a wrong clock. Deadlines are absolute and are
                # read against the router's clock, so the first call from a
                # caller whose clock is off carries a deadline that means
                # something other than it intended - and it has no way to know
                # that until the router tells it. We have just been told. Apply
                # the offset we now know and try once more, rather than failing
                # forever on a misconfiguration we can see.
                if (reason in ("deadline-passed", "deadline-infeasible")
                        and not reclocked and abs(self.clock_offset) > 1.0):
                    reclocked = True
                    continue

                if nb is None or waits >= self.max_waits:
                    raise CallFailed(reason, body.get("detail", "")) from None

                # Both terms come from the router's clock, so the wait is a pure
                # delta and our own skew cancels out of it entirely. Then we
                # sleep that delta on our own clock, which is the one thing our
                # clock is reliable for.
                router_now = exc.headers.get("weir-now")
                wait = (float(nb) - float(router_now)) if router_now \
                    else (float(nb) - self.clock.now())
                if self.clock.now() + max(wait, 0.0) >= deadline:
                    # The slot is past our own deadline.  Fail now rather than
                    # sleep into a certain timeout.
                    raise CallFailed("deadline-infeasible",
                                     f"next slot is {wait:.2f}s out") from None
                waits += 1
                self.clock.sleep(max(wait, 0.0))

    def fanout(self, n: int, *, reserve: float = 0.2) -> int:
        """Per-child budget for an ``n``-way fan-out.

        Held back by ``reserve`` so the parent can still pay to merge the
        results it is about to receive.
        """
        left = max(0, self.budget - self.spent)
        return int(left * (1.0 - reserve)) // max(1, n)
