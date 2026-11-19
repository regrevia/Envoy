"""One diagnosis for every way a read ends without the source's content.

The host sees a dozen ``error.code`` values, each with a free-form message, and
has to answer a much smaller question before it can pick a next step: does a
human have to sign in, was this network exit refused, does the page need a
script to render, or is something missing from this machine? Codes answer
"what was wrong inside Envoy"; they do not answer that.

So every failed result carries the same three things under one key, whichever
path produced it: the kind of failure, what the kind means for the source, and
the single next step that changes the outcome. ``failure_kind`` in an error's
details overrides the mapping when the code alone cannot tell -- a rate-limit
envelope and a sign-in wall are both "the page I got is not the page", but a
sign-in cannot fix a blocked egress.
"""

from __future__ import annotations

from dataclasses import dataclass

# Closed set. A new kind is a new decision for the host, so it belongs in this
# module next to the next step that makes it useful, not inline at a raise site.
LOGIN_REQUIRED = "login_required"
SOURCE_BLOCKED = "source_blocked"
DYNAMIC_CONTENT = "dynamic_content"
CAPABILITY_MISSING = "capability_missing"
TRANSPORT_CLOSED = "transport_closed"
BUDGET_EXHAUSTED = "budget_exhausted"
MATERIAL_EXPIRED = "material_expired"
SOURCE_UNAVAILABLE = "source_unavailable"
NETWORK_ERROR = "network_error"
EMPTY_DOCUMENT = "empty_document"
UNKNOWN = "unknown"

FAILURE_KINDS = (
    LOGIN_REQUIRED,
    SOURCE_BLOCKED,
    DYNAMIC_CONTENT,
    CAPABILITY_MISSING,
    TRANSPORT_CLOSED,
    BUDGET_EXHAUSTED,
    MATERIAL_EXPIRED,
    SOURCE_UNAVAILABLE,
    NETWORK_ERROR,
    EMPTY_DOCUMENT,
    UNKNOWN,
)


@dataclass(frozen=True)
class Diagnosis:
    kind: str
    summary: str
    next_step: str
    retryable: bool = False
    needs_user_action: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "summary": self.summary,
            "next_step": self.next_step,
            "retryable": self.retryable,
            "needs_user_action": self.needs_user_action,
        }


_SIGN_IN = (
    "sign the user in once with envoy_auth and read the same link again; the "
    "session is reused by later reads"
)
_RETRY_LATER = (
    "retry later, or read the same link through a different network exit; a "
    "sign-in does not lift this"
)
_INSTALL = "install the missing piece named in the error, then read the same link again"

_DIAGNOSES = {
    LOGIN_REQUIRED: Diagnosis(
        kind=LOGIN_REQUIRED,
        summary=(
            "the source served a sign-in page instead of the content, so nothing "
            "about the page itself was read"
        ),
        next_step=_SIGN_IN,
        needs_user_action=True,
    ),
    SOURCE_BLOCKED: Diagnosis(
        kind=SOURCE_BLOCKED,
        summary=(
            "the source's edge refused this visit -- rate limit, risk control or "
            "a verification wall -- so the page was never served"
        ),
        next_step=_RETRY_LATER,
        retryable=True,
    ),
    DYNAMIC_CONTENT: Diagnosis(
        kind=DYNAMIC_CONTENT,
        summary=(
            "the source only renders this page's body with a script, and the html "
            "it served contains none of it"
        ),
        next_step=(
            "sign the user in once with envoy_auth if the site also gates the body, "
            "or read it with the browser renderer; a plain fetch returns the same shell"
        ),
        needs_user_action=True,
    ),
    CAPABILITY_MISSING: Diagnosis(
        kind=CAPABILITY_MISSING,
        summary="this machine is missing the piece that reads this source",
        next_step=_INSTALL,
        needs_user_action=True,
    ),
    TRANSPORT_CLOSED: Diagnosis(
        kind=TRANSPORT_CLOSED,
        summary=(
            "Envoy ran the request and its own reader failed before an answer was "
            "written, so this is not the source refusing"
        ),
        next_step=(
            "send the same request id once more; the retry is keyed by that id, so "
            "it cannot duplicate a side effect"
        ),
        retryable=True,
    ),
    BUDGET_EXHAUSTED: Diagnosis(
        kind=BUDGET_EXHAUSTED,
        summary="the source was read fine but this answer was cut by a budget",
        next_step="continue from the offset or page the error reports",
        retryable=True,
    ),
    MATERIAL_EXPIRED: Diagnosis(
        kind=MATERIAL_EXPIRED,
        summary="the material this call names was evicted, so there is nothing left to expand",
        next_step="read the source again with refresh=true for the same url",
        retryable=True,
    ),
    SOURCE_UNAVAILABLE: Diagnosis(
        kind=SOURCE_UNAVAILABLE,
        summary="the source did not serve the page: it is gone, moved, or answered with an error",
        next_step=(
            "take it with your own plain fetch and answer from whatever that returns"
        ),
    ),
    NETWORK_ERROR: Diagnosis(
        kind=NETWORK_ERROR,
        summary="the request to the source failed on the network before a page arrived",
        next_step="retry once; if it fails again, check the network exit",
        retryable=True,
    ),
    EMPTY_DOCUMENT: Diagnosis(
        kind=EMPTY_DOCUMENT,
        summary=(
            "the source answered with a page but no readable text, and it did not "
            "ask for a sign-in"
        ),
        next_step="take it with your own plain fetch and answer from what you get",
    ),
    UNKNOWN: Diagnosis(
        kind=UNKNOWN,
        summary="the read failed for a reason Envoy cannot attribute to the source",
        next_step=(
            "read the error message; if it is not actionable, take the source with "
            "your own plain fetch"
        ),
    ),
}

