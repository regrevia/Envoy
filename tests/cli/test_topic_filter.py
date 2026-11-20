"""The topic filter decides which favourites a stated goal actually asks for."""

from __future__ import annotations

from envoy_cli.topic_filter import goal_tokens, select_by_topic, topic_score


def _candidates(*titles: str) -> list[dict]:
    return [
        {"candidate_id": f"c{index}", "title": title, "position": index}
        for index, title in enumerate(titles)
    ]


def test_a_stated_topic_keeps_the_matching_posts_only():
    candidates = _candidates(
        "为什么你的 UI 看起来很廉价：8 个间距细节",
        "UI 设计入门：组件库怎么搭",
        "后端开发面试：MySQL 索引原理讲透",
        "《黑神话》通关实况",
    )

    result = select_by_topic(candidates, "我要 UI 相关的那些视频")

    assert result["mode"] == "topic_filtered"
    assert [item["title"] for item in result["kept"]] == [
        "为什么你的 UI 看起来很廉价：8 个间距细节",
        "UI 设计入门：组件库怎么搭",
    ]
    assert len(result["dropped"]) == 2


def test_the_errand_words_never_become_the_topic():
    # "整理一下那些视频" is what the user wants done, not what they want kept.
    tokens = goal_tokens("帮我整理一下收藏的那些视频")

    assert tokens == []


def test_no_goal_keeps_the_whole_collection():
    candidates = _candidates("UI 设计", "后端开发")

    result = select_by_topic(candidates, "")

    assert result["mode"] == "no_topic"
    assert result["kept"] == candidates
    assert result["dropped"] == []


def test_a_chinese_topic_matches_its_own_wording_and_nothing_else():
    candidates = _candidates(
        "后端开发面试：MySQL 索引原理讲透",
        "UI 设计入门",
        "如何三天学会做饭",
    )

    result = select_by_topic(candidates, "把后端开发相关的整理一下")

    assert [item["title"] for item in result["kept"]] == [
        "后端开发面试：MySQL 索引原理讲透"
    ]


def test_kept_items_carry_their_score_and_lead_with_the_best_match():
    candidates = _candidates(
        "写设计稿时的一些杂记",
        "UI 设计系统：从零搭一套组件库",
    )

    result = select_by_topic(candidates, "UI 设计系统")

    # One word of rain does not make it a system: a partial hit stays out.
    assert [item["title"] for item in result["kept"]] == [
        "UI 设计系统：从零搭一套组件库"
    ]
    assert result["kept"][0]["topic_score"] == 1.0
    assert result["dropped"][0]["title"] == "写设计稿时的一些杂记"
    assert result["dropped"][0]["topic_score"] < result["min_score"]


def test_a_topic_the_collection_does_not_cover_keeps_nothing():
    candidates = _candidates("UI 设计入门", "Tailwind 配色")

    result = select_by_topic(candidates, "Kubernetes 运维")

    assert result["kept"] == []
    assert len(result["dropped"]) == 2


def test_scoring_prefers_a_full_token_over_a_partial_one():
    tokens = goal_tokens("界面设计")

    assert topic_score("界面设计系统", tokens) > topic_score("界面杂谈", tokens)
