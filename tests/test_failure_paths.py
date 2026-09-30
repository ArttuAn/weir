"""Failure paths: what the router does when something goes wrong.

The rest of the suite tests the mechanisms working.  These test the opposite -
and they are here because every defect in this file was found by a probe, not by
reading, and would have shipped on the strength of a green suite.  A forwarding
plane is judged mostly on what it does when the upstream is down, when a stage
raises something nobody predicted, and when a caller asks for a thing that
cannot be done: a system that only has a happy path has not been tested.
"""

from __future__ import annotations

import inspect
import json
import re
import socket
import threading
import unittest
import urllib.error
import urllib.request

from weir.clock import VirtualClock
from weir.coalesce import Coalescer
from weir.damper import Damper
from weir.errors import Reason, Refused
from weir.fib import Route
from weir.intent import Intent, digest_of, new_root, to_headers
from weir.router import Router, RouterConfig
from weir.server import serve


def mk(**kw) -> Intent:
    base = dict(principal="did:web:lab#arttu", agent="aas:64512/planner",
                capability="agent.task", root=new_root(),
                deadline=1_800_000_030.0, declared_cost=100,
                intent_digest=digest_of("agent.task", {"q": "x"}))
    base.update(kw)
    return Intent(**base)


def up(fwd, route):
    return {"ok": True}


class TestCoalescedWaiterOutlivesLeader(unittest.TestCase):
    """A waiter that gives up must be told, not handed an empty success.

    Single-flight hands every waiter the leader's ``_Flight``.  That object
    starts life holding ``result = None``.  If a waiter's own deadline passes
    before the leader publishes, returning that ``None`` is indistinguishable
    from a real answer: HTTP 200, empty body, billed at zero credits, receipted
    as coalesced.  The caller cannot tell it apart from success, and neither can
    the router.
    """

    def setUp(self):
        self.clock = VirtualClock()
        self.coalescer = Coalescer(self.clock, ttl=30.0)
        self.digest = digest_of("agent.task", {"q": "x"})
        self.work_started = threading.Event()
        self.release = threading.Event()
        self.leader: dict = {}

    def _lead(self, intent):
        def work():
            self.work_started.set()
            self.release.wait(10.0)
            return {"answer": 42}

        self.leader["out"] = self.coalescer.run(intent, work)

    def test_waiter_outlived_by_its_leader_is_refused(self):
        leader_i = mk(intent_digest=self.digest, deadline=self.clock.now() + 600.0)
        waiter_i = mk(intent_digest=self.digest, deadline=self.clock.now() + 0.05)

        t = threading.Thread(target=self._lead, args=(leader_i,), daemon=True)
        t.start()
        self.assertTrue(self.work_started.wait(10.0), "leader never started")

        role, _key, flight = self.coalescer.acquire(waiter_i)
        self.assertEqual(role, "join")

        with self.assertRaises(Refused) as cm:
            self.coalescer.join(flight, waiter_i)
        self.assertEqual(cm.exception.reason, Reason.DEADLINE_PASSED)
        self.assertEqual(cm.exception.status, 408)
        self.assertFalse(cm.exception.retryable, "a missed deadline does not come back")

        self.release.set()
        t.join(10.0)
        # The leader is unaffected: one slow caller must not poison the flight.
        self.assertEqual(self.leader["out"], ({"answer": 42}, "miss"))

    def test_the_router_refuses_the_waiter_rather_than_serving_nothing(self):
        """End to end, because the damage is done at the decision, not in the wait."""
        router = Router(RouterConfig(limit=4))
        router.route(Route("agent.task", "http://origin.invalid", price=100))
        digest = self.digest
        decisions: dict = {}

        def slow_upstream(fwd, route):
            self.work_started.set()
            self.release.wait(10.0)
            return {"answer": 42}

        def lead():
            decisions["leader"] = router.forward(mk(intent_digest=digest),
                                                 upstream=slow_upstream)

        t = threading.Thread(target=lead, daemon=True)
        t.start()
        self.assertTrue(self.work_started.wait(10.0), "leader never started")

        def wait():
            # Feasible at admission (the scheduler's service estimate is 0.4s,
            # so this has to clear that) but gone by the time the leader is due.
            # Any shorter and stage 4 refuses it as infeasible before the
            # coalescer is ever consulted, which is correct and tests nothing.
            decisions["waiter"] = router.forward(
                mk(intent_digest=digest, deadline=router.clock.now() + 0.5),
                upstream=slow_upstream)

        w = threading.Thread(target=wait, daemon=True)
        w.start()
        w.join(10.0)
        self.release.set()
        t.join(10.0)

        d = decisions["waiter"]
        self.assertFalse(d.ok, "a waiter with no answer must be refused")
        self.assertEqual(d.reason, Reason.DEADLINE_PASSED)
        self.assertEqual(d.status, 408)
        self.assertIsNone(d.result)
        self.assertFalse(d.ok,
                         "an empty result must never be dressed up as an answer")
        self.assertEqual(router.counters["coalesced"], 0)
        self.assertTrue(router.receipts.verify()[0])


