# Markdown-First Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Markdown-first export pipeline for long PDF, video, long text, and multi-file text inputs that produces a main document plus chunk documents in a user-selected output directory, keeps UTF-8-safe output and stable naming, and uses Ollama only as optional enhancement.

**Architecture:** Extend the existing train export and preprocess pipeline with a source-aware Markdown document model, deterministic chunking, and a fixed export directory layout. Keep the base Markdown path fully local and deterministic, then add optional Ollama enhancement passes that can fail or timeout without blocking output. Simplify the frontend export flow into a document-style workbench that emphasizes Markdown output and output-directory control instead of card-heavy selection UI.

**Tech Stack:** FastAPI, SQLAlchemy, Celery job flow, local filesystem exports, Next.js App Router, TypeScript, inline style system plus `frontend/styles/globals.css`, optional local Ollama HTTP API.

---

### Task 1: Lock Markdown Export Contract With Tests

**Files:**
- Modify: `backend/tests/test_long_video_rag_pipeline.py`
- Create: `backend/tests/test_markdown_export_pipeline.py`
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Write the failing tests**

```python
def test_markdown_export_writes_overview_index_and_chunk_tree(tmp_path):
    rows = [
        {
            "record_type": "semantic_unit",
            "id": "unit-1",
            "project_id": "project-1",
            "asset_id": "asset-1",
            "unit_type": "document",
            "title": "第一章 项目背景",
            "content": "# 第一章 项目背景\n\n这是正文。",
            "labels": {
                "source": "document_text",
                "task_id": "task-1",
                "source_name": "项目说明书.pdf",
                "section_path": ["第一章 项目背景"],
                "page_range": [1, 3],
            },
            "confidence": 0.91,
        }
    ]
    result = export_markdown_bundle(
        rows,
        out_dir=str(tmp_path),
        task_id="task-1",
        title="project-brief",
        task_name="project-brief",
        project_id="project-1",
    )
    assert Path(result["paths"]["index"]).is_file()
    assert Path(result["paths"]["overview"]).is_file()
    assert Path(result["paths"]["manifest"]).is_file()
    chunk_paths = result["paths"]["chunks"]
    assert len(chunk_paths) == 1
    chunk_text = Path(chunk_paths[0]).read_text(encoding="utf-8")
    assert "# 第一章 项目背景" in chunk_text
    assert "来源：项目说明书.pdf" in chunk_text
    assert "引用：第 1-3 页" in chunk_text
```

```python
def test_markdown_export_records_partial_status_and_ollama_skip(tmp_path):
    rows = [
        {
            "record_type": "semantic_unit",
            "id": "unit-video",
            "project_id": "project-1",
            "asset_id": "asset-video",
            "unit_type": "scene",
            "title": "视频片段 1",
            "content": json.dumps(
                {
                    "transcript": {"text": "可用转写"},
                    "ocr": [{"text": "幻灯片标题"}],
                    "extraction_status": "partial_success",
                    "degraded_capabilities": ["partial_transcript"],
                    "transcription_failed_segments": [{"segment": "seg-2", "error": "timeout"}],
                },
                ensure_ascii=False,
            ),
            "labels": {
                "source": "media_asset",
                "task_id": "task-video",
                "source_name": "演示视频.mp4",
                "time_range": ["00:00:00", "00:05:00"],
                "extraction_status": "partial_success",
                "degraded_capabilities": ["partial_transcript"],
                "transcription_failed_count": 1,
            },
            "confidence": 0.8,
        }
    ]
    result = export_markdown_bundle(
        rows,
        out_dir=str(tmp_path),
        task_id="task-video",
        title="video-brief",
        task_name="video-brief",
        project_id="project-1",
        enhance_with_ollama=False,
    )
    manifest = json.loads(Path(result["paths"]["manifest"]).read_text(encoding="utf-8"))
    assert manifest["partial_chunk_count"] == 1
    assert manifest["failure_breakdown"]["transcription_failed_segments"] == 1
    assert manifest["ollama"]["enabled"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=backend backend/.venv/bin/pytest backend/tests/test_markdown_export_pipeline.py -q`
Expected: FAIL because `export_markdown_bundle` and the Markdown directory layout do not exist yet.

- [ ] **Step 3: Keep the long-video regression test aligned**

