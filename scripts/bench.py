#!/usr/bin/env python3
"""Measure what the physical design has to be dimensioned against.

Two very different kinds of number come out of here, and conflating them would
be the easiest way to mislead someone building this:

**State sizes transfer.** Bytes per root account, bytes per receipt, bytes per
cache entry are facts about the data structures. A C or Rust data plane would
land within a small factor, and the storage and memory arithmetic in
docs/PHYSICAL.md is built on these.

**CPU costs do not transfer.** These are CPython figures. A production data
plane would not be written in Python, so treat every rate here as a *floor* -
evidence that the ordering of stage costs is what the design claims, not a
throughput target anybody should quote.

Usage:  python3 scripts/bench.py
"""

from __future__ import annotations

import json
import platform
import statistics
import sys
import time
import tracemalloc
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from weir.attest import Keyring
from weir.clock import VirtualClock
from weir.errors import Refused
from weir.fib import Route
from weir.intent import (CREDIT, Hop, Intent, digest_of, from_headers, new_root,
                         to_headers)
from weir.ledger import Ledger
from weir.receipts import ReceiptLog
from weir.router import Router, RouterConfig

KEY = b"k" * 32


def mk(**kw) -> Intent:
    base = dict(principal="did:web:example.com#arttu",
                agent="aas:64512/planner-3", capability="llm.completion",
                root=new_root(), deadline=1_800_000_030.0, declared_cost=400,
                coupling="human", path=(Hop(64500, "edge-1"), Hop(64512, "mesh-2")),
                intent_digest=digest_of("llm.completion", {"q": "x" * 64}))
    base.update(kw)
    return Intent(**base)


def timed(label: str, fn, n: int, *, unit="op") -> dict:
    fn()  # warm
    best = None
    for _ in range(5):
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
    per = best / n
    rate = 1.0 / per
    print(f"  {label:<38} {per * 1e6:9.2f} us   {rate:12,.0f} {unit}/s")
    return {"label": label, "us": round(per * 1e6, 3), "per_sec": int(rate)}


def section(title: str) -> None:
    print(f"\n{title}\n" + "-" * len(title))


def bench_cpu() -> list[dict]:
    section("Per-stage CPU cost (CPython — a floor, not a target)")
    out = []

    i = mk()
    headers = {k.lower(): v for k, v in to_headers(i).items()}
    out.append(timed("stage 1  parse WIH headers", lambda: from_headers(headers), 20_000))

    kr = Keyring({64512: KEY})
    signed = kr.attested(i)
    out.append(timed("stage 2  verify attestation (MAC)", lambda: kr.verify(signed), 20_000))

    clock = VirtualClock()
    r = Router(RouterConfig(limit=64, default_grant=10_000 * CREDIT), clock=clock)
    r.route(Route("llm.completion", "upstream", price=400, latency=0.4))

    # A loop, refused at stage 3 - the cheapest possible refusal.
    loop = mk(path=(Hop(64512, "weir-0"),))
    out.append(timed("stages 1-3  refuse a delegation loop",
                     lambda: r.forward(loop, upstream=None), 5_000))

    # Full decision, upstream stubbed out so we measure the router only.
    noop = lambda fwd, route: None
    counter = {"n": 0}

    def full():
        counter["n"] += 1
        r.forward(mk(root=f"root{counter['n'] % 512}",
                     intent_digest=f"b2:u{counter['n']}"), upstream=noop)

    out.append(timed("stages 1-12  full forwarding decision", full, 5_000, unit="dec"))
    return out


def bench_state() -> dict:
    section("State size (transfers to any implementation)")

    # Root accounts.
    n = 50_000
    tracemalloc.start()
    base = tracemalloc.take_snapshot()
    ledger = Ledger(VirtualClock(), default_grant=100 * CREDIT)
    roots = [f"{i:032x}" for i in range(n)]
    for rt in roots:
        ledger.reserve(rt, "did:web:example.com#arttu", 400)
    after = tracemalloc.take_snapshot()
    acct_bytes = sum(s.size_diff for s in after.compare_to(base, "filename")) / n
    tracemalloc.stop()
    print(f"  ledger: {acct_bytes:8.0f} bytes per live root account "
          f"(incl. dict overhead and the root key)")

    # Receipts, as written to disk.
    log = ReceiptLog(VirtualClock())
    rec = log.record(mk(), "forwarded", credits=400)
    line = json.dumps(asdict(rec), separators=(",", ":"))
    print(f"  receipt: {len(line):7d} bytes per decision (one JSON line, on the wire)")

    # Coalescer keys.
    i = mk()
    key_bytes = sum(len(str(x)) for x in (i.principal, i.capability, i.intent_digest))
    print(f"  coalesce: {key_bytes:6d} bytes per cache key "
          f"(principal + capability + digest), plus the cached body")

    # Appointments are stateless on the router: a signed bearer token.
    appt = r_appt = len(
        "1800000000.188|1|1800000000.000|" + "0" * 24)
    print(f"  appointment: {appt:3d} bytes, held by the CALLER — the router keeps"
          f"\n               only a slot cursor per class, not a per-caller booking")

    return {"root_account_bytes": round(acct_bytes),
            "receipt_bytes": len(line), "coalesce_key_bytes": key_bytes}


def dimension(cpu: list[dict], state: dict) -> None:
    section("What that means at 10,000 decisions/second")
    rps = 10_000
    rec_day = rps * state["receipt_bytes"] * 86_400 / 1e12
    print(f"  receipts            {rps * state['receipt_bytes'] / 1e6:6.1f} MB/s"
          f"  =  {rec_day:.2f} TB/day uncompressed")
    print(f"                      {rec_day * 0.12:.2f} TB/day at a conservative 8:1"
          f" (receipts are highly repetitive)")
    for live_s in (60, 3600):
        gb = rps * live_s * state["root_account_bytes"] / 1e9
        print(f"  ledger, {live_s:>4}s TTL {gb:6.1f} GB resident "
              f"({rps * live_s:,} live roots)")
    print("\n  Storage is the binding constraint, not CPU and not bandwidth.")
    print("  See docs/PHYSICAL.md for what to do about it.")


def main() -> int:
    print("weir bench")
    print(f"  {platform.python_implementation()} {platform.python_version()} "
          f"on {platform.machine()}  ·  {platform.system()}")
    print("  CPU rates are a FLOOR (CPython). State sizes transfer.")
    cpu = bench_cpu()
    state = bench_state()
    dimension(cpu, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
