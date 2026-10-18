# Envoy Backend

## Run

```bash
py -m uvicorn app.main:app --reload
```

## Optional Media Runtime

长视频内容提取需要系统媒体工具和至少一种 Python 文本提取能力：

```bash
brew install ffmpeg
python3 -m pip install -r backend/requirements-media.txt
python3 scripts/check_delivery_runtime.py
python3 scripts/probe_media_file.py <video.mp4>
python3 scripts/probe_media_collection.py <media-folder>
python3 scripts/run_media_collection_delivery_e2e.py <media-folder> --out-dir tmp/media-collection-delivery
python3 scripts/audit_delivery_readiness.py --media-dir <media-folder>
```

`ffmpeg/ffprobe` 负责视频/音频切分，`openai-whisper` 负责音频转写，`paddleocr` + `paddlepaddle` 负责视频抽帧 OCR。图片和视频 OCR 在 PaddleOCR 不可用时会尝试调用系统 `tesseract`；两者都不可用时保留 metadata-only 降级状态，不会伪造 OCR 内容。`paddleocr` 只有同时安装 `paddle` 推理后端时才会被运行时检查视为可用。

## URL 转 Markdown 验收

启动后端和前端后，打开前端的 `/video` 页面，粘贴一个视频 URL。默认认证策略先尝试公开访问，再只尝试本机确实存在 Cookie 数据库的 Chrome、Edge、Firefox、Brave、Chromium、Vivaldi、Opera 或 Whale profile；Safari 受保护 Cookie 默认跳过。没有浏览器 profile 时只尝试公开访问，不再因缺失的 Cookie 库产生误导性错误。后端必须和已登录浏览器运行在同一台机器、同一用户下。

当前 URL 下载和主页/播放列表发现由统一来源层负责，底层使用 `yt-dlp`。已针对 YouTube、B 站、抖音、Vimeo、Twitch、Instagram、Facebook、X、快手、西瓜视频、优酷、腾讯视频、爱奇艺和小红书做来源分类，并将单视频、分享链接、创作者主页和集合统一转换为候选视频结构。具体可用能力仍以平台当前页面和 yt-dlp 版本为准。

主页发现遇到登录墙、验证码、挑战页或平台风控时，发现 API 返回 `409` 和 `requires_user_action=true`。前端会提供打开原始来源页面、由用户正常完成处理后重试的入口；不绕过验证码、签名校验或设备风控。若平台仍不能返回作品列表，可改用单条视频链接、批量粘贴链接或本地视频上传。

需要自动读取用户处理后的页面可见作品时，额外安装 `playwright` 并确保运行 Envoy 的机器有 Chrome/Chromium：

```bash
python3 -m pip install -r backend/requirements-media.txt
```

浏览器接管接口会启动独立持久化浏览器配置；用户在弹出窗口中完成登录或验证后，Envoy 只读取可见视频卡片链接，不读取 Cookie，也不把页面内容发送给模型。

浏览器接管接口：

```http
POST /api/v1/video-playlists/browser-sessions
POST /api/v1/video-playlists/browser-sessions/{session_id}/discover
DELETE /api/v1/video-playlists/browser-sessions/{session_id}
```

发现接口：

```http
POST /api/v1/video-playlists/discover
Content-Type: application/json

{
  "source_url": "https://www.youtube.com/@TED/videos",
  "limit": 10
}
```

成功响应包含 `platform`、`source_kind`、`discovery_method` 和候选项的 `source_url`、标题、时长、封面、发布时间。YouTube 和 B 站主页已完成真实公开链路验证；其他平台按相同契约逐一验收。

## 混合素材知识库

`/video` 页面还支持“混合素材”模式，一次选择最多 50 个图片、PDF、文字、Markdown、音频或视频文件。它们会分别走已有的预处理器：

- 图片：OCR
- PDF/文档/文字：文本抽取与分片
- 音频：ASR
- 视频：ASR、OCR 和连续画面理解

