# Physical system design

How weir works as a thing you rack, power, monitor and page someone about.

Everything with a number attached comes from `python3 scripts/bench.py` on the
machine named in its output, or is marked as an assumption you have to measure
for yourself. Where I have not verified something, §12 says so.

---

## 1. What weir is, and what it is not

**It is not a switch ASIC, and it cannot become one.** That is worth settling
first, because "router" invites the wrong mental model and the wrong bill of
materials.

A forwarding ASIC works because the fields it needs sit at fixed offsets in
cleartext, and because the only state it mutates is a counter. Weir fails all
three conditions:

| Requirement | Why silicon cannot take it |
|---|---|
| Read the intent header | Agent traffic is HTTPS. The header is *inside* the TLS session, so the box must terminate TLS before it can decide anything. Switch ASICs do not terminate TLS. |
| Verify attestation | A keyed BLAKE2b MAC per request. Some DPUs have crypto engines; forwarding ASICs do not. |
| Debit the ledger | A read-modify-write on a contended hash table with a rollback protocol (reserve, then settle). ASIC register memory does counters, not transactions. |

So weir is a **TLS-terminating L7 element**: server-class hardware, or more
often just a process. Its capacity is measured in decisions per second,
concurrent connections and bytes of retained state — never in packets per
second. A 100 GbE port would be decoration.

### 1.1 What *can* be offloaded, and why the pipeline order decides it

The pipeline in [`DESIGN.md`](DESIGN.md) §5 orders stages cheapest-first. That
ordering turns out to have a hardware consequence nobody designed in:

```
stages 1-5   parse, attest, loop/depth, deadline, terms
             -> pure functions of the header. No shared mutable state.
             -> OFFLOADABLE to a DPU or a P4 pipeline, given the binary
                encoding in SPEC-WIH-0 §6.

stages 6-12  route, budget, damper, coalesce, forward, settle, receipt
             -> touch contended, mutable, money-bearing state.
             -> HOST ONLY. Not a tuning decision; a correctness one.
```

The offloadable set is exactly the **stateless prefix** of the pipeline. A
looping agent, an expired request or a terms violation could be dropped on the
NIC without ever waking the host — which is the closest weir gets to "line
rate", and it is available precisely because those checks were put first for
unrelated reasons.

---

## 2. Where the box sits

Three placements. Same binary, different configuration — a router is a router
wherever it sits in the topology.

**A — Fleet egress.** Every outbound call from one organisation's agents. This
is the only vantage point from which a *whole root's tree* is visible, so
budget conservation, loop detection and fan-out width belong here and nowhere
else. Agents reach it by `HTTPS_PROXY` or an SDK base URL.

**B — Provider ingress.** In front of an inference cluster, behind the L4 load
balancer. Terms, admission control, coalescing and the human-coupled capacity
reserve belong here, because this is the side that owns the scarce GPUs.

**C — Interconnect.** Between Agent Autonomous Systems. Needs
[CAP](SPEC-CAP-0.md), which is a design and not code.

A and B are both worth deploying and they enforce different things. Neither
subsumes the other: the provider cannot see your fan-out, and you cannot see
their capacity.

---

## 3. The binding constraint is not the one you would guess

Agent traffic is **long-lived, low-packet-rate and high-concurrency**. A
streaming completion holds a socket open for thirty seconds and sends almost
nothing. That inverts the usual sizing:

- **Not bandwidth.** Requests are small and responses are token streams.
- **Not packets per second.** There are very few packets per connection-second.
- **Concurrent connections**, because every in-flight agent task is a held
  socket plus TLS session state.
- **TLS handshakes per second**, if callers do not pool connections. A naive
  agent opening a fresh connection per call is the single most expensive thing
  it can do to you, and it will, because that is the default in most HTTP
  clients used badly.
- **Retained state**, which is where the real ceiling turns out to be.

---

## 4. Measured cost, and what it implies

From `scripts/bench.py` (CPython 3.12, x86_64). **CPU figures are a floor, not
a target** — a production data plane would not be in Python. **State sizes
transfer**, because they are facts about the data structures.

```
stage 1     parse WIH headers                 13.8 us
stage 2     verify attestation (MAC)          18.8 us
stages 1-3  refuse a delegation loop          64.1 us
stages 1-12 full forwarding decision         154.1 us

ledger        288 bytes per live root account
receipt       428 bytes per decision
coalesce key   74 bytes, plus the cached body
appointment    56 bytes — held by the CALLER, not the router
```

Two of those deserve comment.

**Refusing early is 2.4x cheaper in CPU than forwarding — and that is not the
point.** The saving is not the 90 µs. It is that a refused request never makes
the upstream call, which costs 200 ms to 30 s and real money. The honest ratio
is about four orders of magnitude, and it comes from *avoiding the upstream*,
not from the pipeline being clever.

