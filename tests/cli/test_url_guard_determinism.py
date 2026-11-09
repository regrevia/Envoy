"""The public-URL guard must reach one verdict from one DNS lookup.

The guard used to answer a bool: loopback/private addresses, embedded
credentials and an unresolvable host name all came back ``False``, and the read
path then re-resolved the name to decide whether that ``False`` meant "policy"
or "network". Two lookups taken milliseconds apart can disagree, so a DNS
hiccup could turn a policy refusal into a network error or the reverse. These
tests pin the single-lookup contract.

No test resolves a real host name: ``getaddrinfo`` is always replaced.
"""

from __future__ import annotations

import pytest

from envoy_cli import reader
from envoy_core.schemas.read_v1 import ReadRequest


def _request(url: str) -> ReadRequest:
    return ReadRequest.model_validate(
        {
            "protocol_version": "envoy.read/1",
            "request_id": "guard-1",
            "source": {"url": url},
            "goal": "what is this page",
            "budget": {"max_work_seconds": 30},
        }
    )


@pytest.fixture
def lookups(monkeypatch):
    """Count every getaddrinfo call and answer with a chosen address set."""

    calls: list[tuple[str, object]] = []
    state = {"addresses": ["93.184.216.34"], "error": None}

    def fake_getaddrinfo(host, port, *args, **kwargs):
        calls.append((host, port))
        if state["error"] is not None:
            raise state["error"]
        return [(2, 1, 6, "", (address, port or 80)) for address in state["addresses"]]

    monkeypatch.setattr(reader.socket, "getaddrinfo", fake_getaddrinfo)
    return calls, state


def test_unresolvable_host_is_one_lookup_and_one_network_error(lookups):
    calls, state = lookups
    state["error"] = OSError("Name or service not known")
    response = reader._read_public_page_once(_request("https://no-such-host.invalid/a"))
    assert len(calls) == 1
    assert response.result.value == "blocked"
    assert response.errors[0].code.value == "network_error"
    assert "dns" in response.errors[0].message.lower()


def test_private_address_is_one_lookup_and_one_policy_refusal(lookups):
    calls, state = lookups
    state["addresses"] = ["10.0.0.7"]
    response = reader._read_public_page_once(_request("https://internal.example/a"))
    assert len(calls) == 1, calls
    assert response.errors[0].code.value == "source_unavailable"
    assert "Only public HTTP(S) URLs are allowed" in response.errors[0].message


def test_guard_reuses_the_cached_verdict(lookups):
    calls, _state = lookups
    cache: dict[str, str] = {}
    assert reader._is_public_http_url("https://example.com/a", cache) is True
    assert reader._is_public_http_url("https://example.com/b", cache) is True
    assert len(calls) == 1
    assert cache["example.com"] == reader.PUBLIC_URL


def test_cached_dns_failure_stays_a_dns_failure(lookups):
    calls, state = lookups
    state["error"] = OSError("temporary failure in name resolution")
    cache: dict[str, str] = {}
    assert reader._public_url_rejection("https://flaky.example/a", cache) == reader.DNS_FAILURE
    assert reader._public_url_rejection("https://flaky.example/b", cache) == reader.DNS_FAILURE
    assert len(calls) == 1
    # Even if a later lookup would succeed, an attempt that already resolved the
    # host as unreachable keeps that one verdict.
    state["error"] = None
    assert reader._public_url_rejection("https://flaky.example/c", cache) == reader.DNS_FAILURE
    assert len(calls) == 1


def test_policy_rejection_is_not_registered_as_a_dns_failure(lookups):
    calls, _state = lookups
    assert reader._public_url_rejection("https://example.com/a", {}) is None
    assert reader._public_url_rejection("http://127.0.0.1:8080/x", {}) == reader.POLICY_REJECTION
    assert reader._public_url_rejection("file:///etc/passwd", {}) == reader.POLICY_REJECTION
    assert reader._public_url_rejection("https://user:pw@example.com/a", {}) == reader.POLICY_REJECTION
    assert reader._public_url_rejection("http://localhost:8080/x", {}) == reader.POLICY_REJECTION
    # Only the two host names above were looked up; literals and bad schemes never resolve.
    assert [host for host, _port in calls] == ["example.com"]


def test_a_name_answered_from_the_fake_ip_pool_is_fetchable(lookups):
    _calls, state = lookups
    # A TUN proxy in fake-IP mode answers every A record from its own pool
    # (198.18.0.0/15 by default) and routes the connection by name, so the
    # address says nothing about where the request really goes (#19).
    state["addresses"] = ["198.18.4.7"]
    assert reader._public_url_rejection("https://example.com/a", {}) is None
    assert reader._is_public_http_url("https://example.com/a", {}) is True


def test_a_mixed_answer_is_still_a_policy_refusal(lookups):
    _calls, state = lookups
    # A name that answers with the pool *and* with a private address is the
    # machine's own network, not a proxy placeholder.
    state["addresses"] = ["198.18.4.7", "10.0.0.8"]
    assert reader._public_url_rejection("https://example.com/a", {}) == reader.POLICY_REJECTION


def test_a_literal_fake_ip_url_is_not_trusted(lookups):
    _calls, state = lookups
    # The allowance is about a DNS answer, never about a URL a caller wrote.
    state["addresses"] = ["198.18.4.7"]
    assert reader._public_url_rejection("https://198.18.4.7/a", {}) == reader.POLICY_REJECTION


def test_the_fake_ip_pool_can_be_extended_by_the_environment(lookups, monkeypatch):
    _calls, state = lookups
    monkeypatch.setenv(reader.FAKE_IP_ENV_VAR, "not-a-cidr, 100.64.0.0/10")
    state["addresses"] = ["100.100.1.9"]
    # A malformed entry is skipped instead of disabling the pool entirely.
    assert reader._public_url_rejection("https://example.com/a", {}) is None


def test_a_refusal_explains_the_fake_ip_pool_without_a_second_lookup(lookups):
    calls, state = lookups
    state["addresses"] = ["10.0.0.8"]
    response = reader._read_public_page_once(_request("https://internal.example/a"))
    assert len(calls) == 1, calls
    assert response.errors[0].code.value == "source_unavailable"
    message = response.errors[0].message
    assert message.startswith("Only public HTTP(S) URLs are allowed")
    # A refused name is told how to declare a proxy's placeholder pool; a refused
    # literal address is not, because no proxy answered the name there.
    assert reader.FAKE_IP_ENV_VAR in message
    assert reader.DEFAULT_FAKE_IP_RANGES in message
    literal = reader._read_public_page_once(_request("https://10.0.0.8/a"))
    assert reader.FAKE_IP_ENV_VAR not in literal.errors[0].message
