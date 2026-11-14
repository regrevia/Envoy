# Evidence Pack v2

Status: implemented (`envoy evidence export|verify`), behind the
`evidence_pack_export` flag. Supersedes
[Evidence Pack v1](evidence-pack-v1.md), which stays readable, verifiable and
convertible: v2 is a new file layout and a new set of schema versions, not a
migration of packs that already exist.

v1 answered "which text did the model see, and where does it sit". v2 answers a
fourth question a reviewer will ask next: **who wrote each passage** — the source
itself, or a model that transcribed, recognised or described it. It also makes the
whole pack directory, not just its citations, checkable.

## What changed, and what did not

| | v1 | v2 |
| --- | --- | --- |
| `manifest.json` | six `files` entries | eight `files` entries + `inventory` |
| `source/metadata.json` | `envoy.source-metadata/1` | `envoy.source-metadata/2`, now described by a schema of its own |
| `references/evidence-index.json` | no provenance | `origin`, `extractor`, `extractor_version` per row |
| damage detection | pack id + one digest per evidence file | same, plus a digest for every emitted file |
| `pack_id` | sha256 of manifest (minus `pack_id`), document, index | same input, minus `pack_id` **and** `inventory` |

Unchanged on purpose: the pack is still derived and never authoritative; the
material record is still the source of truth; there is still no timestamp, pid or
absolute path in the manifest; the pack is still written to a temporary sibling and
moved in with one `os.replace`; `evidence_pack_export` is still off by default.

## `manifest.json` (`envoy.evidence-pack/2`)

Eight `files` paths instead of six — `manifest.json` and `README.md` are now named,
so every emitted file appears in one table:

```json
"files": {
  "manifest": "manifest.json",
  "readme": "README.md",
  "document": "source/document.json",
  "metadata": "source/metadata.json",
  "sections": "structure/sections.json",
  "outline": "structure/outline.md",
  "evidence_index": "references/evidence-index.json",
  "coverage": "coverage.json"
}
```

`inventory` is new: one row per emitted file except `manifest.json`.

```json
"inventory": [
  {"path": "README.md", "sha256": "…", "bytes": 994},
  {"path": "coverage.json", "sha256": "…", "bytes": 201}
]
```

It is sorted by path and carries no clock, so two exports of the same revision
still produce identical bytes.

### Why `manifest.json` is not in the inventory

A file cannot hold its own digest. `manifest.json` is covered from the other side:
`pack_id` is the sha256 of the manifest header (minus `pack_id` and `inventory`),
plus `source/document.json` and the evidence index taken whole.

### Why the inventory is not part of `pack_id`

`source/metadata.json` carries `pack_id`, and the inventory hashes
`source/metadata.json`. Including the inventory in `pack_id` would make the two
define each other. They are therefore the same trust level: both catch accidental
damage — a truncated write, a half-copied directory, an editor that rewrote a file —
and neither survives a deliberate rewrite of *every* file in the pack. That is by
design; a pack is a derivative, and the material is the thing worth attacking.

## Provenance: `origin`, `extractor`, `extractor_version`

Every row of `references/evidence-index.json` and every block of
`source/document.json` now says where its text came from.

| `origin` | means |
| --- | --- |
| `source` | the text the source carried (HTML, PDF text layer, Office text, subtitle file, GitHub blob) |
| `asr` | speech recognition over audio or video |
| `ocr` | optical character recognition over a page image or an image post |
| `vision` | a description of a picture rather than text read from it |
| `derived` | text the reader computed (an outline, a summary of what it fetched) |

`extractor` names the producer (`asr`, or the OCR backend) and
`extractor_version` its version. Both are `null` when `origin` is `source`:
nothing produced that text but the source itself. Where the material did not
record a version, the field stays `null` rather than being guessed — a video
material persists neither the ASR backend nor its model, so an `asr` row carries
`extractor: "asr"` and `extractor_version: null`.

`source/metadata.json` repeats the summary as `origins` (the sorted distinct set)
and `ocr_applied`, so a reader does not have to walk the index to learn whether a
pack contains model text at all.

### Where the answer comes from

The reader already recorded it; the export only copies it.

- **PDF pages** come from `page_evidence.pages[n]`: `extraction_mode` of `text` or
  `layout` means the page had a text layer (`source`); `ocr` or `ocr_candidate`, or
  a page `status` of `ocr`/`ocr_failed`, means the words came from OCR. The page
  record's `ocr` object supplies the backend and `engine_version`.
- **Video and audio** come from `transcript_source`: `asr` means the transcript was
  recognised; any subtitle source (automatic or manual) means the text was already
  the source's. `video_time`/`audio_time` locators with a non-ASR transcript stay
  `source`.
- **Images** come from the stored record: non-empty `ocr_spans` means `ocr` (with
  `engine` as the extractor), non-empty `visual_resources` with no OCR means
  `vision`, and plain text means `source`.
- **Titles and block text with no locator** stay `source`.

## `source/metadata.json` (`envoy.source-metadata/2`)

