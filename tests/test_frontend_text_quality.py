import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


class FrontendTextQualityTests(unittest.TestCase):
    def test_frontend_source_has_no_mojibake_copy(self):
        checked_roots = [ROOT_DIR / "frontend" / "app"]
        suspicious_fragments = [
            "鍏",
            "垱",
            "姘",
            "姝",
            "涓",
            "绱",
            "璇",
            "鐞",
            "鐭",
            "閫",
            "濂",
            "€",
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
            "frontend/app/page.tsx": ["创建转换任务", "上传素材", "选择生成内容"],
            "frontend/app/projects/page.tsx": ["项目", "新建项目", "项目列表", "创建:"],
            "frontend/app/upload/page.tsx": ["上传素材", "选择任务/项目", "启动预处理"],
            "frontend/app/semantic-units/page.tsx": ["素材转换工作台", "语义单元", "生成样本"],
            "frontend/app/tasks/page.tsx": ["任务", "任务列表", "进度", "项目:", "素材:"],
            "frontend/app/tasks/[task_id]/page.tsx": ["任务详情", "状态", "进度", "项目 ID", "素材 ID", "输入配置", "创建时间", "更新时间"],
            "frontend/app/artifacts/page.tsx": ["产物中心", "产物列表", "项目:", "创建:"],
            "frontend/app/generated-samples/page.tsx": ["生成样本", "样本列表", "刷新"],
            "frontend/app/trace/page.tsx": ["追溯", "查询追溯", "追溯结果"],
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
