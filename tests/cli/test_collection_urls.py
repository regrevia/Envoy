"""Canonical candidate URLs keep what identifies one episode."""

import pytest

from envoy_cli import collections


def test_a_multi_part_video_keeps_its_part():
    canonical = collections.canonical_candidate_url(
        "https://www.bilibili.com/video/BV1xx411c7mD/?p=3&spm_id_from=333.999"
    )

    assert canonical == "https://www.bilibili.com/video/BV1xx411c7mD?p=3"


def test_part_one_and_no_part_share_the_same_video():
    plain = collections.canonical_candidate_url(
        "https://www.bilibili.com/video/BV1xx411c7mD"
    )
    first = collections.canonical_candidate_url(
        "https://m.bilibili.com/video/BV1xx411c7mD?p=1"
    )

    assert plain == first == "https://www.bilibili.com/video/BV1xx411c7mD"


def test_an_invalid_part_is_ignored_instead_of_failing():
    canonical = collections.canonical_candidate_url(
        "https://www.bilibili.com/video/BV1xx411c7mD?p=abc"
    )

    assert canonical == "https://www.bilibili.com/video/BV1xx411c7mD"


def test_two_parts_of_one_video_are_two_candidates():
    first = collections.candidate_id("https://www.bilibili.com/video/BV1xx411c7mD?p=1")
    third = collections.candidate_id("https://www.bilibili.com/video/BV1xx411c7mD?p=3")

    assert first != third


def test_share_parameters_do_not_change_the_candidate_id():
    bare = collections.candidate_id("https://www.bilibili.com/video/BV1xx411c7mD")
    shared = collections.candidate_id(
        "https://m.bilibili.com/video/BV1xx411c7mD?share_source=copy_web&spm_id_from=333.1"
    )

    assert shared == bare


def test_the_same_part_on_the_mobile_host_matches():
    www_part = collections.candidate_id(
        "https://www.bilibili.com/video/BV1xx411c7mD?p=2"
    )
    mobile_part = collections.candidate_id(
        "https://m.bilibili.com/video/BV1xx411c7mD?p=2&share_source=copy_web"
    )

    assert mobile_part == www_part


def test_a_non_video_path_is_still_refused():
    with pytest.raises(collections.CollectionError):
        collections.canonical_candidate_url("https://www.bilibili.com/read/cv123456")


def test_a_bilibili_collection_link_keeps_its_sid():
    canonical, platform = collections.canonical_profile_url(
        "https://space.bilibili.com/2142762/channel/collectiondetail?sid=57445&ctype=0"
    )

    assert platform == "bilibili"
    assert canonical == "https://space.bilibili.com/2142762/channel/collectiondetail?sid=57445"


def test_a_bilibili_series_link_keeps_its_sid():
    canonical, platform = collections.canonical_profile_url(
        "https://space.bilibili.com/1958703906/channel/seriesdetail?sid=547718&ctype=0"
    )

    assert platform == "bilibili"
    assert canonical == "https://space.bilibili.com/1958703906/channel/seriesdetail?sid=547718"


def test_a_collection_link_without_an_sid_says_what_is_missing():
    with pytest.raises(collections.CollectionError) as excinfo:
        collections.canonical_profile_url(
            "https://space.bilibili.com/2142762/channel/collectiondetail"
        )

    assert "sid" in str(excinfo.value)


def test_a_plain_space_link_still_canonicalizes_to_its_video_tab():
    canonical, platform = collections.canonical_profile_url(
        "https://space.bilibili.com/2142762/"
    )

    assert platform == "bilibili"
    assert canonical == "https://space.bilibili.com/2142762/video"


def test_a_favourites_tab_survives_canonicalization():
    from envoy_cli.collections import canonical_profile_url

    canonical, platform = canonical_profile_url(
        "https://www.douyin.com/user/self?showTab=favorite_collection"
    )
    assert canonical == "https://www.douyin.com/user/self?showTab=favorite_collection"
    assert platform == "douyin"


def test_a_profile_without_a_tab_stays_the_works_list():
    from envoy_cli.collections import canonical_profile_url

    canonical, _platform = canonical_profile_url("https://www.douyin.com/user/abc")
    assert canonical == "https://www.douyin.com/user/abc"


def test_a_xiaohongshu_profile_is_a_collection():
    from envoy_cli.collections import canonical_profile_url

    canonical, platform = canonical_profile_url(
        "https://www.xiaohongshu.com/user/profile/5f1a2b3c4d5e6f7a8b9c0d1e"
    )
    assert canonical == (
        "https://www.xiaohongshu.com/user/profile/5f1a2b3c4d5e6f7a8b9c0d1e"
    )
    assert platform == "xiaohongshu"


