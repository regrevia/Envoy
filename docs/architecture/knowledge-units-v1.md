# Knowledge Units

Status: implemented (`envoy knowledge …`), behind the `knowledge_units` flag.
Current schema: `envoy.knowledge-unit/3`, which still reads `/2`, which still reads `/1`.

A Knowledge Unit is a claim that survived contact with its evidence. It is not a
summary and not a note: it carries its sources, its conditions, its limits, and a
record of what it was checked against. The unit is what an agent may act on; the
Evidence Pack stays what it may cite.

```json
{
  "schema_version": "envoy.knowledge-unit/3",
  "unit_id": "ku-<16 hex>",
  "version": 2,
  "content_hash": "<16 hex>",
  "status": "candidate",
  "type": "procedure",
  "title": "…",
  "summary": "…",
  "trigger": "…",
  "inputs": ["…"],
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
  "relations": [
    {
      "relation_id": "kr-<16 hex>",
      "type": "supports",
      "from_unit_id": "ku-<16 hex>",
      "to_unit_id": "ku-<16 hex>",
      "from_version": 2,
      "inverse_of": null,
      "evidence": [{"material_id": "mat-<24 hex>", "evidence_id": "ev-<16 hex>", "locator": {"…": "…"}}],
      "at": "2026-11-10T12:00:00Z",
      "content_hash": "<16 hex>"
    }
  ],
  "verified_at": null,
  "verified_against": null,
  "history": [{"version": 1, "at": "2026-11-10T12:00:00Z", "change": "created", "status": "candidate"}]
}
```

## Four identities, and what each one is for

A unit carries four different fingerprints, and conflating any two of them causes a
bug. They change on different events and are written by different code.

| field | what it identifies | computed as | changes when | written by |
| --- | --- | --- | --- | --- |
| `unit_id` | the claim itself, content-addressed | `"ku-" + sha256(canonical(body))[:16]`, body = `type`, `title`, `summary`, `conditions`, `steps`, `limitations`, `source_refs` | never — it is the address; a claim that changed its body is a different unit | `create` only |
| `version` | one row in the unit's own edit history | `int(previous) + 1` | `update`, `rollback` (and every snapshot). **Not** `verify`, `promote`, `archive`, `relate` | every mutating command that rewrites the body |
| `content_hash` | the body as it stands right now | `sha256(canonical(body))[:16]` — the same digest `unit_id` is built from, kept separately because a legacy unit can carry an `unit_id` from `/1` whose body was normalised on load | `update`, `rollback`. Re-stamped on every write, so a corrupted or hand-edited `content_hash` is repaired rather than trusted | all writes; read paths compute it lazily and never persist |
| `verified_against` | what the evidence looked like when `verify` last succeeded | `sha256(canonical({"refs": source_refs, "material_revisions": sorted(revisions)}))[:16]` | `verify` (set), `update`/`rollback` (cleared to `null`) | `verify` |

Consequences worth stating plainly:

- `unit_id` does **not** change when a unit is edited, even though the body does. The
  id is assigned once, at birth, and stays the unit's name. `content_hash` is what
  tells you whether the body still is what the name was minted from.
- `version` does **not** move on `verify`/`promote`/`archive`/`relate`. Those change
  state or annotations, not the claim, so a reader diffing `v2.json` against
  `v3.json` only ever sees textual edits.
- `verified_against` is the only one that depends on the evidence rather than on the
  unit, which is why it is the pivot of both the stale-verification and the
  re-validation checks below.

## The lifecycle, and why it is strict

```
candidate --verify--> verified --promote--> promoted
    ^                   |                     |
    +---- update -------+      archive <------+
                                    |
                              reopen|
                                    v
                                candidate
```

- **candidate** — written from evidence that has not been re-checked.
- **verified** — `verify` resolved every `source_ref` against the material:
  the material still loads, the `evidence_id` is in its pack index, the claimed
  revision still matches, and the locator still matches the material's own. If
  anything fails, `verify` refuses with `evidence_unresolved` and names each bad
  reference. An unverified claim cannot become verified by editing the unit; only
  resolving evidence does that.
- **promoted** — usable, not merely true. Promotion additionally requires a
  `trigger`, at least one condition, one step, one limitation and one test. The
  difference between "true" and "usable" is exactly the cases in which it stops
  being true — and, for `trigger`, whether anyone can find it. `conditions` says
  when the claim holds; `trigger` says when to reach for it. Those are different
  sentences, and only the second one can be routed on, which is why a unit with no
  trigger stays `verified` and never becomes `promoted` (`unit_incomplete`,
  `missing: ["trigger"]`).

`/3` added two body fields, both part of the content hash: `trigger` (a string,
≤ 400 characters) and `inputs` (a list). A `/1` or `/2` record read from disk gets
`trigger: ""` and `inputs: []` rather than failing to load; its content hash is the
hash of a record that says it has no trigger. Adding a trigger to such a unit is
an ordinary `update`, so it bumps `version` and resets the status to `candidate`,
and the evidence has to resolve again before the unit can be promoted.
- **archived** — kept, out of the way. Archival never deletes: a claim may be
  wrong in a way that is worth remembering, and a source may reappear. It is
  therefore not a dead end, but it is a decision, and nothing else overrides it:
  `update`, `rollback`, `verify`, `promote` and a second `archive` refuse an
  archived unit with `unit_archived` rather than quietly reviving a withdrawn claim
  at a new version or recording the same decision twice. `reopen` is the one path
  out: it puts the unit back at `candidate` without bumping `version`, because the
  body did not change, and without restoring any earlier verification, because a
  claim that was withdrawn has to earn `verified` again.

