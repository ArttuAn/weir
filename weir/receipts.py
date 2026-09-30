"""The accountability plane.

Classical networks account in counters: NetFlow records, interface octets,
SNMP gauges.  Counters answer "how much" and cannot answer "on whose authority"
- which is the only question that matters when the thing that traversed your
network was an autonomous agent spending someone's money.

Every weir forwarding decision - forwarded, coalesced, or refused - emits a
receipt, and receipts are hash-chained: each carries the digest of its
predecessor, so an entry cannot be removed or reordered after the fact without
breaking every link that follows.  That gives an operator something a log file
does not: a tamper-evident answer to "what did this agent do on my network, and
who authorised it".

Deliberately recorded: principal, agent, capability, decision, credits, the
delegation path.  Deliberately *not* recorded: request payloads.  A receipt log
is retained, replicated and subpoenaed; a receipt log full of prompts is a
breach waiting for an occasion.  The intent digest is kept instead, which
supports "were these the same question" without preserving what the question
was.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from hashlib import blake2b

from .intent import Intent

GENESIS = "b2:" + "0" * 32


@dataclass(frozen=True, slots=True)
class Receipt:
    seq: int
    at: float
    root: str
    principal: str
    agent: str
    capability: str
    decision: str          # forwarded | coalesced | cached | refused | failed
    reason: str            # refusal reason, or ""
    credits: int
    depth: int
    path: str
    intent_digest: str
    coupling: str
    prev: str
    digest: str = ""

    def compute_digest(self) -> str:
        body = json.dumps({k: v for k, v in asdict(self).items() if k != "digest"},
                          sort_keys=True, separators=(",", ":")).encode()
        return "b2:" + blake2b(body, digest_size=16).hexdigest()


class ReceiptLog:
    """Append-only hash-chained decision log."""

    def __init__(self, clock, *, path: str | None = None, keep: int = 10_000) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: list[Receipt] = []
        self._tip = GENESIS
        self._seq = 0
        self.path = path
        self.keep = keep

    def record(self, intent: Intent, decision: str, *, reason: str = "",
               credits: int = 0) -> Receipt:
        with self._lock:
            r = Receipt(
                seq=self._seq,
                at=round(self._clock.now(), 6),
                root=intent.root,
                principal=intent.principal,
                agent=intent.agent,
                capability=intent.capability,
                decision=decision,
                reason=reason,
                credits=credits,
                depth=intent.depth,
                path=",".join(str(h) for h in intent.path),
                intent_digest=intent.intent_digest,
                coupling=intent.coupling,
                prev=self._tip,
            )
            from dataclasses import replace
            r = replace(r, digest=r.compute_digest())
            self._entries.append(r)
            self._tip = r.digest
            self._seq += 1
            if self.path:
                with open(self.path, "a") as fh:
                    fh.write(json.dumps(asdict(r), separators=(",", ":")) + "\n")
            if len(self._entries) > self.keep:
                # Trim the in-memory window only; the chain tip is unaffected,
                # and the file (if configured) is the durable record.
                del self._entries[: len(self._entries) - self.keep]
            return r

    @property
    def tip(self) -> str:
        return self._tip

    def entries(self) -> list[Receipt]:
        with self._lock:
            return list(self._entries)

    def for_root(self, root: str) -> list[Receipt]:
        """Every in-window receipt for one delegation tree, oldest first.

        The root is the natural unit of accountability: it is minted by whoever
        asked the question at the top and propagated unchanged down the tree, so
        "everything that root did" is the whole tree at every hop - which is
        exactly the question a principal asking "what did my agent spend, and
        on whose authority" is asking, and exactly what a flat dump of the log
        makes them wade through to answer.

        A linear scan, deliberately. The obvious optimisation is a by-root
        index, and the obvious reason to want it is that this gets called by an
        HTTP request. But an index is a second structure that can disagree with
        the chain, and a chain that misreports its own contents is a worse
        failure than a read that is O(n) over a window already capped at
        ``keep``. The window is the bound; say so rather than pretend otherwise.
        """
        with self._lock:
            return [r for r in self._entries if r.root == root]

    def window(self) -> tuple[int, int]:
        """Inclusive seq range still held in memory.

        Anything below ``from_seq`` was trimmed and survives only in the file,
        if one was configured.  A caller reading a root's receipts through a
        trimmed window is looking at a partial answer and has to be able to
        tell, so this is reported alongside the entries rather than left for
        the reader to infer from a seq number.
        """
        with self._lock:
            if not self._entries:
                return (0, -1)
            return (self._entries[0].seq, self._entries[-1].seq)

    def verify(self) -> tuple[bool, str]:
        """Re-walk the chain.  Returns ``(ok, message)``."""
        entries = self.entries()
        if not entries:
            return True, "empty chain"
        prev = entries[0].prev
        for r in entries:
            if r.prev != prev:
                return False, f"broken link at seq {r.seq}: prev mismatch"
            if r.compute_digest() != r.digest:
                return False, f"tampered entry at seq {r.seq}: digest mismatch"
            prev = r.digest
        return True, f"{len(entries)} receipts verified, tip {prev[:14]}"


def verify_file(path: str) -> tuple[bool, str]:
    """Verify a receipt chain written to disk."""
    prev = None
    n = 0
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            stored = d.pop("digest")
            body = json.dumps(d, sort_keys=True, separators=(",", ":")).encode()
            if "b2:" + blake2b(body, digest_size=16).hexdigest() != stored:
                return False, f"tampered entry at seq {d.get('seq')}"
            if prev is not None and d["prev"] != prev:
                return False, f"broken link at seq {d.get('seq')}"
            prev = stored
            n += 1
    return True, f"{n} receipts verified, tip {(prev or GENESIS)[:14]}"