New in v2 — v1 packs have no schema-described metadata file. It records what the
export knew that is not part of the read answer:

```json
{
  "schema_version": "envoy.source-metadata/2",
  "pack_id": "533f4c0c…",
  "material_id": "mat-bbb…",
  "source_type": "web",
  "source_title": "A page",
  "source_url": "https://example.com/a",
  "source_revision": "rev-1",
  "session_id": "session-a",
  "material_expires_at": null,
  "transcript_source": null,
  "ocr_applied": false,
  "origins": ["source"]
}
```

`material_expires_at` is the material's expiry at export time, in seconds since the
epoch, or `null`. A pack outlives its material by design; this says when the
original would have gone.

## `source/document.json` (`envoy.source-document/2`)

The shape is unchanged from v1 — sections, locators, `evidence_ids`, coverage — and
the version string moves only because the evidence index and metadata moved. The
one behavioural change is that `coverage.ocr` is now derived from the blocks'
`origin` rather than from the material's own flag, so it agrees with the index by
construction.

`coverage.unpinned_blocks` still lists blocks the pack could not place; a title is
not a place.

## `references/evidence-index.json` (`envoy.evidence-index/2`)

Each row gains three keys:

```json
{
  "evidence_id": "ev-417f7357fd95a0bf",
  "block_id": "blk-0001",
  "kind": "text",
  "origin": "source",
  "extractor": null,
  "extractor_version": null,
  "locator": {"type": "web", "source_url": "https://example.com/a"},
  "file": "evidence/text/ev-417f7357fd95a0bf.txt",
  "sha256": "…",
  "chars": 10
}
```

`kind` is still the modality directory (`text`/`transcript`/`visual`);
`locator_kind` is still the precise place type and may still be `null`.

## `coverage.json` (`envoy.evidence-coverage/2`)

Unchanged in shape from v1: `material_id`, `source_type`, `unresolved`,
`truncated_evidence_ids`, `evidence_bytes`. In v2 the file is also in the
inventory, so rewriting it is damage rather than an untracked edit.

## Verification (`envoy evidence verify`)

`verify_pack` recomputes and reports; it never repairs and never trusts.

1. `pack_id` — recomputed from the manifest header (minus `pack_id` and
   `inventory`), the document and the index. A mismatch is
   `pack id does not match the files it names`.
2. Each evidence file — `missing evidence file: <path>` or
   `evidence text changed: <path>`, one problem per file.
3. Each inventory row — `missing file: <path>` or `file changed: <path>`. Rows whose
   path the evidence loop already named are skipped, so one tampered evidence file
   yields exactly one problem.
4. Every other file under the pack root — `unexpected file: <path>`, so a stray copy
   into the directory is damage too.

An inventory row that is empty, absolute or climbs out with `..` is refused as
`manifest names an unusable path: <path>`; the verifier never resolves a path out of
the pack.

A pack with no `inventory` (any v1 pack) runs steps 1 and 2 only: v1 never pinned
the tree, so a file it does not name is not damage.

`verify` exit code is 0 only when the pack is valid, and the CLI prints
`<pack_id> valid` or `<pack_id> damaged`.

## Determinism and safety

- No wall clock, pid, hostname or absolute path is written into any pack file.
- Every text file is written with `newline=""`, so the bytes that are hashed are the
  bytes that land on disk, on every platform.
- The pack is assembled in a temporary sibling directory and moved into place with a
  single `os.replace`; a failure leaves the material and any previous pack
  byte-identical.
- `--overwrite` replaces the whole pack directory; without it, a non-empty target is
  refused with `pack_exists` and nothing is touched.

## Relationship to the layers

- **Reader / material store** — upstream and authoritative. The export reads a
  material and never writes to it; a test asserts the record is byte-identical after
  an export.
- **Cangjie adapter** — reads a verified pack and converts it. It calls
  `verify_pack` first and refuses a damaged pack with `pack_invalid`, so the
  provenance it copies is provenance that matched its digests. Cangjie's own frozen
  schema keeps the coarser `origin` vocabulary (`source`/`inferred`); `asr` and `ocr`
  both collapse to `inferred` there.
- **Knowledge Units** — built from `build_source_document`, so a unit inherits the
  same blocks and the same provenance.

## Schemas

- [evidence-pack-v2.schema.json](../schemas/evidence-pack-v2.schema.json) — `manifest.json`
- [source-document-v2.schema.json](../schemas/source-document-v2.schema.json) — `source/document.json`
- [source-metadata-v2.schema.json](../schemas/source-metadata-v2.schema.json) — `source/metadata.json`
- [evidence-index-v2.schema.json](../schemas/evidence-index-v2.schema.json) — `references/evidence-index.json`
- v1 files stay where they are: [evidence-pack-v1](../schemas/evidence-pack-v1.schema.json),
  [source-document-v1](../schemas/source-document-v1.schema.json),
  [evidence-index-v1](../schemas/evidence-index-v1.schema.json)