class TestFanoutSlotIsNotLeaked(unittest.TestCase):
    """A width counter nobody decrements is a permanently throttled caller.

    ``_fanout_enter`` takes a slot before the stages that can fail, so every one
    of them owes it a give-back.  Catching only ``Refused`` leaves the slot held
    when a stage raises anything else, and since the counter is keyed on the
    delegator, one bug anywhere in the pipeline costs *that node* its ability to
    fan out at all - for as long as its root lives.
    """

    def setUp(self):
        self.router = Router(RouterConfig(max_fanout=2))
        self.router.route(Route("agent.task", "http://origin.invalid", price=100))
        self.intent = mk()

    def _break_terms(self):
        def boom(intent, origin, purpose="answer"):
            raise RuntimeError("simulated stage failure")

        self.router.terms.check = boom

    def test_slot_is_released_when_a_stage_raises_unexpectedly(self):
        self._break_terms()
        with self.assertRaises(RuntimeError):
            self.router.forward(self.intent, upstream=up)
        self.assertEqual(self.router._fanout, {},
                         "the width slot outlived the request that took it")

    def test_a_raising_stage_does_not_throttle_the_delegator_forever(self):
        self._break_terms()
        # One delegator, not a fresh root per call: width is keyed on the
        # delegator and counts calls *outstanding*, so a leak only shows up
        # when the same node keeps asking. Testing this with a new root each
        # time would pass against the bug, which is what it did.
        intent = mk()
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                self.router.forward(intent, upstream=up)
        # Stage repaired; the delegator must work again rather than being
        # refused FANOUT_EXCEEDED by a counter nobody owns any more.
        del self.router.terms.check
        d = self.router.forward(intent, upstream=up)
        self.assertTrue(d.ok, f"throttled by a leaked slot: {d.reason} {d.detail}")

    def test_a_refusal_still_releases_the_slot(self):
        """The pre-existing behaviour, pinned so the widening did not break it."""
        d = self.router.forward(mk(deadline=self.router.clock.now() - 1),
                                upstream=up)
        self.assertFalse(d.ok)
        self.assertEqual(self.router._fanout, {})


class TestFailuresAreNotForwardings(unittest.TestCase):
    """An upstream that died is not a request that got through.

    ``forward`` already released the slot and written the receipt before
    re-raising, so the accounting was the only thing left to be wrong - and it
    was counting a dead call as a delivered one, in the counters an operator
    watches and in the receipt chain they would audit afterwards.
    """

    def setUp(self):
        self.router = Router(RouterConfig(limit=4))
        self.router.route(Route("agent.task", "http://origin.invalid", price=100))

    def _fail(self):
        with self.assertRaises(ConnectionResetError):
            self.router.forward(mk(), upstream=lambda f, r: (_ for _ in ()).throw(
                ConnectionResetError("upstream died")))

    def test_a_dead_upstream_is_counted_as_failed(self):
        self._fail()
        c = self.router.counters
        self.assertEqual(c["failed"], 1)
        self.assertEqual(c["forwarded"], 0,
                         "a call with no answer was reported as delivered")
        self.assertEqual(c["credits_settled"], 0,
                         "a call that never completed was billed")

    def test_a_dead_upstream_is_receipted_as_failed(self):
        self._fail()
        kinds = {e.decision for e in self.router.receipts.entries()}
        self.assertEqual(kinds, {"failed"})
        self.assertTrue(self.router.receipts.verify()[0],
                        "the failure still has to be in the chain")

    def test_the_failure_still_releases_capacity(self):
        self._fail()
        self.assertEqual(self.router.damper.inflight, 0)
        self.assertEqual(self.router._fanout, {})

    def test_a_successful_call_is_still_a_forwarding(self):
        d = self.router.forward(mk(), upstream=up)
        self.assertEqual(d.disposition, "forwarded")
        self.assertEqual(self.router.counters["forwarded"], 1)
        self.assertEqual(self.router.counters["failed"], 0)


class TestDamperStats(unittest.TestCase):
    def test_stats_has_no_silently_dropped_keys(self):
        """A dict literal with the same key twice keeps one and loses the other.

        Nothing about that is visible at the call site - ``stats()`` is just a
        dict either way - so the only way to catch it is to compare the keys the
        source declares with the keys the dict has.
        """
        source = inspect.getsource(Damper.stats)
        declared = re.findall(r'^\s{12}"([a-z_]+)":', source, re.M)
        self.assertTrue(declared, "could not read the stats dict literal")
        stats = Damper(VirtualClock(), b"k" * 16).stats()
        self.assertEqual(sorted(set(declared)), sorted(stats),
                         "declared keys and reported keys disagree")
        self.assertEqual(len(declared), len(set(declared)),
                         "the stats dict literal declares a key twice")


