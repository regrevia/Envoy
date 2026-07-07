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

    def test_upload_page_filters_query_outputs_to_supported_material_types(self):
        page = ROOT_DIR / "frontend" / "app" / "upload" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "const outputOptionIds = new Set(outputOptions.map((option) => option.id));",
            "filter((item) => outputOptionIds.has(item))",
            "Array.from(new Set(",
        ]:
            self.assertIn(copy, text)

    def test_home_page_passes_output_preferences_into_upload_flow(self):
        page = ROOT_DIR / "frontend" / "app" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "const uploadOutputsParam = selectedOutputs.join(',');",
            "const uploadHref = canProceed",
            "asset_type=${encodeURIComponent(selectedAssetType)}",
            "&outputs=${encodeURIComponent(uploadOutputsParam)}",
            "素材类型",
            "setSelectedAssetType",
        ]:
            self.assertIn(copy, text)

    def test_upload_page_accepts_asset_type_context_from_home(self):
        page = ROOT_DIR / "frontend" / "app" / "upload" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "const fileTypeOptionIds = new Set(fileTypeOptions.map((option) => option.id));",
            "const assetTypeFromQuery = params.get('asset_type');",
            "if (assetTypeFromQuery && fileTypeOptionIds.has(assetTypeFromQuery))",
            "setAssetType(assetTypeFromQuery);",
        ]:
            self.assertIn(copy, text)

    def test_upload_page_blocks_empty_files_before_api_request(self):
        page = ROOT_DIR / "frontend" / "app" / "upload" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "file.size === 0",
            "上传文件为空，请选择包含内容的素材",
            "setResult({ error: '上传文件为空，请选择包含内容的素材' })",
        ]:
            self.assertIn(copy, text)

    def test_upload_page_uses_extended_timeout_for_large_asset_uploads(self):
        page = ROOT_DIR / "frontend" / "app" / "upload" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "const uploadRequestTimeoutMs = 120000;",
            "timeoutMs: uploadRequestTimeoutMs",
        ]:
            self.assertIn(copy, text)

    def test_upload_page_shows_supported_asset_types_from_backend_contract(self):
        page = ROOT_DIR / "frontend" / "app" / "upload" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "支持类型：视频、音频、图片、文档、Markdown、长文本",
            "{ id: 'video', label: '视频' }",
            "{ id: 'audio', label: '音频' }",
            "{ id: 'image', label: '图片' }",
            "{ id: 'document', label: '文档' }",
            "{ id: 'markdown', label: 'Markdown' }",
            "{ id: 'text', label: '长文本' }",
        ]:
            self.assertIn(copy, text)
        self.assertNotIn("{ id: 'unknown'", text)

    def test_upload_page_shows_full_conversion_flow_without_card_shell(self):
        page = ROOT_DIR / "frontend" / "app" / "upload" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "上传素材并开始处理",
            "1. 确认任务/项目",
            "2. 上传素材",
            "3. 选择生成内容",
            "交付产物",
            "知识库文档",
            "可复用流程包",
            "检索语料包",
            "训练问答",
            "视频、音频、图片、文档和长文本",
            "启动预处理",
            "查看处理进度",
            "审查内容片段",
            "交付导出",
        ]:
            self.assertIn(copy, text)

        self.assertIn("borderTop: '1px solid #e5e7eb'", text)
        self.assertIn("borderBottom: '1px solid #e5e7eb'", text)
        self.assertNotIn("panelStyle", text)
        self.assertNotIn("borderRadius: 8", text)
        self.assertNotIn("上传响应", text)

    def test_upload_page_shows_readable_next_actions_after_preprocess(self):
        page = ROOT_DIR / "frontend" / "app" / "upload" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "下一步操作",
            "素材已上传",
            "预处理任务",
            "查看处理进度",
            "审查内容片段",
            "生成训练问答",
            "导出交付产物",
            "查看来源链路",
            "preprocessResult?.id",
            "`/tasks/${preprocessResult.id}`",
            "const semanticHref = assetProjectQuery ? `/semantic-units${assetProjectQuery}` : '/semantic-units';",
            "const samplesHref = assetProjectQuery ? `/generated-samples${assetProjectQuery}` : `/generated-samples${projectQuery}`;",
            "`/train-exports?project_id=${asset.project_id}",
            "`/trace?type=asset&id=${asset.id}`",
        ]:
            self.assertIn(copy, text)

        self.assertNotIn("JSON.stringify(result, null, 2)", text)
        self.assertNotIn("JSON.stringify(preprocessResult, null, 2)", text)

    def test_upload_page_keeps_samples_separate_from_material_export_type(self):
        page = ROOT_DIR / "frontend" / "app" / "upload" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "const materialOutputs = selectedOutputs.filter((item) => ['markdown', 'skill', 'rag'].includes(item));",
            "const primaryMaterialOutput = materialOutputs[0] || 'markdown';",
            "outputs=${encodeURIComponent(materialOutputParam)}",
            "export_type=${encodeURIComponent(primaryMaterialOutput)}",
            "const outputQueryParam = selectedOutputs.length ? `outputs=${encodeURIComponent(selectedOutputs.join(','))}` : '';",
            "const samplesHref = assetProjectQuery ? `/generated-samples${assetProjectQuery}` : `/generated-samples${projectQuery}`;",
        ]:
            self.assertIn(copy, text)
        self.assertNotIn("export_type=${encodeURIComponent(selectedOutputs[0] || 'markdown')}", text)
        self.assertNotIn("const samplesHref = asset?.project_id ? `/generated-samples?project_id=${asset.project_id}` : '/generated-samples';", text)

    def test_upload_page_sends_pdf_chunking_strategy_with_preprocess_request(self):
        page = ROOT_DIR / "frontend" / "app" / "upload" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "const [pdfChunkingStrategy, setPdfChunkingStrategy] = useState('auto');",
            "PDF 切片方式",
            "{ id: 'auto', label: '自动智能切片' }",
            "{ id: 'content', label: '按内容主题切片' }",
            "{ id: 'module', label: '按模块/章节切片' }",
            "{ id: 'page', label: '按页切片' }",
            "pdf_chunking_strategy: pdfChunkingStrategy",
            "pdf_chunk_target_chars: 2500",
            "pdf_chunk_max_chars: 4500",
            "pdf_preserve_page_refs: true",
            "pdf_fallback_to_page_chunks: true",
        ]:
            self.assertIn(copy, text)

    def test_upload_page_preserves_output_context_for_review_pages(self):
        page = ROOT_DIR / "frontend" / "app" / "upload" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "const outputQueryParam = selectedOutputs.length ? `outputs=${encodeURIComponent(selectedOutputs.join(','))}` : '';",
            "const projectQuery = projectId ? `?project_id=${encodeURIComponent(projectId)}${outputQueryParam ? `&${outputQueryParam}` : ''}` : (outputQueryParam ? `?${outputQueryParam}` : '');",
            "const assetProjectQuery = asset?.project_id ? `?project_id=${encodeURIComponent(asset.project_id)}${outputQueryParam ? `&${outputQueryParam}` : ''}` : projectQuery;",
            "const semanticHref = assetProjectQuery ? `/semantic-units${assetProjectQuery}` : '/semantic-units';",
            "const samplesHref = assetProjectQuery ? `/generated-samples${assetProjectQuery}` : `/generated-samples${projectQuery}`;",
        ]:
            self.assertIn(copy, text)

    def test_upload_page_disables_material_export_when_only_samples_selected(self):
        page = ROOT_DIR / "frontend" / "app" / "upload" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "const hasMaterialOutput = materialOutputs.length > 0;",
            "const materialOutputParam = hasMaterialOutput ? materialOutputs.join(',') : '';",
            "const exportQuery = projectId && hasMaterialOutput ?",
            "const materialExportHref = hasMaterialOutput",
            "aria-disabled={!hasMaterialOutput}",
            "请先勾选知识库文档、流程包或检索语料后再导出交付产物",
        ]:
            self.assertIn(copy, text)

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

    def test_semantic_units_page_accepts_project_context_from_upload(self):
        page = ROOT_DIR / "frontend" / "app" / "semantic-units" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "URLSearchParams",
            "project_id",
            "outputs",
            "setProjectId(projectFromQuery)",
            "setOutputContext(outputsFromQuery)",
            "const effectiveQuery = nextProjectId ? `?project_id=${encodeURIComponent(nextProjectId)}` : query;",
            "apiJson<SemanticUnit[]>(`/api/v1/semantic-units${effectiveQuery}`)",
            "const outputQueryParam = outputContext ? `outputs=${encodeURIComponent(outputContext)}` : '';",
            "const uploadHref = projectQuery ? `/upload${projectQuery}` : '/upload';",
            "const samplesHref = projectQuery ? `/generated-samples${projectQuery}` : '/generated-samples';",
            "const exportsHref = projectQuery ? `/train-exports${exportQuery}` : '/train-exports';",
            "unitSamplesHref(unit)",
            "unitExportsHref(unit)",
        ]:
            self.assertIn(copy, text)

    def test_semantic_units_page_filters_samples_out_of_export_context(self):
        page = ROOT_DIR / "frontend" / "app" / "semantic-units" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "const exportableOutputTypes = new Set(['sft', 'instruction', 'preference', 'rag', 'skill', 'markdown']);",
            "const requestedOutputs = outputContext.split(',').map((item) => item.trim()).filter(Boolean);",
            "const exportOutputList = requestedOutputs.filter((item) => exportableOutputTypes.has(item));",
            "const exportOutputs = exportOutputList.length ? exportOutputList.join(',') : 'markdown,skill,rag';",
            "const primaryExportType = exportOutputList[0] || 'markdown';",
            "生成样本会保留 samples 目标，但训练导出只接收 Markdown、Skill、RAG 或数据集类型。",
        ]:
            self.assertIn(copy, text)

    def test_generated_samples_page_accepts_project_context_from_upload(self):
        page = ROOT_DIR / "frontend" / "app" / "generated-samples" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "URLSearchParams",
            "project_id",
            "outputs",
            "setProjectId(projectFromQuery)",
            "setOutputContext(outputsFromQuery)",
            "const effectiveQuery = nextProjectId ? `?project_id=${encodeURIComponent(nextProjectId)}` : query;",
            "apiJson<GeneratedSample[]>(`/api/v1/generated-samples${effectiveQuery}`)",
            "const semanticHref = projectId ? `/semantic-units?project_id=${projectId}${outputQueryParam ? `&${outputQueryParam}` : ''}` : '/semantic-units';",
            "const exportHref = projectId ? `/train-exports?project_id=${projectId}&export_type=${primaryExportType}&outputs=${encodeURIComponent(selectedExportOutputs.join(','))}` : '/train-exports';",
            "当前项目",
        ]:
            self.assertIn(copy, text)

    def test_home_page_starts_with_single_task_creation_flow(self):
        page = ROOT_DIR / "frontend" / "app" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "创建转换任务",
            "1. 创建任务",
            "2. 上传素材",
            "3. 选择生成内容",
            "视频",
            "长文本",
            "文档",
            "知识库文档",
            "可复用流程包",
            "检索语料包",
            "训练问答",
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

    def test_home_page_requires_project_before_upload_or_export(self):
        page = ROOT_DIR / "frontend" / "app" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "const canProceed = Boolean(selectedProjectId);",
            "请先创建或选择任务后继续",
            "aria-disabled={!canProceed}",
            "const uploadOutputsParam = selectedOutputs.join(',');",
            "uploadHref = canProceed",
            "semanticHref = canProceed ? `/semantic-units?project_id=${selectedProjectId}` : '#'",
            "samplesHref = canProceed && selectedOutputs.includes('samples') ?",
            "exportHref = useMemo(() => {",
            "if (!canProceed || !hasExportableOutput) {",
            "return '#';",
        ]:
            self.assertIn(copy, text)
        self.assertNotIn("const uploadHref = selectedProjectId ? `/upload?project_id=${selectedProjectId}` : '/upload';", text)
        self.assertNotIn('href="/semantic-units"', text)
        self.assertNotIn('href="/generated-samples"', text)

    def test_home_page_keeps_samples_and_dataset_separate_from_material_export_params(self):
        page = ROOT_DIR / "frontend" / "app" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "const materialOutputs = selectedOutputs.filter((item) => ['markdown', 'skill', 'rag'].includes(item));",
            "const datasetOutputs = selectedOutputs.includes('dataset') ? ['sft'] : [];",
            "const exportOutputs = [...materialOutputs, ...datasetOutputs];",
            "const hasExportableOutput = exportOutputs.length > 0;",
            "const samplesHref = canProceed && selectedOutputs.includes('samples') ?",
            "params.set('outputs', exportOutputs.join(','));",
            "请先勾选知识库文档、流程包、检索语料或数据文件后再进入导出",
        ]:
            self.assertIn(copy, text)
        self.assertNotIn("params.set('outputs', selectedOutputs.join(','));", text)

    def test_home_page_explains_complete_conversion_chain(self):
        page = ROOT_DIR / "frontend" / "app" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "完整转换链路",
            "上传素材 -> 内容处理 -> 内容片段审查 -> 生成训练问答 -> 自动整理交付物 -> 来源追溯",
            "内容处理",
            "长任务诊断",
            "自动整理交付物",
            "来源追溯",
            "查看交付中心",
            "查看追溯链路",
            "href=\"/artifacts\"",
            "href=\"/trace\"",
        ]:
            self.assertIn(copy, text)

    def test_task_pages_allow_retry_after_canceling_stuck_tasks(self):
        list_page = (ROOT_DIR / "frontend" / "app" / "tasks" / "page.tsx").read_text(encoding="utf-8")
        detail_page = (ROOT_DIR / "frontend" / "app" / "tasks" / "[task_id]" / "page.tsx").read_text(encoding="utf-8")

        self.assertIn("task.status === 'failed' || task.status === 'dispatch_failed' || task.status === 'canceled'", list_page)
        self.assertIn("task?.status === 'failed' || task?.status === 'dispatch_failed' || task?.status === 'canceled'", detail_page)
        for text in [list_page, detail_page]:
            self.assertIn("疑似卡住后可先取消任务，再重试任务", text)
            self.assertIn("重试任务", text)

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

    def test_train_exports_page_can_auto_export_selected_outputs_from_home(self):
        page = ROOT_DIR / "frontend" / "app" / "train-exports" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "selectedOutputs",
            "setSelectedOutputs",
            "outputsFromQuery.split(',')",
            "批量自动导出",
            "导出所选产物",
            "batchResults",
            "Promise.all",
            "selectedOutputs.map",
            "/api/v1/train-exports/auto",
            "export_type: item",
            "知识库文档",
            "可复用流程包",
            "检索语料包",
        ]:
            self.assertIn(copy, text)

    def test_train_exports_page_has_chinese_json_validation_feedback(self):
        page = ROOT_DIR / "frontend" / "app" / "train-exports" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "样本 JSON 格式错误",
            "请从首页或上传页带入来源任务，或填写来源信息",
            "exporting",
            "autoExporting",
            "解析样本 JSON",
        ]:
            self.assertIn(copy, text)

    def test_train_exports_page_uses_core_chain_divider_layout(self):
        page = ROOT_DIR / "frontend" / "app" / "train-exports" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "从任务内容自动整理交付物",
            "1. 确认来源",
            "2. 选择产物",
            "3. 导出并审查",
            "查看来源统计",
            "知识库文档",
            "可复用流程包",
            "检索语料包",
            "自动整理交付物",
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

    def test_home_page_can_configure_ollama_runtime(self):
        page = ROOT_DIR / "frontend" / "app" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "ollamaConfig",
            "setOllamaConfig",
            "saveOllamaConfig",
            "/api/v1/health/ollama/config",
            "method: 'PUT'",
            "Ollama 配置",
            "模型名称",
            "服务地址",
            "超时秒数",
            "最大提示词长度",
            "保存配置",
            "配置会写入本地运行时配置文件，服务重启后继续生效",
            "setOllamaStatus(status)",
        ]:
            self.assertIn(copy, text)

    def test_home_page_exposes_media_runtime_status(self):
        page = ROOT_DIR / "frontend" / "app" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "type MediaStatus =",
            "/api/v1/health/media",
            "媒体处理模式",
            "最大处理",
            "分段",
            "抽帧",
            "OCR",
            "frame_extraction_enabled",
            "ocr_enabled",
            "max_input_seconds",
            "transcription_timeout_sec",
            "ocr_timeout_sec",
            "单段转写超时",
            "OCR 超时",
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

    def test_task_pages_retry_for_failed_dispatch_failed_or_canceled_tasks(self):
        task_list = (ROOT_DIR / "frontend" / "app" / "tasks" / "page.tsx").read_text(encoding="utf-8")
        task_detail = (ROOT_DIR / "frontend" / "app" / "tasks" / "[task_id]" / "page.tsx").read_text(encoding="utf-8")

        self.assertIn("return task.status === 'failed' || task.status === 'dispatch_failed' || task.status === 'canceled';", task_list)
        self.assertIn("return task?.status === 'failed' || task?.status === 'dispatch_failed' || task?.status === 'canceled';", task_detail)

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

    def test_task_detail_uses_backend_diagnostics_endpoint(self):
        task_detail = (ROOT_DIR / "frontend" / "app" / "tasks" / "[task_id]" / "page.tsx").read_text(encoding="utf-8")

        for copy in [
            "type TaskDiagnostics",
            "diagnostics",
            "setDiagnostics",
            "`/api/v1/tasks/${task_id}/diagnostics`",
            "recommended_action",
            "next_actions",
            "后端诊断",
            "建议动作",
            "诊断结论",
        ]:
            self.assertIn(copy, task_detail)

    def test_task_detail_collapses_raw_input_config_behind_summary(self):
        task_detail = (ROOT_DIR / "frontend" / "app" / "tasks" / "[task_id]" / "page.tsx").read_text(encoding="utf-8")

        for copy in [
            "function taskInputSummary",
            "输入摘要",
            "生成目标",
            "重试来源",
            "<details",
            "<summary",
            "查看原始输入配置",
            "JSON.stringify(task.input_config || {}, null, 2)",
        ]:
            self.assertIn(copy, task_detail)

    def test_task_pages_make_media_processing_limits_visible(self):
        task_list = (ROOT_DIR / "frontend" / "app" / "tasks" / "page.tsx").read_text(encoding="utf-8")
        task_detail = (ROOT_DIR / "frontend" / "app" / "tasks" / "[task_id]" / "page.tsx").read_text(encoding="utf-8")

        for text in [task_list, task_detail]:
            for copy in [
                "任务摘要",
                "媒体处理上限",
                "result_summary",
                "segment(s)",
                "frame(s)",
                "max",
            ]:
                self.assertIn(copy, text)

    def test_artifact_detail_exposes_manifest_quality_metadata(self):
        page = (ROOT_DIR / "frontend" / "app" / "artifacts" / "[artifact_id]" / "page.tsx").read_text(encoding="utf-8")

        for copy in [
            "质量元数据",
            "素材来源统计",
            "语义类型统计",
            "媒体处理上限",
            "视频转写",
            "画面 OCR",
            "长文本",
            "Ollama 状态",
            "Ollama 截断",
            "结构解析",
            "解析错误",
            "提示词长度",
            "发送长度",
            "AI 摘要",
            "用途",
            "风险",
            "Manifest 路径",
            "manifest",
            "source_counts",
            "unit_type_counts",
            "media_processing_limits",
            "llm_summary",
            "ollama_status",
            "ollama_parse_status",
            "ollama_parse_error",
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
            "交付中心",
            "交付列表",
            "知识库文档",
            "流程包",
            "检索语料",
            "来源统计",
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

    def test_generated_samples_page_can_start_project_batch_generation(self):
        page = ROOT_DIR / "frontend" / "app" / "generated-samples" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        for copy in [
            "批量生成样本",
            "正在创建样本生成任务",
            "onGenerateProjectSamples",
            "/api/v1/tasks/generate-samples",
            "task_type: 'generate_samples'",
            "input_config: { limit: 50 }",
            "batchTask",
            "查看批量任务",
            "`/tasks/${batchTask.id}`",
            "请先从上传页或语义单元页带入项目 ID",
        ]:
            self.assertIn(copy, text)

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
