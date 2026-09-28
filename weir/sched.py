"""Scheduling by deadline and by who is waiting.

DiffServ gives a router 64 code points, of which most networks use about six,
and the class is a property of the *flow* - set once, by configuration, by
somebody who decided a year ago that this port is "interactive".  It works
because human-facing traffic is recognisable by protocol: video is video, VoIP
is VoIP.

Agent traffic is not recognisable by protocol.  A request that a person is
sitting and waiting for, and a request from a nightly batch crawl, are the same
verb to the same endpoint with the same payload shape over the same TLS
connection to the same model.  There is no port number that distinguishes them
and no deep packet inspection that can recover the difference, because the
difference is not in the packet.  It is in whether a human is blocked.

So weir carries it explicitly (``coupling``) and schedules on it, together with
the deadline the caller declares:

**Earliest deadline first, human-coupled class first.**  EDF is optimal for
meeting deadlines on a single resource when the system is feasible, which is
the property you actually want; strict priority between two EDF classes keeps a
batch swarm from ever delaying a person, which is the property you want when it
is not.

**Expiry before execution.**  This is the mechanism with the largest practical
payoff and it barely exists today.  On an agent network an enormous share of
in-flight work is already worthless: the caller timed out, took the fallback
path and moved on, but nothing told the upstream, so the tokens are still being
generated for an answer no one will ever read.  A router holding a deadline can
see this.  Weir drops any request whose deadline has passed *before* forwarding
it, and refuses any request it can see will miss - queue wait plus expected
service exceeding the deadline - rather than starting work that will be thrown
away.  Failing at the edge in microseconds beats failing at the upstream after
forty seconds of inference, and it is the same failure to the caller.

Starvation is bounded by ``batch_floor``: a fraction of admissions is reserved
for batch work so that a permanent stream of human traffic cannot stall the
nightly job forever.  Strict priority without a floor is how you discover, six
months later, that the reindex has not run since March.
"""

from __future__ import annotations

import heapq
import itertools
import threading
from dataclasses import dataclass

from .errors import Reason, Refused
from .intent import Intent


@dataclass(order=True, slots=True)
class _Entry:
    key: tuple
    seq: int
    intent: object = None


class DeadlineScheduler:
    """Two-class EDF queue with feasibility admission.

    Not a work queue in the live proxy sense - the HTTP data plane admits and
    forwards synchronously - but the ordering discipline and the feasibility
    test are used by both the live path and the simulator, so they live here.
    """

    def __init__(self, clock, *, batch_floor: float = 0.15,
                 service_estimate: float = 0.4) -> None:
        self._clock = clock
        self._heap: list[_Entry] = []
        self._seq = itertools.count()
        self._lock = threading.Lock()
        self.batch_floor = batch_floor
        self.service_estimate = service_estimate

        self.admitted_human = 0
        self.admitted_batch = 0
        self.expired_before_execution = 0
        self.infeasible = 0

    # -- admission -----------------------------------------------------

    def check_feasible(self, intent: Intent, queue_wait: float = 0.0) -> None:
        """Reject work that is already worthless, or provably will be.

        Raises :class:`Refused`.  Both reasons are terminal: a deadline that
        has passed does not come back, and retrying into the same queue will
        miss by more.
        """
        now = self._clock.now()
        if intent.deadline <= now:
            self.expired_before_execution += 1
            raise Refused(Reason.DEADLINE_PASSED,
                          f"deadline passed {now - intent.deadline:.3f}s ago; "
                          "not forwarding work nobody is waiting for")
        projected = now + queue_wait + self.service_estimate
        if projected > intent.deadline:
            self.infeasible += 1
            raise Refused(Reason.INFEASIBLE,
                          f"would finish {projected - intent.deadline:.3f}s late "
                          f"(queue {queue_wait:.3f}s + service {self.service_estimate:.3f}s)")

    # -- ordering ------------------------------------------------------

    def push(self, intent: Intent) -> None:
        with self._lock:
            # Class first (0 = human-coupled), deadline second.  Ties broken by
            # arrival order so the discipline is stable.
            cls = 0 if intent.human_coupled else 1
            heapq.heappush(self._heap,
                           _Entry((cls, intent.deadline), next(self._seq), intent))

    def pop(self) -> Intent | None:
        """Next request to run, dropping any that expired while queued."""
        now = self._clock.now()
        with self._lock:
            while self._heap:
                entry = heapq.heappop(self._heap)
                intent: Intent = entry.intent  # type: ignore[assignment]
                if intent.deadline <= now:
                    self.expired_before_execution += 1
                    continue
                if intent.human_coupled:
                    self.admitted_human += 1
                else:
                    self.admitted_batch += 1
                return intent
            return None

    def pop_with_floor(self) -> Intent | None:
        """As :meth:`pop`, but honours the batch starvation floor."""
        with self._lock:
            total = self.admitted_human + self.admitted_batch
            batch_share = self.admitted_batch / total if total else 1.0
            starving = batch_share < self.batch_floor and any(
                not e.intent.human_coupled for e in self._heap)  # type: ignore[union-attr]
        if not starving:
            return self.pop()

        now = self._clock.now()
        with self._lock:
            batch = [e for e in self._heap if not e.intent.human_coupled]  # type: ignore[union-attr]
            if not batch:
                pass
            else:
                pick = min(batch, key=lambda e: e.key[1])
                self._heap.remove(pick)
                heapq.heapify(self._heap)
                intent: Intent = pick.intent  # type: ignore[assignment]
                if intent.deadline > now:
                    self.admitted_batch += 1
                    return intent
                self.expired_before_execution += 1
        return self.pop()

    def __len__(self) -> int:
        return len(self._heap)

    def stats(self) -> dict[str, int]:
        return {
            "queued": len(self._heap),
            "admitted_human": self.admitted_human,
            "admitted_batch": self.admitted_batch,
            "expired_before_execution": self.expired_before_execution,
            "infeasible": self.infeasible,
        }
