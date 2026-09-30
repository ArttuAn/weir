"""Asking the log about one root instead of reading all of it.

The chain existed and could be verified, but there was no way to ask it a
question.  ``GET /_weir/receipts`` returned a boolean and a tip; every entry
was reachable only from inside the process.  For a system whose whole claim is
that you can find out what an agent did and who told it to, "the log verifies"
without "here is the log" is a gap in the middle of the thesis.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

from weir.clock import RealClock
from weir.fib import Route
from weir.intent import Hop, Intent, digest_of, new_root, to_headers
from weir.receipts import ReceiptLog
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


class TestForRoot(unittest.TestCase):
    def setUp(self):
        self.log = ReceiptLog(RealClock())

    def test_only_that_roots_entries_come_back(self):
        mine, theirs = mk(root="a" * 32), mk(root="b" * 32)
        self.log.record(mine, "forwarded", credits=100)
        self.log.record(theirs, "refused", reason="budget-exhausted")
        self.log.record(mine, "refused", reason="deadline-infeasible")

        found = self.log.for_root("a" * 32)
        self.assertEqual([r.decision for r in found], ["forwarded", "refused"])
        self.assertEqual([r.reason for r in found], ["", "deadline-infeasible"])
        self.assertEqual({r.root for r in found}, {"a" * 32})

    def test_a_whole_delegated_tree_is_one_roots_answer(self):
        """The point of scoping on the root: children inherit it.

        A root asks, two hops do the work, and the receipts that matter to the
        root are all three. Scoping on the agent or the capability would have
        split that answer in half and given the asker neither part.
        """
        root = "c" * 32
        parent = mk(root=root)
        child = parent.forwarded(Hop(64512, "aas:64512/planner"), cost=100)
        grandchild = child.forwarded(Hop(64513, "aas:64513/tool"), cost=100)

        self.log.record(parent, "forwarded", credits=100)
        self.log.record(child, "forwarded", credits=100)
        self.log.record(grandchild, "forwarded", credits=100)

        found = self.log.for_root(root)
        self.assertEqual(len(found), 3)
        # depth counts *down* towards the root as the tree is delegated
        # deeper, so this is the three hops in the order they happened.
        self.assertEqual([r.depth for r in found], [8, 7, 6])
        # The vector prepends, so the path reads newest hop first.
        self.assertEqual([r.path for r in found],
                         ["", "64512:aas:64512/planner",
                          "64513:aas:64513/tool,64512:aas:64512/planner"])

    def test_an_unknown_root_is_empty_not_an_error(self):
        self.log.record(mk(), "forwarded", credits=100)
        self.assertEqual(self.log.for_root("f" * 32), [])

    def test_entries_come_back_oldest_first(self):
        intent = mk()
        for _ in range(5):
            self.log.record(intent, "forwarded", credits=100)
        found = self.log.for_root(intent.root)
        self.assertEqual([r.seq for r in found], sorted(r.seq for r in found))

    def test_window_reports_when_the_answer_is_partial(self):
        """Trimmed in-memory history has to be visible, not inferred."""
        self.log = ReceiptLog(RealClock(), keep=4)
        for _ in range(9):
            self.log.record(mk(), "forwarded", credits=100)
        first, last = self.log.window()
        self.assertEqual((first, last), (5, 8))
        self.assertGreater(first, 0, "trimming happened and must be reportable")

    def test_window_of_an_empty_log(self):
        self.assertEqual(self.log.window(), (0, -1))

    def test_scoping_never_mutates_the_chain(self):
        intent = mk()
        self.log.record(intent, "forwarded", credits=100)
        before = self.log.tip
        self.log.for_root(intent.root)
        self.log.for_root("0" * 32)
        self.assertEqual(self.log.tip, before)
        self.assertTrue(self.log.verify()[0])

    def test_a_sweep_cannot_drop_a_receipt_the_caller_was_promised(self):
        """Housekeeping trims the log; the answer must not go quietly wrong."""
        self.log = ReceiptLog(RealClock(), keep=2)
        old = mk()
        self.log.record(old, "forwarded", credits=100)
        for _ in range(4):
            self.log.record(mk(), "forwarded", credits=100)
        self.assertEqual(self.log.for_root(old.root), [])
        first, _ = self.log.window()
        self.assertTrue(first > 0,
                        "an empty scoped answer over a trimmed log is only "
                        "honest because the window says so")


class TestReceiptsOverHttp(unittest.TestCase):
    def setUp(self):
        self.router = Router(RouterConfig(limit=8))
        self.router.route(Route("agent.task", "http://origin.invalid", price=100))
        self.router.route(Route("agent.write", "http://origin.invalid", price=100))
        self.httpd = serve(self.router, "127.0.0.1", 0, housekeep_every=3600.0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.housekeeper.stop.set()
        self.httpd.shutdown()
        self.httpd.server_close()

    def call(self, path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}",
                                        timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def forward(self, **kw):
        kw.setdefault("deadline", self.router.clock.now() + 30.0)
        return self.router.forward(mk(**kw), upstream=up)

    def test_a_root_can_read_its_own_audit_trail(self):
        intent = mk()
        self.forward(root=intent.root)
        status, body = self.call(f"/_weir/receipts/{intent.root}")
        self.assertEqual(status, 200)
        self.assertEqual(body["root"], intent.root)
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["entries"][0]["decision"], "forwarded")
        self.assertEqual(body["entries"][0]["credits"], 100)
        self.assertTrue(body["verified"])

    def test_the_answer_is_one_roots_work_and_nobody_elses(self):
        mine, theirs = mk(), mk()
        self.forward(root=mine.root, capability="agent.task")
        self.forward(root=mine.root, capability="agent.write")
        self.forward(root=theirs.root, capability="agent.task")
        self.refuse(root=mk().root)

        _, body = self.call(f"/_weir/receipts/{mine.root}")
        self.assertEqual(body["count"], 2)
        self.assertEqual({e["capability"] for e in body["entries"]},
                         {"agent.task", "agent.write"})
        self.assertEqual({e["principal"] for e in body["entries"]},
                         {"did:web:lab#arttu"})

    def test_a_refusal_is_visible_to_the_root_that_caused_it(self):
        root = new_root()
        d = self.refuse(root=root)
        self.assertFalse(d.ok)
        _, body = self.call(f"/_weir/receipts/{root}")
        self.assertEqual(body["entries"][0]["decision"], "refused")
        self.assertEqual(body["entries"][0]["reason"], d.reason)
        self.assertTrue(body["entries"][0]["reason"],
                        "a refusal with no reason recorded cannot be acted on")

    def test_a_failed_upstream_is_visible_too(self):
        root = new_root()
        with self.assertRaises(ConnectionResetError):
            self.router.forward(mk(root=root, deadline=self.router.clock.now() + 30.0),
                                upstream=lambda f, r: (_ for _ in ()).throw(
                                    ConnectionResetError("upstream died")))
        _, body = self.call(f"/_weir/receipts/{root}")
        self.assertEqual(body["entries"][0]["decision"], "failed")

    def test_an_unknown_root_is_a_404(self):
        status, body = self.call(f"/_weir/receipts/{new_root()}")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "no receipts for that root")

    def test_a_crafted_root_cannot_reach_another_roots_entries(self):
        mine = mk()
        self.forward(root=mine.root)
        for probe in ("../stats", "..%2f..%2fstats", f"{mine.root}%00",
                      f"{mine.root}../..", "%2e%2e%2f"):
            status, _ = self.call(f"/_weir/receipts/{probe}")
            self.assertEqual(status, 404, f"{probe!r} should not resolve")
        status, _ = self.call(f"/_weir/receipts/{mine.root}")
        self.assertEqual(status, 200, "the real root still resolves")

    def test_no_payload_reaches_the_wire(self):
        """The module's stated invariant, now that entries are readable.

        A receipt log is retained, replicated and subpoenaed; one full of
        prompts is a breach waiting for an occasion. That was a promise about
        what was recorded, and exposing the records over HTTP is exactly when
        somebody would go looking for the body in them.
        """
        marker = "S3CRET-CANARY-b7f3a91c"
        i = mk(deadline=self.router.clock.now() + 30.0)
        headers = {**to_headers(i), "content-type": "application/json"}
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/task",
                                     data=json.dumps({"prompt": marker}).encode(),
                                     method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            # The route points at an origin that is not there, so this is a
            # 502 - which still leaves a receipt, and a failed one, which is
            # the more interesting thing to go looking for a body in.
            self.assertEqual(exc.code, 502)
            exc.read()

        _, body = self.call(f"/_weir/receipts/{i.root}")
        self.assertNotIn(marker, json.dumps(body))
        self.assertEqual(body["entries"][0]["intent_digest"], i.intent_digest,
                         "the digest is kept so sameness can be shown without "
                         "keeping the question")

    def test_the_flat_endpoint_still_answers_as_it_did(self):
        """Additive means additive: the pre-existing shape is untouched."""
        status, body = self.call("/_weir/receipts")
        self.assertEqual(status, 200)
        self.assertEqual(sorted(body), ["detail", "tip", "verified"])

    def test_the_scoped_answer_reports_its_window(self):
        self.forward()
        root = self.router.receipts.entries()[0].root
        _, body = self.call(f"/_weir/receipts/{root}")
        self.assertEqual(sorted(body["window"]), ["from_seq", "to_seq", "trimmed"])
        self.assertFalse(body["window"]["trimmed"])

    def refuse(self, **kw):
        """A past deadline refuses at the scheduler, before anything is touched."""
        kw.setdefault("deadline", self.router.clock.now() - 1)
        return self.forward(**kw)


if __name__ == "__main__":
    unittest.main()
