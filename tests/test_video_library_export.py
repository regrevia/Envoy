import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def test_video_library_is_theme_first_and_preserves_multi_video_evidence(tmp_path):
    from app.services.train_export_service import export_video_library

    evidence_rows = [
        {
            "id": "unit-video-a",
            "project_id": "project",
            "asset_id": "asset-a",
            "unit_type": "transcript",
            "title": "磨石位置",
            "content": "石磨在村口，向东走到桥边。",
            "labels": {
                "source": "media_transcript",
                "video_id": "video-a",
                "video_title": "视频 A",
                "video_url": "https://example.test/a",
            },
        },
        {
            "id": "unit-video-b",
            "project_id": "project",
            "asset_id": "asset-b",
            "unit_type": "video_understanding",
            "title": "石磨地图",
            "content": "画面地图标出村口石磨位置。",
            "labels": {
                "source": "video_understanding",
                "video_id": "video-b",
                "video_title": "视频 B",
                "video_url": "https://example.test/b",
            },
        },
    ]
    videos = [
        {"video_id": "video-a", "title": "视频 A", "source_url": "https://example.test/a", "status": "success", "evidence_count": 1},
        {"video_id": "video-b", "title": "视频 B", "source_url": "https://example.test/b", "status": "success", "evidence_count": 1},
    ]
    result = export_video_library(
        evidence_rows=evidence_rows,
        video_rows=videos,
        out_dir=str(tmp_path),
        library_id="library-one",
        title="神之天平攻略",
        task_id="batch-task",
        project_id="project",
        topic_result={
            "status": "success",
            "topics": [{
                "title": "石磨位置与路线",
                "summary": "两个视频都指向村口桥边。",
                "points": [{
                    "text": "石磨位于村口桥边，地图和口播相互印证。",
                    "evidence_ids": ["unit-video-a", "unit-video-b"],
                }],
            }],
        },
    )

    root = Path(result["root"])
    index = Path(result["path"]).read_text(encoding="utf-8")
    theme_path = next((root / "themes").glob("*.md"))
    theme = theme_path.read_text(encoding="utf-8")
    evidence_index = [
        json.loads(line)
        for line in (root / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert "[石磨位置与路线](themes/" in index
    assert "unit-video-a" in theme
    assert "unit-video-b" in theme
    assert "https://example.test/a" in theme
    assert "https://example.test/b" in theme
    assert len(evidence_index) == 2
    assert "石磨位于村口桥边" in theme
    assert all((root / "evidence").glob("*.md"))


def test_video_library_finalization_accepts_path_objects(tmp_path, monkeypatch):
    from app.services import train_export_service

    seen = {}

    def capture_artifacts(_db, _project_id, _artifact_type, paths):
        seen["names"] = [path.name for path in paths]
        return []

    monkeypatch.setattr(train_export_service, "_write_record", lambda *args: None)
    monkeypatch.setattr(train_export_service, "_write_artifacts", capture_artifacts)
    monkeypatch.setattr(train_export_service, "_write_artifact_trace_links", lambda *args: None)

    result = train_export_service.export_video_library(
        evidence_rows=[],
        video_rows=[],
        out_dir=str(tmp_path),
        library_id="library-finalize",
        title="视频知识库",
        task_id="task-finalize",
        project_id="project",
        db=object(),
    )

    assert "index.md" in seen["names"]
    assert result["path"].endswith("index.md")
