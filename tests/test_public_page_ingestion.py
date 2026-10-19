import sys
import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def test_normalize_public_page_deduplicates_images_and_preserves_article_text(monkeypatch):
    from app.services import web_content_service

    monkeypatch.setattr(web_content_service, "_is_public_http_url", lambda value: value.startswith("https://"))
    page = web_content_service.normalize_page_data(
        {
            "title": "  Public article\n",
            "canonical_url": "https://example.org/article",
            "content_type": "article",
            "author": "Author",
            "text": "First paragraph.\n\nSecond paragraph.",
            "images": [
                {"url": "https://cdn.example.org/a.webp", "alt": "Chart"},
                {"url": "https://cdn.example.org/a.webp", "alt": "Duplicate"},
                {"url": "http://127.0.0.1/private.png", "alt": "Blocked"},
            ],
        },
        "https://example.org/article",
    )

    assert page["title"] == "Public article"
    assert page["text"] == "First paragraph.\n\nSecond paragraph."
    assert page["author"] == "Author"
    assert page["images"] == [{"url": "https://cdn.example.org/a.webp", "alt": "Chart"}]


def test_public_page_network_guard_rejects_private_and_credentialed_urls():
    from app.services.web_content_service import _is_public_http_url

    assert not _is_public_http_url("http://127.0.0.1:8140/api")
    assert not _is_public_http_url("http://169.254.169.254/latest/meta-data")
    assert not _is_public_http_url("https://user:pass@example.org/article")


def test_public_page_image_extension_accepts_raster_and_rejects_svg():
    from app.services.web_content_service import PublicPageError, image_extension

    assert image_extension("image/webp") == ".webp"
    assert image_extension("image/jpeg") == ".jpg"
    try:
        image_extension("image/svg+xml")
    except PublicPageError:
        pass
    else:
        raise AssertionError("SVG images should not be embedded as source assets")