class TestWireEdge(unittest.TestCase):
    """The HTTP edge, over a real socket.

    A handler that raises does not answer: ``socketserver`` closes the
    connection and prints a traceback, so the caller learns nothing and the
    operator learns nothing but noise.  Every one of these is a case where the
    router has all the information and the wire was throwing it away.
    """

    def setUp(self):
        self.router = Router(RouterConfig(limit=4))
        # A port nothing is listening on: connection refused, immediately.
        self.router.route(Route("agent.task", "http://127.0.0.1:1/dead", price=100))
        self.httpd = serve(self.router, "127.0.0.1", 0, housekeep_every=3600.0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.housekeeper.stop.set()
        self.httpd.shutdown()
        self.httpd.server_close()

    def call(self, path, data=None, headers=None, method="GET", content_length=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     data=data, method=method, headers=headers or {})
        if content_length is not None:
            req.add_header("content-length", content_length)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def _post_headers(self, **kw):
        i = mk(deadline=self.router.clock.now() + 30.0, **kw)
        return {**to_headers(i), "content-type": "application/json"}

    def test_routes_endpoint_answers_for_slotted_routes(self):
        """``Route`` is a slots dataclass, so it has no ``__dict__`` to read."""
        status, body = self.call("/_weir/routes")
        self.assertEqual(status, 200)
        routes = json.loads(body)["routes"]
        self.assertEqual(routes[0]["capability"], "agent.task")

    def test_a_dead_upstream_is_a_502_not_a_dropped_connection(self):
        status, body = self.call("/task", data=b'{"q": 1}',
                                 headers=self._post_headers(), method="POST")
        self.assertEqual(status, 502)
        payload = json.loads(body)
        self.assertEqual(payload["reason"], "upstream-failed")
        self.assertIn("URLError", payload["detail"])

    def test_a_dead_upstream_is_still_auditable_after_the_502(self):
        self.call("/task", data=b'{"q": 1}', headers=self._post_headers(),
                  method="POST")
        self.assertEqual(self.router.counters["failed"], 1)
        self.assertTrue(self.router.receipts.verify()[0])

    def test_a_malformed_content_length_is_a_400(self):
        status, body = self.call("/task", data=b"{}", method="POST",
                                 headers=self._post_headers(),
                                 content_length="banana")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["reason"], Reason.MALFORMED)

    def test_a_bad_content_length_closes_the_connection(self):
        """Answering 400 without draining the body desyncs a keep-alive client.

        Driven over a raw socket because the ordinary clients both hide this:
        urllib hangs up on an error response, and http.client has auto_open set
        and quietly opens a second connection, so neither would notice leftover
        body bytes being parsed as the next request. The invariant is the one
        the wire can actually state - the server says "close", and hangs up.
        """
        i = mk(deadline=self.router.clock.now() + 30.0)
        head = "".join(f"{k}: {v}\r\n" for k, v in
                       {**to_headers(i), "content-type": "application/json",
                        "content-length": "banana"}.items())
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        try:
            sock.sendall(f"POST /task HTTP/1.1\r\nHost: x\r\n{head}\r\n".encode()
                         + b'{"q": 1}')
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = sock.recv(4096)
                self.assertNotEqual(chunk, b"",
                                    "server hung up without answering at all")
                data += chunk
            response, body = data.split(b"\r\n\r\n", 1)
            self.assertIn(b"400 Bad Request", response)
            self.assertIn(b"Connection: close", response)
            length = int(next(line.split(b": ")[1] for line in response.split(b"\r\n")
                              if line.lower().startswith(b"content-length")))
            while len(body) < length:
                body += sock.recv(4096)
            sock.settimeout(5)
            self.assertEqual(sock.recv(1), b"",
                             "held the connection open with an undrained body "
                             "in the buffer for the next request to trip over")
        finally:
            sock.close()

    def test_a_negative_content_length_is_a_400(self):
        status, _ = self.call("/task", data=b"{}", method="POST",
                              headers=self._post_headers(),
                              content_length="-1")
        self.assertEqual(status, 400)

    def test_a_malformed_intent_is_still_a_400(self):
        headers = self._post_headers()
        headers["weir-agent"] = "not-an-agent"
        status, body = self.call("/task", data=b"{}", method="POST",
                                 headers=headers)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["reason"], Reason.MALFORMED)

    def test_stats_still_answers(self):
        status, _ = self.call("/_weir/stats")
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
