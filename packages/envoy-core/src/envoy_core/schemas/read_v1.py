from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


PROTOCOL_VERSION = "envoy.read/1"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SourceKind(str, Enum):
    AUTO = "auto"
    ARTICLE = "article"
    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"
    DOCUMENT = "document"
    COLLECTION = "collection"


class MaterialKind(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    CODE = "code"
    TABLE = "table"
    IMAGE = "image"
    OCR = "ocr"
    SUBTITLE = "subtitle"
    TRANSCRIPT = "transcript"
    VIDEO = "video"
    VIDEO_FRAME = "video_frame"
    VIDEO_SEGMENT = "video_segment"
    SUMMARY = "summary"


class MaterialOrigin(str, Enum):
    SOURCE_TEXT = "source_text"
    SOURCE_IMAGE = "source_image"
    SOURCE_VIDEO = "source_video"
    SUBTITLE = "subtitle"
    ASR = "asr"
    OCR = "ocr"
    TABLE_PARSE = "table_parse"
    HOST_VISUAL = "host_visual"
    MODEL_SUMMARY = "model_summary"


class ReadResult(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    NEEDS_HOST = "needs_host"
    BLOCKED = "blocked"
    EMPTY = "empty"


class ExecutionState(str, Enum):
    RUNNING = "running"
    WAITING_FOR_HOST = "waiting_for_host"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class ErrorCode(str, Enum):
    AUTH_REQUIRED = "auth_required"
    CHALLENGE_REQUIRED = "challenge_required"
    SOURCE_UNAVAILABLE = "source_unavailable"
    UNSUPPORTED_SOURCE = "unsupported_source"
    MISSING_CAPABILITY = "missing_capability"
    NETWORK_ERROR = "network_error"
    EMPTY_CONTENT = "empty_content"
    BUDGET_EXCEEDED = "budget_exceeded"
    RANGE_ALREADY_DELIVERED = "range_already_delivered"
    BUDGET_ENFORCEMENT_UNAVAILABLE = "budget_enforcement_unavailable"
    EXPIRED_RESOURCE = "expired_resource"
    INVALID_REQUEST = "invalid_request"
    INTERNAL_ERROR = "internal_error"


class ImageInputCapability(str, Enum):
    NONE = "none"
    LOCAL_FILE = "local_file"
    URI = "uri"
    INLINE = "inline"


class UsageStatus(str, Enum):
    ACTUAL = "actual"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class Scope(ContractModel):
    kind: Literal["current_resource", "range", "collection"] = "current_resource"
    follow_links: bool = False
    max_items: int = Field(default=1, ge=1, le=100)
    max_depth: int = Field(default=0, ge=0, le=5)
    locator: Optional[str] = None
    include_visual: bool = False
    start_seconds: Optional[float] = Field(default=None, ge=0)
    end_seconds: Optional[float] = Field(default=None, gt=0)
    page_start: Optional[int] = Field(default=None, ge=1)
    page_end: Optional[int] = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_page_range(self):
        if (
            self.page_start is not None
            and self.page_end is not None
            and self.page_end < self.page_start
        ):
            raise ValueError("page_end must be greater than or equal to page_start")
        return self

    @model_validator(mode="after")
    def validate_time_range(self):
        if (self.start_seconds is None) != (self.end_seconds is None):
            raise ValueError("start_seconds and end_seconds must be provided together")
        if (
            self.start_seconds is not None
            and self.end_seconds is not None
            and self.end_seconds <= self.start_seconds
        ):
            raise ValueError("end_seconds must be greater than start_seconds")
        return self


class SourceRef(ContractModel):
    url: str = Field(min_length=1, max_length=8192)
    kind: SourceKind = SourceKind.AUTO
    label: Optional[str] = Field(default=None, max_length=1000)


class Budget(ContractModel):
    max_response_chars: int = Field(default=12_000, ge=256, le=1_000_000)
    max_response_tokens: Optional[int] = Field(default=None, ge=64, le=250_000)
    max_total_response_tokens: Optional[int] = Field(default=None, ge=64, le=1_000_000)
    max_total_response_chars: int = Field(default=48_000, ge=256, le=5_000_000)
    max_visual_inputs: int = Field(default=12, ge=0, le=10_000)
    max_visual_pixels_per_input: int = Field(default=2_097_152, ge=0, le=100_000_000)
    max_total_visual_pixels: int = Field(default=16_777_216, ge=0, le=1_000_000_000)
    max_visual_clip_seconds: int = Field(default=15, ge=0, le=3600)
    max_total_visual_clip_seconds: int = Field(default=45, ge=0, le=86_400)
    max_download_bytes: int = Field(default=268_435_456, ge=0, le=10_737_418_240)
    max_work_seconds: int = Field(default=180, ge=1, le=86_400)
    max_expansion_rounds: int = Field(default=3, ge=0, le=100)
    max_optional_model_calls: int = Field(default=0, ge=0, le=10_000)

    @model_validator(mode="after")
    def validate_totals(self):
        # A caller that only raises the per-reply budget means "let one reply be
        # bigger", not "keep the old cumulative cap"; lift the cumulative budget
        # with it unless the caller set that field explicitly (then it wins and
        # the contract still rejects an impossible pair).
        if (
            self.max_total_response_chars < self.max_response_chars
            and "max_total_response_chars" not in getattr(self, "model_fields_set", ())
        ):
            object.__setattr__(self, "max_total_response_chars", self.max_response_chars)
        if self.max_total_response_chars < self.max_response_chars:
            raise ValueError("max_total_response_chars must be >= max_response_chars")
        if self.max_total_visual_pixels < self.max_visual_pixels_per_input:
            raise ValueError("max_total_visual_pixels must be >= max_visual_pixels_per_input")
        if self.max_total_visual_clip_seconds < self.max_visual_clip_seconds:
            raise ValueError("max_total_visual_clip_seconds must be >= max_visual_clip_seconds")
        return self


class Retention(ContractModel):
    raw_media: Literal["ephemeral", "session", "persistent"] = "ephemeral"
    derived_ttl_seconds: int = Field(default=86_400, ge=0, le=31_536_000)


class HostCapabilities(ContractModel):
    tool_execution: bool = False
    image_input: ImageInputCapability = ImageInputCapability.NONE
    video_input: Literal["none", "native", "frames"] = "none"
    usage_reporting: UsageStatus = UsageStatus.UNKNOWN
    async_execution: bool = False


class ReadRequest(ContractModel):
    protocol_version: Literal["envoy.read/1"] = PROTOCOL_VERSION
    request_id: str = Field(min_length=1, max_length=128)
    idempotency_key: Optional[str] = Field(default=None, max_length=256)
    session_id: Optional[str] = Field(default=None, max_length=128)
    source: SourceRef
    goal: str = Field(default="", max_length=20_000)
    scope: Scope = Field(default_factory=Scope)
    budget: Budget = Field(default_factory=Budget)
    retention: Retention = Field(default_factory=Retention)
    host_capabilities: HostCapabilities = Field(default_factory=HostCapabilities)
    ocr_profile: Optional[Literal["fast", "balanced", "quality", "pro"]] = None
    ocr_backend: Optional[
        Literal["auto", "vision", "paddleocr", "rapidocr", "tesseract"]
    ] = None
    ocr_language: Optional[str] = Field(default=None, min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    video_subtitle_language: Optional[str] = Field(default=None, max_length=32)


class LocalVideoReadRequest(ContractModel):
    protocol_version: Literal["envoy.read/1"] = PROTOCOL_VERSION
    request_id: str = Field(min_length=1, max_length=128)
    idempotency_key: Optional[str] = Field(default=None, max_length=256)
    session_id: Optional[str] = Field(default=None, max_length=128)
    path: str = Field(min_length=1, max_length=8192)
    goal: str = Field(default="", max_length=20_000)
    scope: Scope = Field(default_factory=Scope)
    budget: Budget = Field(default_factory=Budget)
    retention: Retention = Field(default_factory=Retention)
    host_capabilities: HostCapabilities = Field(default_factory=HostCapabilities)


class ExpandRequest(ContractModel):
    protocol_version: Literal["envoy.read/1"] = PROTOCOL_VERSION
    request_id: str = Field(min_length=1, max_length=128)
    material_id: str = Field(pattern=r"^mat-[a-f0-9]{24}$")
    session_id: Optional[str] = Field(default=None, max_length=128)
    offset: int = Field(default=0, ge=0)
    max_chars: int = Field(default=4000, ge=128, le=100_000)
    max_response_tokens: Optional[int] = Field(default=None, ge=64, le=25_000)
    max_total_response_tokens: Optional[int] = Field(default=None, ge=64, le=1_000_000)
    max_total_response_chars: int = Field(default=48_000, ge=256, le=5_000_000)
    max_expansion_rounds: Optional[int] = Field(default=None, ge=0, le=100)


class MaterialReplayRequest(ContractModel):
    protocol_version: Literal["envoy.read/1"] = PROTOCOL_VERSION
    request_id: str = Field(min_length=1, max_length=128)
    material_id: str = Field(pattern=r"^mat-[a-f0-9]{24}$")
    source_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_session_id: Optional[str] = Field(default=None, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    max_chars: int = Field(default=12_000, ge=256, le=100_000)
    max_response_tokens: Optional[int] = Field(default=None, ge=64, le=25_000)
    max_total_response_chars: int = Field(default=48_000, ge=256, le=5_000_000)
    max_total_response_tokens: Optional[int] = Field(default=None, ge=64, le=1_000_000)
    max_expansion_rounds: int = Field(default=3, ge=0, le=100)
    include_visual: bool = False
    max_visual_inputs: int = Field(default=12, ge=0, le=10_000)
    host_capabilities: HostCapabilities = Field(default_factory=HostCapabilities)

    @model_validator(mode="after")
    def validate_total_budget(self):
        if self.max_total_response_chars < self.max_chars:
            raise ValueError("max_total_response_chars must be >= max_chars")
        return self


class VisualObservationRequest(ContractModel):
    protocol_version: Literal["envoy.observe/1"] = "envoy.observe/1"
    request_id: str = Field(min_length=1, max_length=128)
    material_id: Optional[str] = Field(default=None, pattern=r"^mat-[a-f0-9]{24}$")
    session_id: Optional[str] = Field(default=None, max_length=128)
    source_url: Optional[str] = Field(default=None, max_length=8192)
    resource_id: str = Field(min_length=1, max_length=256)
    source_block_id: str = Field(min_length=1, max_length=256)
    observation: str = Field(min_length=1, max_length=20_000)
    locator_quote: Optional[str] = Field(default=None, max_length=500)


class VisualObservationHistoryRequest(ContractModel):
    protocol_version: Literal["envoy.observe/1"] = "envoy.observe/1"
    request_id: str = Field(min_length=1, max_length=128)
    material_id: str = Field(pattern=r"^mat-[a-f0-9]{24}$")
    session_id: Optional[str] = Field(default=None, max_length=128)
    resource_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    max_items: int = Field(default=8, ge=1, le=16)
    max_chars: int = Field(default=4000, ge=128, le=20_000)


class MaterialSearchRequest(ContractModel):
    protocol_version: Literal["envoy.search/1"] = "envoy.search/1"
    request_id: str = Field(min_length=1, max_length=128)
    material_id: str = Field(pattern=r"^mat-[a-f0-9]{24}$")
    session_id: Optional[str] = Field(default=None, max_length=128)
    query: str = Field(min_length=1, max_length=5000)
    max_results: int = Field(default=5, ge=1, le=20)
    max_chars: int = Field(default=4000, ge=128, le=20_000)
    max_response_tokens: Optional[int] = Field(default=None, ge=64, le=5000)


class TextContent(ContractModel):
    type: Literal["text"] = "text"
    text: str
    language: Optional[str] = Field(default=None, max_length=32)


class ImageContent(ContractModel):
    type: Literal["image"] = "image"
    resource_id: str
    alt_text: str = ""
    mime_type: Optional[str] = None


class VideoContent(ContractModel):
    type: Literal["video"] = "video"
    resource_id: str
    alt_text: str = ""
    mime_type: Optional[str] = None


class TableContent(ContractModel):
    type: Literal["table"] = "table"
    columns: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    caption: Optional[str] = None


MaterialContent = Annotated[
    Union[TextContent, ImageContent, VideoContent, TableContent],
    Field(discriminator="type"),
]


class WebLocator(ContractModel):
    type: Literal["web"] = "web"
    source_url: str
    block_id: Optional[str] = None
    selector: Optional[str] = None
    quote: Optional[str] = None


class ImageRegionLocator(ContractModel):
    type: Literal["image_region"] = "image_region"
    resource_id: str
    coordinate_system: Literal["normalized_top_left", "pixel_top_left"]
    bbox: list[float] = Field(min_length=4, max_length=4)
    source_width_px: Optional[int] = Field(default=None, ge=1)
    source_height_px: Optional[int] = Field(default=None, ge=1)
    rotation_degrees: int = Field(default=0, ge=0, le=359)

    @model_validator(mode="after")
    def validate_bbox(self):
        left, top, right, bottom = self.bbox
        if not left < right or not top < bottom:
            raise ValueError("bbox must have positive width and height")
        if self.coordinate_system == "normalized_top_left":
            if any(value < 0 or value > 1 for value in self.bbox):
                raise ValueError("normalized bbox coordinates must be between 0 and 1")
        return self


class VideoLocator(ContractModel):
    type: Literal["video_time"] = "video_time"
    source_url: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    frame_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_interval(self):
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        return self


class AudioLocator(ContractModel):
    type: Literal["audio_time"] = "audio_time"
    source_url: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    segment_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_interval(self):
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        return self


class PdfLocator(ContractModel):
    type: Literal["pdf_page"] = "pdf_page"
    # A page number is only present when the text offset really falls inside a
    # parsed page. When it does not, the locator is still useful for "this came
    # from the PDF" but must say so instead of naming a made-up page.
    page_number: Optional[int] = Field(default=None, ge=1)
    locator_valid: bool = True
    coordinate_system: Optional[Literal["normalized_top_left", "pixel_top_left"]] = None
    bbox: Optional[list[float]] = Field(default=None, min_length=4, max_length=4)
    rotation_degrees: int = Field(default=0, ge=0, le=359)

    @model_validator(mode="after")
    def validate_page(self):
        if self.locator_valid and self.page_number is None:
            raise ValueError("a valid pdf locator requires page_number")
        return self

    @model_validator(mode="after")
    def validate_bbox(self):
        if self.bbox is not None:
            left, top, right, bottom = self.bbox
            if not left < right or not top < bottom:
                raise ValueError("bbox must have positive width and height")
            if self.coordinate_system is None:
                raise ValueError("coordinate_system is required when bbox is set")
            if self.coordinate_system == "normalized_top_left" and any(
                value < 0 or value > 1 for value in self.bbox
            ):
                raise ValueError("normalized bbox coordinates must be between 0 and 1")
        return self


class OfficeLocator(ContractModel):
    type: Literal["office_location"] = "office_location"
    part: Literal["paragraph", "slide", "sheet", "cell_range"]
    part_index: Optional[int] = Field(default=None, ge=0)
    sheet_name: Optional[str] = None
    cell_range: Optional[str] = None


MaterialLocator = Annotated[
    Union[WebLocator, ImageRegionLocator, VideoLocator, AudioLocator, PdfLocator, OfficeLocator],
    Field(discriminator="type"),
]


class Relation(ContractModel):
    type: Literal[
        "derived_from",
        "adjacent_to",
        "annotation_of",
        "contains",
        "supports",
        "contradicts",
    ]
    target_id: str
    basis: Literal["extraction", "layout", "model_inference", "host_observation"] = "extraction"


class Quality(ContractModel):
    engine: Optional[str] = None
    engine_score: Optional[float] = Field(default=None, ge=0, le=1)
    unresolved: list[str] = Field(default_factory=list)


class MaterialBlock(ContractModel):
    id: str = Field(min_length=1, max_length=256)
    kind: MaterialKind
    content: MaterialContent
    origin: MaterialOrigin
    locator: Optional[MaterialLocator] = None
    parent_id: Optional[str] = None
    order: int = Field(default=0, ge=0)
    relations: list[Relation] = Field(default_factory=list)
    quality: Quality = Field(default_factory=Quality)


class HostDelivery(ContractModel):
    type: Literal["local_file", "uri", "inline"]
    path: Optional[str] = None
    uri: Optional[str] = None
    data_base64: Optional[str] = None

    @model_validator(mode="after")
    def validate_target(self):
        targets = [bool(self.path), bool(self.uri), bool(self.data_base64)]
        if sum(targets) != 1:
            raise ValueError("delivery must include exactly one path, uri, or data_base64")
        if self.type == "local_file" and not self.path:
            raise ValueError("local_file delivery requires path")
        if self.type == "uri" and not self.uri:
            raise ValueError("uri delivery requires uri")
        if self.type == "inline" and not self.data_base64:
            raise ValueError("inline delivery requires data_base64")
        return self


class ResourceRef(ContractModel):
    resource_id: str
    mime_type: str
    delivery: HostDelivery
    width_px: Optional[int] = Field(default=None, ge=1)
    height_px: Optional[int] = Field(default=None, ge=1)
    duration_seconds: Optional[float] = Field(default=None, gt=0)
    sha256: Optional[str] = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")
    source_resource_id: Optional[str] = None
    transform: Optional[dict[str, object]] = None
    lease_id: Optional[str] = None
    delivery_evidence: Literal["generated_only", "host_tool_event", "host_reported", "unverified"] = "unverified"
    expires_at: Optional[datetime] = None


class CoverageItem(ContractModel):
    discovered: Optional[int] = Field(default=None, ge=0)
    extracted: Optional[int] = Field(default=None, ge=0)
    returned: Optional[int] = Field(default=None, ge=0)
    processed: Optional[int] = Field(default=None, ge=0)
    delivered: Optional[int] = Field(default=None, ge=0)
    observed: Optional[int] = Field(default=None, ge=0)
    extraction: Literal["complete", "partial", "failed", "unknown"] = "unknown"
    unresolved: list[str] = Field(default_factory=list)
    details: dict[str, object] = Field(default_factory=dict)


class Coverage(ContractModel):
    modalities: dict[str, CoverageItem] = Field(default_factory=dict)
    unresolved: list[str] = Field(default_factory=list)
    sampled_ranges: list[dict[str, object]] = Field(default_factory=list)


class Need(ContractModel):
    action_id: str
    type: Literal["inspect_visual", "inspect_video", "expand_source", "request_input", "authenticate"]
    purpose: str
    resource_ids: list[str] = Field(default_factory=list)
    source_block_ids: list[str] = Field(default_factory=list)
    reason: str
    max_resources: Optional[int] = Field(default=None, ge=0)
    resume_token: Optional[str] = None


class EvidenceCheckRequest(ContractModel):
    """Ask whether one cited piece of material still resolves on this machine."""

    material_id: str = Field(min_length=1, max_length=256)
    block_id: Optional[str] = Field(default=None, max_length=256)
    quote: Optional[str] = Field(default=None, max_length=4000)


class EvidenceCheckResponse(ContractModel):
    """Deterministic half of citation checking; support judgement stays with the host.

    ``resolution`` / ``quote_match`` / ``locator_valid`` are what Envoy can settle
    by itself. ``semantic_support`` is deliberately fixed to ``not_evaluated``:
    whether the cited text supports the claim is a host-model or human decision.
    """

    protocol_version: Literal["envoy.evidence/1"] = "envoy.evidence/1"
    # Stable identity of the cited piece: material + block + revision. The same
    # citation checked twice on the same lease yields the same evidence_id.
    evidence_id: str
    material_id: str
    block_id: Optional[str] = None
    source_revision: Optional[str] = None
    # What that revision string actually traces to. The value differs by source
    # kind, so a caller must not read "same revision" as "same bytes" unless the
    # kind says the revision covers content.
    source_revision_kind: Literal[
        "byte_digest",
        "url_digest",
        "unknown",
    ] = "unknown"
    resolution: Literal["matched", "unmatched"]
    quote_match: Literal["exact", "normalized", "none"]
    locator_valid: bool
    semantic_support: Literal["not_evaluated"] = "not_evaluated"
    reason: Literal[
        "block_found",
        "quote_found",
        "material_expired",
        "material_missing",
        "block_missing",
        "quote_missing",
        "quote_not_provided",
        "no_locator",
        "locator_unverifiable",
    ]
    # What the caller can do next when the check did not settle. It is a
    # concrete tool call the host can issue, not prose it has to interpret:
    # a citation that no longer resolves needs the source read again, and a
    # block id that is not in this material needs the check widened.
    next_action: Optional[str] = None
    notes: list[str] = Field(default_factory=list)


class ReadError(ContractModel):
    code: ErrorCode
    message: str
    retryable: bool = False
    action_required: bool = False
    details: dict[str, object] = Field(default_factory=dict)


class UsageItem(ContractModel):
    status: UsageStatus = UsageStatus.UNKNOWN
    provider: Optional[str] = None
    model: Optional[str] = None
    input_tokens: Optional[int] = Field(default=None, ge=0)
    output_tokens: Optional[int] = Field(default=None, ge=0)
    cost: Optional[float] = Field(default=None, ge=0)
    duration_ms: Optional[int] = Field(default=None, ge=0)
    details: dict[str, object] = Field(default_factory=dict)


class Usage(ContractModel):
    host_model: UsageItem = Field(default_factory=UsageItem)
    auxiliary_models: list[UsageItem] = Field(default_factory=list)
    local: dict[str, Union[int, float, str, None]] = Field(default_factory=dict)


class ReadResponse(ContractModel):
    protocol_version: Literal["envoy.read/1"] = PROTOCOL_VERSION
    request_id: str
    session_id: Optional[str] = None
    material_id: Optional[str] = None
    source_revision: Optional[str] = None
    execution_state: ExecutionState
    result: ReadResult
    blocks: list[MaterialBlock] = Field(default_factory=list)
    resources: list[ResourceRef] = Field(default_factory=list)
    coverage: Coverage = Field(default_factory=Coverage)
    needs: list[Need] = Field(default_factory=list)
    errors: list[ReadError] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    expires_at: Optional[datetime] = None

    @model_validator(mode="after")
    def validate_result_state(self):
        if self.result == ReadResult.NEEDS_HOST and self.execution_state != ExecutionState.WAITING_FOR_HOST:
            raise ValueError("needs_host result requires waiting_for_host execution_state")
        if self.result == ReadResult.READY and self.execution_state != ExecutionState.COMPLETED:
            raise ValueError("ready result requires completed execution_state")
        if self.result == ReadResult.BLOCKED and not self.errors:
            raise ValueError("blocked result requires a structured error")
        return self


def read_request_json_schema() -> dict:
    return ReadRequest.model_json_schema()


def read_response_json_schema() -> dict:
    return ReadResponse.model_json_schema()

