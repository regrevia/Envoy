# Cangjie adapter

Status: implemented (`envoy evidence cangjie`), behind the `cangjie_adapter` flag.

Cangjie's five-agent distillation is not shipped here and is not planned. What is
shipped is the boring half that makes any distillation safe: a converter that turns
a verified Evidence Pack into a material document, preserving the original text and
the original places, and labelling anything a model produced.

```bash
envoy extensions enable cangjie_adapter
envoy evidence cangjie --pack ./pack --out ./material.json
```

Output is `envoy.cangjie-material/1` (see
[docs/schemas/cangjie-material-v1.schema.json](../schemas/cangjie-material-v1.schema.json)):
segments in reading order, each carrying `text`, `locator`, `locator_kind`,
`evidence_id`, `sha256`, `section_id`, and `origin`.

## The four rules the converter enforces

1. **A verbatim segment stays verbatim.** The text is read from the pack's own
   evidence file; nothing is rewritten, re-wrapped, or trimmed except the one
   newline the pack adds for diff readability. `sha256` is the file digest, so a
   later check can prove the two agree.
2. **The place survives.** Locators are copied unchanged: page numbers stay page
   numbers, `start_seconds` stays seconds, an image region stays a region. A
   converter that flattens them into a character offset destroys the citation.
3. **Inferred text says so.** `origin` is `inferred` when the transcript came from
   ASR (`metadata.transcript_source == "asr"`) or the visual text came from OCR
   (`metadata.ocr_applied`); otherwise `source`. Original subtitles are `source`.
   The label is derived from the pack's recorded provenance, never guessed from
   the locator type — a video with real captions and a video with ASR have the same
   locator kind.
4. **No distillation, no model call.** `adapter.distilled` is always `false` and
   `adapter.note` says so. Choosing the claims worth keeping is the caller's step
   and the only one that should cost model calls.

## Failure is contained

The converter starts with `evidence_pack.verify_pack()`. A pack whose files no
longer match its manifest is refused (`pack_invalid`) before anything is written,
so a damaged pack cannot be laundered into another tool. Writing is atomic
(temp file + `os.replace`), so a failed conversion leaves no half-written
material. In the other direction, conversion only ever *reads* the pack: a test
asserts every file is byte-identical afterwards. Deleting the converted material
has no effect on the pack, and deleting the pack has no effect on the material a
read returned.

## Interface for other integrations

`cangjie_adapter.py` is one example of a two-function contract any integration can
implement:

- `convert(pack_dir) -> dict` — pure; raises `AdapterError` with a `code`.
- `write(result, out_path) -> Path` — atomic.

An adapter that needs network access or a model is out of scope for this layer:
that belongs to whatever distillation pipeline the user installs, running outside
Envoy's core, with its own credentials. Envoy's job is to make the evidence
portable and to keep the exit honest.

See also: [Evidence Pack v1](evidence-pack-v1.md), [Knowledge Units v1](knowledge-units-v1.md).
