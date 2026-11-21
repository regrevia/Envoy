# Capability Bundle: review of the proposed direction

Status: review + phase plan. Nothing in the "to build" column below exists yet.

The proposal was: turn Envoy from a content-extraction tool into an
evidence-to-capability engine, with a Capability Bundle as the intermediate
layer between a material and a Skill, gated by triple verification, compiled to
several targets, and updated when the evidence changes.

This note records what the proposal got right, what already exists in the tree,
what should be cut, and where the boundary against Envoy's original job sits.

## 1. The proposal's P0 is already implemented, and unreachable

`packages/envoy-cli/src/envoy_cli/knowledge_units.py` is a validated claim with
its evidence attached: `envoy.knowledge-unit/3`, 45 tests green, `envoy knowledge
create|verify|promote|archive|reopen|update|rollback|relate` wired into the CLI.

Checked against the proposed card field by field:

| proposed field | what exists today | verdict |
| --- | --- | --- |
| `capability_id` | `unit_id` = `ku-` + sha256(canonical body)[:16], frozen at creation | have, stronger (content-addressed) |
| `name`, `description` | `title`, `summary` | have |
| `trigger` | nothing. `conditions` says when the claim holds, not when to reach for it | **missing** |
| `inputs` | nothing | **missing** |
| `procedure` | `steps` | have |
| `principles` | a whole unit `type` | have, as a linkable unit |
| `examples`, `counterexamples` | unit types `example`, `counterexample` | have, as linkable units |
| `boundaries` | `limitations` + `tests.kind = near_miss | boundary` | have |
| `evidence_refs` | `source_refs` with `material_id`, `evidence_id`, `locator`, `source_revision` | have, stronger (locator required) |
| `dependencies` | `relations[].type = depends_on`, stored on both sides with `inverse_of` | have |
| `confidence` | nothing. `status` is the only signal | **missing** |
| `validation_status` | `candidate → verified → promoted`, `archived` reachable | have |

So roughly two-thirds of the proposed data model is shipped, including the part
the proposal treated as the hard one: the promotion gate. `verify` resolves every
`source_ref` against the material record — the material still loads, the
`evidence_id` is in the pack index, the claimed revision still matches, the
locator still matches the material's own — and `verified_against` fingerprints
what the evidence looked like at that moment. Editing a verified unit drops it
back to `candidate`; there is no path that keeps a stale fingerprint.

The gate the proposal wanted is not `Source Sufficiency / Executability / Task
Utility / Boundary Safety` under four new names. It is the existing gate, and
the names should not be renamed: `candidate`, `verified`, `promoted` are pinned by
`tests/cli/test_knowledge_units.py` and by units already on disk.

**What was actually missing was reachability,** and that is now closed in the
same release as this review. Before it, `grep -c knowledge` was 0 in both
`mcp.py` and `capabilities.py`: an agent talking to Envoy over MCP could read a
material and never learn that a verified claim existed, let alone which one
applied. The reachable surface is `envoy_knowledge` over MCP plus the
`envoy knowledge` CLI verb group, both gated on the `knowledge_units` extension.
What remains unbuilt is everything in §2 below.

## 2. What the proposal adds that is genuinely new

Three things, in descending order of value:

1. **The compiler.** Nothing today turns a unit into something another runtime
   loads. One bundle should produce `SKILL.md`, a pack manifest, and an MCP
   handler from the same body, deterministically, with no second model pass.
   This is the cheapest real capability in the whole plan and the one that makes
   units worth writing.
2. **`trigger` and `inputs`.** A `steps` list is a procedure; a procedure is not
   routable. Routing needs "reach for this when …" as its own field, because it
   is not the same sentence as the conditions under which the claim is true.
3. **Composition relations.** `depends_on` exists. `composed_of` and
   `alternative_to` do not, and both are needed the moment a pack is assembled
   from several bundles: `composed_of` says what a pack contains, `alternative_to`
   says what to fall back to when one path is blocked.

