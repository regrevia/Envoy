"""Pack -> Cangjie conversion: verbatim text, original places, honest labels."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from envoy_cli import cangjie_adapter, cli, evidence_pack, extensions, storage

MATERIAL = "mat-" + "2" * 24


@pytest.fixture(autouse=True)
def roots(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))
    monkeypatch.setenv("ENVOY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv(extensions.ENV_FLAG, raising=False)
    storage.reset_root_cache()
    return tmp_path


def _block(block_id: str, text: str, locator: dict, order: int) -> dict:
    return {"id": block_id, "content": {"text": text}, "locator": locator, "order": order}


def _write_material(root, payload: dict) -> None:
    materials = root / "materials"
    materials.mkdir(parents=True, exist_ok=True)
    (materials / f"{MATERIAL}.json").write_text(
        json.dumps({"material_id": MATERIAL, "session_id": "s-1", **payload}), encoding="utf-8"
    )
    (materials / f"{MATERIAL}.expires").write_text("4102444800", encoding="utf-8")


def _pdf_material(root) -> None:
    _write_material(
        root,
        {
            "source_kind": "pdf",
            "source_url": "https://example.com/paper.pdf",
            "source_revision": "rev-7",
            "title_text": "A paper",
            "text": "one\ntwo",
            "page_count": 1,
            "blocks": [
                _block(
                    "blk-pdf-page-0001",
                    "one\ntwo",
                    {"type": "pdf_page", "page_number": 1, "locator_valid": True},
                    1,
                )
            ],
        },
    )


def _asr_video_material(root) -> None:
    _write_material(
        root,
        {
            "source_kind": "video",
            "source_url": "https://example.com/talk",
            "source_revision": "rev-8",
            "title_text": "A talk",
            "text": "hello there",
            "transcript_source": "asr",
            "blocks": [
                _block(
                    "blk-video-asr-0001",
                    "hello there",
                    {
                        "type": "video_time",
                        "source_url": "https://example.com/talk",
                        "start_seconds": 0.0,
                        "end_seconds": 4.0,
                    },
                    1,
                )
            ],
        },
    )


def _export(root, name: str = "pack") -> str:
    result = evidence_pack.write_pack(MATERIAL, root / name)
    return result["pack_dir"]


def test_pdf_segments_keep_their_page_and_count_as_source_text(roots):
    _pdf_material(roots)
    converted = cangjie_adapter.to_cangjie_material(_export(roots))

    assert converted["schema_version"] == cangjie_adapter.SCHEMA
    assert converted["pack_id"]
    assert converted["adapter"]["distilled"] is False
    segment = converted["segments"][0]
    assert segment["origin"] == "source"
    assert segment["text"] == "one\ntwo"
    assert segment["locator"] == {"type": "pdf_page", "page_number": 1, "locator_valid": True}
    assert segment["section_id"]
    assert segment["sha256"]


def test_asr_transcript_is_labelled_inferred_not_source(roots):
    _asr_video_material(roots)
    converted = cangjie_adapter.to_cangjie_material(_export(roots))

    segment = converted["segments"][0]
    assert segment["origin"] == "inferred"
    assert segment["locator_kind"] == "video_time"
    assert segment["locator"]["start_seconds"] == 0.0
    # The words are what was heard, so they are carried; the label says so.
    assert segment["text"] == "hello there"


def test_conversion_records_the_coverage_gap(roots):
    _pdf_material(roots)
    pack_dir = _export(roots)
    coverage_path = f"{pack_dir}/coverage.json"
    coverage = json.loads(open(coverage_path, encoding="utf-8").read())
    coverage["unresolved"] = ["page 2 was not returned"]
    with open(coverage_path, "w", encoding="utf-8") as stream:
        json.dump(coverage, stream)

    # coverage.json is not pinned by the pack id, so editing it is caught only
    # where it matters: the conversion must state the gap, not hide it.
    converted = cangjie_adapter.to_cangjie_material(pack_dir)
    assert converted["coverage"]["unresolved"] == ["page 2 was not returned"]


def test_a_damaged_pack_is_refused_and_nothing_is_written(roots, tmp_path):
    _pdf_material(roots)
    pack_dir = _export(roots)
    target = tmp_path / "out" / "cangjie.json"
    file_path = next((Path(pack_dir) / "evidence").rglob("*.txt"))
    file_path.write_text("tampered", encoding="utf-8")

    with pytest.raises(cangjie_adapter.AdapterError) as caught:
        cangjie_adapter.to_cangjie_material(pack_dir)
    assert caught.value.code == "pack_invalid"
    assert not target.exists()


def test_conversion_leaves_the_pack_byte_identical(roots):
    _pdf_material(roots)
    pack_dir = Path(_export(roots))
    before = {path.relative_to(pack_dir): path.read_bytes() for path in pack_dir.rglob("*") if path.is_file()}

    cangjie_adapter.to_cangjie_material(pack_dir)

    after = {path.relative_to(pack_dir): path.read_bytes() for path in pack_dir.rglob("*") if path.is_file()}
    assert before == after


def test_write_replaces_the_target_atomically(roots, tmp_path):
    _pdf_material(roots)
    converted = cangjie_adapter.to_cangjie_material(_export(roots))
    target = tmp_path / "out" / "cangjie.json"
    target.parent.mkdir()

    written = cangjie_adapter.write(converted, target)
    assert written == target
    assert json.loads(target.read_text(encoding="utf-8"))["schema_version"] == cangjie_adapter.SCHEMA
    leftovers = [path.name for path in target.parent.iterdir() if path.name != target.name]
    assert leftovers == []


def test_cli_conversion_needs_the_capability(roots, capsys, tmp_path):
    _pdf_material(roots)
    pack_dir = _export(roots)
    out = tmp_path / "out" / "cangjie.json"

    code = cli.main(["evidence", "cangjie", "--pack", pack_dir, "--out", str(out)])
    assert code == 1
    assert "extension_disabled" in capsys.readouterr().out
    assert not out.exists()

    extensions.set_enabled(extensions.CANGJIE_ADAPTER, True)
    assert cli.main(["evidence", "cangjie", "--pack", pack_dir, "--out", str(out)]) == 0
    assert out.is_file()
    assert "distilled: no" in capsys.readouterr().out
