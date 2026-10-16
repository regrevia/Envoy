# Delivery Package Export Design

## Goal

把长视频、长 PDF、长文本和多文件预处理后的语义单元，封装成一个可商业交付的 Envoy 交付包。交付包必须同时服务四类使用场景：人工阅读、RAG 知识库导入、训练数据整理、agent 初始化。

## Output Contract

`delivery` 导出类型生成如下目录：

```text
<export_root>/
  README.md
  delivery_manifest.json
  readable/
    index.md
    sources/
      01-<source>.md
  knowledge_base/
    rag_corpus.jsonl
    documents.jsonl
    chunks/
      001-<chunk>.md
  training/
    instruction_train.jsonl
    dataset_card.md
  agent/
    skill.md
    skill.json
    import_manifest.json
  indexes/
    chunk_index.json
    source_index.json
    timeline_index.json
    quality_report.json
```

## Import Strategy

`agent/import_manifest.json` 是外部系统的主入口，固定使用 `envoy.agent.import.v1`。推荐导入顺序：

1. `knowledge_base/rag_corpus.jsonl` 导入 RAG 或知识库。
2. `readable/index.md` 导入长上下文工具或人工阅读系统。
3. `agent/skill.md` 作为 agent 使用说明。
4. `training/instruction_train.jsonl` 作为训练或样本生成种子。

所有路径在导入清单和索引中使用相对路径，方便移动整个交付目录或压缩交付。

`compatible_targets` 固定声明四类通用兼容目标：

- `generic_rag_jsonl`
- `markdown_knowledge_base`
- `agent_skill_context`
- `instruction_tuning_seed`

`target_profiles` 为每类目标声明可直接消费的文件和字段：

- `generic_rag_jsonl`：读取 `knowledge_base/rag_corpus.jsonl`，字段为 `doc_id`、`title`、`text`、`metadata`。
- `markdown_knowledge_base`：读取 `readable/index.md`、`readable/sources/*.md`、`knowledge_base/chunks/*.md`。
- `agent_skill_context`：读取 `agent/skill.md`，并可附带 `agent/import_manifest.json`、`indexes/quality_report.json`、`indexes/chunk_index.json`。
- `instruction_tuning_seed`：读取 `training/instruction_train.jsonl`，字段为 `instruction`、`input`、`output`。

## Index Strategy

- `chunk_index.json`：统一记录 `chunk_id`、标题、摘要、来源、章节路径、页码或时间范围、降级状态和文件路径。
- `source_index.json`：按原始文件聚合，记录来源类型、来源名、素材 ID、来源文档路径和分片数量。
- `timeline_index.json`：仅在视频或带时间范围的素材中填充，记录时间段、分片路径和降级状态。
- `quality_report.json`：记录 `partial_success` 分片数、转写失败片段数、来源统计、语义类型统计和媒体处理限制。

## Dedup Strategy

视频预处理会同时生成派生文本语义单元和 `media_asset` 汇总语义单元。同一 `asset_id` 已存在 `media_transcript` 或 `media_ocr` 时，`delivery` 导出只把派生文本写入 RAG、训练和 Markdown chunk，避免现成知识库导入时重复召回同一段内容。`media_asset` 汇总仍参与 `quality_report.json` 统计，用于保留 `partial_success`、`ocr_unavailable`、转写失败片段等质量信号。

## Runtime Strategy

- 长视频切分依赖 `ffmpeg` 和 `ffprobe`，可用 `FFMPEG_BIN`、`FFPROBE_BIN` 指定非 PATH 二进制。
- 长视频内容提取至少需要一种可运行能力：`openai-whisper` 用于音频转写，或 `paddleocr` + `paddlepaddle` 用于帧 OCR。仅安装 `paddleocr` 包但缺少 `paddle` 推理后端时，运行时检查不得把帧 OCR 标为可用。
- `MEDIA_WHISPER_MODEL` 默认 `base`；本地验收和低资源环境可设为 `tiny`，正式环境可按准确率和成本选择更大模型。
- 视频转写输入使用从原视频抽取出的音频分片，而不是直接把视频片段交给 Whisper，避免坏容器或长 GOP 视频让转写进程长时间空转。
- `scripts/probe_media_file.py <video>` 是长视频入库前的输入质量门禁，输出 `envoy.media.probe.v1` JSON，检查 ffprobe 元数据、短音频抽取和单帧抽取。只要音频或画面任一可解码，后续预处理仍可按 transcript-only 或 ocr-only 降级运行。
- `scripts/probe_media_collection.py <folder>` 是课程目录或批量素材导入前的体检入口，输出 `envoy.media.collection_probe.v1` JSON，按 `usable_inputs`、`rejected_inputs` 和 `recommended_next_video` 给出后续长视频验收或批量导入候选。
- `scripts/run_media_collection_delivery_e2e.py <folder>` 是批量课程视频交付验收入口，先执行目录体检，再对计划内可用视频逐个调用长视频交付包 e2e。`--plan-only` 只输出 `envoy.media.collection_delivery_plan.v1`，适合上传前素材准入或人工确认。
- `scripts/audit_delivery_readiness.py --media-dir <folder>` 是商业化交付就绪审计入口，汇总运行时依赖、无外部样本的交付包 smoke 验收和真实媒体目录准入，输出 `envoy.delivery.readiness.v1`。当核心交付包可生成但真实视频目录没有可解码输入时，报告状态为 `partial`，阻塞项为 `no_usable_real_video`。
- `run_large_video_delivery_e2e.py` 默认要求视频至少 60 秒，防止短样本误当作长视频商业验收；可通过 `--min-duration-seconds` 调整门槛。
- 长视频验收要求输入是 `ffmpeg` 可解码的标准媒体文件。播放器私有缓存、加密缓存、`.sz` 文件或损坏 H.264 容器即使能被 `ffprobe` 读到时长，也不视为可生成知识库素材的有效视频样本。
- `scripts/check_delivery_runtime.py` 必须同时报告系统二进制、Python 内容提取模块和 PDF OCR 工具，并据此计算 `long_video_delivery_e2e` 与 `large_pdf_ocr_fallback` 能力。
- 额外媒体依赖通过 `backend/requirements-media.txt` 安装，不强行进入基础后端依赖。

