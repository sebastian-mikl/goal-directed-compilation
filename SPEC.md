# The Network — Architecture Spec: State-Node Graph (authoritative)

Status: active direction. This is the **single authoritative spec**. It builds on the
validated deterministic primitives (branch `arch/deterministic-primitives`, Phase-1 DONE) and
makes the founding move explicit: **node identity becomes a fingerprint of the data's state,
Call-1 declares the destination, execution order is derived from primitive contracts, and A\*
navigates the state-graph reusing what already exists.** The LLM is the oracle; the graph is
the navigator. Where this conflicts with prior notes, this wins.

Worked example used throughout: *"top 5 owners by total mitigation cost among active risks."*

---

## 0. Summary of the shift

Two shifts, in sequence:

1. **Deterministic primitives (DONE).** Known operations are hand-written, parameterized
   primitives executed from a plan; the LLM no longer authors code for any registered kind.
   Validated on the S7 corpus: Call-2 = 0, no LLM in the execution path, every correctly
   *specified* query value-correct.
2. **State-node graph (this build).** The residual failures were all Call-1: ordering /
   structure variance (q10) and under-declaration (q15). The fix to the first is structural —
   stop asking the LLM to author a *route*. Call-1 declares the **unordered set** of
   `{operation, params}` the answer must satisfy; the architecture derives the order from
   primitive **contracts** and navigates the **state-graph** (nodes keyed by a fingerprint of
   the data's logical state) reusing any already-materialised prefix. There is no structure
   left for the LLM to collapse. Under-declaration (naming the wrong/incomplete set) remains a
   separate, now-smaller planner-hardening workstream.

---

## 1. Core principle — the verifiability boundary

An operation can be a deterministic primitive (a recordable node/edge in the graph) **iff
its effect has a reproducible, observer-independent verification — a fixed point — at some
resolution.** This is the single test that sorts every operation in every environment.

Two independent axes, not to be conflated:

- **Resolution** (fine ↔ coarse): tolerate freely. A coarse-but-stable check (HTTP 200) is
  as admissible as a sharp one (exact dataframe equality). The system runs on a blurry
  verify as well as a precise one.
- **Reproducibility** (fixed point ↔ observer-dependent): non-negotiable for anything that
  **defines a state**. State identity *is* the verify signature; if the verify is not
  single-valued, the state cannot be fingerprinted, cached, or trusted later, and
  compounding breaks.

Consequences:

- **In** (open to generate, deterministic to verify): tabular ops, UI driving, file ops,
  API calls, sorting and other spec'd algorithms (verified by formal spec/tests), theorems
  (verified by a proof-checker — a *stronger* verify than a dataframe check, since it
  covers all inputs). Formal/mathematical work is a prime domain, not an edge case.
- **Out**: observer-dependent judgment — "nice-looking", "elegant", "compelling". Not a
  coarser fixed point; a different object (a distribution, observer-relative). Freezing an
  LLM as the judge is forbidden — it makes the LLM the authority on state identity and
  reintroduces the ontological drift typed fingerprints exist to prevent.
- **Rescue for creative work** — the substrate/preference split: define the node by its
  **reproducible substrate** (compiles, satisfies the spec, contains the required
  structure) and let **non-reproducible preference** (which valid option someone likes)
  act only as a runtime selection layer that never touches identity, never gets
  fingerprinted, never gets cached as a state.

---

## 2. Architecture

### 2.1 State / Operation / Verify (environment-general)

- **State** = a conjunction of verify-bits over the environment (the existing bitmask of
  verify functions). Fingerprint only the task-relevant projection, never the whole world.
- **State identity** = a canonical fingerprint of the cumulative `(operation, params)` chain
  from the root — equivalently, the conjunction of verify predicates true at that point. The
  node's **content** is the cached dataframe (the workspace file). Identity is the spec;
  content is the data. (See §3.3.)
- **Operation (morphism)** = a deterministic, parameterized primitive that moves between
  states, carrying an explicit **contract** (needs / produces). Organized by the five
  dimensions: Observe, Transform, Verify, Contract, Route.
- **Verify** = a reproducible check. A node's verify confirms its defining state predicate(s)
  (not merely "a file exists"); state identity = the verify signature.

### 2.2 The LLM's three roles

- **Call-1 — oracle (declare the destination).** NL → the **target answer-state** in
  flat-or-recursive form (§3.5): a flat unordered set of `{operation, params}` for a linear
  query, or — for a join — a `join` that **names its two operands**, each itself a target
  sub-spec. It declares *what the answer contains* (and, for a join only, which operands feed
  it); it does **not** order, route, or emit `depends_on`. This is the LLM's real value (the
  NL→spec mapping) with the free-floating structure-authoring — the part it got wrong (q10) —
  removed.
