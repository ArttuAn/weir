# weir: design

## 1. What a router is for

A router exists because endpoints cannot be trusted to coordinate with each
other. Not because they are hostile — because they cannot see each other. Each
endpoint knows its own intent and nothing about the thousand peers contending
for the same path at the same instant. The router is the only element that sees
the aggregate, so it is the only element that can act on it.

Everything a classical router does follows from that: it forwards, it queues, it
drops, it polices. What it can *decide* is bounded by what it can *see*, and
what it can see is bounded by what the header carries. IPv4 gives it twenty
bytes, of which the load-bearing fields are a destination, a TTL and six bits of
DSCP. A router that forwards on those three can answer exactly one question —
where is this going — and can enforce exactly three things: reachability, a
crude loop bound and a crude priority.

That was the right header for its traffic. It is the wrong header for ours.

## 2. What changed

Four properties of agent traffic break the classical model. Each is worth
stating precisely, because the fixes follow directly from the diagnoses.

### 2.1 Identity moved off the address

On the classical internet, naming the machine you want *is* the job. On the
agent internet, the agent should not know which machine it wants, and the
question the network needs answered is different: on whose authority is this
happening? An agent's IP tells you which datacentre it rents. Two requests from
the same address may be one person's holiday planning and another person's
scraper; two requests from opposite sides of the planet may be one human action.

The relevant unit is the **principal** — the human or organisation that
authorised the work — and the **delegation chain** from that principal to the
agent currently executing. Neither is recoverable from any packet field, so both
must be carried explicitly and attested, or every constraint derived from them
is self-declared.

### 2.2 Cost decoupled from bytes

Byte metering worked because bytes were the scarce thing and every byte cost
about the same. Neither holds. A short request can commission minutes of GPU
time; a long one can be a file copy. Worse, the correlation is *inverted* for
the traffic you most want to control: the requests that fan out into a thousand
descendants are the small ones.

So the meter has to count something else, and the something else has to be
enforceable at the hop rather than reconciled in a billing system a month later.

### 2.3 Loops became semantic

A hop counter is a confession of blindness: the router has no idea why the
packet keeps coming back, so it counts to 255 and gives up. That is an adequate
answer when a lap costs a wire transit and a microsecond.

Agent loops are not topological. A → B → C → A is three different hosts, three
different addresses, three healthy connections, and a hop counter cannot
distinguish it from a legitimate twelve-stage pipeline. Meanwhile a lap now
costs an inference call, so "give up after 255" is not a safety property, it is
an invoice.

BGP already solved this exact shape in 1994. Carry the path; refuse anything
that has already been through you. Applied to delegation rather than topology,
it is strictly better than a counter in *both* directions: it kills a 3-cycle on
its first repetition, and it permits a deep acyclic pipeline that a tight TTL
would have killed.

### 2.4 Endpoints stopped self-limiting

This is the deepest one. TCP's congestion control is not enforced anywhere. It
is a convention, kept since 1988 because everyone ships the same stacks and
nobody benefits from collapse.

Agents break it by instruction rather than by malice. Every framework ships a
retry decorator; every runbook says retry on 5xx; and no agent can see the
hundred siblings hitting the same upstream in the same second. HTTP 429 does not
fix this, because **429 is free**. Ignoring it costs one connection and buys a
chance of being served. The locally rational strategy for every caller
independently is to retry immediately, and the aggregate is the storm.

You cannot fix a payoff matrix with a politer error code. You have to change the
payoff.

## 3. The header

Four questions, where IPv4 asked one:

```
who authorised this          principal, attested
what is it trying to do      capability + intent digest
what may it spend            root, budget, declared cost
when does the answer expire  deadline, coupling
```

Plus the mechanics: `path` (delegation vector), `depth` (swarm TTL),
`idempotency`, `safe`, `attest`. Full field reference in
[`SPEC-WIH-0.md`](SPEC-WIH-0.md) and in the docstring of `weir/intent.py`.

The fields are carried as individual `weir-*` headers so a router can make its
drop decisions after parsing three short ASCII strings without decoding a body —
the same reason IPv4 puts TTL at a fixed offset.

## 4. Three planes

**Forwarding plane** — per-request, sub-millisecond, no network calls. Parse,
attest, loop check, deadline check, terms, route, budget, admission. Stages 1–8
of the pipeline in `weir/router.py`.

**Governance plane** — budgets, grants, terms, policy. Slower-moving state that
the forwarding plane reads on the fast path and that an operator writes.

**Accountability plane** — hash-chained receipts, tamper-evident, payload-free.

The split matters because the three have different latency budgets, different
consistency requirements and different blast radii. Conflating them is how you
get a "gateway" that is really an application server sitting in the data path.

## 5. Pipeline order

Cheapest and most terminal first, exactly like an ACL fast path:

