"""Extensions are off until someone switches them on, and the core never needs them."""

from __future__ import annotations

import json

import pytest

from envoy_cli import cli, extensions, features, storage


@pytest.fixture(autouse=True)
def config_root(tmp_path, monkeypatch):
    root = tmp_path / "config"
    monkeypatch.setenv("ENVOY_CONFIG_DIR", str(root))
    monkeypatch.delenv(extensions.ENV_FLAG, raising=False)
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))
    storage.reset_root_cache()
    return root


def test_every_extension_starts_off():
    report = extensions.status()

    assert sorted(row["name"] for row in report["extensions"]) == sorted(extensions.FLAGS)
    assert all(row["enabled"] is False for row in report["extensions"])
    assert report["env_flag"] == extensions.ENV_FLAG
    assert report["config_key"] == extensions.KEY


def test_the_table_is_grouped_by_tier_before_name():
    report = extensions.status()
    order = [extensions.TIERS.index(row["tier"]) for row in report["extensions"]]

    assert order == sorted(order)


def test_enable_and_disable_round_trip_and_keep_other_settings(config_root):
    features.write_config({"credential_handoff": True})

    extensions.set_enabled(extensions.EVIDENCE_PACK_EXPORT, True)
    assert extensions.enabled(extensions.EVIDENCE_PACK_EXPORT) is True
    assert extensions.enabled(extensions.KNOWLEDGE_UNITS) is False
    assert features.read_config()["credential_handoff"] is True

    extensions.set_enabled(extensions.EVIDENCE_PACK_EXPORT, False)
    assert extensions.enabled(extensions.EVIDENCE_PACK_EXPORT) is False


def test_the_environment_decides_for_the_names_it_lists(monkeypatch):
    extensions.set_enabled(extensions.KNOWLEDGE_UNITS, True)
    monkeypatch.setenv(
        extensions.ENV_FLAG, f"{extensions.EVIDENCE_PACK_EXPORT},{extensions.KNOWLEDGE_UNITS}"
    )

    assert extensions.enabled(extensions.EVIDENCE_PACK_EXPORT) is True
    assert extensions.enabled(extensions.KNOWLEDGE_UNITS) is True

    monkeypatch.setenv(extensions.ENV_FLAG, extensions.EVIDENCE_PACK_EXPORT)
    assert extensions.enabled(extensions.EVIDENCE_PACK_EXPORT) is True
    # A name the environment does not list keeps whatever the config says.
    assert extensions.enabled(extensions.KNOWLEDGE_UNITS) is True


def test_an_unknown_name_in_the_environment_is_ignored(monkeypatch):
    monkeypatch.setenv(extensions.ENV_FLAG, "no_such_extension, evidence_pack_export")

    assert extensions.enabled(extensions.EVIDENCE_PACK_EXPORT) is True
    assert extensions.enabled(extensions.KNOWLEDGE_UNITS) is False


def test_an_unknown_name_in_code_is_an_error():
    with pytest.raises(extensions.UnknownExtension):
        extensions.enabled("no_such_extension")
    with pytest.raises(extensions.UnknownExtension):
        extensions.set_enabled("no_such_extension", True)


def test_a_broken_config_counts_as_off(config_root):
    config_root.mkdir(parents=True, exist_ok=True)
    (config_root / features.CONFIG_NAME).write_text("{not json", encoding="utf-8")

    assert extensions.enabled(extensions.EVIDENCE_PACK_EXPORT) is False
    assert all(row["enabled"] is False for row in extensions.status()["extensions"])


def test_require_names_the_flag_and_the_command_that_turns_it_on():
    with pytest.raises(extensions.ExtensionDisabled) as refusal:
        extensions.require(extensions.SKILL_DISTILLATION)
    assert "skill_distillation" in str(refusal.value)
    assert "envoy extensions enable skill_distillation" in str(refusal.value)

    extensions.set_enabled(extensions.SKILL_DISTILLATION, True)
    extensions.require(extensions.SKILL_DISTILLATION)


def test_the_rendered_table_marks_every_flag_off():
    rendered = extensions.render_status(extensions.status())

    for name in extensions.FLAGS:
        assert name in rendered
    assert rendered.count("[off]") == len(extensions.FLAGS)
    assert "envoy extensions enable" in rendered


def test_cli_status_and_toggles_work_without_any_extension_on(capsys):
    assert cli.main(["extensions", "status", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert all(row["enabled"] is False for row in report["extensions"])

    assert cli.main(["extensions", "enable", extensions.KNOWLEDGE_UNITS]) == 0
    assert "knowledge_units is on" in capsys.readouterr().out

    assert cli.main(["extensions", "status", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    shown = {row["name"]: row["enabled"] for row in report["extensions"]}
    assert shown[extensions.KNOWLEDGE_UNITS] is True
    assert shown[extensions.EVIDENCE_PACK_EXPORT] is False

    assert cli.main(["extensions", "disable", extensions.KNOWLEDGE_UNITS]) == 0
    assert "knowledge_units is off" in capsys.readouterr().out
    assert cli.main(["extensions", "status"]) == 0
    assert "[off] knowledge_units" in capsys.readouterr().out


def test_cli_says_so_when_the_environment_owns_the_switch(capsys, monkeypatch):
    assert cli.main(["extensions", "enable", extensions.KNOWLEDGE_UNITS]) == 0
    capsys.readouterr()

    monkeypatch.setenv(extensions.ENV_FLAG, extensions.KNOWLEDGE_UNITS)
    assert cli.main(["extensions", "disable", extensions.KNOWLEDGE_UNITS]) == 1
    out = capsys.readouterr().out
    assert "knowledge_units is on" in out
    assert extensions.ENV_FLAG in out
