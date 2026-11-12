"""Evidence Pack export: deterministic, self-verifying, and read-only on materials."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

from envoy_cli import cli, evidence_pack, storage

LIVE = "mat-" + "b" * 24


def _public_network(monkeypatch, tmp_path: Path) -> Path:
    root = tmp_path / "materials"
    root.mkdir()
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(root))
    storage.reset_root_cache()
    return root


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    return _public_network(monkeypatch, tmp_path)


def _write_material(root: Path, material_id: str, payload: dict, *, expires_in: float = 600.0) -> Path:
    record = {
        "source_url": "https://example.com/a",
        "source_revision": "rev-1",
        "session_id": "session-a",
        "text": "",
        **payload,
    }
    path = root / f"{material_id}.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    (root / f"{material_id}.expires").write_text(str(time.time() + expires_in), encoding="utf-8")
    return path


def _web_material(root: Path, material_id: str = LIVE) -> Path:
    return _write_material(
        root,
        material_id,
        {
            "source_kind": "web",
            "title_text": "A page worth citing",
            "text": "First paragraph. Second paragraph.",
            "blocks": [
                {
                    "id": "blk-0001",
                    "order": 1,
                    "content": {"text": "First paragraph."},
                    "locator": {"type": "web", "source_url": "https://example.com/a", "block_id": "blk-0001"},
                },
                {
                    "id": "blk-0002",
                    "order": 2,
                    "content": {"text": "Second paragraph."},
                    "locator": {"type": "web", "source_url": "https://example.com/a", "block_id": "blk-0002"},
                },
            ],
        },
    )


def _pdf_material(root: Path, material_id: str = "mat-" + "c" * 24) -> Path:
    pages = [("Page one text.", 1), ("Page two text.", 2)]
    text = "\n".join(page for page, _ in pages)
    spans = []
    offset = 0
    for page, number in pages:
        spans.append({"page_number": number, "start": offset, "end": offset + len(page)})
        offset += len(page) + 1
    return _write_material(
        root,
        material_id,
        {
            "source_kind": "pdf",
            "source_url": "https://example.com/paper.pdf",
            "text": text,
            "page_spans": spans,
            "page_count": 2,
            "page_coverage": {"page_count": 2, "indexed_page_count": 2},
        },
    )


def _video_material(root: Path, material_id: str = "mat-" + "d" * 24) -> Path:
    cues = [
        (0.0, 30.0, "Opening words."),
        (30.0, 65.0, "Middle words."),
        (65.0, 200.0, "Closing words."),
    ]
    return _write_material(
        root,
        material_id,
        {
            "source_kind": "video",
            "source_url": "https://example.com/talk",
            "text": " ".join(cue for _, _, cue in cues),
            "transcript_source": "asr",
            "blocks": [
                {
                    "id": f"blk-cue-{index:04d}",
                    "order": index,
                    "content": {"text": cue},
                    "locator": {
                        "type": "video_time",
                        "source_url": "https://example.com/talk",
                        "start_seconds": start,
                        "end_seconds": end,
                    },
                }
                for index, (start, end, cue) in enumerate(cues)
            ],
        },
    )


def _tree(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_export_is_deterministic_and_verifies(tmp_path):
    root = storage.resolve_material_root()
    _web_material(root)
    first = evidence_pack.write_pack(LIVE, tmp_path / "one")
    second = evidence_pack.write_pack(LIVE, tmp_path / "two")

    assert first["pack_id"] == second["pack_id"]
    assert _tree(tmp_path / "one") == _tree(tmp_path / "two")
    assert evidence_pack.verify_pack(tmp_path / "one") == {
        "pack_dir": str(tmp_path / "one"),
        "pack_id": first["pack_id"],
        "valid": True,
        "problems": [],
    }


def test_export_pins_every_citation_to_a_file_and_locator(tmp_path):
    root = storage.resolve_material_root()
    _web_material(root)
    evidence_pack.write_pack(LIVE, tmp_path / "pack")

    index = json.loads((tmp_path / "pack" / "references" / "evidence-index.json").read_text(encoding="utf-8"))
    assert [entry["block_id"] for entry in index] == ["blk-0001", "blk-0002"]
    assert [entry["file"].split("/")[1] for entry in index] == ["text", "text"]
    for entry in index:
        body = (tmp_path / "pack" / entry["file"]).read_text(encoding="utf-8")
        assert hashlib.sha256(body.encode("utf-8")).hexdigest() == entry["sha256"]
        assert entry["locator"]["source_url"] == "https://example.com/a"

    document = json.loads((tmp_path / "pack" / "source" / "document.json").read_text(encoding="utf-8"))
    assert document["schema_version"] == "envoy.source-document/1"
    assert document["source"]["material_id"] == LIVE
    assert document["coverage"]["text"] is True
    assert document["coverage"]["transcript"] is False


def test_pdf_export_keeps_page_numbers(tmp_path):
    root = storage.resolve_material_root()
    _pdf_material(root)
    evidence_pack.write_pack("mat-" + "c" * 24, tmp_path / "pack")

    document = json.loads((tmp_path / "pack" / "source" / "document.json").read_text(encoding="utf-8"))
    assert [section["locator"]["page_number"] for section in document["sections"]] == [1, 2]
    index = json.loads((tmp_path / "pack" / "references" / "evidence-index.json").read_text(encoding="utf-8"))
    assert [entry["locator"]["page_number"] for entry in index] == [1, 2]
    assert document["coverage"]["page_count"] == 2


def test_video_export_labels_transcript_and_timestamps(tmp_path):
    root = storage.resolve_material_root()
    _video_material(root)
    evidence_pack.write_pack("mat-" + "d" * 24, tmp_path / "pack")

    index = json.loads((tmp_path / "pack" / "references" / "evidence-index.json").read_text(encoding="utf-8"))
    assert {entry["kind"] for entry in index} == {"transcript"}
    assert all(entry["file"].startswith("evidence/transcript/") for entry in index)
    assert [entry["locator"]["start_seconds"] for entry in index] == [0.0, 30.0, 65.0]
    document = json.loads((tmp_path / "pack" / "source" / "document.json").read_text(encoding="utf-8"))
    assert document["coverage"]["transcript"] is True


def test_export_never_copies_raw_media_or_reaches_the_network(tmp_path):
    root = storage.resolve_material_root()
    _video_material(root)
    evidence_pack.write_pack("mat-" + "d" * 24, tmp_path / "pack")

    suffixes = {path.suffix for path in (tmp_path / "pack").rglob("*") if path.is_file()}
    assert suffixes <= {".json", ".md", ".txt"}


def test_export_leaves_the_material_byte_identical(tmp_path):
    root = storage.resolve_material_root()
    path = _web_material(root)
    before = path.read_bytes()
    evidence_pack.write_pack(LIVE, tmp_path / "pack")
    assert path.read_bytes() == before


def test_export_refuses_to_clobber_or_replace_on_request(tmp_path):
    root = storage.resolve_material_root()
    _web_material(root)
    target = tmp_path / "pack"
    target.mkdir()
    (target / "keep.txt").write_text("mine", encoding="utf-8")

    with pytest.raises(evidence_pack.PackError) as refusal:
        evidence_pack.write_pack(LIVE, target)
    assert refusal.value.code == "pack_exists"
    assert (target / "keep.txt").read_text(encoding="utf-8") == "mine"

    evidence_pack.write_pack(LIVE, target, overwrite=True)
    assert (target / "manifest.json").is_file()
    assert evidence_pack.verify_pack(target)["valid"] is True


def test_a_damaged_pack_is_reported_not_trusted(tmp_path):
    root = storage.resolve_material_root()
    _web_material(root)
    evidence_pack.write_pack(LIVE, tmp_path / "pack")
    index = json.loads((tmp_path / "pack" / "references" / "evidence-index.json").read_text(encoding="utf-8"))
    (tmp_path / "pack" / index[0]["file"]).write_text("rewritten\n", encoding="utf-8")

    report = evidence_pack.verify_pack(tmp_path / "pack")
    assert report["valid"] is False
    assert report["problems"] == [f"evidence text changed: {index[0]['file']}"]


def test_missing_expired_and_malformed_materials_are_named(tmp_path):
    root = storage.resolve_material_root()
    _write_material(root, "mat-" + "e" * 24, {"text": "gone"}, expires_in=-5)

    with pytest.raises(evidence_pack.PackError) as missing:
        evidence_pack.write_pack("mat-" + "f" * 24, tmp_path / "pack")
    assert missing.value.code == "material_missing"

    with pytest.raises(evidence_pack.PackError) as expired:
        evidence_pack.write_pack("mat-" + "e" * 24, tmp_path / "pack")
    assert expired.value.code == "material_expired"

    with pytest.raises(evidence_pack.PackError) as invalid:
        evidence_pack.write_pack("../../etc/passwd", tmp_path / "pack")
    assert invalid.value.code == "invalid_material_id"
    assert not (tmp_path / "pack").exists()


def test_cli_export_is_off_until_switched_on(tmp_path, capsys, monkeypatch):
    root = storage.resolve_material_root()
    _web_material(root)
    monkeypatch.setenv("ENVOY_CONFIG_DIR", str(tmp_path / "config"))

    assert cli.main(["evidence", "export", "--material", LIVE, "--out", str(tmp_path / "pack")]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "extension_disabled"
    assert not (tmp_path / "pack").exists()

    monkeypatch.setenv("ENVOY_EXTENSIONS", "evidence_pack_export")
    assert cli.main(["evidence", "export", "--material", LIVE, "--out", str(tmp_path / "pack")]) == 0
    assert "pack" in capsys.readouterr().out
    assert cli.main(["evidence", "verify", "--pack", str(tmp_path / "pack"), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
