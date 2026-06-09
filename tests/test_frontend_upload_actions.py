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
            "分片:",
            "创建:",
            "标签摘要",
            "segment_id",
            "created_at",
            "置信度",
        ]:
            self.assertIn(copy, text)
        self.assertNotIn("Semantic Units", text)
        self.assertNotIn("Generate sample", text)
        self.assertNotIn("Review samples", text)

    def test_semantic_units_page_points_back_to_home_task_creation(self):
        page = ROOT_DIR / "frontend" / "app" / "semantic-units" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "这是第 3 步",
            "先从首页创建转换任务",
            "返回首页创建转换任务",
            "创建转换任务",
            "上传素材",
            "选择生成内容",
            "href=\"/\"",
        ]:
            self.assertIn(copy, text)

    def test_home_page_starts_with_single_task_creation_flow(self):
        page = ROOT_DIR / "frontend" / "app" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "创建转换任务",
            "1. 创建任务/项目",
            "2. 上传素材",
            "3. 选择生成内容",
            "视频",
            "长文本",
            "多模态文件",
            "Markdown",
            "Skill",
            "RAG",
            "训练样本",
            "/api/v1/projects",
            "project_id=",
            "params.set('outputs'",
            "params.set('export_type'",
        ]:
            self.assertIn(copy, text)
        self.assertIn("task-wizard", text)
        self.assertIn("primary-workflow", text)
        self.assertNotIn("Workspace areas", text)
        self.assertNotIn("Envoy Workspace", text)
        self.assertNotIn("已有任务</div>", text)
        self.assertNotIn("已选择生成</div>", text)

    def test_home_page_uses_vertical_divider_flow_not_card_grid(self):
        page = ROOT_DIR / "frontend" / "app" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        self.assertIn("gridTemplateColumns: '1fr'", text)
        self.assertIn("borderBottom: '1px solid #e5e7eb'", text)
        self.assertNotIn("repeat(3, minmax(0, 1fr))", text)
        self.assertNotIn("borderRadius: 12", text)

    def test_train_exports_page_accepts_project_and_output_context_from_home(self):
        page = ROOT_DIR / "frontend" / "app" / "train-exports" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "URLSearchParams",
            "project_id",
            "export_type",
            "outputs",
            "setProjectId(projectFromQuery)",
            "setExportType(outputFromQuery)",
            "来自首页选择",
        ]:
            self.assertIn(copy, text)

    def test_train_exports_page_has_chinese_json_validation_feedback(self):
        page = ROOT_DIR / "frontend" / "app" / "train-exports" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "样本 JSON 格式错误",
            "请填写项目 ID 或来源上下文",
            "exporting",
            "autoExporting",
            "解析样本 JSON",
        ]:
            self.assertIn(copy, text)

    def test_home_page_exposes_ollama_runtime_status(self):
        page = ROOT_DIR / "frontend" / "app" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "/api/v1/health/ollama",
            "Ollama 本地模型",
            "模型",
            "可用",
            "未配置",
        ]:
            self.assertIn(copy, text)

    def test_task_pages_expose_cancel_action_for_long_running_work(self):
        task_list = (ROOT_DIR / "frontend" / "app" / "tasks" / "page.tsx").read_text(encoding="utf-8")
        task_detail = (ROOT_DIR / "frontend" / "app" / "tasks" / "[task_id]" / "page.tsx").read_text(encoding="utf-8")

        for text in [task_list, task_detail]:
            self.assertIn("/cancel", text)
            self.assertIn("取消任务", text)
            self.assertIn("已取消", text)


if __name__ == "__main__":
    unittest.main()