`update` bumps `version`, resets the status to `candidate`, and appends
`{"change": "updated", "from_status": …}`. A verified unit that is edited is not
verified any more, by construction — there is no path that keeps a stale
`verified_against` fingerprint.

`reopen` is the only way out of `archived`, and it is deliberately a command rather
than a side effect of some other write. It moves the unit back to `candidate` without
bumping `version` (the body did not change) and appends
`{"change": "reopened", "from_status": "archived"}`. Verification is cleared, so a
reopened unit has to be verified again. Reopening a unit that is not archived is
`not_archived`; `archive` on an already archived unit is `unit_archived`; and
`update`/`rollback`/`verify`/`promote` refuse an archived unit with `unit_archived`
instead of reviving it.

Every mutation writes a snapshot to `knowledge-units/<unit>/v<version>.json` and
appends to `history`. A snapshot is the whole record as it stood at one body
version, so the snapshot always agrees with the record for the version it names:
`verify`, `promote`, `archive` and `relate` do not bump `version`, and therefore
rewrite `v<version>.json` rather than leaving an older state of that version
behind. Once the body moves to the next version, the earlier snapshot is frozen.

`rollback --version N` restores version N's body as a *new* version
(`{"change": "rolled_back", "restored_version": N}`), so the undo is itself
auditable. It copies only the body fields, never `status`, `relations` or
`verified_against`, so a refreshed snapshot cannot change what a rollback brings
back. Rolling back to the current version (`N >= version`) or to a version that
never existed is `invalid_version`.

### Promotion re-validates; it does not trust the earlier `verify`

`promote` re-runs the whole reference check rather than believing `status ==
"verified"`. Bit-rot between `verify` and `promote` is the normal case, not the
exception: a material can be evicted by its lease, and a source revision can move
on. Two distinct failures are reported separately, because they call for the same
action but mean different things:

- refs that no longer resolve at all → `evidence_unresolved`, `details.problems`
  listing each bad reference;
- refs that all resolve but whose material revisions no longer match the stored
  `verified_against` → `verification_stale`, with `details.expected` (the stored
  fingerprint) and `details.actual` (the current one).

Both messages end with "verify the unit again", and neither is silently upgraded.

### Refs without a revision are visible, not refused

`/1` allowed a `source_ref` with no `source_revision`: such a reference can be
resolved but not pinned, so it cannot prove anything about *which* revision was
checked. Removing that tolerance would retroactively fail every unit written before
pinning was available, so instead:

- `verify` and `promote` accept unpinned refs and record them;
- `verify_unit` returns `unpinned_refs` (a count) and `warnings` (one line per
  unpinned ref, naming its index), and `envoy knowledge verify` prints each as
  `warning: …`;
- nothing raises over an unpinned ref, and no status is withheld because of one.

`revision_unpinned` is therefore a warning string, never an error code — a unit
that was acceptable yesterday stays acceptable today, but an operator can now see
which units are not fully pinned.

## Relations

`relate` records `supports`, `contradicts`, `extends`, `depends_on` between two
units. Relations are evidence-neutral statements of structure; they do not transfer
verification. Two materials supporting one unit is normal, and a conflicting
material is recorded as `contradicts` rather than resolved by deleting the older
claim.

A relation is a standalone, verifiable record, not a bare `{"type", "to"}` pair:

- `from_unit_id` / `to_unit_id` name both ends, so a relation row copied out of a
  unit is still readable on its own;
- `type`, `evidence` (refs already cited by one of the two units), `at` and
  `from_version` say what was asserted, against which evidence, when, and against
  which version of the source;
- `content_hash` fingerprints the assertion itself — `type`, both ends, `evidence`
  and `inverse_of` — so tampering is detectable and the same assertion made twice
  fingerprints the same;
- `relation_id` is `kr-` + a digest that also covers `at`, so it names this row
  rather than the assertion.

