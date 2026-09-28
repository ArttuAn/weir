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

    def __post_init__(self) -> None:
        if not self.root:
            self.root = new_root()

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

        while True:
            intent = Intent(
                principal=self.principal, agent=self.agent, capability=capability,
                root=self.root, deadline=deadline, coupling=coupling,
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
                    self.spent += int(resp.headers.get("weir-credits") or 0)
                    return out
            except urllib.error.HTTPError as exc:
                body = json.loads(exc.read() or b"{}")
                self.spent += int(exc.headers.get("weir-credits") or 0)
                nb = exc.headers.get("weir-retry-not-before")
                ticket = exc.headers.get("weir-appointment") or ticket

                if nb is None or waits >= self.max_waits:
                    raise CallFailed(body.get("reason", "refused"),
                                     body.get("detail", "")) from None

                wait = float(nb) - self.clock.now()
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
