"""Guards what a ``source_revision`` is allowed to mean.

A revision answers "is this the same material". A URL cannot answer that: the
same page or transcript served through a re-signed address is the same
material, and the same address can serve different material tomorrow. These
tests pin the two behaviours that make the answer honest:

* a revision derived from content ignores the address the content arrived
  through;
* a revision derived from an address stays a separate, labelled basis instead
  of being passed off as a content digest.
"""

from __future__ import annotations

import hashlib

from envoy_cli.reader import (
    _content_revision,
    _evidence_revision_kind,
    _public_api_fallback_response,
    _url_basis_revision,
)
from envoy_core.schemas.read_v1 import HostCapabilities, ReadRequest, SourceRef


def _request(source_url: str, *, request_id: str = "req-1", session_id: str = "ses-1") -> ReadRequest:
    return ReadRequest(
        protocol_version="envoy.read/1",
        request_id=request_id,
        session_id=session_id,
        source=SourceRef(url=source_url),
        goal="",
        host_capabilities=HostCapabilities(),
    )


def test_a_content_revision_is_a_sha256_hex_digest():
    revision = _content_revision("video-transcript", "hello")
    assert len(revision) == 64
    assert revision == revision.lower()
    int(revision, 16)
    assert revision == _content_revision("video-transcript", "hello")


def test_the_tag_keeps_two_kinds_of_content_apart():
    shared = "identical text"
    assert _content_revision("video-transcript", shared) != _content_revision(
        "public-api:feed_alternate", shared
    )


def test_parts_cannot_bleed_into_each_other():
    # Without a separator, ("ab", "c") and ("a", "bc") would hash the same
    # string and two different materials would claim one revision.
    assert _content_revision("video-transcript", "ab", "c") != _content_revision(
        "video-transcript", "a", "bc"
    )


def test_a_url_basis_revision_is_never_the_bare_url_digest():
    url = "https://example.com/watch?v=abc"
    tagged = _url_basis_revision("video", url)
    assert tagged == _content_revision("url-basis:video", url)
    assert tagged != hashlib.sha256(url.encode()).hexdigest()
    assert _url_basis_revision("video", url) != _url_basis_revision("image", url)


def test_a_feed_fallback_revision_survives_a_re_signed_address():
    first = _request("https://example.com/feed?token=one")
    second = _request("https://example.com/feed?token=two", request_id="req-2")
    payload = {"backend": "feed_alternate", "text": "same article", "title": "Same"}
    revised_first = _public_api_fallback_response(first, first.source.url, dict(payload))
    revised_second = _public_api_fallback_response(second, second.source.url, dict(payload))
    assert revised_first.source_revision == revised_second.source_revision
    # The address still separates the two materials, which is the identity axis.
    assert revised_first.material_id != revised_second.material_id


def test_the_fallback_revision_moves_when_the_text_does():
    first = _request("https://example.com/feed")
    revised = _public_api_fallback_response(
        first, first.source.url, {"backend": "feed_alternate", "text": "one", "title": "T"}
    )
    changed = _public_api_fallback_response(
        first, first.source.url, {"backend": "feed_alternate", "text": "two", "title": "T"}
    )
    assert revised.source_revision != changed.source_revision


def test_an_address_derived_revision_is_reported_as_such_not_as_content():
    url = "https://example.com/watch?v=abc"
    tagged = _url_basis_revision("video", url)
    assert (
        _evidence_revision_kind({"source_kind": "video", "source_url": url}, tagged)
        == "url_digest"
    )
    # A redacted URL is what several media leases store, so it must resolve too.
    redacted = "https://example.com/watch"
    assert (
        _evidence_revision_kind(
            {"source_kind": "video", "source_url": redacted},
            _url_basis_revision("video", redacted),
        )
        == "url_digest"
    )
    # The bare-URL digests already in leases stay readable until they expire.
    assert (
        _evidence_revision_kind(
            {"source_kind": "video", "source_url": url},
            hashlib.sha256(url.encode()).hexdigest(),
        )
        == "url_digest"
    )


def test_a_content_revision_is_never_claimed_as_a_byte_digest():
    transcript = _content_revision("video-transcript", "spoken words")
    assert (
        _evidence_revision_kind(
            {"source_kind": "video", "source_url": "https://example.com/v"}, transcript
        )
        == "unknown"
    )
