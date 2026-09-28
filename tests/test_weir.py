"""Test suite.  stdlib unittest, no dependencies: ``python3 -m unittest -v``."""

from __future__ import annotations

import dataclasses
import threading
import time
import unittest

from weir.attest import Keyring
from weir.clock import VirtualClock
from weir.coalesce import Coalescer
from weir.damper import Appointment, Damper
from weir.errors import Reason, Refused
from weir.fib import Fib, Route
from weir.intent import (CREDIT, Hop, Intent, MalformedIntent, digest_of,
                         from_headers, new_root, to_headers)
from weir.ledger import InsufficientBudget, Ledger
from weir.pathvec import PathGuard
from weir.receipts import ReceiptLog
from weir.router import Router, RouterConfig
from weir.sched import DeadlineScheduler
from weir.terms import Terms, TermsRegistry


def mk(**kw) -> Intent:
    base = dict(principal="did:web:lab#arttu", agent="aas:64512/planner",
                capability="agent.task", root=new_root(),
                deadline=1_800_000_030.0, declared_cost=100,
                intent_digest=digest_of("agent.task", {"q": "x"}))
    base.update(kw)
    return Intent(**base)


class TestIntent(unittest.TestCase):
    def test_header_roundtrip_is_lossless(self):
        i = mk(path=(Hop(1, "a"), Hop(2, "b")), coupling="human", safe=False)
        self.assertEqual(from_headers(to_headers(i)).canonical(), i.canonical())

    def test_rejects_malformed(self):
        for bad in [dict(agent="planner"), dict(capability="Bad Cap"),
                    dict(coupling="urgent"), dict(depth=-1), dict(principal="")]:
            with self.assertRaises(MalformedIntent):
                mk(**bad).validate()

    def test_digest_is_semantic_not_syntactic(self):
        a = digest_of("llm.completion", {"q": "hi", "n": 1})
        b = digest_of("llm.completion", {"n": 1, "q": "hi"})
        self.assertEqual(a, b, "key order must not change the intent digest")
        self.assertNotEqual(a, digest_of("llm.completion", {"q": "hi", "n": 2}))

    def test_forwarding_decrements_depth_and_extends_path(self):
        i = mk(depth=5, budget=1000)
        f = i.forwarded(Hop(64512, "edge"), cost=100)
        self.assertEqual(f.depth, 4)
        self.assertEqual(f.path[0], Hop(64512, "edge"))
        self.assertEqual(f.budget, 900)
        self.assertEqual(f.attest, "", "forwarding must not carry the caller's attestation")

    def test_child_cannot_exceed_parent(self):
        p = mk(budget=1000, deadline=1_800_000_010.0)
        c = p.child("aas:64512/worker", "agent.task", share=5000,
                    deadline=1_800_000_999.0)
        self.assertLessEqual(c.budget, p.budget)
        self.assertLessEqual(c.deadline, p.deadline)


class TestAttestation(unittest.TestCase):
    def test_signed_header_verifies_and_tamper_does_not(self):
        kr = Keyring({64512: b"k" * 32})
        i = kr.attested(mk())
        self.assertTrue(kr.verify(i))
        self.assertFalse(kr.verify(dataclasses.replace(i, budget=999_999)),
                         "raising your own budget must invalidate attestation")
        self.assertFalse(kr.verify(dataclasses.replace(i, coupling="human")),
                         "claiming the human fast lane must invalidate attestation")

    def test_one_aas_cannot_mint_for_another(self):
        kr = Keyring({1: b"k" * 32, 2: b"j" * 32})
        i = kr.attested(mk(agent="aas:1/a"))
        forged = dataclasses.replace(i, agent="aas:2/a")
        self.assertFalse(kr.verify(forged))