```python
def test_preprocess_video_marks_partial_success_and_persists_clean_semantics(...):
    ...
    assert summary.labels["extraction_status"] == "partial_success"
    assert summary.labels["degraded_capabilities"] == ["partial_transcript"]
```

- [ ] **Step 4: Run the existing regression test**

Run: `PYTHONPATH=backend backend/.venv/bin/pytest backend/tests/test_long_video_rag_pipeline.py -q`
Expected: PASS to confirm the current long-video behavior stays stable before the Markdown refactor.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_markdown_export_pipeline.py backend/tests/test_long_video_rag_pipeline.py backend/requirements.txt
git commit -m "test: lock markdown export contract"
```

### Task 2: Implement Source-Aware Markdown Export Pipeline

**Files:**
- Modify: `backend/app/services/train_export_service.py`
- Modify: `backend/app/services/export_paths.py`
- Modify: `backend/app/services/export_service.py`
- Modify: `backend/app/api/v1/train_exports.py`
- Modify: `backend/app/api/v1/artifacts.py`
- Test: `backend/tests/test_markdown_export_pipeline.py`

- [ ] **Step 1: Write the failing test for directory layout helpers**

```python
def test_markdown_export_uses_fixed_directory_layout(tmp_path):
    root = build_export_root(str(tmp_path), "markdown", "task-1", "中文任务")
    bundle = build_markdown_bundle_paths(root, "01-pdf-xiang-mu-shuo-ming")
    assert bundle["overview"].name == "00-project-overview.md"
    assert bundle["source"].name == "source.md"
    assert bundle["chunks_dir"].name == "chunks"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=backend backend/.venv/bin/pytest backend/tests/test_markdown_export_pipeline.py::test_markdown_export_uses_fixed_directory_layout -q`
Expected: FAIL because `build_markdown_bundle_paths` does not exist.

- [ ] **Step 3: Implement path builders and Markdown bundle export**

```python
def build_markdown_bundle_paths(root: Path, source_dir_name: str) -> dict[str, Path]:
    overview_dir = build_subdir(root, "overview")
    sources_dir = build_subdir(root, "sources")
    source_root = build_subdir(sources_dir, source_dir_name)
    return {
        "index": root / "index.md",
        "overview": overview_dir / "00-project-overview.md",
        "source_root": source_root,
        "source": source_root / "source.md",
        "chunks_dir": build_subdir(source_root, "chunks"),
        "manifest": root / "manifest.json",
    }
```

```python
def export_markdown_bundle(...):
    clean_rows = _dedupe_rows(rows)
    markdown_sources = _group_rows_by_source(clean_rows)
    paths = ...
    write index.md
    write overview markdown
    write source.md for each source
    write chunk markdown files with UTF-8 encoding
    write manifest.json with chunk index, source map, failure counts, ollama state
    _finalize_export(...)
    return {"paths": {...}, "count": len(chunk_paths)}
```

- [ ] **Step 4: Route Markdown exports through the new bundle implementation**

```python
if payload.export_type == "markdown":
    return export_markdown_bundle(
        samples,
        out_dir,
        task_id,
        payload.title or "Markdown Bundle",
        payload.task_name,
        db=db,
        project_id=project_id,
    )
```

- [ ] **Step 5: Run focused Markdown tests**

Run: `PYTHONPATH=backend backend/.venv/bin/pytest backend/tests/test_markdown_export_pipeline.py -q`
Expected: PASS

- [ ] **Step 6: Re-run long-video regression**

Run: `PYTHONPATH=backend backend/.venv/bin/pytest backend/tests/test_long_video_rag_pipeline.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/train_export_service.py backend/app/services/export_paths.py backend/app/services/export_service.py backend/app/api/v1/train_exports.py backend/app/api/v1/artifacts.py backend/tests/test_markdown_export_pipeline.py
git commit -m "feat: add markdown-first export bundle"
```

### Task 3: Add Mixed Chunking for PDF, Video, Long Text, and Multi-File Text

**Files:**
- Modify: `backend/app/services/train_export_service.py`
- Modify: `backend/app/workers/jobs.py`
- Test: `backend/tests/test_markdown_export_pipeline.py`

- [ ] **Step 1: Write the failing tests for mixed chunking**

```python
def test_markdown_chunking_preserves_heading_boundary_before_length_split():
    row = {
        "title": "第二章 方案设计",
        "content": "# 第二章 方案设计\n\n" + ("A" * 9000),
        "labels": {
            "source": "document_text",
            "section_path": ["第二章 方案设计"],
            "source_name": "方案.pdf",
        },
    }
    chunks = build_markdown_chunks([row], target_chars=3000, max_chars=4000)
    assert len(chunks) >= 2
    assert all(chunk["title"] for chunk in chunks)
    assert all(chunk["summary"] for chunk in chunks)
    assert all("第二章 方案设计" in chunk["section_path"][-1] for chunk in chunks)
