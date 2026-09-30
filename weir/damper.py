"""Congestion control for callers that do not back off.

TCP's congestion control works because of an assumption that has held since
1988: the endpoint wants the network to survive, so when it sees loss it slows
down.  AIMD is not enforced anywhere.  It is a convention, kept by everyone
because everyone ships the same stacks.

Agents break the assumption in a way that no previous traffic class did.  Not
out of malice - out of instruction.  Every agent framework ships a retry
decorator, every runbook says "retry on 5xx with backoff", and the agent has no
view of the hundred siblings doing the same thing against the same upstream at
the same moment.  HTTP 429 does not fix this because **429 is free**.  Ignoring
it costs the sender one connection and gains it a chance of being served, so
the locally rational move for every agent independently is to retry
immediately, and the aggregate is the retry storm that turns a brownout into an
outage.

Weir changes the payoff rather than repeating the request:

1.  **Refusals are metered.**  A refused request debits the root's ledger.  Not
    much - a refusal is cheap to produce - but not nothing, and the debit
    escalates geometrically per early retry.  An agent that ignores
    backpressure runs out of budget before the upstream runs out of capacity,
    and it runs out at the edge, which is the cheapest place on the network to
    say no.

2.  **Refusals carry an appointment, not an apology.**  The reason retry storms
    exist at all is that a rejection carries no scheduling information, so
    every caller has to *guess* when to come back, and jitter is just a way of
    making everyone guess differently.  A weir refusal names the time: an
    ``Appointment`` is a signed bearer ticket for a slot the router has
    actually set aside.  Present it at its time and you are admitted ahead of
    unticketed traffic.  So waiting is not merely cheaper than racing, it is
    *faster* than racing.

Once honouring backpressure is both cheaper and faster than ignoring it, the
storm stops being the rational strategy, and no agent had to be well-behaved
for that to happen.  That is the difference between a convention and a
mechanism.

The appointment is signed and self-contained, so verification needs no
per-caller state and a cluster of weir routers behind one anycast address can
honour each other's tickets.  The slot book itself is per-router.
"""

from __future__ import annotations

import hmac
import threading
from dataclasses import dataclass
from hashlib import blake2b

from .errors import Reason, Refused
from .intent import Intent


@dataclass(frozen=True, slots=True)
class Appointment:
    """A signed, bearer-token slot reservation."""

    not_before: float
    attempt: int
    issued: float
    mac: str

    def encode(self) -> str:
        return f"{self.not_before:.3f}|{self.attempt}|{self.issued:.3f}|{self.mac}"

    @staticmethod
    def decode(text: str) -> "Appointment | None":
        try:
            nb, att, iss, mac = text.split("|")
            return Appointment(float(nb), int(att), float(iss), mac)
        except (ValueError, AttributeError):
            return None


