"""Start-up and disk-footprint behaviour added for the lightweighting work.

The Windows measurement put a full CLI start at ~650-720 ms, mostly imports. These
tests pin the two cheap wins so a later edit cannot quietly restore the cost:
`envoy --version` must not load the read stack, and `reader` must not import
httpx/pypdf until something actually uses them.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from envoy_cli import _lazy_module, browser


REPO_SRC = [
    str(Path(__file__).resolve().parents[2] / "packages" / "envoy-core" / "src"),
    str(Path(__file__).resolve().parents[2] / "packages" / "envoy-cli" / "src"),
]


def _run_probe(code: str) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(REPO_SRC + [env.get("PYTHONPATH", "")]).strip(
        os.pathsep
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_version_fast_path_does_not_load_the_read_stack():
    payload = _run_probe(
        "import json, sys\n"
        "from envoy_cli.entry import main\n"
        "code = main(['--version'])\n"
        "print(json.dumps({'code': code,"
        " 'reader': 'envoy_cli.reader' in sys.modules,"
        " 'mcp': 'envoy_cli.mcp' in sys.modules,"
        " 'httpx': 'httpx' in sys.modules,"
        " 'pypdf': 'pypdf' in sys.modules}))\n"
    )
    assert payload == {
        "code": 0,
        "reader": False,
        "mcp": False,
        "httpx": False,
        "pypdf": False,
    }


def test_fast_path_prints_exactly_what_the_parser_prints(monkeypatch, capsys):
    from envoy_cli import cli, entry

    assert entry.main(["--version"]) == 0
    fast = capsys.readouterr().out.strip()

    monkeypatch.setattr(sys, "argv", ["envoy", "--version"])
    try:
        cli.main(["--version"])
    except SystemExit as exc:  # argparse --version exits 0
        assert exc.code == 0
    parser = capsys.readouterr().out.strip()

    assert fast == parser
    assert fast.startswith("envoy-cli ")


def test_other_commands_still_go_through_the_parser(monkeypatch):
    from envoy_cli import cli, entry

    seen: list[list[str]] = []

    def fake_main(argv=None):
        seen.append(list(argv or []))
        return 7

    monkeypatch.setattr(cli, "main", fake_main)

    assert entry.main(["doctor", "--json"]) == 7
    assert seen == [["doctor", "--json"]]


def test_reader_does_not_import_network_and_pdf_libraries_at_import():
    payload = _run_probe(
        "import json, sys\n"
        "import envoy_cli.reader as reader\n"
        "print(json.dumps({'httpx': 'httpx' in sys.modules,"
        " 'pypdf': 'pypdf' in sys.modules,"
        " 'module': type(reader.httpx).__name__}))\n"
    )
    assert payload == {"httpx": False, "pypdf": False, "module": "_LazyModule"}


def test_lazy_module_resolves_on_first_attribute_read():
    imported: list[str] = []
    real_import = _lazy_module.importlib.import_module

    def tracking_import(name):
        imported.append(name)
        return real_import(name)

    proxy = _lazy_module.lazy_module("json")
    assert imported == []
    assert proxy.dumps({"a": 1}) == '{"a": 1}'
    assert proxy.loads('{"a": 1}') == {"a": 1}
    assert repr(proxy) == "<lazy module 'json'>"

    attr = _lazy_module.lazy_attr("json", "dumps")
    assert attr({"a": 1}) == '{"a": 1}'
    assert repr(attr) == "<lazy json.dumps>"


def test_browser_disk_usage_sums_the_playwright_cache(monkeypatch, tmp_path):
    normal = tmp_path / "chromium-1223"
    normal.mkdir()
    (normal / "chrome").write_bytes(b"x" * 2048)
    headless = tmp_path / "chromium_headless_shell-1223"
    headless.mkdir()
    (headless / "shell").write_bytes(b"y" * 1024)
    (tmp_path / "not-a-browser.txt").write_text("ignored")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))

    report = browser.browser_disk_usage()

    assert report["path"] == str(tmp_path)
    assert report["components"] == {
        "chromium-1223": 0.0,
        "chromium_headless_shell-1223": 0.0,
    }
    # The MB figure is rounded for display, so the exact sum is reported too.
    assert report["total_bytes"] == 3072
    assert report["total_mb"] == 0.0


def _browser_cache(tmp_path, *names):
    for name in names:
        (tmp_path / name).mkdir()
        (tmp_path / name / "asset").write_bytes(b"x" * 1024)
    return tmp_path


def test_prune_browser_versions_keeps_only_the_newest_revision(monkeypatch, tmp_path):
    _browser_cache(
        tmp_path,
        "chromium-1208",
        "chromium-1248",
        "chromium_headless_shell-1208",
        "chromium_headless_shell-1248",
        "ffmpeg-1013",
    )
    (tmp_path / "not-a-browser.txt").write_text("ignored")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))

    report = browser.prune_browser_versions()

    assert sorted(item["name"] for item in report["removed"]) == [
        "chromium-1208",
        "chromium_headless_shell-1208",
    ]
    assert {item["name"] for item in report["kept"]} == {
        "chromium-1248",
        "chromium_headless_shell-1248",
        "ffmpeg-1013",
    }
    assert report["freed_bytes"] == 2048
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "chromium-1248",
        "chromium_headless_shell-1248",
        "ffmpeg-1013",
        "not-a-browser.txt",
    ]


def test_prune_browser_versions_dry_run_reports_without_deleting(monkeypatch, tmp_path):
    _browser_cache(tmp_path, "chromium-1208", "chromium-1248")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))

    report = browser.prune_browser_versions(dry_run=True)

    assert report["dry_run"] is True
    assert [item["name"] for item in report["removed"]] == ["chromium-1208"]
    assert (tmp_path / "chromium-1208").is_dir()


def test_prune_browser_versions_ignores_a_missing_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "absent"))

    report = browser.prune_browser_versions()

    assert report["removed"] == [] and report["kept"] == []


def test_prune_browser_versions_leaves_unversioned_names_alone(monkeypatch, tmp_path):
    _browser_cache(tmp_path, "chromium", "chromium-latest", "chromium-1248")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))

    report = browser.prune_browser_versions()

    assert report["removed"] == []
    assert (tmp_path / "chromium").is_dir()
    assert (tmp_path / "chromium-latest").is_dir()


def test_browser_disk_usage_ignores_a_missing_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "absent"))

    report = browser.browser_disk_usage()

    assert report["total_mb"] == 0
    assert report["components"] == {}


def test_doctor_text_reports_browser_footprint(monkeypatch, capsys):
    from envoy_cli import cli

    monkeypatch.setattr(
        cli,
        "media_runtime_status",
        lambda verify_asr=False: {
            "asr_operational": "unknown",
            "install": {},
            "ffmpeg": True,
            "ffprobe": True,
        },
    )
    monkeypatch.setattr(
        cli.browser_runtime,
        "browser_runtime_status",
        lambda: {
            "installed": True,
            "browser": "chromium",
            "ready": True,
            "disk": {"total_mb": 683.0, "components": {}},
        },
    )
    monkeypatch.setattr(cli, "available_ocr_backends", lambda: {})
    monkeypatch.setattr(cli.install_state, "install_report", lambda: {})

    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "browser    ready (683 MB on disk)" in out


def test_default_install_never_pulls_the_media_stack():
    """L6: torch/whisper stay behind the ``[media]`` extra.

    The Windows report measured 447 MB for torch. A stray top-level import (or a
    dependency moved back into ``dependencies``) would put that on every install,
    so both the metadata and the import chain are pinned here.
    """
    import tomllib

    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[2] / "packages/envoy-cli/pyproject.toml").read_text()
    )
    dependencies = " ".join(pyproject["project"]["dependencies"])
    assert "whisper" not in dependencies
    assert "torch" not in dependencies
    assert "openai-whisper" in " ".join(pyproject["project"]["optional-dependencies"]["media"])

    payload = _run_probe(
        "import json, sys\n"
        "import envoy_cli.cli, envoy_cli.reader, envoy_cli.mcp\n"
        "print(json.dumps({name: name in sys.modules"
        " for name in ('torch', 'whisper', 'openai')}))\n"
    )
    assert payload == {"torch": False, "whisper": False, "openai": False}
