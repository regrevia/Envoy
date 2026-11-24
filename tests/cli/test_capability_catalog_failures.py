"""The runtime catalog publishes the codes a host has to branch on.

A host that reads ``error.code`` needs to know the set is closed and what each
member asks of it. Envoy already answers that per failure through
``diagnosis``; the catalog makes the same answer discoverable before the first
failure, without a second hand-maintained list that could disagree with it.
"""

from __future__ import annotations

from envoy_cli import capabilities, diagnosis
from envoy_core.schemas.read_v1 import ErrorCode


def test_every_read_error_code_is_published_exactly_once():
    published = capabilities.failure_catalog()["codes"]

    assert [row["code"] for row in published] == [code.value for code in ErrorCode]
    assert len(published) == len(ErrorCode)


def test_published_rows_are_the_diagnosis_the_read_would_attach():
    """No row is authored here; each is what ``diagnosis`` says for that code."""
    for row in capabilities.failure_catalog()["codes"]:
        expected = diagnosis.diagnose(row["code"])
        assert row["kind"] == expected.kind
        assert row["retryable"] == expected.retryable
        assert row["needs_user_action"] == expected.needs_user_action
        assert row["next_step"] == expected.next_step


def test_every_published_kind_is_a_declared_failure_kind():
    published = capabilities.failure_catalog()["codes"]

    assert {row["kind"] for row in published} <= set(diagnosis.FAILURE_KINDS)


def test_the_catalog_carries_the_failure_table_and_the_rule_that_uses_it():
    catalog = capabilities.capability_catalog()

    failures = catalog["failures"]
    assert failures["codes"]
    assert "do not parse message text" in failures["rule"]
    assert any("next_step" in line for line in catalog["selection_policy"])


def test_a_code_that_needs_a_human_says_so_in_the_catalog():
    """The one thing a host must not get wrong: retrying what only a person fixes."""
    by_code = {row["code"]: row for row in capabilities.failure_catalog()["codes"]}

    assert by_code["auth_required"]["needs_user_action"] is True
    assert by_code["auth_required"]["retryable"] is False
    assert "envoy_auth" in by_code["auth_required"]["next_step"]
    # A refused egress is not a sign-in problem; retrying it is waste.
    assert by_code["source_unavailable"]["needs_user_action"] is False
