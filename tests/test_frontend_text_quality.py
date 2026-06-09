import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


class FrontendTextQualityTests(unittest.TestCase):
    def test_frontend_source_has_no_mojibake_copy(self):
        checked_roots = [ROOT_DIR / "frontend" / "app"]
        suspicious_fragments = [
            "鎽",
            "璇",
            "鐢",
            "浜",
            "绗",
            "椤",
            "杩",
            "鏍",
            "鍒",
            "鍔",
            "鏌",
            "鏆",
            "瀵",
            "绱",
            "浠",
            "瑙",
            "丼kill",
            "丷AG",
        ]
        offenders = []

        for root in checked_roots:
            for path in root.rglob("*.tsx"):
                text = path.read_text(encoding="utf-8")
                matches = [fragment for fragment in suspicious_fragments if fragment in text]
                if matches:
                    offenders.append(f"{path.relative_to(ROOT_DIR)}: {', '.join(matches)}")

        self.assertEqual(offenders, [])

    def test_core_workflow_pages_are_chinese_first(self):
        expectations = {
            "frontend/app/projects/page.tsx": ["项目", "新建项目", "项目列表"],
            "frontend/app/tasks/page.tsx": ["任务", "任务列表", "进度"],
            "frontend/app/train-exports/page.tsx": ["训练导出", "导出历史", "开始导出"],
        }
        offenders = []

        for relative_path, required_texts in expectations.items():
            text = (ROOT_DIR / relative_path).read_text(encoding="utf-8")
            missing = [item for item in required_texts if item not in text]
            if missing:
                offenders.append(f"{relative_path}: missing {', '.join(missing)}")

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
