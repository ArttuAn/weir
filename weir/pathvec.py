"""Loop prevention for delegation, not for topology.

IP's hop limit exists because a packet can circle a physical topology.  It is a
counter because the router has no idea *why* the packet is going round; eight
bits of "give up eventually" is the best you can do when you cannot see the
loop.

Agent loops are not topological.  A research agent calls a summariser, which
calls a search tool, which calls the research agent to expand a query.  Every
hop is a different host, a different address, a different TCP connection; no
hop limit can distinguish that cycle from a legitimately deep pipeline, so
today it terminates only when somebody's bill does.

BGP solved exactly this shape of problem in 1994 with the AS_PATH: carry where
you have been, and refuse anything that has already been through you.  A weir
router applies the same rule to delegation.  The difference from a hop limit
matters in both directions:

  * a genuine 12-stage pipeline is *allowed*, where a tight TTL would kill it;
  * a 3-cycle is killed on its first repetition, where a TTL would let it run
    255 times first - and in agent terms 255 laps is a five-figure invoice.

Two checks, in this order:

``loop``   this hop already appears in the path vector -> terminal refusal.
``depth``  hops remaining hit zero -> terminal refusal.

Both are terminal: retrying a loop reproduces the loop.
"""

from __future__ import annotations

from collections import Counter

from .errors import Reason, Refused
from .intent import Hop, Intent


class PathGuard:
    """Delegation loop and depth enforcement.

    ``repeat_limit`` allows a hop to legitimately appear more than once in a
    path - a router that fronts several capabilities may be traversed twice in
    one honest pipeline.  The default of 1 is strict BGP semantics; raise it
    per-deployment rather than globally disabling the check.
    """

    def __init__(self, *, repeat_limit: int = 1, max_path: int = 32) -> None:
        self.repeat_limit = repeat_limit
        self.max_path = max_path

    def check(self, intent: Intent, here: Hop) -> None:
        if intent.depth <= 0:
            raise Refused(Reason.DEPTH_EXCEEDED,
                          f"depth exhausted after {len(intent.path)} hops")
        if len(intent.path) >= self.max_path:
            raise Refused(Reason.DEPTH_EXCEEDED,
                          f"path vector at limit ({self.max_path})")

        counts = Counter(intent.path)
        if counts[here] >= self.repeat_limit:
            cycle = _cycle_from(intent.path, here)
            raise Refused(Reason.DELEGATION_LOOP,
                          f"{here} already in path; cycle: {cycle}")

        # A hop that appears repeatedly *anywhere* in the path is a loop that
        # happens not to include us - worth killing while we can see it, since
        # the router that could see it best may be the one being looped.
        for hop, n in counts.items():
            if n > self.repeat_limit:
                raise Refused(Reason.DELEGATION_LOOP,
                              f"{hop} appears {n}x in delegation path")


def _cycle_from(path: tuple[Hop, ...], here: Hop) -> str:
    """Render the cycle for the operator, most recent hop last."""
    out: list[str] = []
    for hop in path:
        out.append(str(hop))
        if hop == here:
            break
    return " -> ".join(reversed(out)) + f" -> {here}"
