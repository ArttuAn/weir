# Status

The honest line between what runs and what is argued.

## Implemented and tested

Everything here is exercised by `tests/` (46 tests, stdlib `unittest`) and by
`python3 -m weir demo`. The HTTP path is verified end to end over real sockets.

| Mechanism | Module | Notes |
|---|---|---|
| WIH-0 header parse/serialise, canonical form | `intent.py` | Lossless round-trip tested |
| Keyed-MAC attestation | `attest.py` | Symmetric; single trust domain only |
| Delegation path vector + depth | `pathvec.py` | Names the cycle it killed |
| Root budget ledger, reserve/settle | `ledger.py` | Thread-safe; concurrency tested |
| Metered refusal, signed appointments, horizon | `damper.py` | Gradient limit adaptation |
| EDF + human reserve + expiry-before-execution | `sched.py`, `damper.py` | Reserve enforced at admission |
| Intent-digest coalescing, principal-scoped | `coalesce.py` | Cross-principal leak tested against; consulted before admission, see DESIGN.md §5 |
| Terms enforcement + per-principal rate | `terms.py` | Allow/deny/purpose/rate/price |
| Hash-chained receipts | `receipts.py` | Tamper and deletion detection tested |
| Capability FIB, longest-match, deadline-aware selection | `fib.py` | |
| Forwarding pipeline, sync + async admission | `router.py` | |
| Idle-based state collection (housekeeping) | `router.py`, `ledger.py` | Age-based expiry silently reset budgets; see PHYSICAL.md §11 |
| Clock-skew-immune appointments | `server.py`, `client.py` | Tested at ±45s over real sockets |
| HTTP/1.1 data plane, agent SDK, CLI | `server.py`, `client.py`, `cli.py` | |

## Specified, not implemented

| Thing | Where | Why not |
|---|---|---|
| Public-key attestation (WIH-1) | `SPEC-WIH-0.md` §7 | Needs a real signature primitive; stdlib has none. Interface is the same shape, so swapping it does not move the trust boundary. |
| CAP — capability/budget/reputation advertisement between routers | `SPEC-CAP-0.md` | Needs more than one router to be interesting. This is what would make budget conservation hold across administrative domains. |
| Binary WIH encoding for a hardware data plane | `SPEC-WIH-0.md` §6 | Nothing in the design depends on the textual encoding; the binary form is a layout sketch. |
| Receipt replication / external anchoring | — | The chain is tamper-evident against edits, not against an operator who discards the whole log. |

## Known-unsolved

- **Cross-router budget conservation.** One root can spend its grant once per
  independent router. Needs CAP. Solved *within* a cluster by sharding roots
  (PHYSICAL.md §6), not across administrative domains.
- **No hardware has been tested.** The offload analysis and hardware profiles
  in PHYSICAL.md are reasoning, not measurement; §12 lists what that leaves open.
- **Self-declared urgency.** Attestation makes a `human` coupling claim
  attributable, not true. The defence is economic and reputational.
- **Semantic coalescing.** Exact canonical digests only. Real equivalence needs
  an embedding in the forwarding path, which the stage-9 latency budget will not
  take.
- **Penalty calibration.** No principled way yet to distinguish a buggy agent
  from a greedy one, so they are charged identically.

## Not a benchmark

The demo numbers come from the model in `weir/sim.py`, not from measurements of
any real endpoint. They support the *shape* of each mechanism's effect and one
honest crossover where a mechanism stops helping. Every assumption is a named
parameter in that file.