class TestPathVector(unittest.TestCase):
    def test_detects_cycle_a_b_c_a(self):
        g = PathGuard()
        i = mk(path=(Hop(3, "c"), Hop(2, "b"), Hop(1, "a")))
        with self.assertRaises(Refused) as cm:
            g.check(i, Hop(1, "a"))
        self.assertEqual(cm.exception.reason, Reason.DELEGATION_LOOP)
        self.assertFalse(cm.exception.retryable, "a loop retried is still a loop")

    def test_allows_deep_but_acyclic_pipeline(self):
        g = PathGuard()
        path = tuple(Hop(1, f"stage{n}") for n in range(12))
        g.check(mk(path=path, depth=20), Hop(1, "stage99"))

    def test_depth_exhaustion(self):
        with self.assertRaises(Refused) as cm:
            PathGuard().check(mk(depth=0), Hop(1, "x"))
        self.assertEqual(cm.exception.reason, Reason.DEPTH_EXCEEDED)


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.clock = VirtualClock()
        self.ledger = Ledger(self.clock, default_grant=1000)

    def test_reservation_prevents_sibling_double_spend(self):
        root = new_root()
        self.ledger.reserve(root, "p", 600)
        with self.assertRaises(InsufficientBudget):
            self.ledger.reserve(root, "p", 600)

    def test_settlement_releases_unused_reservation(self):
        root = new_root()
        r = self.ledger.reserve(root, "p", 900)
        self.ledger.settle(root, r, 100)
        self.ledger.reserve(root, "p", 800)   # must not raise

    def test_overrun_is_charged_not_ignored(self):
        root = new_root()
        r = self.ledger.reserve(root, "p", 100)
        self.ledger.settle(root, r, 700)
        acct = self.ledger.get(root)
        self.assertEqual(acct.settled, 700)
        self.assertEqual(acct.overruns, 1)

    def test_fanout_cannot_multiply_the_grant(self):
        """The core invariant: a swarm cannot outspend its root."""
        root = new_root()
        self.ledger.open(root, "p")
        funded = 0
        for _ in range(5000):
            try:
                self.ledger.reserve(root, "p", 100)
                funded += 1
            except InsufficientBudget:
                pass
        self.assertEqual(funded, 10)
        self.assertLessEqual(self.ledger.get(root).committed, 1000)


class TestDamper(unittest.TestCase):
    def setUp(self):
        self.clock = VirtualClock()
        self.d = Damper(self.clock, b"k" * 32, limit=2, horizon=10.0)
        # Human-coupled, so the whole limit is available: the batch reserve has
        # its own test and would otherwise silently halve the ceiling here.
        self.i = mk(deadline=self.clock.now() + 60, coupling="human")

    def _fill(self):
        self.d.admit(self.i, None)
        self.d.admit(self.i, None)

    def test_refusal_carries_an_appointment(self):
        self._fill()
        with self.assertRaises(Refused) as cm:
            self.d.admit(self.i, None)
        self.assertEqual(cm.exception.reason, Reason.CONGESTED)
        self.assertIsNotNone(cm.exception.retry_not_before)
        self.assertGreater(cm.exception.retry_not_before, self.clock.now())

    def test_early_retry_costs_more_and_gains_nothing(self):
        self._fill()
        try:
            self.d.admit(self.i, None)
        except Refused as exc:
            slot, ticket = exc.retry_not_before, self.d.issue(self.i, exc.retry_not_before).encode()

        charges = []
        for n in range(4):
            with self.assertRaises(Refused) as cm:
                self.d.admit(self.i, ticket)
            self.assertEqual(cm.exception.reason, Reason.EARLY_RETRY)
            # Appointments are carried on the wire at millisecond precision, so
            # the slot round-trips to 3 decimals; the damper's grace window is
            # 50ms, which swallows the rounding in either direction.
            self.assertAlmostEqual(cm.exception.retry_not_before, slot, places=3,
                                   msg="retrying early must not move the slot earlier")
            charges.append(cm.exception.charged)
            ticket = self.d.issue(self.i, slot, attempt=n + 2).encode()
        self.assertEqual(charges, sorted(charges))
        self.assertGreater(charges[-1], charges[0], "penalty must escalate")

    def test_punctual_ticket_is_admitted(self):
        self._fill()
        try:
            self.d.admit(self.i, None)
        except Refused as exc:
            ticket = self.d.issue(self.i, exc.retry_not_before).encode()
            self.clock._now = exc.retry_not_before
        self.d.complete(0.1)
        self.d.complete(0.1)
        self.assertIsNone(self.d.admit(self.i, ticket))

    def test_forged_ticket_is_ignored_not_honoured(self):
        self._fill()
        forged = Appointment(self.clock.now(), 1, self.clock.now(), "0" * 24).encode()
        with self.assertRaises(Refused):
            self.d.admit(self.i, forged)

    def test_never_books_past_the_deadline(self):
        d = Damper(self.clock, b"k" * 32, limit=1, horizon=60.0)
        tight = mk(deadline=self.clock.now() + 0.05, coupling="human")
        d.admit(tight, None)
        with self.assertRaises(Refused) as cm:
            d.admit(tight, None)
        self.assertEqual(cm.exception.reason, Reason.INFEASIBLE)

    def test_batch_cannot_occupy_the_human_reserve(self):
        d = Damper(self.clock, b"k" * 32, limit=10, human_reserve=0.4)
        n = 0
        while True:
            try:
                d.admit(mk(coupling="batch", deadline=self.clock.now() + 60), None)
                n += 1
            except Refused:
                break
        self.assertEqual(n, 6)
        self.assertIsNone(d.admit(mk(coupling="human",
                                     deadline=self.clock.now() + 60), None))

    def test_limit_does_not_collapse_on_one_slow_window(self):
        d = Damper(self.clock, b"k" * 32, limit=20)
        for _ in range(3):
            d.complete(5.0)
        self.assertGreater(d.limit, 10, "one excursion must not floor the limit")


