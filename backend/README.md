# Envoy Backend

## Run

```bash
py -m uvicorn app.main:app --reload
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
- `MEDIA_SEGMENT_SECONDS` (default: `600`) 媒体分段长度，影响长视频/音频转写粒度
- `MEDIA_MAX_SEGMENT_COUNT` (default: `12`) 单个媒体任务最多读取的分段数量，避免超长素材拖垮转写任务
- `MEDIA_MAX_FRAME_COUNT` (default: `240`) 单个视频任务最多抽帧数量
- `MEDIA_ENABLE_FRAME_EXTRACTION` (default: `true`) 是否对视频执行抽帧；长视频只需要转写时可设为 `false`
- `MEDIA_ENABLE_OCR` (default: `true`) 是否对视频抽帧结果执行 OCR；长视频吞吐优先时可设为 `false`
- `OLLAMA_BASE_URL` / `OLLAMA_MODEL` / `OLLAMA_MAX_PROMPT_CHARS` 本地 Ollama 辅助生成配置

## Current Business Coverage
- 项目、素材、任务、产物、语义单元、追溯、训练导出均已具备基础接口
- 预处理、分析、样本、导出任务已接入 Celery
- 导出支持标准化目录与历史记录落库
- 素材上传在 MinIO 失败时会自动回退本地占位存储并记录原因
