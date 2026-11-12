"""The media worker must not inherit the caller's directory as an import root.

A stray ``struct.py`` in the directory Envoy was launched from used to kill the
spawn child during its own bootstrap imports, and the failure surfaced only as
"Media worker exited without a result" with no traceback.  These tests pin the
isolation and the stderr reporting that replaced it.
"""

from __future__ import annotations

import os
import sys

import pytest

from envoy_cli.media import MediaProcessingError, run_bounded_worker


def _poisoned(tmp_path, monkeypatch):
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "struct.py").write_text(
        'raise RuntimeError("stray struct.py shadowed the stdlib")\n', encoding="utf-8"
    )
    monkeypatch.chdir(shadow)
    monkeypatch.setattr(sys, "path", ["", *[p for p in sys.path if p]])
    return shadow


def _pack_with_stdlib_struct():
    import struct

    return struct.pack("<H", 7).hex()


def _current_directory():
    return os.path.realpath(os.getcwd())


def _die_after_complaining():
    sys.stderr.write("envoy worker crash probe: import stage exploded\n")
    sys.stderr.flush()
    os._exit(9)


def test_worker_imports_stdlib_despite_shadowing_caller_directory(tmp_path, monkeypatch):
    _poisoned(tmp_path, monkeypatch)

    assert run_bounded_worker(_pack_with_stdlib_struct, max_work_seconds=60) == "0700"


def test_worker_still_runs_in_the_callers_directory(tmp_path, monkeypatch):
    shadow = _poisoned(tmp_path, monkeypatch)

    assert run_bounded_worker(_current_directory, max_work_seconds=60) == os.path.realpath(
        str(shadow)
    )


def test_a_worker_that_dies_without_a_result_reports_its_stderr(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(MediaProcessingError) as caught:
        run_bounded_worker(_die_after_complaining, max_work_seconds=60)

    message = str(caught.value)
    assert "worker stderr" in message
    assert "envoy worker crash probe" in message
