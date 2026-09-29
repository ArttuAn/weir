"""Budget as a forwarding field.

A classical router meters bytes because bytes were the scarce thing.  On an
agent network the correlation between bytes and cost is not weak, it is
inverted: a 300-byte request that says "research this and cite twelve sources"
commissions more compute than a 4 MB file transfer, and the cheap-looking one
is the one that fans out.  Metering bytes on agent traffic is metering the
wrong noun.

Weir meters *credits*, and it does so with one structural rule:

    Budget belongs to the root request, not to the hop.

Every descendant of one human action carries the same ``root``.  The router
keeps a ledger per root and debits it at every hop, wherever in the tree that
hop occurs.  The header's ``budget`` field is the caller's view and is treated
as advisory - a hint for the caller's own planning - exactly as a sender's idea
of TTL is advisory.  The ledger is the truth.

The consequence is the property that matters, and it is worth stating plainly
because it is the whole reason this design exists:

    An agent swarm cannot spend more than its root was granted, no matter how
    it fans out, how deep it recurses, how many peers it recruits, or how badly
    its authors got the termination condition wrong.

Fan-out does not multiply the budget, it divides it.  A node that splits into
five children gives each a share of what it holds; five levels of that is not
5^5 = 3125 funded calls, it is 3125 calls competing for one root's credits, of
which the ledger funds as many as the grant covers and refuses the rest at the
edge - cheaply, without touching any upstream.  Exponential fan-out becomes
self-limiting as an arithmetic property rather than as a thing every agent
author has to remember.

Reservation, not deduction
--------------------------
Admission *reserves* the declared cost; settlement replaces the reservation
with the observed cost.  A hop that declares 500 and burns 2000 is settled at
2000 and the overrun is charged to the root.  A hop that declares 500 and burns
50 releases 450 back.  Without reservation, concurrent siblings each see the
full balance and collectively overcommit it - the classic double-spend, which
on this network shows up as a bill.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class RootAccount:
    root: str
    granted: int
    reserved: int = 0
    settled: int = 0
    refused: int = 0          # credits burned on refusals (see damper.py)
    hops: int = 0
    overruns: int = 0
    opened_at: float = 0.0
    #: Last time this root was touched. Collection is driven by this, not
    #: by ``opened_at`` - see :meth:`Ledger.expire`.
    last_seen: float = 0.0

    @property
    def committed(self) -> int:
        return self.reserved + self.settled + self.refused

    @property
    def available(self) -> int:
        return max(0, self.granted - self.committed)


class InsufficientBudget(Exception):
    def __init__(self, account: RootAccount, wanted: int) -> None:
        super().__init__(
            f"root {account.root[:8]} has {account.available} of {account.granted} "
            f"credits left, needs {wanted}")
        self.account = account
        self.wanted = wanted


class Ledger:
    """Authoritative per-root budget accounting.

    Thread-safe: the live data plane runs a thread per connection, and two
    siblings of one fan-out routinely hit the same root concurrently.
    """

    def __init__(self, clock, *, default_grant: int = 100_000, ttl: float = 3600.0) -> None:
        self._clock = clock
        self._accounts: dict[str, RootAccount] = {}
        self._lock = threading.Lock()
        self.default_grant = default_grant
        #: Seconds of *inactivity* after which a root's accounting is dropped.
        self.ttl = ttl
        self.expired = 0
        self.grants: dict[str, int] = {}   # principal -> per-root grant

    # -- accounts ------------------------------------------------------

    def grant_for(self, principal: str) -> int:
        return self.grants.get(principal, self.default_grant)

    def open(self, root: str, principal: str, *, grant: int | None = None) -> RootAccount:
        with self._lock:
            now = self._clock.now()
            acct = self._accounts.get(root)
            if acct is None:
                acct = RootAccount(root=root,
                                   granted=grant if grant is not None else self.grant_for(principal),
                                   opened_at=now, last_seen=now)
                self._accounts[root] = acct
            else:
                acct.last_seen = now
            return acct

    def get(self, root: str) -> RootAccount | None:
        return self._accounts.get(root)

    # -- the fast path -------------------------------------------------

    def reserve(self, root: str, principal: str, amount: int) -> int:
        """Hold ``amount`` against the root.  Raises if the root cannot fund it."""
        acct = self.open(root, principal)
        with self._lock:
            if amount > acct.available:
                raise InsufficientBudget(acct, amount)
            acct.reserved += amount
            acct.hops += 1
            return amount

    def settle(self, root: str, reserved: int, observed: int) -> int:
        """Replace a reservation with the observed cost.  Returns the delta."""
        acct = self._accounts.get(root)
        if acct is None:
            return 0
        with self._lock:
            acct.last_seen = self._clock.now()
            acct.reserved = max(0, acct.reserved - reserved)
            acct.settled += observed
            if observed > reserved:
                acct.overruns += 1
            return observed - reserved

    def charge_refusal(self, root: str, principal: str, amount: int) -> None:
        """Burn credits on a refusal.

        This is the line that makes backpressure enforceable rather than
        advisory - see :mod:`weir.damper`.  It is deliberately allowed to drive
        an account to zero: an agent that spends its entire grant on ignored
        backpressure has spent its entire grant, and that is the correct
        outcome and a far cheaper one than letting it reach an upstream.
        """
        acct = self.open(root, principal)
        with self._lock:
            acct.refused += min(amount, max(0, acct.granted - acct.committed) + amount)
            acct.refused = min(acct.refused, acct.granted)

    def split(self, root: str, n: int, *, reserve_fraction: float = 0.2) -> int:
        """Per-child share when a hop fans out ``n`` ways.

        Holds back ``reserve_fraction`` so the parent can still pay for merging
        the children's results - the step that runs when the budget would
        otherwise be exactly empty.
        """
        acct = self._accounts.get(root)
        if acct is None or n <= 0:
            return 0
        return int(acct.available * (1.0 - reserve_fraction)) // n

    # -- housekeeping --------------------------------------------------

    def expire(self) -> int:
        """Collect roots that have gone quiet. Returns how many were dropped.

        Collection is keyed on **idleness, not age**, and the difference is a
        correctness property rather than a tuning preference.

        Dropping an account by age deletes the ledger entry of a task that is
        still running, and the next hop under that root re-opens it with a
        *full grant*. Budget conservation - the one invariant this whole design
        rests on - would then silently fail for precisely the long-running
        swarms it exists to contain, and fail in the quietest possible way: no
        error, no refusal, just an agent that gets a fresh wallet every hour.

        Keyed on idleness, a root that is still doing work is never collected,
        and a root that has finished is collected promptly. Memory is still
        bounded, by ``rate x ttl`` live roots rather than by ``rate x
        task-duration``.

        ``ttl`` therefore means "how long after a task goes quiet do we keep
        its accounting", and it no longer has to be guessed against the
        duration of the longest task anyone might run.
        """
        now = self._clock.now()
        with self._lock:
            dead = [r for r, a in self._accounts.items()
                    if now - a.last_seen > self.ttl and a.reserved == 0]
            for r in dead:
                del self._accounts[r]
            self.expired += len(dead)
            return len(dead)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "roots": len(self._accounts),
                "granted": sum(a.granted for a in self._accounts.values()),
                "settled": sum(a.settled for a in self._accounts.values()),
                "refused": sum(a.refused for a in self._accounts.values()),
                "hops": sum(a.hops for a in self._accounts.values()),
                "overruns": sum(a.overruns for a in self._accounts.values()),
                "expired": self.expired,
            }
