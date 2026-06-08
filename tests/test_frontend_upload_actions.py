import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


class FrontendUploadActionsTests(unittest.TestCase):
    def test_upload_page_exposes_preprocess_action_after_upload(self):
        page = ROOT_DIR / "frontend" / "app" / "upload" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        self.assertIn("/api/v1/tasks/preprocess", text)
        self.assertIn("启动预处理", text)

    def test_upload_page_accepts_project_context_from_home(self):
        page = ROOT_DIR / "frontend" / "app" / "upload" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        self.assertIn("project_id", text)
        self.assertIn("URLSearchParams", text)
        for copy in [
            "上传素材",
            "选择任务/项目",
            "素材类型",
            "文件",
            "启动预处理",
            "视频",
            "音频",
            "文档",
            "图片",
        ]:
            self.assertIn(copy, text)
        self.assertNotIn(">Upload<", text)
        self.assertNotIn("Upload asset", text)
        self.assertNotIn("Choose project", text)

    def test_semantic_units_page_exposes_generate_sample_action(self):
        page = ROOT_DIR / "frontend" / "app" / "semantic-units" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        self.assertIn("/api/v1/tasks/generate-samples", text)
        self.assertIn("生成样本", text)

    def test_semantic_units_page_uses_dividers_over_cards(self):
        page = ROOT_DIR / "frontend" / "app" / "semantic-units" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        self.assertIn("borderTop: '1px solid #e5e7eb'", text)
        self.assertNotIn("panelStyle", text)
        self.assertNotIn("borderRadius: 8", text)

    def test_semantic_units_page_is_chinese_first_product_workspace(self):
        page = ROOT_DIR / "frontend" / "app" / "semantic-units" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "素材转换工作台",
            "多媒体/长文本",
            "预处理转换",
            "语义单元",
            "生成样本",
            "Markdown",
            "Skill",
            "RAG",
            "上传素材",
            "转换任务",
            "训练导出",
            "项目:",
            "素材:",
            "置信度:",
        ]:
            self.assertIn(copy, text)
        self.assertNotIn("Semantic Units", text)
        self.assertNotIn("Generate sample", text)
        self.assertNotIn("Review samples", text)

    def test_home_page_is_task_flow_not_module_directory(self):
        page = ROOT_DIR / "frontend" / "app" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "新建转换任务",
            "1. 创建任务/项目",
            "2. 上传素材",
            "3. 选择生成内容",
            "Markdown",
            "Skill",
            "RAG",
            "训练样本",
            "/api/v1/projects",
            "project_id=",
        ]:
            self.assertIn(copy, text)
        self.assertNotIn("Workspace areas", text)
        self.assertNotIn("Envoy Workspace", text)

    def test_home_page_uses_vertical_task_flow(self):
        page = ROOT_DIR / "frontend" / "app" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        self.assertIn("task-flow", text)
        self.assertIn("gridTemplateColumns: '1fr'", text)
        self.assertNotIn("repeat(3, minmax(0, 1fr))", text)


if __name__ == "__main__":
    unittest.main()
