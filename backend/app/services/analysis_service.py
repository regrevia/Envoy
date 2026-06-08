import json
from typing import Any

from app.services.ollama_service import generate_with_ollama


def _content_text(source: dict[str, Any] | None) -> str:
    source = source or {}
    if isinstance(source.get("text"), str):
        return source["text"]
    transcript = source.get("transcript")
    if isinstance(transcript, dict) and isinstance(transcript.get("text"), str):
        return transcript["text"]
    return json.dumps(source, ensure_ascii=False)


def _parse_llm_text(text: str) -> dict[str, Any]:
    if not text.strip():
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"summary": text}
    except json.JSONDecodeError:
        return {"summary": text}


def build_analysis_payload(source: dict[str, Any] | None, asset_type: str | None = None) -> dict[str, Any]:
    text = _content_text(source)
    fallback_summary = text[:240] if text else "暂无可分析内容"
    llm_result = generate_with_ollama(
        "请将输入素材整理为稳定可用的 AI 素材分析摘要，优先输出 JSON，字段包含 summary 和 labels。",
        text,
    )
    llm_payload = _parse_llm_text(llm_result.get("text", ""))
    llm_summary = llm_payload.get("summary") if llm_result.get("status") == "success" else None
    llm_labels = llm_payload.get("labels") if isinstance(llm_payload.get("labels"), list) else []

    labels = ["scene", "event", "rule", "concept", *(str(label) for label in llm_labels)]
    summary = llm_summary or fallback_summary

    return {
        "scenes": [
            {"scene_id": "scene_001", "start_ms": 0, "end_ms": 1000, "summary": summary},
        ],
        "events": [
            {"event_id": "event_001", "scene_id": "scene_001", "name": "素材要点"},
        ],
        "rules": [
            {"rule_id": "rule_001", "text": "保留来源、摘要和可追溯标签，生成内容必须可复核。"},
        ],
        "concepts": [
            {"concept_id": "concept_001", "name": asset_type or "general"},
        ],
        "questions": [
            {"question": "这段素材适合生成什么 AI 资产？", "answer": summary},
        ],
        "labels": list(dict.fromkeys(labels)),
        "confidence": 0.86 if llm_result.get("status") == "success" else 0.76,
        "stage": "analyze",
        "llm": {
            "enabled": llm_result.get("enabled", False),
            "status": llm_result.get("status", "disabled"),
            "summary": llm_summary or "",
            "error": llm_result.get("error"),
        },
    }