- **Call-2 — frontier extension (rare).** Fires only when a declared operation has no
  registered primitive — a genuinely novel operation. Authors a candidate primitive,
  value-gated, promoted permanently. For a closed-algebra environment, ≈ 0.
- **Call-3 — verify/interpret.** Confirms the reached state satisfies the answer spec and
  interprets it for the user. Unchanged in spirit.

The graph navigates. The LLM sits at the destination (Call-1) and the frontier (Call-2).

### 2.3 Deterministic vs frontier

Everything with a registered primitive executes deterministically — no LLM in the
execution path. The LLM is confined to declaring (Call-1), genuine extension (Call-2), and
verification/interpretation (Call-3). The default expectation in a mature environment is
Call-2 ≈ 0.

---

## 3. The tabular state-node graph — Phase 1+ (implement now)

The first and reference instantiation. Relational algebra is a small, complete basis.

### 3.1 Primitive set (~7)

Each is a plain deterministic Python function, `params + frame → frame`. No LLM.

- `filter(frame, predicates)` — `predicates`: list of `{col, op, value}`, `op ∈
  {==, !=, <, <=, >, >=, in, contains}`. Conjunction.
- `compute(frame, new_col, expr)` — `expr`: restricted arithmetic over columns
  (`A op B`, `op ∈ {+, -, *, /}`, column refs resolved against the frame). Adds `new_col`.
- `aggregate(frame, group_by, agg, value_col)` — `agg ∈ {sum, mean, count, min, max}`.
  Output = `group_by` cols + the aggregated column.
- `sort(frame, by, ascending)` — `by`: list of cols; `ascending`: bool or list.
- `limit(frame, n)` — first `n` rows.
- `join(left, right, on, how)` — `how ∈ {inner, left, right, outer}`; `on`: key cols.
- `project(frame, columns)` — select/reorder columns.

The variety lives entirely in params; the kinds are fixed and small. (`filter` is registered
under its codebase name `select`; `write` — persist + pass-through — is also registered, as
the corpus needs it.)

### 3.2 Primitive contracts (needs / produces)

Each primitive gains an explicit **contract**. This drives ordering (§3.6) and routing
(§3.7); it is not optional metadata.

| primitive | needs (precondition) | produces (postcondition) |
|-----------|----------------------|--------------------------|
| `filter(predicates)` | referenced columns exist | row-subset; same columns |
| `compute(new_col, expr)` | columns referenced in `expr` exist | adds `new_col` (value = `eval(expr)`) |
| `aggregate(group_by, agg, value_col)` | `group_by` + `value_col` exist | columns `group_by + agg_col`; one row per group |
| `sort(by, ascending)` | `by` columns exist | ordering over rows; same rows/cols |
| `limit(n)` | an ordering exists (for top-N semantics) | ≤ `n` rows |
| `join(right, on, how)` | `on` keys exist in both inputs | merged column set |
| `project(columns)` | `columns` exist | subset of columns |

Each contract keeps its **value-level verify** (§3.4) as the postcondition check that
confirms the produced state — the basis for the node's identity predicate.

### 3.3 Node identity = state fingerprint

A node's **identity** is `sha256(canonical_chain)` of the cumulative `(operation, params)`
chain from the root; its **content** is the cached workspace file. The example produces a
chain of states, each a node:

| node | chain (identity) | meaning |
|------|------------------|---------|
| `S0` | `[]` | raw register |
| `S1` | `[filter status=Active]` | active risks |
| `S2` | `S1 + [aggregate group=Owner, sum(Cost)]` | total cost per owner (active) |
| `S3` | `S2 + [sort total_cost desc]` | …ordered |
| `S4` | `S3 + [limit 5]` | **the answer** |

Any query that filters to active reuses `S1`, because the node is keyed on the data's logical
state, **not the query wording**. The fingerprint material already existed in Phase 1:
`make_workspace_path` (db.py 209–233) hashes `kind + _lineage(parent) + params` — a first
approximation of the chain. The change (§5) promotes that to the node id (`make_node_id`,
db.py 204, currently discards it for kind-only).