class TestScheduler(unittest.TestCase):
    def test_human_first_then_earliest_deadline(self):
        clock = VirtualClock()
        s = DeadlineScheduler(clock)
        now = clock.now()
        for coupling, dl in [("batch", 5), ("human", 9), ("batch", 1), ("human", 3)]:
            s.push(mk(coupling=coupling, deadline=now + dl))
        got = [(i.coupling, round(i.deadline - now)) for i in iter(s.pop, None)]
        self.assertEqual(got, [("human", 3), ("human", 9), ("batch", 1), ("batch", 5)])

    def test_expired_work_is_never_forwarded(self):
        clock = VirtualClock()
        s = DeadlineScheduler(clock)
        with self.assertRaises(Refused) as cm:
            s.check_feasible(mk(deadline=clock.now() - 1))
        self.assertEqual(cm.exception.reason, Reason.DEADLINE_PASSED)

    def test_infeasible_work_is_refused_before_it_starts(self):
        clock = VirtualClock()
        s = DeadlineScheduler(clock, service_estimate=2.0)
        with self.assertRaises(Refused) as cm:
            s.check_feasible(mk(deadline=clock.now() + 0.5))
        self.assertEqual(cm.exception.reason, Reason.INFEASIBLE)


class TestCoalescing(unittest.TestCase):
    def setUp(self):
        self.clock = VirtualClock()
        self.co = Coalescer(self.clock)

    def test_identical_intents_hit_the_upstream_once(self):
        calls = []
        i = mk()
        for _ in range(64):
            self.co.run(i, lambda: calls.append(1) or "answer")
        self.assertEqual(len(calls), 1)

    def test_different_principals_are_not_shared(self):
        """The cache must not become an exfiltration primitive."""
        calls = []
        d = digest_of("agent.task", {"q": "secret"})
        a = mk(principal="did:web:lab#alice", intent_digest=d)
        b = mk(principal="did:web:lab#bob", intent_digest=d)
        for i in (a, b):
            self.co.run(i, lambda: calls.append(1) or "answer")
        self.assertEqual(len(calls), 2, "cross-principal sharing leaks answers")

    def test_public_capability_is_shared(self):
        co = Coalescer(self.clock, public=frozenset({"search.web"}))
        calls = []
        d = digest_of("search.web", {"q": "weather"})
        for who in ("alice", "bob"):
            co.run(mk(principal=f"did:web:lab#{who}", capability="search.web",
                      intent_digest=d), lambda: calls.append(1) or "x")
        self.assertEqual(len(calls), 1)

    def test_unsafe_requests_are_never_coalesced(self):
        calls = []
        i = mk(safe=False)
        for _ in range(5):
            self.co.run(i, lambda: calls.append(1) or "x")
        self.assertEqual(len(calls), 5, "coalescing a write hands one agent another's result")


