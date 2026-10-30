# Docker 一键启动

从零到能用的 Web 界面只需要 Docker Desktop，不需要本机装 Python、Node、MySQL、FFmpeg 或 Chromium——这些都在镜像里。

## 这个 Docker 栈负责什么

Docker 只跑仓库里这套旧的 Web 界面（FastAPI + Next.js + MySQL + Redis）。`envoy` CLI、DSH 插件和三个宿主的 skill 全部跑在宿主机上，用 `install.sh` / `install.ps1` 安装脚本装（或从 GitHub Release 下 `envoy_cli-0.1.35-py3-none-any.whl` 交给 `uv tool install`；PyPI 上还没有发布包），不需要 Docker、也不需要这套 compose；只读网页/视频时本机装 FFmpeg 和 Chromium 就够。两套东西版本号相同但互不依赖，Web 栈只是可选的图形界面。

## 启动

```bash
git clone https://github.com/regrevia/Envoy.git
cd Envoy
cp .env.example .env
docker compose up -d --build
```

`docker compose ps` 里 `mysql`、`redis`、`backend`、`frontend` 四个服务都变成 `healthy` 才算起来（MinIO 只在 `object-storage` profile 下才有）。`frontend` 等 `backend` 健康、`backend` 等 `mysql`/`redis` 健康，所以不需要手动等。

首次启动时 MySQL 会自动执行 `sql/` 下的建表脚本（挂到 `/docker-entrypoint-initdb.d`），不需要额外跑迁移。

| 入口 | 地址 |
| --- | --- |
| 界面 | `http://127.0.0.1:8200` |
| 后端健康 | `http://127.0.0.1:8140/api/v1/health` |
| MinIO 控制台（可选） | `http://127.0.0.1:9001` |

端口与密码都在 `.env`，改完 `docker compose up -d` 生效。默认 `ENVOY_STORAGE_MODE=local`，上传与导出放在 `envoy_runtime` 卷里，不依赖对象存储；要用 S3/MinIO 时把 `.env` 改成 `minio` 并加 profile 启动：

```bash
docker compose --profile object-storage up -d
```

MinIO 镜像自 2025 年起不再提供免费 tag，`docker pull minio/minio` 会失败；需要对象存储时用自己已有的 S3 兼容服务，或在有授权的环境里提供镜像。

## 后台任务（可选 worker）

默认 `ENVOY_TASK_EXECUTION_MODE=local`：任务在 API 进程内的线程池里跑（`ENVOY_LOCAL_TASK_MAX_CONCURRENCY`，默认 2），不用额外服务，适合单人本地用。

同时提交多个耗时任务、或希望 API 重启不打断任务时，切到 worker：

```bash
# .env
ENVOY_TASK_EXECUTION_MODE=celery
ENVOY_CELERY_CONCURRENCY=2

docker compose --profile worker up -d
```

`worker` 服务和 `backend` 用同一个镜像、同一个 `envoy_runtime` 卷，队列走已有的 Redis（`envoy` 队列）。转写/OCR 这类媒体任务在 worker 里直接跑在任务进程内（不嵌套 fork），所以不需要额外权限；`ENVOY_CELERY_CONCURRENCY` 控制同时处理几个任务。`docker compose ps` 会多出 `worker` 一行，日志在 `docker compose logs -f worker`。切回 `local` 时把 `.env` 改回去并 `docker compose up -d`，worker 可以留着不启。

## 数据

| 卷 | 内容 |
| --- | --- |
| `mysql_data` | 任务、素材记录、设置 |
| `minio_data` | 只在 `object-storage` profile 下使用 |
| `envoy_runtime` | 任务临时目录、上传的原文件与导出的 Markdown（`local` 存储模式） |

`docker compose down` 保留三个卷；`docker compose down -v` 才会删除。备份用 `docker run --rm -v envoy_mysql_data:/data -v "$PWD":/backup alpine tar czf /backup/mysql.tgz /data`。

## Docker Hub 连不上

`docker compose build` 报 `failed to resolve source metadata for docker.io/library/python:3.11-slim` 是网络到 registry 的问题，不是镜像的问题。在 `.env` 里换成可达的镜像站，再构建：

```bash
ENVOY_BACKEND_BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim
ENVOY_FRONTEND_BASE_IMAGE=docker.m.daocloud.io/library/node:20-alpine
```

`mysql`、`redis`、`minio` 不走构建，需要先拉取并打回原名：

```bash
docker pull docker.m.daocloud.io/library/mysql:8.4 && docker tag docker.m.daocloud.io/library/mysql:8.4 mysql:8.4
docker pull docker.m.daocloud.io/library/redis:7-alpine && docker tag docker.m.daocloud.io/library/redis:7-alpine redis:7-alpine
docker pull docker.m.daocloud.io/minio/minio:latest && docker tag docker.m.daocloud.io/minio/minio:latest minio/minio:latest
```

要设成全局生效，可在 Docker Desktop 的 Docker Engine 配置里加 `"registry-mirrors"`；上面的写法不改全局配置。