**The router holds no per-caller booking.** An appointment is a signed bearer
token carried by the client. The router keeps one slot cursor per class. This
is why appointments survive failover (§7) and why the slot book cannot itself
become a memory leak.

### Dimensioning at 10,000 decisions/second

```
receipts           4.3 MB/s   =  0.37 TB/day raw
                              ~= 0.05 TB/day at a conservative 8:1
ledger,   60s TTL  0.2 GB resident  (600,000 live roots)
ledger, 3600s TTL 10.4 GB resident  (36,000,000 live roots)
```

**Storage and memory are the binding constraints, not CPU and not bandwidth.**
A box sized for this is one with a lot of RAM and an NVMe device, not one with
fast ports.

---

## 5. Time

Appointments are absolute timestamps. That makes clocks load-bearing, in two
places that need to be treated very differently.

**Appointments: no synchronisation required.** A refusal carries `weir-now`
alongside `weir-retry-not-before`, so the caller computes the wait as a
difference between two readings of *the router's* clock. Its own skew cancels
exactly. This matters because the failure it prevents is perverse: a caller
whose clock is 200 ms fast would compute a short wait, arrive early, and be
charged an early-retry penalty for what is actually an NTP fault. It would be
punished for someone else's misconfiguration, by a mechanism whose entire
purpose is to make incentives honest.

`tests/test_clock_skew.py` runs callers ±45 s out, over real sockets, and
asserts that zero early-retry penalties are charged.

**Deadlines: synchronisation required.** A deadline is absolute and is compared
against the router's clock, so a caller with a wrong clock declares something
other than what it meant. There is no trick that removes this; carrying the
sender's clock would let it be restated but not verified.

- **Requirement: chrony/NTP, offset held under ~100 ms.** Ordinary datacentre
  NTP does far better than this. PTP is unnecessary — deadlines are seconds,
  not microseconds, and specifying PTP here would be cargo cult.
- **Alarm on clock offset.** It is a correctness input, not a housekeeping
  detail.
- The client SDK self-heals gross skew: on a `deadline-passed` refusal it
  applies the offset it just learned from `weir-now` and retries once, so a
  misconfigured caller fails once and recovers instead of failing forever.

---

## 6. Clustering

One root's budget must be authoritative somewhere. Replicating every debit
synchronously would put a consensus round trip inside stage 7.

**Shard by root.** Consistent-hash `weir-root` to a node; each root is owned by
exactly one node; no cross-node coordination is needed for budget at all,
because no two nodes ever debit the same account.

Physically that is a two-tier arrangement, because a plain L4 load balancer
cannot hash on an HTTP header:

```
        L4 LB  (any, stateless, ECMP/anycast)
          |
   +------+------+          front tier: terminates TLS, parses the header,
   | front tier  |          forwards to the owner of weir-root
   +------+------+
          |  consistent hash on weir-root
   +------+------+
   | owner tier  |          ledger, damper, coalescer for its shard
   +-------------+
```

The front tier is stateless and scales horizontally. The owner tier holds the
money.

**This inherits the reset hazard from §11.** Losing an owner node loses the
ledger for its roots, and those roots re-open on their new owner with a *full
grant*. A node failure therefore grants at most one extra budget per live root.
Mitigate with an asynchronous checkpoint of `committed` per root — 288 bytes
each, so checkpointing 600k live roots is ~170 MB, entirely tractable — or
accept the bound and say so out loud. Do not pretend it does not exist.

---

## 7. Failure modes

What survives a node loss, and why:

| State | Survives? | Because |
|---|---|---|
| Appointments | **Yes** | Signed bearer tokens verified statelessly. Any node sharing the key honours any other node's tickets. This is a real payoff of making them tokens rather than server-side bookings. |
| Slot cursor | No — soft | Rebuilds within one service interval. Cost is brief over-admission. |
| Ledger | No — hard | The only state that must be checkpointed. See §6. |
| Receipts | Only if shipped | The chain is tamper-evident against edits, **not** against an operator who discards the log. Stream it off-box or it is not evidence. |
| Coalescer cache | No — soft | Cost is a cold cache, i.e. more upstream calls. |

**Fail closed.** For the egress placement this is the uncomfortable but correct
default: failing open means agents bypass budget enforcement entirely, and the
failure mode of that is an unbounded bill arriving silently. Get availability
from an HA pair, not from a bypass. If you do choose fail-open, alarm on it
loudly, because it is a spending decision disguised as an availability one.

---

## 8. Receipts as a physical problem

At 428 bytes per decision, receipts are the largest thing weir produces. They
are also the only thing that is legally interesting, which means retention is a
policy question with a storage bill attached.

- **Stream off-box.** A tamper-evident chain on the same box as the operator
  who might want to edit it is a chain with one obvious attack.
- **Compress.** Receipts are extremely repetitive — same principals, same
  capabilities, same decisions — so ordinary block compression does well. The
  8:1 in §4 is deliberately conservative and you should measure your own.
