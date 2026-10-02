"""Runnable demonstrations.

Every number in ``README.md`` and ``docs/DESIGN.md`` comes from ``weir demo``.
Run them yourself; they are deterministic.

Read the caveats in :mod:`weir.sim` first.  These are simulations under a
documented model, not measurements of any real endpoint.  What they establish
is the *shape* of each mechanism's effect and, in the storm case, an honest
boundary where the mechanism stops helping.
"""

from __future__ import annotations

import json

from .clock import VirtualClock
from .errors import Reason
from .fib import Route
from .intent import CREDIT, Hop, Intent, new_root
from .router import Router, RouterConfig
from .sim import AgentSpec, run_storm


def _rule(title: str) -> None:
    print(f"\n{title}\n" + "=" * len(title))


# ---------------------------------------------------------------------------

def demo_storm(n: int = 400) -> None:
    _rule("1. Retry storm under overload")
    print(f"{n} agents, upstream ~9 req/s for 20s (so ~180 of {n} can possibly be served).")
    print("Same caller population against both routers; only the router differs.")
    print("A 'noncompliant' caller ignores whatever backoff it is given and retries")
    print("every 200ms - not malice, just a retry decorator left at its defaults.\n")

    head = (f"{'noncompliant':>12} | {'served':>7} {'p95 human':>10} {'attempts':>9} "
            f"{'wasted up.s':>12} | {'served':>7} {'p95 human':>10} {'attempts':>9} {'wasted up.s':>12}")
    print(" " * 14 + "| " + "classic gateway".center(42) + "| " + "weir".center(42))
    print(head)
    print("-" * len(head))
    for nc in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0):
        c = run_storm(AgentSpec(n=n, noncompliant=nc), weir=False)
        w = run_storm(AgentSpec(n=n, noncompliant=nc), weir=True, grant=2 * CREDIT)
        print(f"{nc:>11.0%} | {c.succeeded:7} {c.p(95, True):10.1f} {c.attempts:9} "
              f"{c.wasted_seconds:12.1f} | {w.succeeded:7} {w.p(95, True):10.1f} "
              f"{w.attempts:9} {w.wasted_seconds:12.1f}")

    print("""
Read this honestly.  Up to roughly half the callers misbehaving, weir serves
more requests, at a better p95 for the people actually waiting, with half the
attempts and a fraction of the wasted upstream work.

Past that point weir serves *fewer* requests than the classic gateway, and that
is the mechanism working rather than failing: callers that ignore backpressure
spend their grant on escalating refusals and are cut off, and the capacity they
would have taken goes to callers that behaved.  When every caller misbehaves
there is nobody left to hand it to, so goodput falls - but it falls having done
a quarter of the work, and the people who were waiting still saw a better p95.

The honest conclusion is not "weir beats a gateway".  A well-configured gateway
with a hard concurrency limit already survives a simple storm.  It is that the
*cost* of the storm - in attempts, in wasted inference, in the latency a person
experiences - is borne by whoever caused it, and that the penalty schedule is a
real dial with a real trade-off, not a free win.""")


# ---------------------------------------------------------------------------

def demo_cycle() -> None:
    _rule("2. Delegation loop")
    print("A researcher agent calls a summariser, which calls a search tool, which")
    print("calls the researcher to expand the query.  Three different hosts, three")
    print("different addresses, three healthy TCP connections.  No hop limit can see")
    print("this, because nothing about it is topological.\n")

    clock = VirtualClock()
    router = Router(RouterConfig(aas=64512, name="edge", default_grant=50 * CREDIT),
                    clock=clock)
    router.route(Route("agent", "upstream", price=100, latency=0.2))

    root = new_root()
    ring = ["research", "summarise", "search"]
    path: tuple[Hop, ...] = ()
    for lap in range(3):
        for name in ring:
            intent = Intent(principal="did:web:lab#arttu", agent=f"aas:64512/{name}",
                            capability="agent.task", root=root,
                            deadline=clock.now() + 60, declared_cost=100,
                            path=path, depth=64,
                            intent_digest=f"b2:{name}-{lap}")
            d = router.forward(intent, upstream=lambda f, r: {"ok": True})
            mark = "forwarded" if d.ok else f"REFUSED {d.reason}"
            print(f"  lap {lap} {name:<10} depth={intent.depth:<3} "
                  f"path={len(path):<2} -> {mark}")
            if not d.ok:
                print(f"\n  {d.detail}")
                print("\n  Killed on the first repetition, not the 255th.  With a hop limit")
                print("  of 255 this cycle runs 85 more laps first, and on this network a")
                print("  lap is an inference call, not a wire transit.")
                return
            path = (intent.hop,) + path


