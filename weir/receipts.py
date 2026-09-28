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
    decision: str          # forwarded | coalesced | cached | refused
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
