from pathlib import Path
import json
from app.core.config import EXPORT_DIR


def ensure_export_dir(base_dir: str | None = None):
    base_dir = base_dir or str(EXPORT_DIR)
    path = Path(base_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_dataset_jsonl(rows: list[dict], out_path: str):
    ensure_export_dir(Path(out_path).parent.as_posix())
    with open(out_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return out_path


def build_markdown_report(title: str, content: str, out_path: str):
    ensure_export_dir(Path(out_path).parent.as_posix())
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n{content}\n")
    return out_path


def build_rich_export_report(title: str, summary: dict, semantic_units: list[dict], samples: list[dict], trace_links: list[dict], out_path: str):
    ensure_export_dir(Path(out_path).parent.as_posix())
    content = [f"# {title}", "", "## 摘要", "", json.dumps(summary, ensure_ascii=False, indent=2), "", "## 语义单元", "", json.dumps(semantic_units, ensure_ascii=False, indent=2), "", "## 样本", "", json.dumps(samples, ensure_ascii=False, indent=2), "", "## 追溯关系", "", json.dumps(trace_links, ensure_ascii=False, indent=2)]
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(content) + "\n")
    return out_path