```

```python
def test_multi_text_files_are_split_by_file_then_heading():
    rows = [...]
    chunks = build_markdown_chunks(rows, target_chars=3000, max_chars=4000)
    assert chunks[0]["source_name"] == "a.md"
    assert chunks[-1]["source_name"] == "b.txt"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=backend backend/.venv/bin/pytest backend/tests/test_markdown_export_pipeline.py -q`
Expected: FAIL because mixed chunk builders do not exist yet.

- [ ] **Step 3: Implement deterministic chunk builders**

```python
def build_markdown_chunks(rows, target_chars: int, max_chars: int):
    structured = []
    for row in rows:
        structured.extend(_split_row_by_source_structure(row))
    final_chunks = []
    for block in structured:
        final_chunks.extend(_split_block_by_natural_boundaries(block, target_chars, max_chars))
    return final_chunks
```

```python
def _split_row_by_source_structure(row):
    source = _row_source(row)
    if source == "document_text":
        return _split_markdown_or_document_sections(row)
    if source in {"media_transcript", "media_asset"}:
        return _split_video_sections(row)
    return _split_text_sections(row)
```

- [ ] **Step 4: Ensure preprocess metadata feeds Markdown export**

```python
summary_labels = {
    "source_name": asset.file_name if asset else "uploaded file",
    "page_range": ...,
    "time_range": ...,
    "section_path": ...,
}
```

- [ ] **Step 5: Run tests**

Run: `PYTHONPATH=backend backend/.venv/bin/pytest backend/tests/test_markdown_export_pipeline.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/train_export_service.py backend/app/workers/jobs.py backend/tests/test_markdown_export_pipeline.py
git commit -m "feat: add mixed markdown chunking"
```

### Task 4: Add Optional Ollama Enhancement Without Blocking Base Export

**Files:**
- Modify: `backend/app/services/train_export_service.py`
- Modify: `backend/app/services/ollama_service.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/api/v1/health.py`
- Test: `backend/tests/test_markdown_export_pipeline.py`

- [ ] **Step 1: Write the failing tests for Ollama optional enhancement**

```python
def test_markdown_export_skips_ollama_when_disabled(tmp_path):
    result = export_markdown_bundle(..., enhance_with_ollama=False)
    manifest = json.loads(Path(result["paths"]["manifest"]).read_text(encoding="utf-8"))
    assert manifest["ollama"]["enabled"] is False
    assert manifest["ollama"]["chunk_enhancement_status"] == "skipped"
```

```python
def test_markdown_export_keeps_base_output_when_ollama_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(
        train_export_service,
        "generate_with_ollama",
        lambda *args, **kwargs: {"status": "failed", "text": "", "error": "timeout"},
    )
    result = export_markdown_bundle(..., enhance_with_ollama=True)
    assert Path(result["paths"]["overview"]).is_file()
    manifest = json.loads(Path(result["paths"]["manifest"]).read_text(encoding="utf-8"))
    assert manifest["ollama"]["chunk_enhancement_status"] == "failed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=backend backend/.venv/bin/pytest backend/tests/test_markdown_export_pipeline.py -q`
Expected: FAIL because manifest does not yet report enhancement state.

- [ ] **Step 3: Implement optional enhancement layer**

```python
def enhance_markdown_chunk(chunk: dict, enabled: bool, timeout_sec: int):
    if not enabled:
        return chunk, {"status": "skipped"}
    result = generate_with_ollama(...)
    if result.get("status") != "success":
        return chunk, {"status": "failed", "error": result.get("error")}
    return updated_chunk, {"status": "success"}
```

- [ ] **Step 4: Expose enhancement settings in config and health**

