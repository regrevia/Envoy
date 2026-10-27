import pytest

from envoy_cli.fallbacks import SITE_BACKENDS
from envoy_cli.platforms import read_support_verdict


@pytest.mark.parametrize(("name", "pattern", "sample"), SITE_BACKENDS, ids=[b[0] for b in SITE_BACKENDS])
def test_every_endpoint_backend_is_marked_supported(name, pattern, sample):
    """A backend whose URL the router calls unsupported can never run."""

    assert pattern.match(sample), f"{name} no longer matches its own sample URL"
    assert read_support_verdict(sample) == "supported"