class TestTerms(unittest.TestCase):
    def test_denied_capability_never_reaches_the_origin(self):
        reg = TermsRegistry(VirtualClock())
        reg.publish(Terms(origin="example.com", deny=("train.*",)))
        with self.assertRaises(Refused) as cm:
            reg.check(mk(capability="train.corpus"), "example.com")
        self.assertEqual(cm.exception.reason, Reason.TERMS_DENIED)

    def test_purpose_is_enforced(self):
        reg = TermsRegistry(VirtualClock())
        reg.publish(Terms(origin="example.com", purposes=("search",)))
        reg.check(mk(), "example.com", purpose="search")
        with self.assertRaises(Refused):
            reg.check(mk(), "example.com", purpose="train")


class TestReceipts(unittest.TestCase):
    def test_chain_verifies_and_detects_tampering(self):
        log = ReceiptLog(VirtualClock())
        for d in ("forwarded", "coalesced", "refused"):
            log.record(mk(), d, credits=100)
        self.assertTrue(log.verify()[0])
        log._entries[1] = dataclasses.replace(log._entries[1], credits=0)
        self.assertFalse(log.verify()[0])

    def test_chain_detects_deletion(self):
        log = ReceiptLog(VirtualClock())
        for _ in range(4):
            log.record(mk(), "forwarded")
        del log._entries[2]
        ok, msg = log.verify()
        self.assertFalse(ok)
        self.assertIn("broken link", msg)

    def test_receipts_do_not_store_payloads(self):
        log = ReceiptLog(VirtualClock())
        r = log.record(mk(), "forwarded")
        self.assertNotIn("payload", dataclasses.asdict(r))
        self.assertTrue(r.intent_digest.startswith("b2:"))


class TestFib(unittest.TestCase):
    def setUp(self):
        self.fib = Fib()
        self.fib.add(Route("llm.completion", "cheap", price=100, latency=2.0))
        self.fib.add(Route("llm.completion", "fast", price=900, latency=0.25))
        self.fib.add(Route("llm.completion.long-context", "bigctx", price=1500))

    def test_longest_capability_match_wins(self):
        i = mk(capability="llm.completion.long-context", deadline=1e12)
        self.assertEqual(self.fib.lookup(i, 0.0).upstream, "bigctx")

    def test_urgent_buys_speed_patient_buys_cheap(self):
        now = 1000.0
        urgent = mk(capability="llm.completion", coupling="human", deadline=now + 1)
        patient = mk(capability="llm.completion", coupling="batch", deadline=now + 3600)
        self.assertEqual(self.fib.lookup(urgent, now).upstream, "fast")
        self.assertEqual(self.fib.lookup(patient, now).upstream, "cheap")

    def test_no_route_is_terminal(self):
        with self.assertRaises(Refused) as cm:
            self.fib.lookup(mk(capability="nope.at.all"), 0.0)
        self.assertEqual(cm.exception.reason, Reason.NO_ROUTE)