- **Anchor, then prune.** Publish a periodic Merkle root and you can discard
  leaves while keeping the ability to prove any retained receipt belonged to
  the chain. This is the only approach that makes long retention affordable.
- **Never put payloads in them** (see [THREAT-MODEL.md](THREAT-MODEL.md)).

---

## 9. Hardware profiles

**Profile 0 — a process.** 4–8 vCPU, 8–16 GB, no special hardware. This is what
almost everyone should deploy, and the honest recommendation is to start here
and measure before buying anything.

**Profile 1 — 1U appliance**, for a provider ingress at scale: high core count,
128–256 GB RAM (the ledger lives here), 2× 10/25 GbE (not 100 — see §3), NVMe
for the receipt spool, TPM or HSM for attestation keys, redundant PSU. Size the
RAM from §4 and your own TTL, not from a rule of thumb.

**Profile 2 — DPU-assisted**, where TLS handshake rate dominates: offload TLS,
and optionally the stateless prefix (§1.1). Only justified once you have
measured that handshakes, not decisions, are the ceiling.

Keys live in a TPM/HSM in profiles 1 and 2. A symmetric attestation key on disk
is a forgeable identity for the whole AAS.

---

## 10. Operating it

Endpoints: `/_weir/stats`, `/_weir/receipts`, `/_weir/receipts/<root>`,
`/_weir/routes`, `/_weir/housekeep`.

`/_weir/receipts` verifies the chain and reports its tip. `/_weir/receipts/<root>`
returns one delegation tree's entries — every hop, oldest first — because the
question a caller actually has is "what did *my* agent do, and who authorised
it", and answering it from a flat dump means reading everyone else's activity on
the network to find it. The response carries the in-memory window, since
entries older than the window survive only in the receipt file. No payloads: the
digest is kept so sameness can be shown without keeping the question.

Like the rest of `/_weir/*`, it is unauthenticated and is meant for the single
operator who already has the box.

Signals worth alarming on, each derived from a specific failure this design can
actually have:

| Signal | Means |
|---|---|
| `ledger.roots` rising monotonically | Housekeeping is dead. You have hours before OOM. See §11. |
| Clock offset > 100 ms | Deadlines are now lies. §5. |
| `damper.limit` pinned at minimum | The upstream is sick and weir is correctly protecting it. Page the upstream, not weir. |
| `damper.reneged` rising | The router is over-promising slots; the horizon is too long for this upstream. |
| `sched.expired_before_execution` rising | Callers are giving up before you serve them. Either shed earlier or add capacity. |
| `ledger.overruns` rising | Declared costs do not match observed. Someone's pricing is wrong. |

**In-service upgrade.** Rolling, provided all nodes share the appointment key
and the WIH version is compatible — outstanding tickets stay valid across the
restart because nothing about them lives on the node.

---

## 11. Two failures that only dimensioning found

Both were present in code that passed 49 tests and four simulated scenarios.
Neither is the kind of thing a simulation can surface, because both are about
what happens after hours of real uptime.

**Nothing ever swept.** `Ledger.expire()` and `Coalescer.sweep()` existed and
were never called. At 10k decisions/second and 288 bytes per root, that is
roughly 250 GB/day of accumulation. The router does not misbehave as it fills;
it dies, of memory, having passed every functional test on the way. Fixed:
`Router.housekeep()`, run on a timer by `weir.server.serve`.

**Expiry silently reset budgets.** The original sweep dropped accounts by *age*.
A task that outlived the TTL therefore had its ledger entry deleted while it was
still running, and its next hop re-opened the account with a full grant. Budget
conservation — the invariant the entire design rests on — would have failed
exactly for the long-running swarms it exists to contain, and failed silently:
no error, no refusal, just an agent handed a fresh wallet every hour.

Fixed by collecting on **idleness rather than age**, which is both correct and
strictly better operationally: `ttl` now means "how long after a task goes quiet
do we keep its accounting", and no longer has to be guessed against the duration
of the longest task anybody might run. Memory is bounded by `rate x ttl` instead
of by `rate x task-duration`.

---

## 12. What I have not verified

Stated plainly, because a physical design that overstates its evidence is worse
than none:

- **No real hardware has been tested.** No appliance, no DPU, no NIC offload. §1.1
  and §9 are analysis, not measurement.
- **No TLS benchmark.** Handshake rate is called a likely ceiling in §3 on the
  basis of how TLS works, not on the basis of a number I produced.
- **No cluster has been built.** The sharding in §6 is a design. The front/owner
  split is standard practice, but I have not run it.
- **CPU figures are CPython** and would be materially different in C or Rust.
  They establish the *ordering* of stage costs, nothing more.
- **Compression ratio for receipts is an estimate.** Measure yours.
- **CAP is unimplemented**, so cross-domain budget conservation remains an open
  problem — §6 solves it only inside one cluster.
