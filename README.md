# Envoy v1.0

闭源 MVP 开发仓库。

## 当前核心能力
- 项目管理
- 素材上传与存储
- 任务创建、状态更新与详情查看
- 预处理、分析、样本生成、导出
- 语义单元与追溯链
- 训练导出（SFT / Instruction / Preference / RAG / Skill / Markdown）
- 导出历史记录与导出目录预览
- 按标准化目录存放输出产物
- 一键启动脚本（PowerShell / BAT）

## 技术栈
- 前端：Next.js
- 后端：FastAPI
- 异步任务：Celery + Redis
- 数据库：MySQL
- 文件存储：MinIO

## 本地启动顺序
### 推荐：一键启动
```powershell
.\scripts\start_env.ps1
```

### 或使用 BAT
```bat
scripts\start_env.bat
```

### 手动启动
1. 启动依赖服务：
```bash
docker compose up -d mysql redis minio
```

2. 启动后端：
```bash
cd backend
py -m uvicorn app.main:app --reload
```

3. 启动前端：
```bash
cd frontend
npm install
npm run dev
```

## 导出能力
支持导出：
- SFT
- Instruction
- Preference
- RAG corpus
- Skill
- Markdown

支持：
- 目录预览
- 自定义任务目录名
- 导出历史记录
- 导出历史详情
- 一键打开输出目录

## 端口说明
- 前端：3000
- 后端：8000
- MySQL：3307
- Redis：6379
- MinIO API：9000
- MinIO Console：9001
