"""A goal stated by the user decides which discoveries get collected."""

from __future__ import annotations

import json

import pytest

from envoy_cli import collect as collect_module
from envoy_cli import collections as checkpoint

CANDIDATES = [
    {"candidate_id": "c-ui-1", "source_url": "https://www.bilibili.com/video/BV1aa4111111",
     "platform": "bilibili", "item_type": "video", "title": "UI 设计入门：组件库怎么搭", "position": 0},
    {"candidate_id": "c-ui-2", "source_url": "https://www.bilibili.com/video/BV1bb4222222",
     "platform": "bilibili", "item_type": "video", "title": "为什么你的 UI 看起来很廉价", "position": 1},
    {"candidate_id": "c-be-1", "source_url": "https://www.bilibili.com/video/BV1cc4333333",
     "platform": "bilibili", "item_type": "video", "title": "后端开发面试：MySQL 索引原理", "position": 2},
    {"candidate_id": "c-food", "source_url": "https://www.bilibili.com/video/BV1dd4444444",
     "platform": "bilibili", "item_type": "video", "title": "如何三天学会做饭", "position": 3},
]


@pytest.fixture
def collect_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVOY_COLLECTIONS_DIR", str(tmp_path / "collections"))
    monkeypatch.setenv("ENVOY_AUTH_DIR", str(tmp_path / "auth"))
    return tmp_path


def _fake_discovery(profile_url: str, *, limit: int, max_work_seconds: int):
    return {
        "profile_url": profile_url,
        "platform": "bilibili",
        "discovery_revision": "rev-topic-1",
        "cursor_mode": "fresh",
        "ordering": "source",
        "candidates": [dict(item) for item in CANDIDATES],
        "has_more": False,
        "stop_reason": "complete",
    }


def _manifest_after_select(monkeypatch, goal: str):
    monkeypatch.setattr(
        collect_module.checkpoint, "discover_profile", _fake_discovery
    )
    manifest, _ = collect_module._open_collection(
        "collect-topic", "run-1", "https://space.bilibili.com/1"
    )
    return collect_module._select_candidates(
        manifest,
        "https://space.bilibili.com/1",
        limit=10,
        goal=goal,
        max_work_seconds=30,
    )


def test_the_goal_selects_only_the_matching_candidates(collect_home, monkeypatch):
    manifest = _manifest_after_select(monkeypatch, "我要 UI 相关的")

    assert [item for item in manifest["selected_candidate_ids"]] == ["c-ui-1", "c-ui-2"]
    assert manifest["topic"]["goal"] == "我要 UI 相关的"
    assert manifest["topic"]["kept"] == 2
    assert manifest["topic"]["dropped"] == 2


def test_without_a_goal_every_discovered_post_is_selected(collect_home, monkeypatch):
    manifest = _manifest_after_select(monkeypatch, "")

    assert len(manifest["selected_candidate_ids"]) == 4
    assert "topic" not in manifest or not manifest["topic"]


def test_a_goal_matching_nothing_says_so_instead_of_collecting_everything(
    collect_home, monkeypatch
):
    monkeypatch.setattr(
        collect_module.checkpoint, "discover_profile", _fake_discovery
    )
    manifest, _ = collect_module._open_collection(
        "collect-topic", "run-1", "https://space.bilibili.com/1"
    )

    with pytest.raises(checkpoint.CollectionError) as error:
        collect_module._select_candidates(
            manifest,
            "https://space.bilibili.com/1",
            limit=10,
            goal="Kubernetes 运维",
            max_work_seconds=30,
        )

    assert "No post in this profile matched the goal" in str(error.value)
    assert "4 were checked" in str(error.value)


def test_the_report_states_which_goal_filtered_it(collect_home, monkeypatch):
    manifest = _manifest_after_select(monkeypatch, "我要 UI 相关的")

    text = collect_module._render(
        manifest,
        {},
        platform="bilibili",
        profile_url=manifest["profile_url"],
    )

    assert "已按目标筛选" in text
    assert "我要 UI 相关的" in text
    assert "保留 2 条" in text
    assert "跳过 2 条" in text


def test_the_observation_is_readable_back_from_disk(collect_home, monkeypatch):
    manifest = _manifest_after_select(monkeypatch, "UI")

    stored = checkpoint.get_collection(manifest["collection_id"], manifest["run_id"])

    assert stored["selected_candidate_ids"] == ["c-ui-1", "c-ui-2"]
    assert json.dumps(stored["topic"], ensure_ascii=False)