父任务只负责并行调度、断点复用和统一 Markdown 导出，不会把原始文件整体重复发送给模型。默认不调用额外 LLM；开启 Markdown 增强时只对已经分片的内容做可选优化。已完成的混合库可以通过 `POST /api/v1/tasks/{task_id}/append-assets` 追加新素材，保持同一个 `library_id` 和输出目录。

可选的连续画面理解使用 OpenAI Chat Completions 或 Anthropic Messages 兼容接口。配置 `HERMES_VISION_BASE_URL`、`HERMES_VISION_TOKEN`、`HERMES_VISION_MODEL=deepseek-v4.1flash`，并可用 `HERMES_VISION_PROTOCOL=chat|messages` 选择协议。未配置时仍会完成下载、转写、OCR 和 Markdown 导出。

稳定版 API：

```http
POST /api/v1/tasks/video-to-markdown
Content-Type: application/json

{
  "project_id": "your-project-id",
  "source_url": "https://...",
  "title": "可选标题",
  "input_config": {
    "video_frame_interval_seconds": 5,
    "video_max_frame_count": 480
  }
}
```

接口返回任务 ID；使用 `GET /api/v1/tasks/{task_id}` 轮询，完成后从任务 `input_config.checkpoints.export.artifact_id` 调用 `/api/v1/artifacts/{artifact_id}/download` 下载 Markdown。默认认证策略是 `auto`：公开访问失败后依次尝试本机浏览器登录态。

命令行验收：

```bash
python3 scripts/run_video_url_markdown_e2e.py '<video-url>' \
  --api http://127.0.0.1:8140 \
  --output tmp/video-output.md
```

任务会按下载、媒体预处理、Markdown 导出三个阶段写入检查点。失败后在页面或任务 API 调用重试，会复用仍然存在的下载文件、预处理任务和导出文件，不重复成功阶段。最终下载的是无时间轴的知识文档；完整分片仍保存在导出目录中。

本地媒体运行时至少需要：

```bash
python3 -m pip install -r backend/requirements-media.txt
brew install ffmpeg
```

## Core Modules
- `app/api/v1/projects.py` 项目管理
- `app/api/v1/assets.py` 素材上传
- `app/api/v1/tasks.py` 任务流转
- `app/api/v1/artifacts.py` 产物管理
- `app/api/v1/semantic_units.py` 语义单元
- `app/api/v1/trace.py` 追溯查询
- `app/api/v1/train_exports.py` 训练导出与历史记录

## Environment
- `DATABASE_URL` (default: `mysql+pymysql://root:password@127.0.0.1:3307/envoy_v1`)
- `REDIS_URL`
- `MINIO_ENDPOINT` (default: `127.0.0.1:9000`)
- `MINIO_ACCESS_KEY` (default: `minio`)
- `MINIO_SECRET_KEY` (default: `minio123456`)
- `MINIO_BUCKET` (default: `envoy`)
- `FFMPEG_BIN` / `FFPROBE_BIN` (default: `ffmpeg` / `ffprobe`) 长视频预处理与验收使用的媒体二进制；容器或 CI 可设为绝对路径
- `MEDIA_SEGMENT_SECONDS` (default: `600`) 媒体分段长度，影响长视频/音频转写粒度
- `MEDIA_MAX_SEGMENT_COUNT` (default: `12`) 单个媒体任务最多读取的分段数量，避免超长素材拖垮转写任务
- `MEDIA_TRANSCRIPTION_TIMEOUT_SEC` (default: `900`) 单个音频/视频分段转写最长等待秒数，超时会终止隔离转写进程
- `MEDIA_OCR_TIMEOUT_SEC` (default: `300`) 单个 OCR 批次最长等待秒数，超时会终止隔离 OCR 进程并返回空 OCR 文本
- `MEDIA_MAX_FRAME_COUNT` (default: `240`) 单个视频任务最多抽帧数量
- `MEDIA_WHISPER_MODEL` (default: `base`) Whisper 转写模型；本地验收可用 `tiny` 降低下载和推理成本
- `MEDIA_ENABLE_FRAME_EXTRACTION` (default: `true`) 是否对视频执行抽帧；长视频只需要转写时可设为 `false`
- `MEDIA_ENABLE_OCR` (default: `true`) 是否对视频抽帧结果执行 OCR；长视频吞吐优先时可设为 `false`
- 大 PDF 页级 OCR fallback 依赖系统命令 `pdfinfo`、`pdftoppm`、`tesseract`
- 本地 100MB 级 PDF 验收脚本：`scripts/run_large_pdf_rag_e2e.py`
- `OLLAMA_BASE_URL` / `OLLAMA_MODEL` / `OLLAMA_MAX_PROMPT_CHARS` 本地 Ollama 辅助生成默认配置
- `OLLAMA_RUNTIME_CONFIG_PATH` (default: `tmp/config/ollama_runtime.json`) 首页保存的 Ollama 运行时配置文件，服务重启后会优先读取

