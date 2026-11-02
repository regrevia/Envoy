"""PDF 页面光栅化：把一页渲染成 PNG，供 OCR 使用（契约 §2）。

为什么单独成文件：OCR 只吃图片，扫描页必须先渲染；而渲染后端是**可选**的
（pypdfium2 属可选 extra，系统也可能只有 poppler/mutool 命令行）。把"怎么把
一页变成 PNG"收在这里，reader.py 只负责调用与失败上报，缺依赖时不静默降级。

只做渲染，不做 OCR；结果以字节返回并附 sha256，不落长期文件。
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import io
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

DEFAULT_RENDER_DPI = 200
# 缺渲染后端时给人看的唯一动作；reader 侧原样写进 ocr.failed_reason。
INSTALL_HINT = "pip install 'envoy-cli[pdf-render]'"
# 渲染出来的 PNG 必须能独立被 OCR 后端读，先挡住明显的坏数据。
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class PdfRenderUnavailable(RuntimeError):
    """没有任何可用渲染后端；``install_hint`` 是可直接执行的那条命令。"""

    def __init__(self, install_hint: str = INSTALL_HINT, *, reason: str = "") -> None:
        detail = reason or "no PDF rendering backend is available"
        super().__init__(f"{detail}; install one with: {install_hint}")
        self.install_hint = install_hint
        self.reason = detail


def _pdfium_version() -> str:
    """pdfium 版本必须真实：它会进 OCR 缓存键，写错会让旧图像被当成新图复用。"""

    try:
        from pypdfium2.version import PYPDFIUM_INFO  # type: ignore[import-not-found]

        return str(getattr(PYPDFIUM_INFO, "version", None) or PYPDFIUM_INFO)
    except Exception:  # pragma: no cover - 仅在 pypdfium2 版本布局变化时走到
        try:
            return importlib.metadata.version("pypdfium2")
        except importlib.metadata.PackageNotFoundError:
            return "unknown"


def _pillow_available() -> bool:
    return importlib.util.find_spec("PIL") is not None


def _render_with_pdfium(pdf_bytes: bytes, page_number: int, dpi: int) -> tuple[bytes, dict[str, Any]]:
    try:
        import pypdfium2 as pdfium  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - _resolve_backend 已先探测
        raise PdfRenderUnavailable(reason="pypdfium2 is not installed") from exc
    if not _pillow_available():
        # pypdfium2 只给位图缓冲，PNG 编码还需要 Pillow；两者同属 pdf-render extra。
        raise PdfRenderUnavailable(reason="Pillow is required to encode rendered pages as PNG")
    document = pdfium.PdfDocument(pdf_bytes)
    try:
        page = document[page_number - 1]
        # PDF 用户空间是 72 dpi，pypdfium2 的 scale 是相对它而言的。
        bitmap = page.render(scale=dpi / 72.0)
        image = bitmap.to_pil()
        try:
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            png = buffer.getvalue()
            size = image.size
        finally:
            image.close()
        return png, {"width": int(size[0]), "height": int(size[1])}
    finally:
        document.close()


def _run_cli(command: list[str], *, timeout: float = 60.0) -> None:
    completed = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()[:300]
        raise RuntimeError(f"{Path(command[0]).name} failed: {detail or 'no stderr'}")


def _render_with_pdftoppm(pdf_bytes: bytes, page_number: int, dpi: int) -> tuple[bytes, dict[str, Any]]:
    executable = shutil.which("pdftoppm")
    if executable is None:  # pragma: no cover - _resolve_backend 已先探测
        raise PdfRenderUnavailable(reason="pdftoppm is not installed")
    # 中间文件只活在这次调用的临时目录里，不污染用户目录也不留缓存。
    with tempfile.TemporaryDirectory(prefix="envoy-pdf-render-") as tmp:
        root = Path(tmp)
        source = root / "page.pdf"
        source.write_bytes(pdf_bytes)
        target = root / "page"
        _run_cli([
            executable,
            "-r", str(int(dpi)),
            "-f", str(int(page_number)),
            "-l", str(int(page_number)),
            "-png",
            "-singlefile",
            str(source),
            str(target),
        ])
        png = target.with_suffix(".png").read_bytes()
    return png, {"width": None, "height": None}


def _render_with_mutool(pdf_bytes: bytes, page_number: int, dpi: int) -> tuple[bytes, dict[str, Any]]:
    executable = shutil.which("mutool")
    if executable is None:  # pragma: no cover - _resolve_backend 已先探测
        raise PdfRenderUnavailable(reason="mutool is not installed")
    with tempfile.TemporaryDirectory(prefix="envoy-pdf-render-") as tmp:
        root = Path(tmp)
        source = root / "page.pdf"
        source.write_bytes(pdf_bytes)
        target = root / "page.png"
        _run_cli([
            executable,
            "draw",
            "-r", str(int(dpi)),
            "-o", str(target),
            str(source),
            str(int(page_number)),
        ])
        png = target.read_bytes()
    return png, {"width": None, "height": None}


_Renderer = Callable[[bytes, int, int], tuple[bytes, dict[str, Any]]]


def _resolve_backend() -> tuple[str, str, _Renderer]:
    """按契约顺序选后端：pypdfium2 → pdftoppm → mutool → 抛安装提示。"""

    if importlib.util.find_spec("pypdfium2") is not None and _pillow_available():
        return "pypdfium2", _pdfium_version(), _render_with_pdfium
    if shutil.which("pdftoppm"):
        return "pdftoppm", _cli_version("pdftoppm"), _render_with_pdftoppm
    if shutil.which("mutool"):
        return "mutool", _cli_version("mutool"), _render_with_mutool
    raise PdfRenderUnavailable()


def _cli_version(executable: str) -> str:
    path = shutil.which(executable)
    if path is None:
        return "unknown"
    try:
        completed = subprocess.run(
            [path, "-v"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    # 两类工具都把版本打到 stderr/stdout 的首行，例如 "pdftoppm version 24.02.0"。
    first = completed.stdout.decode("utf-8", "replace").strip().splitlines()
    if not first:
        return "unknown"
    text = first[0].strip()
    return text.split("version")[-1].strip() if "version" in text else text


def render_backend_status() -> dict[str, Any]:
    """渲染能力现状；用于 MISSING_CAPABILITY 消息与测试，不触发渲染。"""

    try:
        backend, version, _ = _resolve_backend()
    except PdfRenderUnavailable as exc:
        return {
            "available": False,
            "backend": None,
            "backend_version": None,
            "install_hint": exc.install_hint,
            "reason": exc.reason,
        }
    return {
        "available": True,
        "backend": backend,
        "backend_version": version,
        "install_hint": INSTALL_HINT,
        "reason": "",
    }


def render_page_to_png(
    pdf_bytes: bytes,
    page_number: int,
    *,
    dpi: int = DEFAULT_RENDER_DPI,
) -> tuple[bytes, dict[str, Any]]:
    """渲染第 ``page_number``（1 起）页，返回 ``(png_bytes, info)``。

    ``info`` 含 ``backend`` / ``backend_version`` / ``dpi`` / ``sha256`` / 像素尺寸，
    调用方把前四项写进 OCR 缓存键，避免换后端或换 dpi 后复用旧图。
    """

    if not pdf_bytes or not pdf_bytes[:5].startswith(b"%PDF-"):
        raise ValueError("render_page_to_png expects PDF bytes")
    if int(page_number) < 1:
        raise ValueError("page_number is 1-based and must be >= 1")
    resolved_dpi = max(36, min(int(dpi), 600))
    backend, backend_version, renderer = _resolve_backend()
    png, geometry = renderer(pdf_bytes, int(page_number), resolved_dpi)
    if not png.startswith(_PNG_MAGIC):
        raise RuntimeError(f"{backend} returned data that is not a PNG")
    info: dict[str, Any] = {
        "backend": backend,
        "backend_version": backend_version,
        "dpi": resolved_dpi,
        "page_number": int(page_number),
        "sha256": hashlib.sha256(png).hexdigest(),
        "bytes": len(png),
        "width": geometry.get("width"),
        "height": geometry.get("height"),
    }
    return png, info
