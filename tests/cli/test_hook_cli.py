"""`envoy hook status` must be read-only and honest about what it cannot see."""

import json

from envoy_cli import hooks


def _envoy_entry(hooks_path):
    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    return data["hooks"]["UserPromptSubmit"]


def test_status_reports_missing_codex_config_without_creating_it(tmp_path, monkeypatch, capsys):
    from envoy_cli.cli import main

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    code = main(["hook", "status", "--host", "codex"])

    hooks_path = tmp_path / "hooks.json"
    assert code == 1
    assert not hooks_path.exists()
    printed = capsys.readouterr().out
    assert "host: codex" in printed
    assert "installed: no" in printed
    assert "envoy hook install --host codex" in printed


def test_status_confirms_installed_codex_hook(tmp_path, monkeypatch, capsys):
    from envoy_cli.cli import main

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    installed = hooks.install_codex_hooks(home=tmp_path)
    assert installed["installed"] is True

    code = main(["hook", "status", "--host", "codex", "--json"])

    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["installed"] is True
    assert report["ok"] is True
    assert report["entries"] == 1
    assert report["delivery"] == "user-config"
    assert report["scripts"]["envoy-hook.sh"]["exists"] is True
    assert report["scripts"]["envoy-hook.sh"]["executable"] is True
    # Codex approval is a CLI-side action; the file cannot prove it happened.
    assert report["trusted"] == "unknown"


def test_status_flags_a_missing_wrapper(tmp_path, capsys):
    hooks.install_codex_hooks(home=tmp_path)
    (tmp_path / "envoy" / "hooks" / "envoy-hook.sh").unlink()

    report = hooks.hook_status("codex", home=tmp_path)

    assert report["installed"] is True
    assert report["ok"] is False
    assert "envoy-hook.sh" in report["reason"]
    assert "missing" in report["reason"]


def test_status_reports_invalid_json_and_exits_two(tmp_path, monkeypatch, capsys):
    from envoy_cli.cli import main

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "hooks.json").write_text("{not json", encoding="utf-8")

    code = main(["hook", "status", "--host", "codex", "--json"])

    assert code == 2
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert "invalid JSON" in report["error"]


def test_status_leaves_an_installed_config_untouched(tmp_path):
    hooks.install_codex_hooks(home=tmp_path)
    hooks_path = tmp_path / "hooks.json"
    before = hooks_path.read_bytes()
    stat_before = hooks_path.stat()

    first = hooks.hook_status("codex", home=tmp_path)
    second = hooks.hook_status("codex", home=tmp_path)

    assert first == second
    assert hooks_path.read_bytes() == before
    assert hooks_path.stat().st_mtime_ns == stat_before.st_mtime_ns


def test_status_marks_plugin_hosts_as_host_managed(capsys):
    from envoy_cli.cli import main

    for host, delivery in (
        ("claude-code", "plugin"),
        ("dsh", "plugin-bundle"),
    ):
        code = main(["hook", "status", "--host", host, "--json"])
        assert code == 0
        report = json.loads(capsys.readouterr().out)
        assert report["host"] == host
        assert report["delivery"] == delivery
        assert report["installed"] is None
        assert report["ok"] is None
        assert report["trusted"] == "not-required"
        assert report["verify"]


def test_status_rejects_unknown_host_in_the_library():
    assert hooks.hook_status("vim")["error"] == "unknown_host"


def test_render_hook_status_keeps_one_fact_per_line(tmp_path):
    report = hooks.hook_status("codex", home=tmp_path)
    rendered = hooks.render_hook_status(report)
    assert "host: codex" in rendered
    assert "installed: no" in rendered
    assert "trusted: unknown" in rendered
    assert rendered.count("verify:") == 1


def test_codex_hook_entry_is_the_one_status_reports(tmp_path):
    hooks.install_codex_hooks(home=tmp_path)
    entries = _envoy_entry(tmp_path / "hooks.json")

    assert len(entries) == 1
    report = hooks.hook_status("codex", home=tmp_path)
    assert report["entries"] == len(entries)
