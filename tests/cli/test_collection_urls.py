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