_BY_CODE = {
    "auth_required": LOGIN_REQUIRED,
    "challenge_required": LOGIN_REQUIRED,
    "missing_capability": CAPABILITY_MISSING,
    "expired_resource": MATERIAL_EXPIRED,
    "budget_exceeded": BUDGET_EXHAUSTED,
    "range_already_delivered": BUDGET_EXHAUSTED,
    "budget_enforcement_unavailable": BUDGET_EXHAUSTED,
    "source_unavailable": SOURCE_UNAVAILABLE,
    "unsupported_source": SOURCE_UNAVAILABLE,
    "network_error": NETWORK_ERROR,
    "internal_error": TRANSPORT_CLOSED,
}


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value))


def diagnose(
    code: object,
    details: dict | None = None,
    message: str = "",
) -> Diagnosis:
    """Classify one failure. ``details`` may carry an explicit ``failure_kind``."""

    details = details if isinstance(details, dict) else {}
    explicit = _text(details.get("failure_kind"))
    if explicit in _DIAGNOSES:
        return _DIAGNOSES[explicit]

    name = _text(code).strip().lower()
    if name == "empty_content":
        # An empty body plus a handoff means the page renders for a session;
        # the same empty body without one was simply never a document.
        if isinstance(details.get("needs_user_action"), dict):
            return _DIAGNOSES[DYNAMIC_CONTENT]
        return _DIAGNOSES[EMPTY_DOCUMENT]

    kind = _BY_CODE.get(name, UNKNOWN)
    diagnosis = _DIAGNOSES[kind]
    if kind == CAPABILITY_MISSING:
        action = details.get("needs_user_action")
        command = ""
        if isinstance(action, dict):
            command = _text(action.get("command"))
        if command:
            diagnosis = Diagnosis(
                kind=diagnosis.kind,
                summary=diagnosis.summary,
                next_step=f"install it with `{command}`, then read the same link again",
                retryable=diagnosis.retryable,
                needs_user_action=True,
            )
    if message and kind == UNKNOWN:
        summary = f"{diagnosis.summary}: {message.strip()[:200]}"
        diagnosis = Diagnosis(
            kind=diagnosis.kind,
            summary=summary,
            next_step=diagnosis.next_step,
            retryable=diagnosis.retryable,
            needs_user_action=diagnosis.needs_user_action,
        )
    return diagnosis


def kind_diagnosis(kind: str) -> Diagnosis:
    """The diagnosis for a failure kind that never produced a read payload."""

    return _DIAGNOSES.get(kind, _DIAGNOSES[UNKNOWN])


def attach(payload: dict) -> dict | None:
    """Stamp ``payload["diagnosis"]`` from a serialized read payload.

    Every surface that shows a failed read goes through this, so the same
    failure is described the same way in ``envoy_read`` text, in the MCP result
    and in the CLI's JSON. Returns the diagnosis it attached, if any.
    """

    if not isinstance(payload, dict):
        return None
    existing = payload.get("diagnosis")
    if isinstance(existing, dict):
        return existing

    errors = [item for item in payload.get("errors") or [] if isinstance(item, dict)]
    if errors:
        first = errors[0]
        details = first.get("details")
        diagnosis = diagnose(
            first.get("code"),
            details if isinstance(details, dict) else {},
            _text(first.get("message")),
        )
        payload["diagnosis"] = diagnosis.as_dict()
        return payload["diagnosis"]

    if _text(payload.get("result")) != "partial":
        return None
    # A partial read that hands over a sign-in is the same page as a blocked one,
    # only it delivered whatever it could first. Say which failure it is too.
    needs = [item for item in payload.get("needs") or [] if isinstance(item, dict)]
    if any(_text(need.get("type")) == "authenticate" for need in needs):
        payload["diagnosis"] = _DIAGNOSES[DYNAMIC_CONTENT].as_dict()
        return payload["diagnosis"]
    return None