## Current Business Coverage
- 项目、素材、任务、产物、语义单元、追溯、训练导出均已具备基础接口
- 预处理、分析、样本、导出任务已接入 Celery
- 导出支持标准化目录与历史记录落库；`delivery` 类型会生成 `readable/`、`knowledge_base/`、`training/`、`agent/`、`indexes/` 交付包，并通过 `agent/import_manifest.json` 指导现成 agent 或知识库导入
- 运行时依赖预检：`python scripts/check_delivery_runtime.py`
- 单个视频可解码预检：`python scripts/probe_media_file.py <video.mp4>`，会检查 ffprobe 元数据、短音频抽取和单帧抽取，返回 `envoy.media.probe.v1` JSON
- 批量课程目录体检：`python scripts/probe_media_collection.py <media-folder>`，会递归扫描常见媒体扩展，输出可用输入、拒绝输入和 `recommended_next_video`
- 批量课程目录交付验收：`python scripts/run_media_collection_delivery_e2e.py <media-folder> --out-dir tmp/media-collection-delivery`，会先体检目录，再对可用视频执行长视频交付包 e2e；可用 `--plan-only` 只输出准入计划
- 商业化交付就绪审计：`python scripts/audit_delivery_readiness.py --media-dir <media-folder>`，会汇总运行时依赖、无外部样本的交付包 smoke 验收和真实媒体目录准入结果，输出 `envoy.delivery.readiness.v1`
- 交付包可用 `python scripts/validate_delivery_package.py <delivery_package_root>` 做离线验收
- 无外部样本依赖的交付包 smoke 验收：`python scripts/run_delivery_package_e2e.py --out-dir tmp/delivery-e2e`
- 真实长文本预处理到交付包验收：`python scripts/run_large_text_delivery_e2e.py --out-dir tmp/large-text-delivery`
- 真实大 PDF 预处理到交付包验收：`python scripts/run_large_pdf_delivery_e2e.py --pdf <large.pdf> --out-dir tmp/large-pdf-delivery`
- 真实长视频预处理到交付包验收：`python scripts/run_large_video_delivery_e2e.py --video <video.mp4> --out-dir tmp/large-video-delivery`，默认要求视频时长至少 60 秒；可用 `--min-duration-seconds` 调整验收门槛
- 长视频输入必须是 `ffmpeg` 可解码的标准媒体文件。`.sz`、加密缓存、播放器私有缓存或损坏 H.264 容器即使能被 `ffprobe` 读到时长，也可能无法抽音频或抽帧；验收脚本会先执行媒体预检，失败时返回 `reason=media_probe_failed` 和 `probe.failed_checks`，不会进入长任务。
- 素材上传在 MinIO 失败时会自动回退本地占位存储并记录原因