## Failure Model

交付包导出不依赖 Ollama 或云服务。即使长视频部分转写失败，只要存在转写或 OCR 文本，交付包仍然生成，并在 `quality_report.json`、`chunk_index.json` 和 `delivery_manifest.json` 中记录降级信息。

当视频抽帧失败但音频转写成功时，预处理任务以 `partial_success` 完成，记录 `ocr_unavailable`、`frame_extraction_error` 和可用文本来源。若媒体预检阶段已确认音频和画面均不可解码，`run_large_video_delivery_e2e.py` 直接返回 `reason=media_probe_failed`，不会创建预处理任务。若运行中音频抽取和抽帧都失败，任务快速失败，不调用 Whisper 转写坏视频片段；错误写入 `task_error` 和 `semantic_error`，并对 ffmpeg stderr 做去重和截断，避免长任务卡死或数据库写入巨量日志。

## Acceptance Evidence

- `backend/tests/test_markdown_export_pipeline.py::test_delivery_package_writes_importable_agent_and_knowledge_base_indexes`
- `backend/tests/test_markdown_export_pipeline.py::test_delivery_package_avoids_duplicate_media_summary_when_transcript_exists`
- `tests/test_train_export_auto_flow.py::TrainExportAutoFlowTests::test_manual_delivery_export_returns_import_manifest`
- `tests/test_delivery_package_validator.py::DeliveryPackageValidatorTests::test_validator_accepts_generated_delivery_package`
- `tests/test_delivery_package_validator.py::DeliveryPackageValidatorTests::test_delivery_e2e_script_generates_and_validates_package`
- `tests/test_delivery_package_validator.py::DeliveryPackageValidatorTests::test_delivery_runtime_check_reports_missing_python_media_modules`
- `tests/test_delivery_package_validator.py::DeliveryPackageValidatorTests::test_delivery_runtime_check_requires_paddle_backend_for_paddleocr`
- `tests/test_delivery_package_validator.py::DeliveryPackageValidatorTests::test_large_video_delivery_script_reports_missing_python_media_dependency`
- `tests/test_delivery_package_validator.py::DeliveryPackageValidatorTests::test_large_video_delivery_script_requires_paddle_backend_for_paddleocr`
- `tests/test_delivery_package_validator.py::DeliveryPackageValidatorTests::test_large_video_delivery_script_preprocess_failure_payload_includes_error`
- `tests/test_delivery_package_validator.py::DeliveryPackageValidatorTests::test_media_probe_script_reports_decode_capabilities`
- `tests/test_delivery_package_validator.py::DeliveryPackageValidatorTests::test_media_probe_script_fails_when_audio_and_frame_decode_fail`
- `tests/test_delivery_package_validator.py::DeliveryPackageValidatorTests::test_media_collection_probe_summarizes_usable_and_rejected_inputs`
- `tests/test_delivery_package_validator.py::DeliveryPackageValidatorTests::test_media_collection_delivery_script_builds_batch_plan`
- `tests/test_delivery_package_validator.py::DeliveryPackageValidatorTests::test_readiness_report_marks_real_video_blocker_without_failing_core_delivery`
- `tests/test_delivery_package_validator.py::DeliveryPackageValidatorTests::test_large_video_delivery_script_stops_when_media_probe_fails`
- `tests/test_delivery_package_validator.py::DeliveryPackageValidatorTests::test_large_pdf_delivery_script_reports_missing_pdf`
- `tests/test_delivery_package_validator.py::DeliveryPackageValidatorTests::test_large_video_delivery_script_reports_missing_video`
- `tests/test_delivery_package_validator.py::DeliveryPackageValidatorTests::test_large_text_delivery_script_generates_and_validates_chunks`
- `backend/tests/test_long_video_rag_pipeline.py::test_preprocess_video_transcribes_audio_segments_instead_of_video_segments`
- `backend/tests/test_long_video_rag_pipeline.py::test_preprocess_video_degrades_when_frame_extraction_fails`
- `backend/tests/test_long_video_rag_pipeline.py::test_preprocess_video_does_not_transcribe_video_when_audio_extraction_fails`
- `backend/tests/test_long_video_rag_pipeline.py::test_preprocess_video_compacts_long_media_errors`
- `python scripts/check_delivery_runtime.py`
- `python scripts/probe_media_file.py <video.mp4>`
- `python scripts/probe_media_collection.py <media-folder>`
- `python scripts/run_media_collection_delivery_e2e.py <media-folder> --plan-only`
- `python scripts/audit_delivery_readiness.py --media-dir <media-folder>`
- `python scripts/run_delivery_package_e2e.py --out-dir tmp/delivery-e2e`
- `python scripts/run_large_text_delivery_e2e.py --out-dir tmp/large-text-delivery`
- `python scripts/run_large_pdf_delivery_e2e.py --pdf <large.pdf> --out-dir tmp/large-pdf-delivery`
- `python scripts/run_large_video_delivery_e2e.py --video <video.mp4> --out-dir tmp/large-video-delivery`，默认 `--min-duration-seconds 60`
- `python scripts/validate_delivery_package.py <delivery_package_root>`
- 前端导出中心、上传页、首页、语义片段页、样本页均保留 `delivery` 作为可导出类型。
