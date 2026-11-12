# Knowledge Units v1

Status: implemented (`envoy knowledge …`), behind the `knowledge_units` flag.

A Knowledge Unit is a claim that survived contact with its evidence. It is not a
summary and not a note: it carries its sources, its conditions, its limits, and a
record of what it was checked against. The unit is what an agent may act on; the
Evidence Pack stays what it may cite.

```json
{
  "schema_version": "envoy.knowledge-unit/1",
  "unit_id": "ku-<16 hex>",
  "version": 2,
  "status": "candidate",
  "type": "procedure",
  "title": "…",
  "summary": "…",
  "conditions": ["…"],
  "steps": ["…"],
  "limitations": ["…"],
  "source_refs": [
    {
      "material_id": "mat-<24 hex>",
      "evidence_id": "ev-<16 hex>",
      "locator": {"type": "pdf_page", "page_number": 3},
      "source_revision": "rev-…"
    }
  ],
  "tests": [{"kind": "positive", "question": "…"}],
  "relations": [],
  "verified_at": null,
  "verified_against": null,
  "history": [{"version": 1, "at": "2026-11-10T12:00:00+00:00", "change": "created", "status": "candidate"}]
}
```

## Identity

`unit_id` is `ku-` + sha256 of the canonical body it was created from: `type`,
`title`, `summary`, `conditions`, `steps`, `limitations`, `source_refs` — the
claim's content, not its bookkeeping. Two extractions of the same claim from the
same evidence produce one unit; `create` returns the stored unit instead of a
duplicate. Everything else (`status`, `history`, `relations`, `verified_at`) is
state that moves without changing what the unit says.

`type` is one of `principle`, `procedure`, `method`, `example`, `counterexample`,
`condition`. No free-form types: an unclassifiable claim is usually not a unit yet.

## The lifecycle, and why it is strict

```
candidate --verify--> verified --promote--> promoted
    ^                   |                     |
    +---- update -------+      archive <------+
```

- **candidate** — written from evidence that has not been re-checked.
- **verified** — `verify` resolved every `source_ref` against the material:
  the material still loads, the `evidence_id` is in its pack index, the claimed
  revision still matches, and the locator still matches the material's own. If
  anything fails, `verify` refuses with `evidence_unresolved` and names each bad
  reference. An unverified claim cannot become verified by editing the unit; only
  resolving evidence does that.
- **promoted** — usable, not merely true. Promotion additionally requires at least
  one condition, one step, one limitation and one test. The difference between
  "true" and "usable" is exactly the cases in which it stops being true.
- **archived** — kept, out of the way. Archival never deletes: a claim may be
  wrong in a way that is worth remembering, and a source may reappear.

`update` bumps `version`, resets the status to `candidate`, and appends
`{"change": "updated", "from_status": …}`. A verified unit that is edited is not
verified any more, by construction — there is no path that keeps a stale
`verified_against` fingerprint. `verified_against` is a sha256 over the refs and
their material revisions at verification time, so a source revision moving on
invalidates the earlier check rather than silently blessing it.

Every mutation writes a snapshot to `knowledge-units/<unit>/v<version>.json` and
appends to `history`. `rollback --version N` restores version N's body as a *new*
version (`{"change": "rolled_back", "restored_version": N}`), so the undo is itself
auditable. Rolling back to the current version (`N >= version`) or to a version
that never existed is `invalid_version`.

## Relations

`relate` records `supports`, `contradicts`, `extends`, `depends_on` between two
units, stored on both sides with the type and the other unit's id. Relations are
evidence-neutral statements of structure; they do not transfer verification. Two
materials supporting one unit is normal, and a conflicting material is recorded as
`contradicts` rather than resolved by deleting the older claim. A relation that
already exists is `relation_exists`, not a duplicate row.

## Storage

```
<envoy-root>/knowledge-units/
  ku-<hex>.json              # current record
  ku-<hex>/v1.json           # version snapshots, immutable after write
  ku-<hex>/v2.json
```

Writes are atomic (`tmp` file + `os.replace`), and a snapshot is never rewritten
after it is written. Unit records are plain JSON so they can be read with `cat`,
diffed, and copied out — no database, no server. That is a deliberate non-goal:
Knowledge Units are not a knowledge graph product.

## What is *not* here

- No model call. `create` validates and stores; it does not summarise, and it does
  not choose claims for you. Distillation is an extension you run yourself.
- No automatic promotion. Nothing becomes `promoted` without evidence resolving
  and the usability fields being filled.
- No deletion API. `archive` is the only way to retire a unit.
- No coupling to one extractor. Units are produced by any process, including a
  hand-written JSON file; the format is the contract.

## Flag

`knowledge_units` is off by default, and gates only the writing commands
(`create`, `update`, `verify`, `promote`, `archive`, `relate`, `rollback`).
`list` and `show` stay ungated — constraint 4: turning an extension off must never
make existing material harder to read.

See also: [Evidence Pack v1](evidence-pack-v1.md).
