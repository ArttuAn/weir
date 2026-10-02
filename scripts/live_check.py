#!/usr/bin/env python3
"""End-to-end check over real sockets.

Not a unit test - it stands up an origin, a router and a pack of concurrent
agents on real TCP and asserts the interesting behaviours survive the wire.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from weir.client import Caller, CallFailed
from weir.fib import Route
from weir.intent import CREDIT
from weir.router import Router, RouterConfig
from weir.server import EchoOrigin, serve, serve_origin

ORIGIN_PORT, ROUTER_PORT = 8911, 8910
failures = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    EchoOrigin.delay = 0.15
    origin = serve_origin(port=ORIGIN_PORT)
    threading.Thread(target=origin.serve_forever, daemon=True).start()

    router = Router(RouterConfig(limit=4, default_grant=3 * CREDIT, name="edge"))
    router.route(Route("agent.task", f"http://127.0.0.1:{ORIGIN_PORT}/", price=100,
                       latency=0.15))
    httpd = serve(router, port=ROUTER_PORT)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.4)
    url = f"http://127.0.0.1:{ROUTER_PORT}/"

    print("\n1. one call over real TCP")
    c = Caller(url, "did:web:lab#arttu", "aas:64512/cli")
    out = c.call("agent.task", {"q": "hello"}, coupling="human")
    body = json.loads(out["result"]["body"])
    check("forwarded", out["disposition"] == "forwarded")
    check("origin saw the principal", body["saw_principal"] == "did:web:lab#arttu")
    check("origin saw the path vector", body["saw_path"] == "64512:edge", body["saw_path"])
    check("depth was decremented", int(body["saw_depth"]) == 7, body["saw_depth"])

    print("\n2. 24 concurrent agents against a limit of 4")
    res = {"ok": 0, "refused": {}}
    lock = threading.Lock()

    def agent(i: int) -> None:
        cc = Caller(url, "did:web:lab#arttu", f"aas:64512/a{i}", budget=3 * CREDIT)
        try:
            cc.call("agent.task", {"q": f"q{i}"},
                    coupling="human" if i % 2 else "batch", deadline=time.time() + 10)
            with lock:
                res["ok"] += 1
        except CallFailed as exc:
            with lock:
                res["refused"][exc.reason] = res["refused"].get(exc.reason, 0) + 1

    threads = [threading.Thread(target=agent, args=(i,)) for i in range(24)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - t0
    check("all callers resolved", res["ok"] + sum(res["refused"].values()) == 24)
    check("appointments were honoured",
          router.damper.stats()["appointments_kept"] > 0,
          f"{router.damper.stats()['appointments_kept']} kept")
    check("no caller was starved", res["ok"] >= 20,
          f"{res['ok']}/24 in {elapsed:.1f}s, refusals={res['refused']}")

    print("\n3. budget exhaustion over the wire")
    poor = Caller(url, "did:web:poor#p", "aas:64512/poor", budget=1 * CREDIT)
    reason = None
    for n in range(40):
        try:
            poor.call("agent.task", {"q": n}, deadline=time.time() + 6)
        except CallFailed as exc:
            reason = exc.reason
            break
    check("grant is enforced", reason == "budget-exhausted", str(reason))

    print("\n4. accountability")
    ok, msg = router.receipts.verify()
    check("receipt chain verifies", ok, msg)
    seen = router.counters["seen"]
    check("every request left a receipt", router.receipts._seq == seen,
          f"{router.receipts._seq} receipts / {seen} requests")

    httpd.shutdown()
    origin.shutdown()

    print("\n" + ("all live checks passed" if not failures
                  else f"FAILED: {', '.join(failures)}"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
