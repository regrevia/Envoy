"""Anything that is hashed later must be written byte for byte.

The evidence pack stores the sha256 of the text as it existed in memory, and it
verifies that hash by reading the bytes back off disk.  On a platform whose text
mode rewrites ``\\n`` to ``\\r\\n`` those two disagree, so a fresh export fails its
own manifest.  The same rule covers the worker stderr capture, whose descriptor
is not inherited on Windows.
"""

from __future__ import annotations

import hashlib
import io
import json
import pathlib
import sys
import time

import pytest

from envoy_cli import evidence_pack, media, storage

LIVE = "mat-" + "c" * 24


def _translation_prone_write_text(monkeypatch):
    """Make ``Path.write_text`` add ``\\r\\n`` unless the caller pinned ``newline``."""

    original = pathlib.Path.write_text

    def wrapper(self, data, encoding=None, errors=None, newline=None):
        if newline == "":
            return original(self, data, encoding=encoding, errors=errors, newline="")
        text = data if isinstance(data, str) else str(data)
        return original(
            self, text.replace("\n", "\r\n"), encoding=encoding, errors=errors, newline=""
        )

    monkeypatch.setattr(pathlib.Path, "write_text", wrapper)


@pytest.fixture()
def _material_root(monkeypatch, tmp_path):
    root = tmp_path / "materials"
    root.mkdir()
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(root))
    storage.reset_root_cache()
    record = {
        "source_url": "https://example.com/a",
        "source_revision": "rev-1",
        "session_id": "session-a",
        "source_kind": "web",
        "title_text": "A page worth citing",
        "text": "First paragraph. Second paragraph.",
        "blocks": [
            {
                "id": "blk-0001",
                "order": 1,
                "content": {"text": "First paragraph."},
                "locator": {
                    "type": "web",
                    "source_url": "https://example.com/a",
                    "block_id": "blk-0001",
                },
            },
            {
                "id": "blk-0002",
                "order": 2,
                "content": {"text": "Second paragraph."},
                "locator": {
                    "type": "web",
                    "source_url": "https://example.com/a",
                    "block_id": "blk-0002",
                },
            },
        ],
    }
    (root / f"{LIVE}.json").write_text(json.dumps(record), encoding="utf-8")
    (root / f"{LIVE}.expires").write_text(str(time.time() + 600), encoding="utf-8")
    return root


def test_a_pack_verifies_where_text_mode_would_rewrite_newlines(
    _material_root, tmp_path, monkeypatch
):
    _translation_prone_write_text(monkeypatch)

    written = evidence_pack.write_pack(LIVE, tmp_path / "pack")

    assert evidence_pack.verify_pack(tmp_path / "pack") == {
        "pack_dir": str(tmp_path / "pack"),
        "pack_id": written["pack_id"],
        "valid": True,
        "problems": [],
    }
    for path in (tmp_path / "pack").rglob("*"):
        if path.is_file():
            assert b"\r\n" not in path.read_bytes(), path


def test_the_exported_evidence_text_is_the_text_the_index_hashes(
    _material_root, tmp_path, monkeypatch
):
    _translation_prone_write_text(monkeypatch)

    evidence_pack.write_pack(LIVE, tmp_path / "pack")

    entries = json.loads(
        (tmp_path / "pack" / "references" / "evidence-index.json").read_text(encoding="utf-8")
    )
    assert entries
    for entry in entries:
        on_disk = (tmp_path / "pack" / entry["file"]).read_bytes()
        assert hashlib.sha256(on_disk).hexdigest() == entry["sha256"], entry["file"]


def test_the_worker_stderr_capture_exists_even_where_fd2_is_not_inherited():
    handle = media._open_worker_stderr()

    try:
        assert handle is not None
        assert (handle.name or "").strip()
        assert pathlib.Path(handle.name).exists()
    finally:
        name = handle.name
        media._discard_worker_stderr(handle)
    assert not pathlib.Path(name).exists()


def test_the_worker_can_be_told_where_to_write_its_own_stderr(tmp_path, monkeypatch):
    target = tmp_path / "worker.log"
    original = sys.stderr
    try:
        media._redirect_worker_stderr(str(target))
        sys.stderr.write("envoy worker crash probe: import stage exploded\n")
        sys.stderr.flush()
    finally:
        stream, sys.stderr = sys.stderr, original
        stream.close()

    assert "envoy worker crash probe" in target.read_text(encoding="utf-8")
    assert sys.stderr is original


def test_the_redirect_ignores_a_blank_path(monkeypatch):
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    media._redirect_worker_stderr("")

    assert isinstance(sys.stderr, io.StringIO)
