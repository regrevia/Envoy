"""A partial read that stopped on authentication must stay retryable."""

from envoy_cli import reader

from envoy_core.schemas.read_v1 import ErrorCode, ReadResult


def _payload(result, code):
    return {
        "request_id": "r-1",
        "execution_state": "completed",
        "result": result.value,
        "blocks": [],
        "errors": [{"code": code.value, "message": "sign in", "retryable": False}],
    }


def test_partial_with_auth_required_is_not_cached():
    payload = _payload(ReadResult.PARTIAL, ErrorCode.AUTH_REQUIRED)
    assert reader._read_operation_result_is_cacheable(payload) is False


def test_partial_with_challenge_required_is_not_cached():
    payload = _payload(ReadResult.PARTIAL, ErrorCode.CHALLENGE_REQUIRED)
    assert reader._read_operation_result_is_cacheable(payload) is False


def test_a_clean_partial_stays_cached():
    payload = _payload(ReadResult.PARTIAL, ErrorCode.EMPTY_CONTENT)
    assert reader._read_operation_result_is_cacheable(payload) is True
