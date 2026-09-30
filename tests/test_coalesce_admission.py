"""Coalescing must not be gated by admission control.

Admission control exists to protect the upstream. A request served from cache,
or joined onto a call someone else is already making, never reaches the
upstream - so refusing it for lack of upstream capacity refuses work that would
have cost nothing.

Before the ordering was fixed, twenty callers asking one question behind a limit
of four got four answers and sixteen `congested` refusals, for a single upstream
call. These tests pin that shut, and pin the failure mode the fix introduces: a
leader that is refused admission must wake its waiters instead of leaving them
blocked until their deadlines expire.
"""

from __future__ import annotations

import threading
import time
import unittest

from weir.clock import VirtualClock
from weir.errors import Reason
from weir.fib import Route
from weir.intent import CREDIT, Intent, digest_of, new_root
from weir.router import Router, RouterConfig

SAME = digest_of("agent.task", {"q": "the same question"})


def intent(n: int, *, digest: str = SAME, deadline: float | None = None,
           root: str | None = None) -> Intent:
    return Intent(principal="did:web:lab#arttu", agent=f"aas:64512/a{n}",
                  capability="agent.task", root=root or new_root(),
                  deadline=deadline if deadline is not None else time.time() + 30,
                  declared_cost=100, coupling="human", intent_digest=digest)


def router(**kw) -> Router:
    cfg = dict(limit=4, default_grant=10_000 * CREDIT)
    cfg.update(kw)
    r = Router(RouterConfig(**cfg))
    r.route(Route("agent.task", "upstream", price=100, latency=0.4))
    return r


def run_concurrently(r: Router, n: int, upstream, *, digests=None) -> dict:
    out = {"ok": 0, "refused": {}}
    lock = threading.Lock()

    def caller(i: int) -> None:
        d = SAME if digests is None else digests(i)
        dec = r.forward(intent(i, digest=d), upstream=upstream)
        with lock:
            if dec.ok:
                out["ok"] += 1
            else:
                out["refused"][dec.reason] = out["refused"].get(dec.reason, 0) + 1

    threads = [threading.Thread(target=caller, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    out["stuck"] = sum(1 for t in threads if t.is_alive())
    return out


class TestCoalescingBeatsAdmission(unittest.TestCase):

    def test_identical_requests_are_not_gated_by_capacity(self):
        r = router(limit=4)
        calls = []
        lock = threading.Lock()

        def up(fwd, route):
            with lock:
                calls.append(1)
            time.sleep(0.5)          # everyone else arrives during this
            return {"answer": 1}

        out = run_concurrently(r, 20, up)
        self.assertEqual(out["ok"], 20,
                         f"coalesced callers were refused: {out['refused']}")
        self.assertEqual(len(calls), 1, "should be exactly one upstream call")

    def test_distinct_requests_are_still_gated(self):
        """The control: the limit must still mean something."""
        r = router(limit=4)
        calls = []
        lock = threading.Lock()

        def up(fwd, route):
            with lock:
                calls.append(1)
            time.sleep(0.5)
            return {"answer": 1}

        out = run_concurrently(r, 20, up,
                               digests=lambda i: digest_of("agent.task", {"q": i}))
        self.assertEqual(out["ok"], 4)
        self.assertEqual(out["refused"].get(Reason.CONGESTED), 16)
        self.assertEqual(len(calls), 4, "admission must still cap upstream load")

    def test_a_refused_leader_wakes_its_waiters(self):
        """The failure mode the fix introduces, and must not have.

        If the leader registers a flight and is then refused admission, it has
        to publish that refusal. A leader that returns without releasing leaves
        every waiter blocked until its deadline.
        """
        r = router(limit=1)
        # Occupy the only slot with an unrelated request so the leader of the
        # coalesced group cannot be admitted.
        blocker = r.begin(intent(99, digest=digest_of("agent.task", {"q": "other"})))
        self.assertEqual(r.damper.inflight, 1)

        out = run_concurrently(r, 8, lambda fwd, route: {"answer": 1})
        blocker.settle(observed=100)

        self.assertEqual(out["stuck"], 0, "waiters blocked behind a refused leader")
        self.assertEqual(out["ok"], 0)
        self.assertEqual(sum(out["refused"].values()), 8,
                         "every caller should have been told, not left hanging")

    def test_an_unaffordable_root_is_refused_before_the_cache(self):
        """Budget is checked before the coalescer, deliberately.

        Note what this does *not* say. A cache hit settles at zero, so it
        releases its reservation and never accumulates spend - a root can draw
        answers it already paid for as often as it likes, and that is correct:
        the cache is principal-scoped, so it is reading back its own results.

        What budget gates is the *reservation*. A root that cannot afford one
        call is refused before the cache is consulted, which bounds total
        requests per root rather than only total spend. Without that, an agent
        whose grant is gone can still spin on the cache indefinitely, consuming
        connections and router CPU for free.
        """
        r = router(limit=8, default_grant=150)          # one 100-credit call, no more
        root = new_root()
        first = r.forward(intent(0, root=root), upstream=lambda f, rt: {"answer": 1})
        self.assertTrue(first.ok)
        self.assertEqual(first.credits, 100)

        # Same root, same question - it would be a cache hit, but the root can
        # no longer afford the reservation that precedes the lookup.
        second = r.forward(intent(1, root=root), upstream=lambda f, rt: {"answer": 1})
        self.assertFalse(second.ok)
        self.assertEqual(second.reason, Reason.BUDGET_EXHAUSTED)

    def test_a_solvent_root_may_reread_its_own_cached_answers(self):
        """The other side of the same coin: hits are free and stay free."""
        r = router(limit=8, default_grant=10_000 * CREDIT)
        root = new_root()
        r.forward(intent(0, root=root), upstream=lambda f, rt: {"answer": 1})
        spent_after_first = r.ledger.get(root).settled
        for i in range(1, 30):
            d = r.forward(intent(i, root=root), upstream=lambda f, rt: {"answer": 1})
            self.assertTrue(d.ok)
        self.assertEqual(r.ledger.get(root).settled, spent_after_first,
                         "cache hits must not accumulate spend")

    def test_coalesced_requests_are_not_billed(self):
        r = router(limit=8)
        r.forward(intent(0), upstream=lambda f, rt: {"answer": 1})
        second = r.forward(intent(1), upstream=lambda f, rt: {"answer": 1})
        self.assertEqual(second.disposition, "cached")
        self.assertEqual(second.credits, 0,
                         "charging for a call that was never made is a broken meter")


if __name__ == "__main__":
    unittest.main(verbosity=2)