**Canonicalisation.** Two chains can reach the same state (`filter(A)→filter(B)` ≡
`filter(B)→filter(A)`). Start with **light** canonicalisation: order commutable filters
deterministically; merge consecutive filters into one predicate set. The gold standard is
behavioural — two states that co-pass/co-fail the same verifies across snapshots are the same
node — kept as the **principled backstop** for ambiguous cases, **not built in v1**. Do not
over-build canonicalisation.

**Join-state fingerprint.** A join state is not a linear chain, so its identity is the
**canonical composition of its operand fingerprints plus the join params**:
`id(join) = sha256(canonical_chain( parent_chain + [(join, params + {@branch: sorted(operand_ids)})] ))`,
where each `operand_id` is itself a chain fingerprint (so the composition transitively captures
both branches and is deterministic and order-independent in the operands). **Operand sub-states
are first-class reusable nodes**: `{filter status=Active → aggregate Owner sum Cost}` is a node
in its own right, reused by any later query — join or not — that reaches the same sub-state
(this is the shared-operand reuse the cross-query gate, §10, exercises).

### 3.4 Contracts & value-level verify (the hard-won lesson)

Each primitive declares a **contract** (its output schema, §3.2) and a **value-level verify** —
presence is not enough. Specifically:

- `compute`: produced `new_col` values **must equal `eval(expr)`** on the frame, within
  float tolerance. Evaluate `expr` via a restricted/safe evaluator (column refs +
  arithmetic only; no arbitrary code). A no-op that adds an empty/mislabelled column fails.
- `aggregate`: output values **must equal a recomputed `group_by`/`agg`** for the params.
- Others: assert the declared output schema/shape and the structural post-condition.

These verifies are the node's identity predicate(s): a node's verify confirms the state it
defines, not merely that a file exists (§5).

### 3.5 Call-1 — declare the destination, not the route

Call-1 (`define_target`) stops emitting an ordered DAG with `depends_on`. It emits the
**target answer-state** in a **flat-or-recursive** form:

- **linear query → a flat unordered set** of required `{operation, params}`; order is derived
  (§3.6). For the worked example:

  ```
  {filter status=Active, aggregate group=Owner sum(Cost), sort total_cost desc, limit 5}
  ```

- **join query → the join names its two operands**, each itself a target sub-spec (recursively
  flat-or-recursive). An unordered set is genuinely ambiguous for a join (two filters, two
  identical aggregates, one join — which sub-chain feeds which side?), so the **join declares
  its operands** rather than the architecture guessing:

  ```
  join(on=Owner,
       left ={filter status=Active  → aggregate Owner sum Cost},
       right={filter status=Closed  → aggregate Owner sum Cost})
  ```

  Operand sub-chains are ordered internally by topo-sort; the join follows both operands;
  post-join `compute`/`sort`/`limit` follow the join. **No heuristic reconstruction anywhere** —
  branch membership is *declared* (it is part of what the answer is), not inferred.