```python
MARKDOWN_ENHANCE_WITH_OLLAMA = _env_bool("MARKDOWN_ENHANCE_WITH_OLLAMA", False)
MARKDOWN_OLLAMA_TIMEOUT_SEC = _env_positive_int("MARKDOWN_OLLAMA_TIMEOUT_SEC", 90)
MARKDOWN_CHUNK_TARGET_CHARS = _env_positive_int("MARKDOWN_CHUNK_TARGET_CHARS", 3000)
MARKDOWN_CHUNK_MAX_CHARS = _env_positive_int("MARKDOWN_CHUNK_MAX_CHARS", 4500)
```

- [ ] **Step 5: Run tests**

Run: `PYTHONPATH=backend backend/.venv/bin/pytest backend/tests/test_markdown_export_pipeline.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/train_export_service.py backend/app/services/ollama_service.py backend/app/core/config.py backend/app/api/v1/health.py backend/tests/test_markdown_export_pipeline.py
git commit -m "feat: add optional ollama markdown enhancement"
```

### Task 5: Simplify Frontend Export Flow and Reduce Card Feel

**Files:**
- Modify: `frontend/app/page.tsx`
- Modify: `frontend/app/train-exports/page.tsx`
- Modify: `frontend/app/artifacts/[artifact_id]/page.tsx`
- Modify: `frontend/styles/globals.css`

- [ ] **Step 1: Add the failing UI expectation notes in code comments and layout checklist**

```tsx
// Markdown-first workbench requirements:
// - output directory field visible in primary flow
// - markdown export first, other outputs secondary
// - use section/list layout instead of card wall
```

- [ ] **Step 2: Replace card-wall sections with document-workbench sections**

```tsx
<section className="workbench-section">
  <header className="workbench-header">
    <h2>Markdown Export</h2>
    <p>主文档 + 分片文档，适合直接导入其他模型软件。</p>
  </header>
  ...
</section>
```

- [ ] **Step 3: Add output directory and enhancement controls to the primary export flow**

```tsx
<label>
  <span>输出目录</span>
  <input value={outputDir} onChange={...} />
</label>
<label>
  <input type="checkbox" checked={markdownEnhanceWithOllama} onChange={...} />
  <span>使用 Ollama 优化摘要与标题</span>
</label>
```

- [ ] **Step 4: Reduce card styling in globals**

```css
.workbench-section {
  border-top: 1px solid #e5e7eb;
  padding: 24px 0;
}

.surface-muted {
  background: transparent;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  box-shadow: none;
}
```

- [ ] **Step 5: Run frontend checks**

Run: `cd frontend && npm run lint`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/app/page.tsx frontend/app/train-exports/page.tsx frontend/app/artifacts/[artifact_id]/page.tsx frontend/styles/globals.css
git commit -m "feat: simplify markdown export workbench"
```

### Task 6: End-to-End Verification With Docker-Backed Services

**Files:**
- Modify: `backend/README.md`
- Modify: `README.md`
- Reuse: `docker-compose.yml`

- [ ] **Step 1: Write the verification script**

```bash
docker compose ps
docker exec envoy-redis redis-cli ping
docker exec envoy-mysql mysqladmin ping -h127.0.0.1 -uroot -ppassword
curl -fsS http://127.0.0.1:9000/minio/health/live >/dev/null
PYTHONPATH=backend backend/.venv/bin/pytest backend/tests/test_long_video_rag_pipeline.py backend/tests/test_markdown_export_pipeline.py -q
PYTHONPATH=backend backend/.venv/bin/python -c "import app.main; print('app.main import ok')"
```

- [ ] **Step 2: Add one deterministic local end-to-end Markdown export script**

```python
preprocess_result = jobs.preprocess_job.run({...})
export_result = export_markdown_bundle(...)
assert Path(export_result["paths"]["overview"]).is_file()
assert Path(export_result["paths"]["index"]).is_file()
assert manifest["partial_chunk_count"] >= 1
```

- [ ] **Step 3: Run full verification**

Run: the full Docker and Python verification commands above
Expected: all services healthy, tests pass, deterministic local end-to-end export succeeds

- [ ] **Step 4: Update docs**

```md
- Markdown-first export layout
- UTF-8 and naming guarantees
- Ollama enhancement is optional
- Recommended output directory usage
```

- [ ] **Step 5: Commit**

```bash
git add README.md backend/README.md
git commit -m "docs: document markdown-first export flow"
```
