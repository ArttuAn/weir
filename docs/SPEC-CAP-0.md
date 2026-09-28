# CAP-0: Capability Advertisement Protocol

Status: **design only. Not implemented.** See [`STATUS.md`](STATUS.md).

## Why

Everything in the running implementation works at one router. Two of weir's
properties do not survive that limitation:

- **Budget conservation is per-router.** A swarm that fans out across several
  independent weir routers can spend its root's grant once per router. The
  ledger is authoritative locally and blind globally.
- **Attestation is per-domain.** A symmetric MAC cannot be verified by anyone
  who could not also forge it, so trust stops at the fleet boundary.

Both are the same missing thing: routers have no way to talk to each other. BGP
is the obvious model — not because agent routing resembles IP routing, but
because BGP is the working example of independent administrative domains
exchanging just enough state to make globally sane local decisions, without
anyone running the network.

## What is advertised

A CAP speaker advertises, per AAS:

```
capabilities   dotted names reachable through this speaker, with price
               and observed p50/p95 latency
budget-authority  roots this speaker may debit, and the ceiling
identity       Ed25519 public keys for WIH-1 attestation
reputation     observed compliance: appointment-keeping rate, overrun rate,
               loop-origination rate
```

Advertisements carry a path vector of AAS numbers and are refused on seeing the
receiving AAS in the path — the same loop prevention weir already applies to
delegation, applied one layer up.

## Budget across domains

The hard part, and the reason this is a spec rather than code.

Sketch: a root's grant is issued by a **budget authority** (the principal's home
AAS) as a signed capability with a total ceiling. A router that wants to debit
it must hold a **lease** — a signed, time-bounded share of the ceiling, obtained
from the authority. Leases are small and short so a partition costs at most one
lease per router, and unused leases expire back to the authority.

This is a distributed reservation problem with a known shape and known
trade-offs: lease size against round trips, lease lifetime against partition
loss. It should not be invented in a README. What matters for the present design
is that the *header* already carries everything such a protocol would need —
`root`, `principal`, `declared_cost` — so adding it does not change WIH-0.

## Reputation

Deliberately narrow. CAP advertises **observed mechanical behaviour**, not
judgement: did this AAS keep its appointments, did its declared costs match
observed ones, did it originate delegation loops. Those are things a router
measures directly and can be checked by anyone who carries the traffic.

Anything broader — "is this agent trustworthy" — is a reputation system, and
reputation systems attached to network reachability are a censorship
infrastructure with extra steps. Out of scope, and should stay out.