The only structure the LLM declares is a join's operands; everything else (intra-chain order,
routing) is derived. This removes ordering/structure variance from the LLM — there is no
free-floating structure left for it to collapse (cf. q10's warm collapse).

### 3.6 Order is derived; declared operands are resolved

Within any (sub-)chain, execution order is the **topological sort of its operations over their
contract dependencies** (a primitive that *needs* what another *produces* must follow it),
with a relational stage-rank tiebreak for otherwise-independent ops:

- `sort` needs `total_cost` → only `aggregate` produces it → `sort` follows `aggregate`
- `limit` (top-N) needs an ordering → follows `sort`
- `aggregate` operates on filtered rows → follows `filter`
- a `compute` whose `expr` references raw columns runs early (after `filter`); a `compute`
  whose `expr` references join/aggregate-produced columns runs after them — its **column
  dependencies place it correctly**

For a join spec, resolution is **structural, not heuristic**: each declared operand is resolved
(recursively) to its terminal sub-state; the `join` step then depends on both operand
terminals; post-join steps follow. The compiled ordered chain (operands → join → post-join)
**is** the target node's fingerprint.

Result for the worked example: `filter → aggregate → sort → limit`. The LLM never chooses
order, so it cannot get it wrong. (For join queries the diamond is *declared* — see §3.5 — so
the operands are resolved structurally, never reconstructed; ≤ 2 branches / 1 join covers the
corpus. Deeper/equivalent decompositions are out of scope, §11.)

### 3.7 Routing — A\* over states with longest-prefix reuse

The target is the single answer node, whose fingerprint *is* the canonical chain. Routing:

1. Derive the chain (§3.6).
2. Find the **longest prefix already materialised** in the graph. If `S2` exists from an
   earlier query, start there and execute only the suffix `[sort, limit]`. If nothing exists,
   execute the whole chain from `S0`.
3. Execute the suffix as deterministic primitives, recording each new state-node.
4. **Gap → Call-2** only if the chain references a kind with no registered primitive. For
   tabular, ≈ never.

A\* generalises step 2: where multiple **equivalent** chains reach the same answer
(commutative reorderings), it selects the one with the deepest existing prefix — maximising
reuse. Heuristic = remaining chain-suffix length (= count of target verify-predicates not yet
satisfied) — admissible. The target spec bounds the search; no blind search over param space.

### 3.8 Acceptance

See §10.

---

## 4. Backend environment — Phase 2 (design only; do not build yet)

Same trinity, instantiated on a running service. Documented here to fix the direction.

### 4.1 Trinity mapping

- **State** = verify-bits over the live system: `user_42_exists`, `orders_nonempty`,
  `GET /checkout → 200`, `schema_at_v7`.
- **Operations** (~12–15 kinds): `get`, `list`, `query` (reuses the tabular algebra as the
  store query sub-language), `create`, `update`, `delete`, `call(endpoint, payload)`,
  `authenticate → token`, `begin/commit/rollback`, `migrate(version)`. Resource, endpoint,
  and payload are the params.
- **Verify**: `assert_status`, `assert_schema(response, schema)`, `assert_exists`,
  `assert_field`. Reproducible, at any resolution.

A backend task is: from the current verified state, A\* a path of API calls to a target
verified state; `create(user_42)` is the morphism that flips the `user_42_exists` bit.

### 4.2 Alignment wins

- The five dimensions map onto REST almost one-to-one (Observe=GET, Transform=POST/PUT/
  PATCH/DELETE, Verify=status/schema, Route=resource graph). **Contract is pre-populated**
  by OpenAPI/GraphQL schemas — the precondition/postcondition layer is handed over
  machine-readable rather than inferred.
- HTTP method semantics (safe/idempotent/unsafe) are a built-in **reversibility**
  classification of morphisms, which feeds the entropy-as-edge-cost model: GET is
  zero-cost and freely repeatable, POST/DELETE are irreversible and high-cost. The planner
  therefore prefers read-heavy paths and treats mutations as expensive — a safety property.

### 4.3 The irreversibility adaptation (the real new requirement)

Tabular exploration is free (pure functions, recompute anything). Backend morphisms have
irreversible real-world cost. Therefore:

- **Split morphisms into safe (reads, freely explorable) and unsafe (mutations).**
- **Exploration runs against a reversible or simulated copy** — a sandbox/staging instance,
  a transaction rolled back, or pure contract-level simulation using the OpenAPI schema to
  *plan* without executing. Only the final verified plan commits to production, once.
- **State is live and shared**, so verifies assert post-conditions *relative to the
  operation* ("after `create(X)`, `X` is readable"), scoped and snapshot-relative. Identity
  is per-transaction, not timeless.

### 4.4 Building vs operating

Operating (above) is the closed-algebra case. Building a backend splits on the
substrate/frontier line: scaffold (routes, schemas, CRUD handlers, migrations) is
algebraic and verifiable (compiles, round-trips, contract test passes) → primitive-izable;
novel business logic is the frontier (LLM-authored, verified by its test suite, which keeps
it a fixed point and lets it become a recorded node).

---

## 5. Code changes (concrete)

- **`make_node_id`** (db.py 204): kind-only → `sha256(canonical_chain)`. Promote the
  lineage+params fingerprint already computed in `make_workspace_path` (db.py 209–233).
- **Primitives**: add explicit `needs` / `produces` contracts (§3.2); keep the value-level
  verify.
- **`define_target`** (Call-1): emit an unordered `{operation, params}` set, not an ordered
  DAG with `depends_on` (orchestrator 547; the docstring at 503 describing "DAG steps").
- **New: spec compiler — contract-based topological ordering + operand resolution** — compiles
  the flat-or-recursive target spec (§3.5) into the ordered execution chain: topo-sort each
  (sub-)chain over the contracts, resolve each declared join operand recursively, then the join,
  then post-join steps. Replaces reliance on the LLM's `depends_on` and
  `validate_and_fix_ordering`'s trust in LLM order (orchestrator 44).
- **`route()`** (graph.py 135): from "cover the set of kind-nodes" to "reach the answer state
  via longest-prefix reuse + suffix execution; A\* over equivalent chains for max reuse."
  Heuristic (graph.py 142) → remaining chain-suffix / target-predicate distance.
- **Verify** (orchestrator 627): a node's verify becomes its identity predicate(s), not
  `assert file exists` — the file is the content, the predicate is the identity.
- **Retain unchanged**: primitive execution, value-level verify, Call-3, SQLite storage.

---

## 6. Compounding (the real story) — the new metric

The graph accumulates **state nodes**. The more queries run, the more intermediate states
exist, the longer the reusable prefix for any new query, and the **fewer primitives execute /
the less the LLM is consulted**. Reuse is keyed on the **state fingerprint**, so "active
risks" is shared across queries regardless of wording — the cross-query reuse a query-string
cache could never give.

Metric (replaces the old "Call-2 morphism library grows / code-authoring → 0"):
**primitives-executed-per-query** and **LLM-calls-per-query** decline as the state-graph grows
over a diverse corpus. Demonstration: a sequence of queries sharing intermediate states (many
filtering to "active") — later queries execute strictly fewer primitives because the shared
prefix is already materialised. This is the compounding figure the paper now reports.

---

## 7. What this fixes — and what it does not

**Fixes:** ordering variance, routing, and structure collapse (the **q10** class — there is
no structure for the LLM to collapse). Real cross-query compounding via shared state-nodes.

**Does not fix:** Call-1 declaring an **incomplete or wrong set** of properties (the **q15**
class — declaring 3 of the 8 operations the answer needs still yields a confidently wrong
answer, because the system only builds what was declared). Planner hardening is still required
— but its surface has shrunk from "author a correct ordered, routed DAG every time" to "name
the correct set of operations and params." It is a separate, now-smaller workstream that
composes with this architecture.

---

## 8. Open questions / out of scope

- **Node identity — RESOLVED to STATE FINGERPRINT.** The Phase-1 open question ({kind} vs
  {kind,params}) is closed: identity is `sha256(canonical_chain)` over the cumulative
  `(operation, params)` from root (§3.3). Rationale: the bug class that killed codegen (the
  `4643100` cross-query leak) was identity collapsing structurally-distinct states; the chain
  fingerprint keeps every distinct state its own node, which is exactly what makes
  cross-query state reuse (§6) sound — a cache key is trustworthy only if it is single-valued
  and reproducible. `{kind,params}` was the Phase-1 stepping stone; the full chain fingerprint
  is its principled completion (it captures *lineage*, so "aggregate of active" ≠ "aggregate
  of closed"). Canonicalisation depth is the only remaining sub-question (light in v1;
  behavioural equivalence as backstop, §3.3, §11).
- **Planner hardening ("declare the correct, complete set")** — first-class, separate
  workstream (the q15 class). Out of scope for this build.
- **Backend (§4)** — still Phase 2; the state-node model carries over (verify-bits over a
  live service), with the irreversibility adaptation already specified.

---

## 9. Out of scope for this build

- Planner-hardening / q15 under-declaration (the named workstream; q15 may stay failing).
- Backend (Phase 2).
- Deep canonicalisation (commutativity beyond filters, equivalent decompositions). Join
  operands are **declared** (§3.5), not reconstructed; declared specs of ≤ 2 operands / 1 join
  cover the corpus. No heuristic branch reconstruction.

---

## 10. Acceptance / validation

- **S7 corpus, persistent graph.** Queries for which Call-1 declares the correct property set
  resolve correctly with order **derived** (not authored). Call-2 = 0; no LLM in the
  execution path.
- **q10 fixed**: the ordering/structure-collapse mode is gone (order is derived).
- **Cross-query reuse demonstrated** (the real gate / new compounding figure): run a sequence
  sharing intermediate states (several filtering to "active risks", then diverging); assert
  later queries execute **strictly fewer primitives** because the shared prefix (e.g. the
  "active" filter → S1) is materialised once and reused. Per-query primitives-executed counts
  emitted to `results/compounding/`.
- **q15 may remain** (under-declaration) → planner-hardening workstream, out of scope here.
