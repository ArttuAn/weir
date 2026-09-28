"""The Weir Intent Header (WIH-0) - what a weir router forwards on.

A classical router reads a 20-byte IPv4 header and asks one question: *where is
this going?*  Everything it can do follows from the answer, which is why its
only tools are drop, delay and forward, and why its only loop defence is a hop
counter.

A weir router reads an intent header and asks four:

    who authorised this          -> principal, attested
    what is it trying to do      -> capability + intent digest
    what may it spend            -> root, budget, declared cost
    when does the answer expire  -> deadline, coupling

Those four questions are sufficient to express every mechanism in this
repository.  They are also, deliberately, the smallest set that is: each field
below exists because some classical mechanism fails without it.

Field reference
---------------

``version``        WIH version.  0 is this document.
``principal``      Who ultimately authorised the work - a human or an org, not
                   a machine.  Survives every hop unchanged.  This is the field
                   an address can never carry: an agent's IP tells you which
                   datacentre it rents, not whose authority it acts under.
``agent``          The agent executing *this* hop, as ``aas:<n>/<name>``.
                   Changes at every hop.
``aas``            Agent Autonomous System number - the administrative domain
                   that vouches for ``agent``, analogous to a BGP AS.
``path``           Delegation path vector, most recent first.  The AS_PATH of
                   the agent internet, and the reason weir can kill semantic
                   loops that a TTL cannot even see.
``depth``          Hops remaining.  Decremented every hop; a swarm TTL.
``root``           Identifier of the originating request.  Every descendant of
                   one human action shares a root, which is what makes budget
                   conservation possible across fan-out.
``budget``         The caller's *view* of remaining budget, in millicredits.
                   Advisory only - the router's root ledger is authoritative,
                   exactly as TTL is authoritative in the network rather than
                   at the sender.
``declared_cost``  What the caller expects this hop's subtree to cost.  Used
                   for admission; reconciled against observed cost afterwards.
``deadline``       Absolute unix time after which the answer is worthless.
                   Not a timeout: a timeout says when the caller gives up, a
                   deadline says when the work stops being worth doing.
``coupling``       ``human`` if a person is blocked on this right now, else
                   ``batch``.  The single most valuable bit in the header and
                   the one DSCP has no way to express.
``capability``     What is being asked for, as a dotted capability name
                   (``search.web``, ``llm.completion``).  The forwarding key.
``intent_digest``  Canonical digest of the semantic request.  Two agents that
                   ask the same question in different words produce the same
                   digest, which is what makes cross-caller coalescing possible.
``idempotency``    Key for safe retries; identical retries are recognised
                   rather than re-executed.
``safe``           True if this request has no side effects.  Only safe
                   requests may be coalesced or served from cache.
``attest``         Keyed MAC over the canonical form, by the issuing AAS.

Wire encoding
-------------

On HTTP/1.1 and HTTP/2 each field is its own ``weir-*`` header, so a router can
make its drop decisions (loop, depth, deadline) after parsing three short ASCII
headers and without decoding a body.  That is the same reason IPv4 puts TTL in
a fixed offset.  A binary encoding for a hardware data plane is sketched in
``docs/SPEC-WIH-0.md``; nothing in the design depends on the textual one.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Iterable

WIH_VERSION = 0

#: Millicredits.  One credit is defined as the cost of one unit of reference
#: work (see docs/DESIGN.md "What a credit is"); millicredits keep the field an
#: integer so that routers never do floating point on the fast path.
CREDIT = 1000

_AGENT_RE = re.compile(r"^aas:(\d+)/([A-Za-z0-9._\-]{1,64})$")
_CAP_RE = re.compile(r"^[a-z][a-z0-9]*(\.[a-z0-9\-]+){0,6}$")


class MalformedIntent(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Hop:
    """One element of the delegation path vector."""

    aas: int
    agent: str

    def __str__(self) -> str:
        return f"{self.aas}:{self.agent}"

    @staticmethod
    def parse(text: str) -> "Hop":
        aas, _, agent = text.partition(":")
        if not agent or not aas.isdigit():
            raise MalformedIntent(f"bad path element {text!r}")
        return Hop(int(aas), agent)


@dataclass(frozen=True, slots=True)
class Intent:
    """A parsed Weir Intent Header."""

    principal: str
    agent: str
    capability: str
    root: str
    deadline: float
    depth: int = 8
    budget: int = 10 * CREDIT
    declared_cost: int = CREDIT
    coupling: str = "batch"
    path: tuple[Hop, ...] = ()
    intent_digest: str = ""
    idempotency: str = ""
    safe: bool = True
    version: int = WIH_VERSION
    attest: str = ""
    #: Not on the wire: set by the router when it recognises a retry.
    attempt: int = 0

    # -- derived -------------------------------------------------------

    @property
    def aas(self) -> int:
        m = _AGENT_RE.match(self.agent)
        if not m:
            raise MalformedIntent(f"bad agent id {self.agent!r}")
        return int(m.group(1))

    @property
    def hop(self) -> Hop:
        m = _AGENT_RE.match(self.agent)
        if not m:
            raise MalformedIntent(f"bad agent id {self.agent!r}")
        return Hop(int(m.group(1)), m.group(2))

    @property
    def human_coupled(self) -> bool:
        return self.coupling == "human"

    def time_left(self, now: float) -> float:
        return self.deadline - now

    # -- validation ----------------------------------------------------

    def validate(self) -> None:
        if self.version != WIH_VERSION:
            raise MalformedIntent(f"unsupported WIH version {self.version}")
        if not _AGENT_RE.match(self.agent):
            raise MalformedIntent(f"bad agent id {self.agent!r}")
        if not _CAP_RE.match(self.capability):
            raise MalformedIntent(f"bad capability {self.capability!r}")
        if not self.principal:
            raise MalformedIntent("missing principal")
        if not self.root:
            raise MalformedIntent("missing root")
        if self.coupling not in ("human", "batch"):
            raise MalformedIntent(f"bad coupling {self.coupling!r}")
        if self.depth < 0:
            raise MalformedIntent("negative depth")
        if self.budget < 0 or self.declared_cost < 0:
            raise MalformedIntent("negative budget")
        if len(self.path) > 64:
            raise MalformedIntent("path vector too long")

    # -- canonical form ------------------------------------------------

    def canonical(self) -> bytes:
        """Deterministic byte form, for attestation and receipts.

        Excludes ``attest`` (it signs this) and ``attempt`` (router-local).
        """
        return json.dumps(
            {
                "v": self.version,
                "principal": self.principal,
                "agent": self.agent,
                "capability": self.capability,
                "root": self.root,
                "deadline": round(self.deadline, 3),
                "depth": self.depth,
                "budget": self.budget,
                "declared_cost": self.declared_cost,
                "coupling": self.coupling,
                "path": [str(h) for h in self.path],
                "intent_digest": self.intent_digest,
                "idempotency": self.idempotency,
                "safe": self.safe,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    # -- transforms ----------------------------------------------------

    def forwarded(self, by: Hop, cost: int) -> "Intent":
        """The header as it leaves this router, heading to the next hop.

        Decrements depth, prepends this hop to the path vector, and debits the
        advisory budget.  Attestation is cleared: the next hop's attestation is
        the forwarding router's to make, not the caller's to forge.
        """
        return replace(
            self,
            depth=self.depth - 1,
            path=(by,) + self.path,
            budget=max(0, self.budget - cost),
            attest="",
        )

    def child(self, agent: str, capability: str, share: int, *, safe: bool = True,
              digest: str = "", deadline: float | None = None) -> "Intent":
        """Derive a sub-request for a fan-out call.

        The child inherits root, principal, deadline and coupling.  It cannot
        inherit more budget than the parent holds, and it cannot outlive the
        parent's deadline - the two invariants that make a recursive swarm
        terminate without anyone having to remember to make it terminate.
        """
        return replace(
            self,
            agent=agent,
            capability=capability,
            budget=min(share, self.budget),
            declared_cost=min(share, self.budget),
            deadline=self.deadline if deadline is None else min(deadline, self.deadline),
            intent_digest=digest,
            idempotency=uuid.uuid4().hex,
            safe=safe,
            attest="",
            attempt=0,
        )


def digest_of(capability: str, payload: Any) -> str:
    """Canonical intent digest.

    Keyed on the *semantic* request, not the byte stream: the same question
    asked with different whitespace, key order or transport framing yields one
    digest.  A CDN cache keyed on a URL cannot do this, which is why a hundred
    agents asking one question today produce a hundred origin hits.
    """
    blob = json.dumps({"c": capability, "p": payload}, sort_keys=True,
                      separators=(",", ":"), default=str).encode()
    return "b2:" + hashlib.blake2b(blob, digest_size=16).hexdigest()


def new_root() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# HTTP wire form
# ---------------------------------------------------------------------------

_H = "weir-"


def to_headers(i: Intent) -> dict[str, str]:
    h = {
        _H + "version": str(i.version),
        _H + "principal": i.principal,
        _H + "agent": i.agent,
        _H + "capability": i.capability,
        _H + "root": i.root,
        _H + "depth": str(i.depth),
        _H + "budget": str(i.budget),
        _H + "declared-cost": str(i.declared_cost),
        _H + "deadline": f"{i.deadline:.3f}",
        _H + "coupling": i.coupling,
        _H + "safe": "1" if i.safe else "0",
    }
    if i.path:
        h[_H + "path"] = ",".join(str(x) for x in i.path)
    if i.intent_digest:
        h[_H + "intent-digest"] = i.intent_digest
    if i.idempotency:
        h[_H + "idempotency"] = i.idempotency
    if i.attest:
        h[_H + "attest"] = i.attest
    return h


def _req(h: dict[str, str], name: str) -> str:
    v = h.get(_H + name)
    if v is None:
        raise MalformedIntent(f"missing {_H}{name}")
    return v


def from_headers(h: dict[str, str]) -> Intent:
    """Parse a WIH-0 header set.  Header names are assumed lowercased."""
    try:
        path_raw = h.get(_H + "path", "")
        path = tuple(Hop.parse(p) for p in path_raw.split(",") if p) if path_raw else ()
        i = Intent(
            version=int(h.get(_H + "version", WIH_VERSION)),
            principal=_req(h, "principal"),
            agent=_req(h, "agent"),
            capability=_req(h, "capability"),
            root=_req(h, "root"),
            depth=int(_req(h, "depth")),
            budget=int(h.get(_H + "budget", "0")),
            declared_cost=int(h.get(_H + "declared-cost", "0")),
            deadline=float(_req(h, "deadline")),
            coupling=h.get(_H + "coupling", "batch"),
            safe=h.get(_H + "safe", "1") == "1",
            path=path,
            intent_digest=h.get(_H + "intent-digest", ""),
            idempotency=h.get(_H + "idempotency", ""),
            attest=h.get(_H + "attest", ""),
        )
    except MalformedIntent:
        raise
    except (ValueError, TypeError) as exc:
        raise MalformedIntent(str(exc)) from exc
    i.validate()
    return i


def path_contains(path: Iterable[Hop], hop: Hop) -> bool:
    return any(p == hop for p in path)
