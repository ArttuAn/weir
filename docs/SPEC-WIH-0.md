# WIH-0: the Weir Intent Header

Status: draft. Version 0 is what `weir/intent.py` implements.

## 1. Terminology

**Principal** — the human or organisation that ultimately authorised the work.
Survives every hop unchanged.

**Agent** — the software executing one hop, as `aas:<n>/<name>`.

**AAS (Agent Autonomous System)** — the administrative domain that vouches for
an agent, analogous to a BGP AS. The unit of trust, attestation and policy.

**Root** — identifier of the originating request. Every descendant of one human
action shares a root. The unit of budget.

**Capability** — dotted name for what is being asked (`search.web`,
`llm.completion.long-context`). The forwarding key.

## 2. Fields

| Field | Header | Type | Required | Mutability |
|---|---|---|---|---|
| version | `weir-version` | uint | yes | fixed |
| principal | `weir-principal` | string | yes | immutable end to end |
| agent | `weir-agent` | `aas:<n>/<name>` | yes | rewritten per hop |
| capability | `weir-capability` | dotted | yes | immutable |
| root | `weir-root` | opaque | yes | immutable |
| path | `weir-path` | csv of `<aas>:<agent>` | no | prepend per hop |
| depth | `weir-depth` | uint | yes | decrement per hop |
| budget | `weir-budget` | uint millicredits | no | advisory |
| declared-cost | `weir-declared-cost` | uint millicredits | no | per hop |
| deadline | `weir-deadline` | unix seconds, 3dp | yes | may only decrease |
| coupling | `weir-coupling` | `human` \| `batch` | no | immutable |
| intent-digest | `weir-intent-digest` | `b2:<hex>` | no | immutable |
| idempotency | `weir-idempotency` | opaque | no | per request |
| safe | `weir-safe` | `0` \| `1` | no | immutable |
| attest | `weir-attest` | `b2k:<aas>:<mac>` | see §7 | rewritten per hop |

Absent optional fields take the defaults in `weir/intent.py`.

## 3. Processing rules

A conforming router MUST, in this order:

1. Reject a header set that fails §2 typing as `malformed-intent`.
2. Verify `attest` per §7; on failure `bad-attestation`.
3. Refuse `delegation-loop` if its own `<aas>:<name>` already appears in `path`
   more than `repeat_limit` times (default 1), or if any hop appears more than
   that. This refusal is terminal.
4. Refuse `depth-exceeded` if `depth <= 0`. Terminal.
5. Refuse `deadline-passed` if `deadline <= now`. Terminal.
6. Refuse `deadline-infeasible` if projected completion exceeds `deadline`.
   Terminal.
7. Apply origin terms; `terms-denied` is terminal.
8. Debit the root ledger. `budget-exhausted` is terminal.
9. Apply admission control. `congested` and `early-retry` are the only
   **retryable** refusals and MUST carry an appointment (§5).

On forwarding a router MUST decrement `depth`, prepend its own hop to `path`,
and clear `attest` before re-signing as itself. A router MUST NOT increase
`budget`, MUST NOT extend `deadline`, and MUST NOT alter `principal`, `root`,
`capability` or `coupling`.

## 4. Budget

The header's `budget` is the caller's *view* and is advisory. The router's
per-root ledger is authoritative, exactly as TTL is authoritative in the network
rather than at the sender. A caller that inflates `budget` gains nothing.

Admission *reserves* `max(declared_cost, route_price)`; settlement replaces the
reservation with the observed cost. Reservation rather than deduction is what
prevents concurrent siblings from each seeing the full balance and collectively
overcommitting it.

A child request MUST NOT carry a budget exceeding what its parent holds, nor a
deadline later than its parent's. These two invariants are what make a recursive
swarm terminate without anyone remembering to make it terminate.

## 5. Appointments

A retryable refusal carries:

```
weir-retry-not-before: <unix seconds, 3dp>
weir-appointment:      <not_before>|<attempt>|<issued>|<mac>
retry-after:           <seconds, integer>
```

`retry-after` is present for clients that have never heard of weir, so they back
off approximately correctly. `weir-appointment` is the exact slot for clients
that have. A router MUST NOT issue an appointment at or after the request's
`deadline`; it MUST refuse `deadline-infeasible` instead, because a slot the
caller cannot use converts an immediate actionable failure into a caller that
waits and fails anyway.

A caller presenting a valid appointment at or after `not_before` (less a grace
window, default 50 ms for clock skew and flight time) is admitted ahead of
unticketed traffic. Presenting it early MUST be charged an escalating penalty
and MUST NOT move the slot earlier.

Appointments are self-contained and signed, so verification needs no per-caller
state and a cluster behind one anycast address can honour each other's tickets.
The slot book itself is per-router.

Wire precision is milliseconds; the grace window subsumes the rounding.

## 6. Binary encoding (sketch, not implemented)

The textual form exists so WIH-0 rides existing HTTP infrastructure. Nothing in
the design depends on it. A fixed-layout form for a hardware data plane:

```
 0      1      2      3      4                                  8
+------+------+------+------+----------------------------------+
| ver  |flags |depth |paths | root (16 octets)                  |
+------+------+------+------+----------------------------------+
| deadline (u64 ms)         | budget (u32) | declared (u32)     |
+---------------------------+--------------+--------------------+
| capability id (u32, from a CAP-advertised table)              |
+--------------------------------------------------------------+
| principal id (16) | intent digest (16) | path vector (8*n)    |
+--------------------------------------------------------------+
| attestation (16 or 64)                                        |
+--------------------------------------------------------------+
```

`flags` carries `coupling` and `safe`. Loop, depth and deadline checks — stages
3 to 5, the ones that must run at line rate — touch only the first 24 octets.

## 7. Attestation

WIH-0: `b2k:<aas>:<mac>` where `mac` is keyed BLAKE2b-128 over the canonical
form (`Intent.canonical()`), keyed per AAS. The MAC's AAS MUST equal the AAS in
`agent`, or one AAS could mint headers claiming to be another.

This is symmetric, so it is adequate only where the verifying router shares
administrative trust with the issuing AAS — your own fleet, your own router.
Across domains, verification requires a secret that also permits forgery, so
WIH-1 replaces the primitive with Ed25519 over the same canonical form, with
keys distributed by CAP. The interface is unchanged, so swapping it does not
move the trust boundary; it widens where the boundary can be drawn.

Attestation is REQUIRED for any deployment that acts on `principal`, `budget`,
`deadline` or `coupling` — which is every deployment that does anything useful.
Without it those four fields are self-service: `weir-budget: 999999999` is a
credit line and `weir-coupling: human` is a fast lane. `weir run` warns loudly
when attestation is not required.

## 8. Refusal reasons

Terminal: `malformed-intent`, `bad-attestation`, `delegation-loop`,
`depth-exceeded`, `fanout-exceeded`, `budget-exhausted`, `deadline-passed`,
`deadline-infeasible`, `terms-denied`, `no-route`.

Retryable: `congested`, `early-retry`.

HTTP status mapping is in `weir/errors.py`. Terminal reasons deliberately map to
codes no ordinary retry library treats as retryable; 429 is reserved for reasons
that carry an appointment.
