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

    def test_upload_page_shows_full_conversion_flow_without_card_shell(self):
        page = ROOT_DIR / "frontend" / "app" / "upload" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "多媒体转换任务",
            "1. 确认任务/项目",
            "2. 上传素材",
            "3. 选择生成内容",
            "AI 素材",
            "Markdown",
            "Skill",
            "RAG",
            "训练样本",
            "视频/音频/图片/文档/长文本",
            "预处理转换",
            "查看转换任务",
            "查看语义单元",
            "训练导出",
        ]:
            self.assertIn(copy, text)

        self.assertIn("borderTop: '1px solid #e5e7eb'", text)
        self.assertIn("borderBottom: '1px solid #e5e7eb'", text)
        self.assertNotIn("panelStyle", text)
        self.assertNotIn("borderRadius: 8", text)
        self.assertNotIn("上传响应", text)

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

    def test_home_page_explains_complete_conversion_chain(self):
        page = ROOT_DIR / "frontend" / "app" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "完整转换链路",
            "上传素材 -> 预处理转换 -> 语义单元审查 -> 生成训练样本 -> 自动导出 AI 素材 -> 追溯审查",
            "预处理转换",
            "长任务诊断",
            "自动导出 AI 素材",
            "产物追溯",
            "查看产物中心",
            "查看追溯链路",
            "href=\"/artifacts\"",
            "href=\"/trace\"",
        ]:
            self.assertIn(copy, text)

    def test_home_page_uses_vertical_divider_flow_not_card_grid(self):
        page = ROOT_DIR / "frontend" / "app" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        self.assertIn("gridTemplateColumns: '1fr'", text)
        self.assertIn("borderBottom: '1px solid #e5e7eb'", text)
        self.assertNotIn("repeat(3, minmax(0, 1fr))", text)
        self.assertNotIn("borderRadius: 12", text)

    def test_projects_page_uses_divider_layout_over_cards(self):
        page = ROOT_DIR / "frontend" / "app" / "projects" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "项目工作台",
            "创建转换项目",
            "项目列表",
            "视频、长文本和多模态素材",
            "上传素材",
            "训练导出",
            "追溯审查",
            "borderTop: '1px solid #e5e7eb'",
            "borderBottom: '1px solid #e5e7eb'",
        ]:
            self.assertIn(copy, text)

        self.assertNotIn("panelStyle", text)
        self.assertNotIn("borderRadius: 8", text)

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

    def test_train_exports_page_uses_core_chain_divider_layout(self):
        page = ROOT_DIR / "frontend" / "app" / "train-exports" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "从语义单元到 AI 素材",
            "1. 确认来源",
            "2. 选择产物",
            "3. 导出并审查",
            "查看产物来源统计",
            "Markdown",
            "Skill",
            "RAG",
            "自动读取导出",
            "borderTop: '1px solid #e5e7eb'",
            "borderBottom: '1px solid #e5e7eb'",
        ]:
            self.assertIn(copy, text)

        self.assertNotIn("panelStyle", text)
        self.assertNotIn("borderRadius: 8", text)

    def test_train_export_detail_uses_divider_layout(self):
        page = ROOT_DIR / "frontend" / "app" / "train-exports" / "[record_id]" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "导出记录详情",
            "返回训练导出",
            "文件列表",
            "记录 ID",
            "项目 ID",
            "任务 ID",
            "borderTop: '1px solid #e5e7eb'",
            "borderBottom: '1px solid #e5e7eb'",
        ]:
            self.assertIn(copy, text)

        self.assertNotIn("panelStyle", text)
        self.assertNotIn("borderRadius: 8", text)

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

    def test_task_pages_expose_retry_action_for_failed_work(self):
        task_list = (ROOT_DIR / "frontend" / "app" / "tasks" / "page.tsx").read_text(encoding="utf-8")
        task_detail = (ROOT_DIR / "frontend" / "app" / "tasks" / "[task_id]" / "page.tsx").read_text(encoding="utf-8")

        for text in [task_list, task_detail]:
            self.assertIn("/retry", text)
            self.assertIn("重试任务", text)
            self.assertIn("正在重试", text)

    def test_task_pages_use_divider_layout_over_cards(self):
        task_list = (ROOT_DIR / "frontend" / "app" / "tasks" / "page.tsx").read_text(encoding="utf-8")
        task_detail = (ROOT_DIR / "frontend" / "app" / "tasks" / "[task_id]" / "page.tsx").read_text(encoding="utf-8")

        for text in [task_list, task_detail]:
            self.assertIn("borderTop: '1px solid #e5e7eb'", text)
            self.assertIn("borderBottom: '1px solid #e5e7eb'", text)
            self.assertNotIn("panelStyle", text)
            self.assertNotIn("borderRadius: 8", text)

    def test_task_pages_expose_long_task_diagnostics(self):
        task_list = (ROOT_DIR / "frontend" / "app" / "tasks" / "page.tsx").read_text(encoding="utf-8")
        task_detail = (ROOT_DIR / "frontend" / "app" / "tasks" / "[task_id]" / "page.tsx").read_text(encoding="utf-8")

        for text in [task_list, task_detail]:
            for copy in [
                "长任务诊断",
                "自动刷新",
                "卡住阈值",
                "未更新时间",
                "疑似卡住",
                "取消任务",
                "重试任务",
                "stale_after_seconds",
                "stale_seconds",
            ]:
                self.assertIn(copy, text)

    def test_artifact_detail_exposes_manifest_quality_metadata(self):
        page = (ROOT_DIR / "frontend" / "app" / "artifacts" / "[artifact_id]" / "page.tsx").read_text(encoding="utf-8")

        for copy in [
            "质量元数据",
            "素材来源统计",
            "语义类型统计",
            "视频转写",
            "画面 OCR",
            "长文本",
            "Ollama 状态",
            "Ollama 截断",
            "提示词长度",
            "发送长度",
            "AI 摘要",
            "用途",
            "风险",
            "Manifest 路径",
            "manifest",
            "source_counts",
            "unit_type_counts",
            "llm_summary",
            "ollama_status",
            "ollama_truncated",
            "ollama_original_prompt_chars",
            "ollama_sent_prompt_chars",
            "ollama_max_prompt_chars",
        ]:
            self.assertIn(copy, page)

        self.assertIn("borderTop: '1px solid #e5e7eb'", page)
        self.assertIn("borderBottom: '1px solid #e5e7eb'", page)
        self.assertNotIn("panelStyle", page)
        self.assertNotIn("borderRadius: 8", page)

    def test_artifact_detail_links_to_trace_chain(self):
        page = (ROOT_DIR / "frontend" / "app" / "artifacts" / "[artifact_id]" / "page.tsx").read_text(encoding="utf-8")
        trace_page = (ROOT_DIR / "frontend" / "app" / "trace" / "page.tsx").read_text(encoding="utf-8")

        for copy in [
            "查看来源链路",
            "追溯审查",
            "href={`/trace?type=artifact&id=${artifact.id}`}",
            "useSearchParams",
            "searchParams.get('type')",
            "searchParams.get('id')",
            "setSourceType(typeFromQuery)",
            "setSourceId(idFromQuery)",
            "queryTrace(typeFromQuery, idFromQuery)",
            "已从链接带入追溯对象，正在自动查询",
        ]:
            self.assertTrue(copy in page or copy in trace_page, copy)

    def test_artifacts_page_uses_divider_layout_over_cards(self):
        page = ROOT_DIR / "frontend" / "app" / "artifacts" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "产物中心",
            "产物列表",
            "Markdown",
            "Skill",
            "RAG",
            "产物来源统计",
            "borderTop: '1px solid #e5e7eb'",
            "borderBottom: '1px solid #e5e7eb'",
        ]:
            self.assertIn(copy, text)

        self.assertNotIn("panelStyle", text)
        self.assertNotIn("borderRadius: 8", text)

    def test_generated_samples_page_uses_divider_layout_over_cards(self):
        page = ROOT_DIR / "frontend" / "app" / "generated-samples" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "生成样本",
            "样本列表",
            "语义单元 -> 生成样本 -> 训练导出",
            "来源语义单元",
            "输入",
            "输出",
            "borderTop: '1px solid #e5e7eb'",
            "borderBottom: '1px solid #e5e7eb'",
        ]:
            self.assertIn(copy, text)

        self.assertNotIn("panelStyle", text)
        self.assertNotIn("borderRadius: 8", text)

    def test_trace_page_uses_core_chain_divider_layout(self):
        page = ROOT_DIR / "frontend" / "app" / "trace" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "追溯审查",
            "素材 -> 语义单元 -> 生成样本 -> AI 产物",
            "来源类型",
            "来源 ID",
            "查询追溯",
            "追溯结果",
            "导出产物",
            "处理任务",
            "borderTop: '1px solid #e5e7eb'",
            "borderBottom: '1px solid #e5e7eb'",
        ]:
            self.assertIn(copy, text)

        self.assertNotIn("panelStyle", text)
        self.assertNotIn("borderRadius: 8", text)

    def test_trace_page_renders_readable_multihop_trace_rows(self):
        page = ROOT_DIR / "frontend" / "app" / "trace" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "链路层级",
            "上游来源",
            "下游去向",
            "直接关系",
            "第 {link.depth || 1} 层",
            "directionLabel",
            "relationTypeLabel",
            "source_type",
            "target_type",
            "depth",
            "direction",
        ]:
            self.assertIn(copy, text)


if __name__ == "__main__":
    unittest.main()