def test_a_xiaohongshu_note_keeps_only_its_id():
    from envoy_cli.collections import canonical_candidate_url

    assert canonical_candidate_url(
        "https://www.xiaohongshu.com/explore/65f1a2b3c4d5e6f7a8b9c001?xsec_token=abc"
    ) == "https://www.xiaohongshu.com/explore/65f1a2b3c4d5e6f7a8b9c001"


def test_a_xiaohongshu_note_and_a_douyin_post_never_share_a_candidate_id():
    from envoy_cli.collections import candidate_id

    assert candidate_id(
        "https://www.xiaohongshu.com/explore/65f1a2b3c4d5e6f7a8b9c001"
    ).startswith("xhs-")


def test_the_favourites_link_is_offered_as_a_collection():
    from envoy_cli.platforms import is_collection_url

    assert is_collection_url("https://www.douyin.com/user/self?showTab=favorite_collection")
    assert not is_collection_url("https://www.xiaohongshu.com/user/profile/5f1a2b3c4d5e6f7a8b9c0d1e")
    assert not is_collection_url("https://www.douyin.com/video/7300000000000000000")


def test_a_xianyu_item_keeps_only_its_id():
    """`spm`/`ut_sk` track the share, not the listing."""

    from envoy_cli.collections import canonical_candidate_url

    assert canonical_candidate_url(
        "https://www.goofish.com/item?id=800000000000&spm=a21ybx.home.feeds.1&ut_sk=1.abc"
    ) == "https://www.goofish.com/item?id=800000000000"


def test_a_xianyu_user_page_is_a_collection_profile():
    from envoy_cli.collections import canonical_profile_url

    canonical, platform = canonical_profile_url(
        "https://www.goofish.com/personal?userId=3604975982&spm=a21ybx"
    )

    assert canonical == "https://www.goofish.com/personal?userId=3604975982"
    assert platform == "goofish"


def test_taobao_and_tmall_items_keep_their_own_host():
    from envoy_cli.collections import canonical_candidate_url

    assert canonical_candidate_url(
        "https://item.taobao.com/item.htm?id=612345678901&spm=a1z10"
    ) == "https://item.taobao.com/item.htm?id=612345678901"
    assert canonical_candidate_url(
        "https://detail.tmall.com/item.htm?id=612345678901"
    ) == "https://detail.tmall.com/item.htm?id=612345678901"


def test_the_taobao_favourites_list_is_a_collection_profile():
    from envoy_cli.collections import canonical_profile_url

    assert canonical_profile_url("https://favorites.taobao.com/favorite") == (
        "https://favorites.taobao.com/favorite",
        "taobao",
    )


def test_a_jd_item_is_one_sku_whatever_host_served_it():
    from envoy_cli.collections import canonical_candidate_url

    assert canonical_candidate_url("https://item.jd.hk/100012043978.html") == (
        "https://item.jd.com/100012043978.html"
    )
    assert canonical_candidate_url(
        "https://item.jd.com/100012043978.html?extension_id=abc"
    ) == "https://item.jd.com/100012043978.html"


def test_the_jd_favourites_list_is_a_collection_profile():
    from envoy_cli.collections import canonical_profile_url

    assert canonical_profile_url("https://favorites.jd.com/") == (
        "https://favorites.jd.com/",
        "jd",
    )


def test_a_shared_shopping_link_names_no_item_until_it_is_opened():
    from envoy_cli.collections import CollectionError, canonical_candidate_url

    for url in ("https://u.jd.com/abcdEFz", "https://m.tb.cn/h.jnU2OZ6"):
        with pytest.raises(CollectionError):
            canonical_candidate_url(url)


def test_shopping_candidates_carry_their_marketplace_and_title():
    from envoy_cli.collections import _shopping_candidates

    html = (
        '<a href="https://www.goofish.com/item?id=800000000001&spm=x" title="iPhone 15">'
        "<img></a>"
        '<a href="https://item.jd.com/100012043978.html" aria-label="AirPods Pro">'
        "<div></div></a>"
        '<a href="https://item.taobao.com/item.htm?id=612345678901"><img alt="机械键盘"></a>'
        '<a href="https://www.goofish.com/item?id=800000000001">dup</a>'
    )

    rows = _shopping_candidates(html)

    assert [(row["platform"], row["item_type"], row["title"]) for row in rows] == [
        ("goofish", "listing", "iPhone 15"),
        ("jd", "listing", "AirPods Pro"),
        ("taobao", "listing", "机械键盘"),
    ]