## 3. What should be cut or deferred

- **The model-calling distillation engine.** This is the only part of the
  proposal that damages something Envoy already promises. `knowledge_units.py`
  opens with "No model is called from here", and `docs/architecture/
  cangjie-adapter.md` says model-driven distillation "belongs to whatever
  distillation pipeline the user installs, running outside Envoy's core, with its
  own credentials". Adding a config'd `base_url`/`api_key` to the CLI would
  replace an offline integrity guarantee with a networked one, and would make
  every failure mode of a model endpoint into a failure mode of Envoy's own
  `verify` story.
- **The four-state promotion model.** It is the existing three-state model with
  new names plus an extra hop. Renaming costs a schema migration and buys
  vocabulary.
- **The three-arm evaluation harness, for now.** It is the right experiment and
  it is not the first thing to build: it needs tasks, human labels, and budget,
  and it cannot be run before there is a bundle and a compiler to compare
  against raw material. Build it once the compiler exists, not before.
- **Incremental update and version governance as a phase.** Versioning already
  works (`version`, snapshots, `rollback`, `verified_against`). Source-diff-driven
  regeneration is real work, but it is work on top of a reachable bundle, not a
  prerequisite for one.

## 4. Where the model should sit instead

The proposal assumed Envoy must run distillation, so it needs a model. It does
not. The host agent already has the material in its context — it called
`envoy_read` and paid for those tokens. Let it write the candidate unit body, and
let Envoy judge it:

- `envoy_knowledge` (new MCP tool): `create` / `verify` / `promote` / `list` /
  `relate` over the existing store, plus `compile` once a unit is promoted.
- The agent submits a body with `source_refs` it took from the material it just
  read. Envoy checks `material_id` and `evidence_id` against the pack index and
  the locator against the material's own. A claim whose refs do not resolve is
  refused with the same `evidence_unresolved` code the CLI uses.
- No API key, no model config, no outbound call, no new failure mode. The offline
  guarantee is untouched because Envoy still never calls a model — it only
  refuses claims that cannot be pointed at.

This is also the answer to the drift worry, in one sentence: **the model proposes,
evidence disposes.** Envoy's job does not become "write skills"; it stays "decide
what may be kept and where it came from". A prompt-pack generator has no gate. A
knowledge store whose every entry resolves to a material revision is a different
kind of object, and the reason it stays different is that the refusal path is
mechanical and the model cannot talk its way past it.

## 5. Phases, revised

1. **Reachable bundle — shipped in 0.1.62.** `trigger` and `inputs` are in the
   unit body (schema `/3`, `/2` still reads — the loader is version-tolerant),
   and the store is exposed over MCP as `envoy_knowledge`. Acceptance held: an
   agent creates a unit from a material it read, a unit with a wrong locator is
   refused with `evidence_unresolved`, `list` answers with trigger and status.
2. **Compiler — shipped in 0.1.63.** `envoy knowledge compile <unit_id> --target
   codex|claude|dsh` emits `SKILL.md` plus a manifest from the stored body, under
   `skill_distillation` and only for a promoted unit. It is a local, deterministic
   step: no model, no write-back to the unit, artifacts written beside the store
   rather than into it. Acceptance held: three golden files (one per target) match
   byte for byte, a second compile reports nothing written, and the manifest names
   the material revision and unit revision the skill came from.
3. **Composition.** `composed_of` and `alternative_to`; `envoy knowledge pack`
   over a set of units with dependency closure; routing by `trigger`.
4. **Evaluation.** Three arms against the same tasks and budget. Report task
   success, not skill count.
5. **Evolution.** Diff a re-ingested material against the revision a unit was
   verified against; list affected units; re-verify only those.

Phases 1 and 2 are what exists today and they are small on purpose: a unit is
only an execution rule when its evidence still resolves, and a skill is only a
rendering of such a unit.
