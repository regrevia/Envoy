# Evidence Pack v1

Status: implemented (`envoy evidence export|verify`), behind the
`evidence_pack_export` flag.

An Evidence Pack is a directory written *from* a retained material record. It is
derived, never authoritative: the material record keeps that role, and deleting a
pack does not change what a read already returned. That split is what lets a
downstream tool (a Knowledge Unit, a Cangjie conversion, a RAG importer) work on
evidence without becoming a second source of truth.

## What it is for

Three questions that a plain read cannot answer, because a read answer lives in a
conversation and dies with it:

- Which exact text did the model see, and where in the source does each piece sit?
- What did the material *not* cover (pages skipped, transcript missing, export truncated)?
- Can a later process cite a passage without asking the model to retype it?

A pack answers all three with files and hashes, so citations stay checkable after
the conversation ends.

## Package layout

```
<out-dir>/
  manifest.json                      # envoy.evidence-pack/1
  README.md                          # what this is and what it is not
  source/
    document.json                    # envoy.source-document/1
    metadata.json                    # envoy.source-metadata/1 (session, expiry, pack id)
  structure/
    sections.json                    # ordered sections, each pinning evidence ids
    outline.md                       # human-readable outline
  evidence/
    text/<evidence-id>.txt           # exact exported text, one file each
    transcript/<evidence-id>.txt
    visual/<evidence-id>.txt
  references/
    evidence-index.json              # evidence id -> block id, locator, file, sha256
  coverage.json                      # envoy.evidence-coverage/1
```

`evidence/` holds three directories even when empty. A pack is written into a
temporary sibling directory and moved into place with one `os.replace`, so a
failure leaves the material and any previous pack byte-identical.

## Identifiers

Every identifier is a hash of stable inputs, so exporting twice yields byte-identical
files and identical ids:

| id | derived from |
| --- | --- |
| `material_id` | the reader's material record (`mat-<24 hex>`) |
| `source_id` | `src-` + material id body |
| `section_id` | material id + section key (`page-3`, `time-90`, `document`) |
| `evidence_id` | material id + block id + block order |
| `pack_id` | sha256 of the manifest (minus `pack_id`), the Source Document and the evidence index |

There is no `generated_at` field anywhere, deliberately: a timestamp would make two
exports of the same revision differ, and the pack would stop being a reproducible
derivative.

## Source Document v1 (`envoy.source-document/1`)

The one contract every other layer reads. It is the read answer, normalised, with
places attached.

```json
{
  "schema_version": "envoy.source-document/1",
  "source": {
    "source_id": "src-<hex>",
    "material_id": "mat-<hex>",
    "type": "web|pdf|video|audio|image|github|office",
    "title": "…",
    "url": "…",
    "revision": "…"
  },
  "sections": [
    {
      "section_id": "…",
      "title": "Page 3",
      "start": 1200,
      "end": 4800,
      "locator": {"type": "pdf_page", "page_number": 3},
      "evidence_ids": ["ev-…"]
    }
  ],
  "coverage": {
    "text": true,
    "transcript": false,
    "ocr": false,
    "visual": false,
    "page_count": 10,
    "indexed_page_count": 9,
    "unpinned_blocks": ["blk-title"]
  }
}
```

- `sections` come only from spans the material stored (`page_spans`, video/audio
  spans). When there are none, the document is one `document` section; a pack never
  invents structure it cannot locate.
- `coverage.unpinned_blocks` names blocks that carried text but no locator. A title is
  not a place; those blocks are counted, never exported, and never cited.

## Evidence mapping

`references/evidence-index.json` is the citation contract:

```json
{
  "evidence_id": "ev-<hex>",
  "block_id": "blk-pdf-page-0003",
  "kind": "text|transcript|visual",
  "locator_kind": "web|pdf_page|video_time|audio_time|image_region|office_location",
  "locator": {"type": "pdf_page", "page_number": 3},
  "file": "evidence/text/ev-<hex>.txt",
  "sha256": "…",
  "chars": 812
}
```

Rules:

1. One citation, one file. The quote is the exported text itself; a second copy in
   the index would let the two disagree, so the index holds a digest instead.
2. `kind` is the modality directory (`text`, `transcript`, `visual`); `locator_kind`
   is the precise place type. They are separate because "transcript" and "PDF page"
   answer different questions.
3. The reader synthesises whole-document blocks (`blk-body-0001`, `blk-video-transcript`, …)
   so that a `-range-` id has a base. Those repeat the entire text and are dropped
   as soon as finer blocks exist — otherwise every passage would be exported twice.
4. Text a model produced is labelled by its locator (`video_time` from ASR,
   `image_region` from OCR) and never presented as original source text.
5. `sha256` is over the file bytes exactly as written; `chars` counts the text
   without the trailing newline.

Locators keep the medium's own coordinates: PDF page numbers, video/audio
timestamps, GitHub line numbers, image regions, office locations. Nothing is
flattened into a character offset that would not survive a re-read.

## Coverage

`coverage.json` is where a partial read admits it is partial:

```json
{
  "schema_version": "envoy.evidence-coverage/1",
  "material_id": "mat-<hex>",
  "source_type": "pdf",
  "unresolved": ["pages 4-5 were not returned", "the source text exceeded the budget"],
  "truncated_evidence_ids": [],
  "evidence_bytes": 92140
}
```

`unresolved` copies the material's own gaps (`source_coverage_unresolved`,
`delivery_gap`) and adds export-side limits: evidence capped per block
(`MAX_EVIDENCE_CHARS`) or overall (`MAX_TOTAL_EVIDENCE_CHARS`) is listed in
`truncated_evidence_ids`. An empty `unresolved` list is a claim that the export is
complete with respect to the material — not that the material covers the whole
source.

## Verification

`envoy evidence verify <dir>` recomputes `pack_id` from the manifest, the Source
Document and the evidence index, and re-hashes every evidence file. It reports
`{"valid": true|false, "problems": [...]}` and exits non-zero when anything was
edited, removed or added. A damaged pack is reported, not trusted; it is never
silently repaired, because a repaired pack would hide the damage.

## Determinism and safety

- Export reads the retained material only. It never fetches, never calls a model and
  never copies raw media; `evidence/` holds text.
- Two exports of the same revision are byte-identical (`tests/cli/test_evidence_pack.py`).
- Export leaves the material byte-identical, including its expiry file.
- `pack_exists` refuses to clobber a non-empty directory unless `--overwrite`;
  `--overwrite` replaces the whole pack, not individual files.
- Expired, missing and unreadable materials are reported as such (`material_expired`,
  `material_missing`, `material_unreadable`) rather than as empty packs.

## Flag

`evidence_pack_export` is off by default. `envoy evidence export` refuses with
`ExtensionDisabled` until `envoy extensions enable evidence_pack_export` (or
`ENVOY_EXTENSIONS=evidence_pack_export`). `envoy evidence verify` stays available
because reading a pack someone handed you costs nothing and needs no capability.

## Relationship to the layers

The pack is the *protocol* between layers: Core produces materials and packs;
Extensions read packs (structure analysis, distillation, relations); Integrations
convert packs into someone else's format. Nothing in this format knows about
Cangjie, a Skills runtime or a vector store — those are adapters on top of it, and
removing them leaves the pack untouched.

Schemas: [manifest](../schemas/evidence-pack-v1.schema.json), [evidence index](../schemas/evidence-index-v1.schema.json), [source document](../schemas/source-document-v1.schema.json).

See also: [Knowledge Units v1](knowledge-units-v1.md), [Cangjie adapter](cangjie-adapter.md).