def test_public_image_download_retries_transient_transport_eof(monkeypatch):
    from contextlib import contextmanager
    import httpx
    from app.services import web_content_service

    calls = []

    class Response:
        status_code = 200
        headers = {"content-type": "image/webp"}

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b"image-bytes"

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @contextmanager
        def stream(self, *_args, **_kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise httpx.RemoteProtocolError("EOF occurred in violation of protocol")
            yield Response()

    monkeypatch.setattr(web_content_service, "_is_public_http_url", lambda _url: True)
    monkeypatch.setattr(web_content_service.httpx, "Client", Client)
    monkeypatch.setattr(web_content_service.time, "sleep", lambda _seconds: None)

    data, content_type = web_content_service.download_public_image(
        "https://cdn.example.org/photo.webp",
        "https://www.douyin.com/note/123",
    )

    assert data == b"image-bytes"
    assert content_type == "image/webp"
    assert len(calls) == 2


def test_public_page_bundle_includes_complete_source_text_and_every_image(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.workers.jobs import _attach_public_page_source_to_bundle
    from app.core.storage import storage_client

    image_data = {f"image-{index}.webp": f"image-{index}".encode() for index in range(1, 5)}
    source_text = b"# A public note\n\nThe complete article text."

    def read_bytes(object_name):
        if object_name == "note.md":
            return source_text
        return image_data[object_name]

    monkeypatch.setattr(storage_client, "mode", "local")
    monkeypatch.setattr(storage_client, "read_bytes", read_bytes)
    export_root = tmp_path / "library"
    overview = export_root / "overview" / "00-project-overview.md"
    overview.parent.mkdir(parents=True)
    (export_root / "index.md").write_text("# Index\n", encoding="utf-8")
    (export_root / "manifest.json").write_text("{}", encoding="utf-8")
    text_asset = SimpleNamespace(file_path="note.md", file_name="note.md")
    image_assets = [
        SimpleNamespace(
            id=f"image-{index}",
            file_path=f"image-{index}.webp",
            mime_type="image/webp",
            extra_metadata={"artifact_filename": f"image-{index}.webp", "alt": f"Figure {index}"},
        )
        for index in range(1, 5)
    ]

    result = _attach_public_page_source_to_bundle(
        db=None,
        task_id="task-1",
        project_id="project-1",
        bundle_path=str(overview),
        source_url="https://www.douyin.com/note/123",
        page_info={
            "title": "A public note",
            "content_type": "image_text",
            "output_formats": ["markdown", "html"],
        },
        text_asset=text_asset,
        image_assets=image_assets,
    )

    source_page = Path(result["markdown_path"]).read_text(encoding="utf-8")
    manifest = (export_root / "manifest.json").read_text(encoding="utf-8")
    materials = json.loads((export_root / "materials.json").read_text(encoding="utf-8"))
    html = (export_root / "index.html").read_text(encoding="utf-8")

    assert "The complete article text." in source_page
    assert all(f"image-{index}.webp" in source_page for index in range(1, 5))
    assert all((export_root / "assets" / "images" / f"image-{index}.webp").is_file() for index in range(1, 5))
    assert '"image_count": 4' in manifest
    assert all(f"image-{index}.webp" in html for index in range(1, 5))
    assert materials["protocol_version"] == "envoy.read/1"
    assert materials["result"] == "ready"
    assert materials["coverage"]["modalities"]["images"]["returned"] == 4
    assert [block["origin"] for block in materials["blocks"] if block["kind"] == "paragraph"] == ["source_text"]
    assert len([block for block in materials["blocks"] if block["kind"] == "image"]) == 4
    assert materials["resources"][0]["delivery"]["uri"].startswith("assets/images/")


def test_public_page_bundle_passes_ocr_blocks_into_materials(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.workers.jobs import _attach_public_page_source_to_bundle
    from app.core.storage import storage_client

    monkeypatch.setattr(storage_client, "mode", "local")
    monkeypatch.setattr(storage_client, "read_bytes", lambda name: b"body" if name == "note.md" else b"image")
    export_root = tmp_path / "library"
    (export_root / "overview").mkdir(parents=True)
    (export_root / "index.md").write_text("# Index\n", encoding="utf-8")
    (export_root / "manifest.json").write_text("{}", encoding="utf-8")
    text_asset = SimpleNamespace(file_path="note.md", file_name="note.md")
    image_asset = SimpleNamespace(
        id="image-1",
        file_path="image.webp",
        mime_type="image/webp",
        extra_metadata={"artifact_filename": "image.webp", "alt": "Screenshot"},
    )

    result = _attach_public_page_source_to_bundle(
        db=None,
        task_id="task-ocr",
        project_id="project-1",
        bundle_path=str(export_root / "overview" / "page.md"),
        source_url="https://example.org/note",
        page_info={"title": "Note", "text": "Body", "output_formats": ["markdown"]},
        text_asset=text_asset,
        image_assets=[image_asset],
        ocr_blocks=[{"asset_id": "image-1", "content": "Command: envoy read", "engine": "test"}],
    )

    materials = json.loads((export_root / "materials.json").read_text(encoding="utf-8"))
    ocr = next(block for block in materials["blocks"] if block["kind"] == "ocr")
    assert ocr["content"]["text"] == "Command: envoy read"
    assert ocr["parent_id"] == "blk-image-0001"


def test_public_page_material_response_marks_source_and_ocr_origins():
    from app.services.web_content_service import public_page_material_response

    response = public_page_material_response(
        page={"title": "Note", "text": "Body", "images": [{"url": "https://example.org/a.webp"}]},
        source_url="https://example.org/note",
        image_assets=[{
            "id": "asset-1",
            "file_name": "image-01.webp",
            "mime_type": "image/webp",
            "sha256": "0" * 64,
            "alt": "A",
        }],
        ocr_blocks=[{"asset_id": "asset-1", "content": "OCR text", "engine": "test"}],
    )

    origins = {block["id"]: block["origin"] for block in response.model_dump(mode="json")["blocks"]}
    assert origins["blk-body-0001"] == "source_text"
    assert origins["blk-image-0001"] == "source_image"
    assert origins["blk-ocr-0001"] == "ocr"


def test_public_page_bundle_can_include_ocr_blocks_from_image_assets(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.services.web_content_service import public_page_material_response

    response = public_page_material_response(
        page={"title": "Note", "text": "Body"},
        source_url="https://example.org/note",
        image_assets=[{
            "id": "asset-1",
            "file_name": "image-01.webp",
            "mime_type": "image/webp",
            "sha256": "0" * 64,
            "alt": "A",
        }],
        ocr_blocks=[{
            "asset_id": "asset-1",
            "content": "Command: envoy read",
            "engine": "PP-OCRv6_medium",
        }],
    )

    ocr = next(block for block in response.blocks if block.kind.value == "ocr")
    assert ocr.parent_id == "blk-image-0001"
    assert ocr.content.text == "Command: envoy read"