# ---------------------------------------------------------------------------

def demo_swarm() -> None:
    _rule("3. Runaway fan-out")
    print("An agent whose termination condition is wrong: every node spawns 5")
    print("children, 5 levels deep.  3,905 calls, and nothing in the code is going")
    print("to stop it, because the bug is that nothing in the code stops it.")
    print("Per-node fan-out is 5, which is within the router's width limit - so the")
    print("width limiter never fires and the budget is the only thing holding.\n")

    clock = VirtualClock()
    router = Router(RouterConfig(default_grant=20 * CREDIT, max_fanout=5), clock=clock)
    router.route(Route("agent", "upstream", price=100, latency=0.05))

    root = new_root()
    funded = 0
    refused = 0
    reasons: dict[str, int] = {}
    frontier = [Hop(64512, "root-0")]
    total = 0

    print(f"{'level':>6} {'nodes':>8} {'funded':>8} {'refused':>9}   {'binding constraint':<20}")
    for level in range(1, 6):
        nxt: list[Hop] = []
        lf = lr = 0
        for parent in frontier:
            for i in range(5):
                total += 1
                child = Hop(64512, f"n{level}-{len(nxt)}")
                intent = Intent(principal="did:web:lab#arttu", agent=str(child).replace(":", "/", 1).replace("64512/", "aas:64512/"),
                                capability="agent.task", root=root,
                                deadline=clock.now() + 300, declared_cost=100,
                                depth=16, path=(parent,),
                                intent_digest=f"b2:{level}-{total}")
                d = router.forward(intent, upstream=lambda f, r: {"ok": True})
                if d.ok:
                    lf += 1
                    nxt.append(child)
                else:
                    lr += 1
                    reasons[d.reason] = reasons.get(d.reason, 0) + 1
        funded += lf
        refused += lr
        top = max(reasons, key=reasons.get) if reasons else "-"
        print(f"{level:>6} {len(frontier) * 5:>8} {lf:>8} {lr:>9}   {top:<20}")
        frontier = nxt
        if not frontier:
            break

    acct = router.ledger.get(root)
    print(f"\n  {total} calls attempted, {funded} funded, {refused} refused at the edge.")
    print(f"  root spent {acct.settled + acct.refused} of {acct.granted} millicredits.")
    print("\n  Nobody configured a limit for this swarm.  The grant was the limit.")
    print("  Fan-out divides a budget, it does not multiply one, so the tree")
    print("  terminates as arithmetic rather than as a thing someone remembered.")
    print("  Note the shape: the swarm dies at the level where the money runs out,")
    print("  and every call after that is refused for the cost of parsing a header.")


# ---------------------------------------------------------------------------

def demo_deadline() -> None:
    _rule("4. Expiry before execution")
    print("Half the load is a person waiting; half is a batch crawl. Both are the")
    print("same verb to the same endpoint - no port number tells them apart.\n")

    clock = VirtualClock()
    router = Router(RouterConfig(limit=8, default_grant=100 * CREDIT), clock=clock)
    router.route(Route("agent", "upstream", price=100, latency=0.4))

    saved = 0
    forwarded = 0
    refused_expired = 0
    for i in range(200):
        # A third of the batch work arrives already past its deadline: the
        # caller timed out, took a fallback, and moved on - but nothing on the
        # path ever told the upstream that.
        human = i % 2 == 0
        expired = (not human) and (i % 3 == 0)
        deadline = clock.now() + (5.0 if not expired else -0.5)
        intent = Intent(principal="did:web:lab#arttu", agent=f"aas:64512/a{i}",
                        capability="agent.task", root=new_root(),
                        deadline=deadline, declared_cost=100,
                        coupling="human" if human else "batch",
                        intent_digest=f"b2:x{i}")
        d = router.forward(intent, upstream=lambda f, r: {"ok": True})
        if d.ok:
            forwarded += 1
        elif d.reason in (Reason.DEADLINE_PASSED, Reason.INFEASIBLE):
            refused_expired += 1
            saved += 1

    print(f"  forwarded            {forwarded}")
    print(f"  dropped as worthless {refused_expired}")
    print(f"\n  Those {refused_expired} would have been forwarded by any router on earth")
    print("  today, computed in full, billed in full, and thrown away on arrival,")
    print("  because nothing in HTTP carries 'the caller already gave up'.")
    print(json.dumps(router.stats()["sched"], indent=2))


DEMOS = {
    "storm": demo_storm,
    "cycle": demo_cycle,
    "swarm": demo_swarm,
    "deadline": demo_deadline,
}


def run(name: str = "all") -> None:
    if name == "all":
        for fn in DEMOS.values():
            fn()
    else:
        DEMOS[name]()