class Damper:
    """Adaptive admission control plus appointment issuing.

    The concurrency limit is adapted the way a router *can* adapt it - from its
    own observation of upstream latency - rather than by hoping senders react
    to drops.  Additive increase while the upstream is healthy, multiplicative
    decrease when queueing delay shows up, which is Vegas-style gradient
    control moved to the one box that can actually enforce the outcome.
    """

    def __init__(self, clock, key: bytes, *, limit: int = 32, min_limit: int = 2,
                 max_limit: int = 4096, target_latency: float = 0.750,
                 refusal_cost: int = 5, penalty_cap: int = 640,
                 grace: float = 0.050, horizon: float = 2.0,
                 ticket_overshoot: int = 2, human_reserve: float = 0.4) -> None:
        self._clock = clock
        self._key = key
        self._lock = threading.Lock()

        self.limit = float(limit)
        self.min_limit = min_limit
        self.max_limit = max_limit
        self.target_latency = target_latency
        self.refusal_cost = refusal_cost
        self.penalty_cap = penalty_cap
        #: Clock skew and flight time allowance.  A caller that is 30 ms early
        #: is punctual; only a caller that is meaningfully early is racing.
        self.grace = grace
        #: How far ahead the slot book may be written.  Past this the
        #: router hands out re-evaluation points instead of promises.
        self.horizon = horizon
        #: Slots a ticket holder may exceed the limit by, so an
        #: over-promised appointment is still mostly honoured.
        self.ticket_overshoot = ticket_overshoot
        #: Share of the concurrency limit that only human-coupled traffic may
        #: use.  Batch work is capped at the remainder, so a batch swarm can
        #: saturate the router without ever occupying the last slots a waiting
        #: person needs.  This is the admission-control half of the scheduling
        #: policy in ``sched.py``; without it the priority is a preference
        #: nobody enforces.
        self.human_reserve = human_reserve

        self.inflight = 0
        self._base_latency = target_latency / 2
        self._ewma_latency = target_latency / 2
        #: End of the last handed-out slot; the slot book.
        #: Separate books per class, so a deep batch queue never pushes a
        #: human-coupled appointment further out.
        self._slot_cursor = 0.0
        self._slot_cursor_batch = 0.0
        self._service_estimate = target_latency / 2

        self._window_count = 0
        self._window_min = float("inf")
        self._window_failed = False

        self.admitted = 0
        self.refused = 0
        self.shed = 0
        self.reneged = 0
        self.early_retries = 0
        self.appointments_kept = 0
        self.credits_burned = 0

    # -- ticket crypto -------------------------------------------------

    def _mac(self, intent: Intent, not_before: float, attempt: int, issued: float) -> str:
        msg = f"{intent.root}|{intent.agent}|{intent.capability}|{not_before:.3f}|{attempt}|{issued:.3f}"
        return blake2b(msg.encode(), key=self._key, digest_size=12).hexdigest()

    def _valid(self, intent: Intent, appt: Appointment) -> bool:
        expect = self._mac(intent, appt.not_before, appt.attempt, appt.issued)
        return hmac.compare_digest(expect, appt.mac)

    # -- slot book -----------------------------------------------------

    def _book(self, intent: Intent, now: float) -> tuple[float, bool]:
        """Find this caller a time.  Returns ``(when, committed)``.

        Two things a naive slot book gets wrong, both learned the hard way in
        ``sim.py``:

        **It must not write far into the future.**  A book written at t=0 is
        priced with t=0's estimate of the upstream, and that estimate is worst
        exactly when the book is longest - during the burst that made everyone
        queue.  Promising three hundred callers a slot at 32/s against an
        upstream that turns out to do 9/s is not scheduling, it is
        over-subscription with extra steps.  So the book has a horizon.  Beyond
        it the router returns a *re-evaluation point* rather than a promise:
        come back at the horizon and we will price you against what we know
        then.  Over-promising is bounded by horizon x rate rather than
        unbounded.

        **It must not book past a deadline.**  A slot the caller cannot use is
        worse than a refusal, because it converts an immediate failure into a
        caller that waits and then fails anyway.

        The re-evaluation point carries deterministic jitter derived from the
        root id, which spreads the returning herd without needing randomness -
        the same appointment computed twice is the same appointment.
        """
        human = intent.human_coupled
        drain = self._service_estimate / max(self.limit, 1.0)
        cursor_now = self._slot_cursor if human else self._slot_cursor_batch
        cursor = max(cursor_now, now + drain)

        if cursor - now > self.horizon:
            spread = (int(intent.root[-4:] or "0", 36) % 997) / 997.0
            when = now + self.horizon * (1.0 + 0.5 * spread)
            if when >= intent.deadline:
                self.shed += 1
                raise Refused(Reason.INFEASIBLE,
                              f"no capacity within {self.horizon:.1f}s horizon and "
                              f"only {intent.deadline - now:.2f}s of deadline left")
            return when, False

        if cursor >= intent.deadline:
            self.shed += 1
            raise Refused(Reason.INFEASIBLE,
                          f"earliest slot is {cursor - now:.2f}s out, "
                          f"deadline is {intent.deadline - now:.2f}s out")

        if human:
            self._slot_cursor = cursor + drain
        else:
            self._slot_cursor_batch = cursor + drain
        return cursor, True


    # -- the fast path -------------------------------------------------

    def _ceiling(self, intent: Intent) -> int:
        """Concurrency this request's class may occupy.

        Human-coupled work may use the whole limit; batch work may not touch
        the reserved share.  The reservation is what makes the priority real:
        a scheduler that merely orders a queue still lets a batch swarm hold
        every slot, and then the person waits for a nightly job to finish.
        """
        if intent.human_coupled:
            return max(1, int(self.limit))
        return max(1, int(self.limit * (1.0 - self.human_reserve)))

    def admit(self, intent: Intent, ticket: str | None) -> Appointment | None:
        """Decide whether ``intent`` may proceed to the upstream.

        Returns ``None`` when admitted.  Raises :class:`Refused` carrying an
        appointment when the caller must wait.
        """
        now = self._clock.now()
        appt = Appointment.decode(ticket) if ticket else None
        if appt is not None and not self._valid(intent, appt):
            # A forged or tampered ticket is treated as no ticket, not as an
            # error: the caller may simply be talking to a different cluster.
            appt = None

        with self._lock:
            if appt is not None:
                if now + self.grace < appt.not_before:
                    # Racing its own appointment.  Refuse, charge, and reissue
                    # the *same* slot: an early retry cannot move you forward
                    # in the queue, it can only cost you.
                    self.early_retries += 1
                    penalty = min(self.refusal_cost * (2 ** appt.attempt), self.penalty_cap)
                    self.credits_burned += penalty
                    raise Refused(Reason.EARLY_RETRY,
                                  f"appointment is at {appt.not_before:.3f}, "
                                  f"arrived {appt.not_before - now:.3f}s early",
                                  retry_not_before=appt.not_before,
                                  charged=penalty)

                # Punctual.  An appointment buys priority over unticketed
                # traffic, but it cannot buy capacity that does not exist: if
                # the router over-promised, dumping the ticket holder into a
                # saturated upstream serves nobody.  A small overshoot honours
                # the promise where it can; past that the caller is rebooked at
                # no charge, because it did exactly what it was asked to do.
                if self.inflight < self._ceiling(intent) + self.ticket_overshoot:
                    self.appointments_kept += 1
                    self.admitted += 1
                    self.inflight += 1
                    return None
                self.reneged += 1
                when, _ = self._book(intent, now)
                raise Refused(Reason.CONGESTED,
                              f"appointment honoured but upstream is at "
                              f"{self.inflight}/{int(self.limit)}; rebooked",
                              retry_not_before=when, charged=0)

            ceiling = self._ceiling(intent)
            if self.inflight < ceiling:
                self.admitted += 1
                self.inflight += 1
                return None

            when, committed = self._book(intent, now)
            self.refused += 1
            self.credits_burned += self.refusal_cost
            raise Refused(Reason.CONGESTED,
                          f"upstream at capacity ({self.inflight}/{ceiling} for "
                          f"{intent.coupling}), "
                          + (f"slot held for {when - now:.3f}s" if committed
                             else f"re-evaluate in {when - now:.3f}s"),
                          retry_not_before=when,
                          charged=self.refusal_cost)


    def _sign(self, intent: Intent, not_before: float, attempt: int, issued: float) -> Appointment:
        return Appointment(not_before, attempt, issued,
                           self._mac(intent, not_before, attempt, issued))

    def issue(self, intent: Intent, not_before: float, attempt: int = 1) -> Appointment:
        """Public ticket minting, used by the pipeline for non-damper refusals."""
        now = self._clock.now()
        return self._sign(intent, not_before, attempt, now)

    # -- feedback ------------------------------------------------------

    def complete(self, latency: float, *, failed: bool = False) -> None:
        """Report an upstream result.  This is where the limit adapts.

        Gradient control, evaluated **once per window of ``limit``
        completions** rather than per completion.  That distinction is not a
        detail: applying a multiplicative decrease on every completion during a
        latency excursion collapses the limit to its floor in a handful of
        samples, and the router then starves an upstream that was merely busy.
        One adjustment per window is the same "once per round trip" discipline
        that keeps TCP's AIMD stable.

        The signal is the ratio of the best latency the upstream has shown to
        what it is showing now.  Using a ratio rather than an absolute target
        means the router discovers the upstream's healthy latency instead of
        being told it, which matters when one FIB entry fronts a 200 ms search
        endpoint and another a 30 s reasoning model.
        """
        with self._lock:
            self.inflight = max(0, self.inflight - 1)

            a = 0.2
            self._ewma_latency = (1 - a) * self._ewma_latency + a * latency
            self._service_estimate = max(1e-3, self._ewma_latency)

            # Decaying minimum: the best latency seen recently, allowed to
            # drift up so a single fast sample at startup does not pin the
            # baseline forever.
            if latency < self._base_latency:
                self._base_latency = latency
            else:
                self._base_latency *= 1.0005
            self._base_latency = max(1e-3, self._base_latency)

            self._window_min = min(self._window_min, latency)
            self._window_failed |= failed
            self._window_count += 1
            if self._window_count < max(1, int(self.limit)):
                return

            sample = max(self._window_min, 1e-3)
            failed_window = self._window_failed
            self._window_count = 0
            self._window_min = float("inf")
            self._window_failed = False

            if failed_window:
                self.limit = max(self.min_limit, self.limit * 0.8)
                return

            # gradient < 1 means we are queueing somewhere downstream.
            gradient = max(0.5, min(1.0, self._base_latency / sample))
            headroom = self.limit ** 0.5
            target = self.limit * gradient + headroom
            self.limit = max(self.min_limit,
                             min(self.max_limit, self.limit * 0.8 + target * 0.2))

    def stats(self) -> dict[str, float]:
        return {
            "limit": round(self.limit, 1),
            "inflight": self.inflight,
            "admitted": self.admitted,
            "refused": self.refused,
            "shed": self.shed,
            "reneged": self.reneged,
            "early_retries": self.early_retries,
            "appointments_kept": self.appointments_kept,
            "credits_burned": self.credits_burned,
            "ewma_latency_ms": round(self._ewma_latency * 1000, 1),
        }
