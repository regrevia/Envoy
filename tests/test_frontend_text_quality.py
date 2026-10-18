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
            "frontend/app/video/page.tsx": ["视频转知识库", "最近任务", "English"],
            "frontend/app/projects/page.tsx": ["项目", "新建项目", "项目列表", "创建:"],
            "frontend/app/upload/page.tsx": ["上传素材", "选择任务/项目", "启动预处理"],
            "frontend/app/semantic-units/page.tsx": ["素材转换工作台", "语义单元", "生成样本"],
            "frontend/app/tasks/page.tsx": ["处理进度", "处理列表", "进度", "项目:", "素材:", "无响应判定", "最后更新"],
            "frontend/app/tasks/[task_id]/page.tsx": ["任务详情", "状态", "进度", "项目 ID", "素材 ID", "输入配置", "创建时间", "更新时间"],
            "frontend/app/artifacts/page.tsx": ["交付中心", "交付列表", "来源任务:", "创建:"],
            "frontend/app/generated-samples/page.tsx": ["生成样本", "样本列表", "刷新"],
            "frontend/app/trace/page.tsx": ["追溯", "查询追溯", "追溯结果"],
            "frontend/app/train-exports/page.tsx": ["交付导出", "交付历史", "开始导出"],
            "frontend/app/artifacts/[artifact_id]/page.tsx": ["Markdown bundle 产物", "Markdown 目录树", "分片索引"],
            "frontend/app/train-exports/[record_id]/page.tsx": ["Markdown 目录树", "分片索引", "导出记录详情"],
        }
        offenders = []

        for relative_path, required_texts in expectations.items():
            text = (ROOT_DIR / relative_path).read_text(encoding="utf-8")
            missing = [item for item in required_texts if item not in text]
            if missing:
                offenders.append(f"{relative_path}: missing {', '.join(missing)}")

        self.assertEqual(offenders, [])

    def test_core_pages_follow_design_palette_and_font_stack(self):
        checked_pages = ["frontend/styles/globals.css"]
        offenders = []

        for relative_path in checked_pages:
            text = (ROOT_DIR / relative_path).read_text(encoding="utf-8")
            for required in [
                "SF Pro Text",
                "#1d1d1f",
                "#f5f5f7",
            ]:
                if required not in text:
                    offenders.append(f"{relative_path}: missing {required}")
            for banned in [
                "Inter, system-ui, sans-serif",
                "background: '#f8fafc'",
                "background: '#111827'",
            ]:
                if banned in text:
                    offenders.append(f"{relative_path}: contains {banned}")

        self.assertEqual(offenders, [])

    def test_primary_flow_uses_product_language_over_developer_terms(self):
        expectations = {
            "frontend/app/video/page.tsx": ["Markdown", "HTML", "知识库", "最近任务"],
            "frontend/app/upload/page.tsx": ["知识库文档", "可复用流程包", "检索语料包", "交付产物"],
            "frontend/app/train-exports/page.tsx": ["交付导出", "自动整理交付物", "交付历史", "查看交付中心"],
            "frontend/app/tasks/page.tsx": ["处理进度", "处理列表", "无响应判定"],
            "frontend/app/artifacts/page.tsx": ["交付中心", "交付列表", "下载"],
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