Each relation is stored on **both** units, as two distinct rows: the source gets the
asserted direction, the target gets the reversed one (`from_unit_id` and
`to_unit_id` swapped, `from_version` taken from the target's own version) with
`inverse_of` pointing at the asserted row's `relation_id`. Both directions are
queryable directly, `relation_view(unit)` normalises them for rendering and
comparison, and `verify`/`promote` re-check every `/2` row's `content_hash`.

Old inline relations — `{"type", "to", "evidence", "at"}`, as `/1` wrote them — are
still read: `relation_view` fills `from_unit_id` from the owning unit, `to_unit_id`
from `to`, and reports `legacy: true` with `content_hash: null`. They are never
refused and never block verification, because there is no fingerprint to check and
no `from_version` was ever recorded. New rows use only the `/2` shape; nothing
rewrites a legacy row in place.

A relation that already exists is `relation_exists`, not a duplicate row. The two
ends are one fact, so the check is unordered: `a supports b` and `b supports a`
are the same relation written the other way round, and the second one is refused
rather than appended as a second row on each unit. A different relation type
between the same pair is a different fact and is allowed.

Because the mirror is a second write, the two ends are written as one fact: if the
target cannot be committed, the asserted end is put back, and only if that also
fails does `relate` raise `relation_half_written`, naming both units to check. A
relation that survives on one end only would read as two disagreeing records, which
is worse than a refused write.

## Storage

```
<envoy-root>/knowledge-units/
  ku-<hex>.json              # current record
  ku-<hex>/v1.json           # version snapshots; only the current version is rewritten
  ku-<hex>/v2.json           # a snapshot freezes once a later version exists
  .units.lock                # empty; flock target for writers
```

Writes are atomic (`tmp` file + `os.replace`). A snapshot is written with every
mutation and rewritten only while its version is still the current one; from the
moment a later version exists it is never touched again. A write whose text
matches what the file already holds is skipped, so closing a version that was
already stored in step costs nothing. Unit records are plain
JSON so they can be read with `cat`, diffed, and copied out — no database, no
server. That is a deliberate non-goal: Knowledge Units are not a knowledge graph
product.

## Compare-and-set: `--if-version`

Every writing command that targets an existing unit takes an optional
`expected_version`. When it is supplied and the stored `version` differs, the write
is refused with `version_conflict` (details `expected`, `actual`) instead of
applying on top of a state the caller never saw. When it is omitted the behaviour is
exactly what it was before: last writer wins.

On the CLI that is `--if-version N`, available on `update`, `archive`, `reopen`,
`rollback`, `relate` and `promote`:

```console
$ envoy knowledge show ku-…            # v3
$ envoy knowledge update ku-… --if-version 3 --file patch.json
$ envoy knowledge update ku-… --if-version 3 --file patch.json
{"error": "version_conflict", "message": "ku-… is at version 4, not 3; reload and retry", "expected": 3, "actual": 4}
$ envoy knowledge reopen ku-… --if-version 4   # archived -> candidate, still v4
```

`relate` writes to two units, so it takes two gates: `--if-version` for the unit
named on the command line and `--if-target-version` for the one behind `--to`.
Either one being stale refuses the whole write, which is what keeps the two ends
from recording different `from_version`s for one relation.

Every `envoy knowledge` subcommand takes `--json` and prints one object (or a list,
for `list`) on success, and `{"error": <code>, …}` with exit status 1 on a refusal,
so a caller never has to read the human-readable lines to learn what happened.

Because the check and the write are two steps, they run under an exclusive lock on
`knowledge-units/.units.lock` (`fcntl.flock`, `msvcrt.locking` on Windows — the same
idiom `batch.py` uses). Readers never take the lock: every write replaces a whole
file with `os.replace`, so a reader always sees a complete unit, at worst a stale
one. With the lock, N concurrent writers holding the same `expected_version` produce
exactly one success and N−1 `version_conflict`; without it, all N succeed and the
last one wins silently. The lock is best-effort across hosts (it is advisory and
local-filesystem oriented), which is enough for the single-host material store.

## Compatibility: `/1` files in a `/2` world

`SCHEMAS = ("envoy.knowledge-unit/1", "envoy.knowledge-unit/2", "envoy.knowledge-unit/3")`, and the reader
accepts both. The differences are additive:

- `content_hash` did not exist. A `/1` file without it still loads, lists, verifies
  and renders — `unit_content_hash` computes it on demand and never persists it, so
  a read path performs no write. It is filled in the next time the unit is written.
- `relations` were inline `{"type", "to", "evidence", "at"}`. They still read, and
  render as `relation <type> -> <unit_id>`; only new relations use the `/2` shape.
- `schema_version` stays whatever the file says. Nothing migrates on read, and
  `create` writes `/2`.

The schema document at `docs/schemas/knowledge-unit-v1.schema.json` still describes
`/1` and is stricter than the reader (`additionalProperties: false`, `schema_version`
const`/1`): it will reject a `/2` record. No code validates against it, so this is a
consumer-contract drift to fix when the schema is versioned, not a runtime problem.

## What is *not* here

- No model call. `create` validates and stores; it does not summarise, and it does
  not choose claims for you. Distillation is an extension you run yourself.
- No automatic promotion. Nothing becomes `promoted` without evidence resolving
  and the usability fields being filled.
- No deletion API. `archive` is the only way to retire a unit.
- No coupling to one extractor. Units are produced by any process, including a
  hand-written JSON file; the format is the contract.
- No semantic similarity, no cross-unit merging, no graph algorithms. Relations are
  recorded and read; they are not traversed.

## Flag

`knowledge_units` is off by default, and gates only the writing commands
(`create`, `update`, `verify`, `promote`, `archive`, `relate`, `rollback`).
`list` and `show` stay ungated — constraint 4: turning an extension off must never
make existing material harder to read.

See also: [Evidence Pack v1](evidence-pack-v1.md).
