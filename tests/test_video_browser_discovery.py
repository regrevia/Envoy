import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def test_extract_visible_video_items_filters_navigation_and_deduplicates_links():
    from app.services.browser_discovery_service import extract_visible_video_items

    records = [
        {
            "href": "https://www.douyin.com/video/100",
            "text": "攻略第一集",
            "visible": True,
        },
        {
            "href": "https://www.douyin.com/video/100",
            "text": "攻略第一集重复卡片",
            "visible": True,
        },
        {
            "href": "https://www.douyin.com/user/creator",
            "text": "创作者主页",
            "visible": True,
        },
        {
            "href": "https://www.douyin.com/video/200",
            "text": "攻略第二集",
            "visible": False,
        },
        {
            "href": "https://www.douyin.com/video/300",
            "text": "攻略第三集",
            "visible": True,
        },
    ]

    result = extract_visible_video_items(
        records,
        platform="douyin",
        limit=10,
    )

    assert [item["source_url"] for item in result] == [
        "https://www.douyin.com/video/100",
        "https://www.douyin.com/video/300",
    ]
    assert result[0]["title"] == "攻略第一集"
    assert result[0]["discovery_method"] == "browser_visible"


def test_browser_page_state_requests_user_action_for_challenge_or_login():
    from app.services.browser_discovery_service import classify_browser_page_state

    state = classify_browser_page_state(
        title="抖音",
        body_text="登录后查看更多作品，请完成验证码验证",
        item_count=0,
    )

    assert state["status"] == "needs_user_action"
    assert state["requires_user_action"] is True


def test_browser_page_state_is_ready_when_visible_items_exist():
    from app.services.browser_discovery_service import classify_browser_page_state

    state = classify_browser_page_state(
        title="创作者主页",
        body_text="作品 推荐",
        item_count=10,
    )

    assert state["status"] == "ready"
    assert state["requires_user_action"] is False