## 首次构建慢

后端镜像要装 FFmpeg、Tesseract 中文包、Playwright 的 Chromium，第一次通常 5–15 分钟，取决于网络；之后有缓存，改动代码只重建应用层。

走国内网络时把依赖源指向镜像站，能把构建从“几十分钟”压到“几分钟”。在 `.env` 里设置：

```bash
ENVOY_PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENVOY_DEBIAN_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian
ENVOY_PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright
```

同一台 arm64 机器上实测：容器内清华 pip 源装完整应用依赖 + 媒体依赖 43 秒，单包峰值 11.6 MB/s；阿里云 pip 源只有 0.28 MB/s。Playwright 的 Chromium（190.5 MiB）与 FFmpeg（119.9 MiB）走 npmmirror 各约 30 秒。宿主机的代理/镜像设置对容器内的 CDN 速度没有影响，要在构建参数里指定。

`ENVOY_WITH_MEDIA=1`（默认）会再装 `openai-whisper`、`paddleocr`（含 PaddlePaddle 与中文字体）和 `yt-dlp`。本机（arm64、Docker 29.9）实测：带媒体依赖 `envoy-backend:latest` 是 **4.64 GB**，`python -c "import torch, whisper, paddleocr"` 通过，`torch 2.9.1+cpu`、`paddle 3.2.2`、`paddleocr 3.7.0`。只做界面与联网抓取时设 `ENVOY_WITH_MEDIA=0`，省掉这一层。

两个和 arm64 有关的坑已经写进 Dockerfile：

- `torch` 固定 `2.9.1` 并 `--no-deps` 安装。PyPI 上更高的 `torch` 在 arm64 上是 CUDA 构建，会拉 3 GB 的 nvidia 轮子，而且 `import torch` 直接报 `libcublasLt.so.* not found`；2.9.1 的 manylinux aarch64 轮子是纯 CPU（104 MB，`Requires-Dist` 里没有 `nvidia-`）。
- `paddlepaddle` 约束 `>=3.2,<3.3`：3.3.x 没有 linux/arm64 轮子。

`openai-whisper` 第一次转写会下载 `small` 模型（461 MB）。容器把 `WHISPER_DOWNLOAD_ROOT` 指到 `/app/tmp/whisper`（`envoy_runtime` 卷），所以重建容器不会重新下；要换更小/更大的模型设 `MEDIA_WHISPER_MODEL`（`base`、`medium`…）。

## 排错

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `port is already allocated` | 本机已占用 8140/8200/3307/6380/9000/9001 | 改 `.env` 里对应端口，再 `docker compose up -d` |
| `backend` 一直 `unhealthy` | MySQL 首次初始化未完成，或依赖装失败 | `docker compose logs backend --tail=200`；等 mysql 变 healthy 后 `docker compose restart backend` |
| `exec: "uvicorn": executable file not found` | 旧镜像只装了媒体依赖，没装应用依赖 | 升级到含 `requirements.txt` 安装步骤的镜像后 `docker compose build backend` |
| `Table 'envoy_v1.processing_tasks' doesn't exist` | MySQL 卷是旧版本初始化的，没跑过建表脚本 | `docker compose down -v` 后重新 `up -d`（会清空本地数据） |
| `Access denied for user 'root'` | 卷里的旧密码与 `.env` 的 `MYSQL_ROOT_PASSWORD` 不一致 | 改回旧密码，或 `docker compose down -v` 重建 |
| 上传报 `Storage backend unavailable` | 设了 `minio` 模式但 MinIO 没启动 | `.env` 改 `ENVOY_STORAGE_MODE=local`，或 `docker compose --profile object-storage up -d` |
| 界面能开、请求 502 | `NEXT_PUBLIC_API_BASE_URL` 指到了访问不了的地址 | 保持 `http://127.0.0.1:8140`，或改成浏览器能访问的后端地址后重建 frontend |
| 视频没字幕也没转写 | 镜像内没有本地模型，属预期 | 在设置里配好多模态 API，或改用带公开字幕的视频 |
| 需要登录才能读的页面 | 这是访问边界，不是故障 | 用安装版 `envoy browser setup` 在桌面登录一次；容器内不做登录绕过 |

## 验收记录

`docs/acceptance/docker-quickstart-2026-10-26.md` 记录这台机器上的实际启动结果与耗时。

容器内视频转 Markdown 已在 arm64 Mac 上跑通（2026-10-26）：6 秒中文 H.264/AAC 样片放进 `envoy_runtime` 后，`POST /api/v1/tasks/video-to-markdown` 返回 `success`，导出目录里有 `index.md`、`themes/转写.md`、`evidence.jsonl` 和逐条证据页，`themes/转写.md` 里的正文就是 Whisper 在容器内转出的中文。同一条任务在 `local` 和 `celery` 两种执行模式下都通过。首次请求会下载 Whisper `small` 权重（461 MB，CPU），之后从 `envoy_runtime` 卷复用。