class TestRouterPipeline(unittest.TestCase):
    def setUp(self):
        self.clock = VirtualClock()
        self.router = Router(RouterConfig(limit=4, default_grant=10 * CREDIT),
                             clock=self.clock)
        self.router.route(Route("agent", "upstream", price=100, latency=0.2))

    def up(self, fwd, route):
        return {"ok": True}

    def test_happy_path_forwards_and_settles(self):
        d = self.router.forward(mk(), upstream=self.up)
        self.assertTrue(d.ok)
        self.assertEqual(d.disposition, "forwarded")
        self.assertEqual(d.credits, 100)

    def test_refusal_happens_before_the_upstream_is_touched(self):
        """A loop, an expiry or an empty wallet must cost the upstream nothing."""
        called = []

        def up(fwd, route):
            called.append(1)
            return {}

        for bad in [mk(path=(Hop(64512, "weir-0"),)),         # loop through us
                    mk(deadline=self.clock.now() - 1),        # already worthless
                    mk(capability="unrouted.thing")]:         # nowhere to go
            d = self.router.forward(bad, upstream=up)
            self.assertFalse(d.ok, bad.capability)
        self.assertEqual(called, [], "refused work must never reach an upstream")

    def test_admission_holds_capacity_until_settled(self):
        held = [self.router.begin(mk(coupling="human")) for _ in range(4)]
        self.assertEqual(self.router.damper.inflight, 4)
        d = self.router.forward(mk(coupling="human"), upstream=self.up)
        self.assertFalse(d.ok)
        self.assertEqual(d.reason, Reason.CONGESTED)
        for a in held:
            a.settle(observed=100)
        self.assertEqual(self.router.damper.inflight, 0)

    def test_settling_twice_is_an_error(self):
        a = self.router.begin(mk())
        a.settle(observed=100)
        with self.assertRaises(RuntimeError):
            a.settle(observed=100)

    def test_every_decision_leaves_a_receipt(self):
        self.router.forward(mk(), upstream=self.up)
        self.router.forward(mk(deadline=self.clock.now() - 1), upstream=self.up)
        kinds = {r.decision for r in self.router.receipts.entries()}
        self.assertEqual(kinds, {"forwarded", "refused"})
        self.assertTrue(self.router.receipts.verify()[0])

    def test_coalesced_calls_are_not_billed_for_work_not_done(self):
        i = mk()
        self.router.forward(i, upstream=self.up)
        d = self.router.forward(i, upstream=self.up)
        self.assertEqual(d.disposition, "cached")
        self.assertEqual(d.credits, 0)

    def test_budget_is_enforced_by_the_router_not_the_header(self):
        """A caller that lies about its remaining budget gains nothing."""
        root = new_root()
        # Distinct digests, or coalescing serves these from cache at zero cost
        # and the grant is never touched - which would make this test pass for
        # entirely the wrong reason.
        funded = 0
        attempts = 200
        for n in range(attempts):
            d = self.router.forward(
                mk(root=root, budget=10 ** 9, declared_cost=1, coupling="human",
                   intent_digest=f"b2:liar{n}"), upstream=self.up)
            funded += 1 if d.ok else 0
        self.assertLessEqual(self.router.ledger.get(root).committed, 10 * CREDIT)
        self.assertLess(funded, attempts,
                        "a caller claiming a billion credits must still hit the grant")

    def test_fanout_width_is_bounded_per_delegator(self):
        """Concurrent breadth is capped..."""
        # Concurrency limit raised well above the width limit, so the refusal
        # under test is the one this test is about.
        router = Router(RouterConfig(limit=256, default_grant=10_000 * CREDIT,
                                     max_fanout=16), clock=self.clock)
        router.route(Route("agent", "upstream", price=100, latency=0.2))
        root = new_root()
        parent = Hop(64512, "parent")
        held = []
        reasons = set()
        for n in range(40):
            try:
                held.append(router.begin(
                    mk(root=root, path=(parent,), agent=f"aas:64512/child{n}",
                       coupling="human", intent_digest=f"b2:c{n}")))
            except Refused as exc:
                reasons.add(exc.reason)
        self.assertIn(Reason.FANOUT_EXCEEDED, reasons)
        self.assertEqual(len(held), 16)
        for a in held:
            a.settle(observed=100)
        # Released: the same delegator may fan out again once the first wave lands.
        router.begin(mk(root=root, path=(parent,), agent="aas:64512/again",
                        coupling="human", intent_digest="b2:again")).settle(observed=100)

    def test_sequential_calls_are_not_a_fanout(self):
        """...but cumulative volume is not breadth.

        An agent making many calls one after another is not fanning out, and a
        width limiter that cannot tell the difference throttles the wrong thing.
        """
        root = new_root()
        parent = Hop(64512, "parent")
        for n in range(self.router.config.max_fanout * 4):
            d = self.router.forward(
                mk(root=root, path=(parent,), agent="aas:64512/worker",
                   coupling="human", intent_digest=f"b2:s{n}"), upstream=self.up)
            if not d.ok:
                self.assertNotEqual(d.reason, Reason.FANOUT_EXCEEDED,
                                    f"sequential call {n} counted as fan-out")


class TestConcurrency(unittest.TestCase):
    def test_ledger_is_safe_under_parallel_siblings(self):
        """Real fan-out hits one root from many threads at once."""
        ledger = Ledger(VirtualClock(), default_grant=1000)
        root = new_root()
        ledger.open(root, "p")
        funded = []
        lock = threading.Lock()

        def worker():
            for _ in range(200):
                try:
                    ledger.reserve(root, "p", 100)
                    with lock:
                        funded.append(1)
                except InsufficientBudget:
                    pass

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(funded), 10, "grant must hold under concurrency")


if __name__ == "__main__":
    unittest.main(verbosity=2)