```
 1  parse         few hundred bytes of ASCII, no body decode
 2  attest        one keyed hash; everything downstream trusts these fields
 3  loop / depth  pure header arithmetic, terminal, no shared state
 4  deadline      one comparison; drops work that is already worthless
 5  terms         origin policy, before the origin is contacted at all
 6  route         capability lookup, needed to price anything
 7  budget        ledger reservation, first contended lock
 8  damper        admission and appointments
 9  coalesce      dedupe, may avoid the upstream entirely
10  forward       the only expensive step
11  settle        reconcile declared against observed cost
12  receipt       record the decision either way
```

On a packet network the ordering is a minor optimisation. Here it is the whole
point: the expensive thing is not the forwarding, it is the upstream inference,
so a refusal that happens after the upstream call has saved nothing. A looping
agent with an expired deadline and an empty wallet is refused at stage 3 for the
cost of parsing a header.

Budget (7) deliberately precedes admission (8): a request that cannot be paid for
should never occupy a queue slot, or a bankrupt swarm can still deny service to a
solvent one.

## 6. Congestion control in detail

The mechanism has two halves, and both are necessary.

**Refusals cost money.** A refused request debits the root's ledger — little,
but not nothing — and the debit escalates geometrically per early retry. An
agent that ignores backpressure runs out of budget before the upstream runs out
of capacity, and it runs out at the edge, the cheapest place on the network to
say no.

**Refusals carry an appointment.** A signed bearer ticket for a slot the router
has actually set aside. Punctual arrival is admitted ahead of unticketed
traffic; early arrival is charged and *cannot* move the slot earlier. So waiting
is not merely cheaper than racing, it is faster.

Two things a naive slot book gets wrong, both found by running `sim.py` rather
than by reasoning about it:

*It must not write far into the future.* A book written at t=0 is priced with
t=0's estimate of the upstream, and that estimate is worst exactly when the book
is longest. The first implementation promised 32 slots/second against an
upstream that turned out to do 9, and because ticket holders bypassed the
concurrency ceiling they all arrived at once and buried it. The fix is a
**horizon**: beyond it the router returns a re-evaluation point rather than a
promise, so over-promising is bounded by horizon × rate.

*An appointment buys priority, not capacity.* If the router over-promised,
dumping the ticket holder into a saturated upstream serves nobody. A small
overshoot honours the promise where it can; past that the caller is rebooked at
no charge, because it did exactly what it was asked to do.

The concurrency limit itself adapts by gradient — the ratio of the best latency
the upstream has shown to what it is showing now — evaluated **once per window
of `limit` completions**. That is not a detail. The first implementation applied
multiplicative decrease on every completion and collapsed to the floor in about
ten samples, starving an upstream that was merely busy. One adjustment per
window is the same "once per round trip" discipline that keeps AIMD stable.

Using a ratio rather than an absolute latency target means the router discovers
each upstream's healthy latency instead of being told it, which matters when one
FIB entry fronts a 200 ms search endpoint and another a 30 s reasoning model.

## 7. Where this is honestly weak

**Metering is a real trade-off, not a free win.** The storm demo crosses over:
past roughly half the callers misbehaving, weir serves fewer requests than a
classic gateway because it cuts misbehavers off. That is the intended behaviour
and it is still a cost. A too-aggressive penalty schedule punishes a
buggy-but-legitimate agent as hard as a greedy one, and nothing in the design
distinguishes them. The schedule is a dial an operator has to own.

**A well-configured classic gateway already survives a simple storm.** Hard
concurrency limits protect upstreams and cheap 429s are cheap for the router
too. Weir's advantage is not surviving the storm; it is that the storm's cost is
attributed, that the human-coupled request is protected, and that the wasted
inference does not happen.

**Symmetric attestation does not cross trust domains.** WIH-0 uses keyed
BLAKE2b, which is fine inside one fleet and useless between two, because
verifying requires a secret that also lets you forge. Cross-domain needs
public-key signatures. Specified, not implemented.

**The ledger is per-router.** Budget conservation holds at one router. A swarm
that fans out across several independent weir routers can spend its root's
grant once per router. Fixing this needs the control plane in
[`SPEC-CAP-0.md`](SPEC-CAP-0.md), which is a design, not code.

**Intent digests are only as good as the caller's canonicalisation.** Two agents
asking the same question in genuinely different words produce different digests
and do not coalesce. Real semantic equivalence needs an embedding, which puts a
model in the forwarding path and breaks the latency budget of stage 9. This
version does exact canonical matching and no more.

**Deadlines are self-declared.** An agent that declares `human` coupling and a
tight deadline on everything gets priority on everything. Attestation makes the
claim attributable but not true. The defence is reputational and economic — the
control plane again — not cryptographic.

## 8. What a credit is

One credit is the cost of one unit of reference work, fixed per deployment. The
router does not care what it means; it cares that the number is comparable
across routes so the FIB's cost function and the ledger's arithmetic are sound.
A deployment fronting one model family can set it to a thousand output tokens. A
deployment fronting many should set it to currency and let each route publish a
price.

Millicredits keep the wire field an integer, so the forwarding plane never does
floating point.
