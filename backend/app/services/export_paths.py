from __future__ import annotations

from pathlib import Path
from datetime import datetime
import re
from typing import Optional


def slugify_name(name: str) -> str:
    name = (name or '').strip().lower()
    name = re.sub(r'[^\w\u4e00-\u9fff-]+', '-', name)
    name = re.sub(r'-+', '-', name).strip('-')
    return name or 'default'


def build_export_root(base_dir: str, task_type: str, task_id: str, custom_name: Optional[str] = None):
    date_part = datetime.now().strftime('%Y%m%d')
    task_part = slugify_name(custom_name) if custom_name else slugify_name(task_type)
    root = Path(base_dir) / date_part / f'{task_part}_{task_id}'
    root.mkdir(parents=True, exist_ok=True)
    return root


def build_subdir(root: Path, kind: str):
    path = root / kind
    path.mkdir(parents=True, exist_ok=True)
    return path
