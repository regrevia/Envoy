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

## Current Business Coverage
- 项目、素材、任务、产物、语义单元、追溯、训练导出均已具备基础接口
- 预处理、分析、样本、导出任务已接入 Celery
- 导出支持标准化目录与历史记录落库
- 素材上传在 MinIO 失败时会自动回退本地占位存储并记录原因
