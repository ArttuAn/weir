"""A caller with a wrong clock must not be punished for it.

Appointments are absolute timestamps produced by the router's clock. A caller
that subtracts its *own* wall clock from one folds its skew straight into the
wait, arrives early, and is charged an early-retry penalty for what is actually
an NTP problem. These tests pin the two properties that make that impossible.

See docs/PHYSICAL.md, "Time".
"""

from __future__ import annotations

import threading
import time
import unittest

from weir.client import CallFailed, Caller
from weir.fib import Route
from weir.intent import CREDIT
from weir.router import Router, RouterConfig
from weir.server import EchoOrigin, serve, serve_origin


class SkewedClock:
    """A wall clock that is wrong by a fixed amount."""

    def __init__(self, skew: float) -> None:
        self.skew = skew

    def now(self) -> float:
        return time.time() + self.skew

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class TestClockSkew(unittest.TestCase):
    """End to end, over real sockets, with the caller's clock badly wrong."""

    ORIGIN, ROUTER = 8931, 8930

    @classmethod
    def setUpClass(cls):
        EchoOrigin.delay = 0.05
        cls.origin = serve_origin(port=cls.ORIGIN)
        threading.Thread(target=cls.origin.serve_forever, daemon=True).start()

        cls.router = Router(RouterConfig(limit=2, default_grant=50 * CREDIT))
        cls.router.route(Route("agent.task", f"http://127.0.0.1:{cls.ORIGIN}/",
                               price=100, latency=0.05))
        cls.httpd = serve(cls.router, port=cls.ROUTER)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        time.sleep(0.3)
        cls.url = f"http://127.0.0.1:{cls.ROUTER}/"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.origin.shutdown()

    def test_router_echoes_its_own_clock(self):
        c = Caller(self.url, "did:web:lab#a", "aas:64512/skew0")
        c.call("agent.task", {"q": 1}, deadline=time.time() + 10)
        self.assertNotEqual(c.clock_offset, 0.0,
                            "caller should have learned a clock offset")
        self.assertLess(abs(c.clock_offset), 1.0,
                        "offset against a local router should be tiny")

    def test_skewed_caller_learns_the_offset(self):
        for skew in (-45.0, 45.0):
            with self.subTest(skew=skew):
                c = Caller(self.url, "did:web:lab#a", f"aas:64512/s{int(skew)}",
                           clock=SkewedClock(skew))
                c.call("agent.task", {"q": 2}, deadline=c.clock.now() + 10)
                # The learned offset must cancel the skew we injected.
                self.assertAlmostEqual(c.clock_offset, -skew, delta=1.0)

    def test_skewed_caller_is_not_charged_an_early_retry_penalty(self):
        """The property that matters: skew must not cost the caller money."""
        before = self.router.damper.stats()["early_retries"]

        results = []
        lock = threading.Lock()

        def agent(i: int) -> None:
            # Every caller's clock is wrong, by a different amount, in both
            # directions - far beyond the damper's 50ms grace window.
            skew = [-30.0, -5.0, 0.0, 5.0, 30.0][i % 5]
            c = Caller(self.url, "did:web:lab#a", f"aas:64512/w{i}",
                       clock=SkewedClock(skew), budget=50 * CREDIT)
            try:
                c.call("agent.task", {"q": i}, coupling="human",
                       deadline=c.clock.now() + 20)
                with lock:
                    results.append(True)
            except CallFailed:
                with lock:
                    results.append(False)

        threads = [threading.Thread(target=agent, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        early = self.router.damper.stats()["early_retries"] - before
        self.assertEqual(early, 0,
                         f"{early} callers were charged for having a wrong clock")
        self.assertTrue(all(results), "every caller should still have been served")


if __name__ == "__main__":
    unittest.main(verbosity=2)
