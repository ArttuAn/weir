# Threat model

What a hostile or broken agent can do, and what weir does and does not stop.

The interesting adversary is not a state actor. It is **a competent developer
whose agent is misconfigured, plus a small number who have read this document
and want more than their share.**

## Stopped

**Budget inflation.** Rewriting `weir-budget` gains nothing: the ledger is
authoritative and the header advisory. Tested (`test_budget_is_enforced_by_the_router_not_the_header`).

**Runaway fan-out.** Bounded three ways: width per delegator, depth per
delegation path, total spend per root. All three are enforced at the edge for
the cost of parsing a header, and containment compounds because refused nodes
never spawn children.

**Delegation loops.** Killed on first repetition and named in the refusal.
Terminal, so retrying reproduces nothing.

**Retry storms.** Refusals cost credits with a geometric escalation, and early
retries cannot advance a slot. An agent that ignores backpressure exhausts its
grant at the edge rather than the upstream's capacity.

**Cache-based exfiltration.** Coalescing is scoped to a principal unless the
capability is explicitly declared public, and unsafe requests are never
coalesced. Both tested — these are the two failure modes that would turn the
cache into "ask your victim's question and receive their answer".

**Wasted upstream work.** Requests past their deadline, or provably unable to
meet it, are dropped before forwarding.

**Receipt tampering.** Edits and deletions both break the chain. Tested.

## Not stopped

**A lying principal claim, without attestation.** If `require_attestation` is
false — the default, for lab use — any caller may claim any principal, budget or
priority. `weir run` warns on every startup. This is the single most important
deployment decision and the design does not hide it.

**Priority inflation, with attestation.** An attested agent that marks
everything `human` with a tight deadline gets priority on everything.
Attestation makes the claim *attributable*, not *true*. The defence is economic
and reputational, not cryptographic: an AAS whose declared urgency never matches
observed behaviour is measurable, and CAP is where that would be acted on.

**Cross-router budget splitting.** One root spends its grant once per
independent router. Needs CAP.

**A hostile router.** A weir router on the path sees principals, capabilities and
intent digests, and can refuse anything. It cannot forge receipts into someone
else's chain, but it can discard its own. Receipts are tamper-evident against
edits, not against an operator who throws the log away.

**Sybil AAS numbers.** Nothing in WIH-0 constrains who may claim an AAS number.
Allocation is an out-of-band, registry-shaped problem.

**Traffic analysis.** Intent digests are stable by design — that is what makes
coalescing work — so a router can tell that two callers asked the same question
without knowing what it was. That is a real privacy cost of mechanism 6 and it
is inherent, not an implementation gap.

## Deliberately not collected

Receipts record principal, agent, capability, decision, credits and path. They
do **not** record request or response payloads. A receipt log is retained,
replicated and subpoenaed; a receipt log full of prompts is a breach waiting for
an occasion. The intent digest supports "were these the same question" without
preserving what the question was.

## Deployment checklist

1. Set `require_attestation=True` and distribute per-AAS keys. Everything else
   is decoration without it.
2. Size grants to the task. A grant large relative to the penalty schedule means
   metering never bites; the storm demo shows exactly this.
3. Own the penalty schedule. It punishes a buggy agent as hard as a greedy one.
4. Declare `public_capabilities` explicitly and never by pattern. This is the
   cross-principal cache boundary.
5. Persist receipts off-box if they are meant to be evidence.
