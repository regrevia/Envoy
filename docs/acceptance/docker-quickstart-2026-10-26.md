# Docker 一键启动验收记录（2026-10-26）

机器：macOS / Apple Silicon（arm64），Docker Desktop，Server 29.9.2、Compose v5.6.1，context `desktop-linux`。
目标：关闭 issue #1，`cp .env.example .env && docker compose up -d` 之后四个服务健康、界面可开、上传与导出可跑、`down` 后数据还在。

## 结论

在这台机器上闭环成立。四条验证全部实测通过：

| 验证 | 结果 |
| --- | --- |
| 服务健康 | `mysql` / `redis` / `backend` / `frontend` 四个服务全部 `healthy` |
| 后端与界面 | `GET /api/v1/health` → `200 {"status":"ok"}`；`http://127.0.0.1:8200` → `307` 跳 `/video`（Next 正常重定向） |
| 业务闭环 | 建项目 → 上传文档（落盘 `/app/tmp/storage`）→ `export-markdown` 任务 `success 100` → 产物下载 `200`，343 B Markdown |
| 数据存活 | `docker compose down` 再 `up -d` 后 4×healthy，2 个项目 / 1 个素材 / 3 个产物仍在，产物再下载仍 `200`，343 B |

## 修掉的四个真实缺陷

1. **镜像里没有 uvicorn。** `backend/Dockerfile` 只装了 `requirements-media.txt`，从没装 `requirements.txt`，启动即 `exec: "uvicorn": executable file not found in $PATH`。现在按“应用依赖 → 媒体依赖 → 媒体 extras”分层安装，`WITH_MEDIA=0` 时可跳过 whisper/paddleocr。
2. **MySQL 从来没有建表。** `backend/app/main.py` 的 `create_all` 只在 `sqlite` 下执行，MySQL 路径依赖 `sql/` 里的脚本，而 compose 没把脚本挂进去，于是启动即 `Table 'envoy_v1.processing_tasks' doesn't exist`，后端退出码 3。现在把 `./sql` 挂到 `/docker-entrypoint-initdb.d`（只读），空卷首次启动自动建表。
3. **前端容器健康检查永远失败。** Next standalone 默认绑定容器 hostname，容器内 `127.0.0.1:3000` 拒连，`frontend` 卡在 `health: starting` → `unhealthy`。`frontend/Dockerfile` 里补 `ENV HOSTNAME=0.0.0.0`、`ENV PORT=3000` 后转为 `healthy`。
4. **上传与导出写在容器临时层。** `app/core/config.py` 用 `parents[3]` 推 `BASE_DIR`，在容器里算出 `/`，于是路径变成 `/tmp/storage`、`/tmp/exports`，容器一重建就丢。现在 compose 显式给 `ENVOY_LOCAL_STORAGE_DIR=/app/tmp/storage`、`ENVOY_EXPORT_DIR=/app/tmp/exports`、`ENVOY_VIDEO_DOWNLOAD_DIR=/app/tmp/video-downloads`，都落在 `envoy_runtime` 卷里。

另外两处行为调整：`.env` 里 `ENVOY_STORAGE_MODE` 由 `minio` 改为默认 `local`（对象存储需要 `--profile object-storage`）；上传遇到存储不可用时，503 文案直接给出两条修复路径，而不是只报底层异常。

## 需要 `down -v` 的两类历史坑

- `Access denied for user 'root'`：卷是按旧密码初始化的，改 `.env` 的 `MYSQL_ROOT_PASSWORD` 不会改已存在卷里的账号。
- `Table ... doesn't exist`：卷建于“还没有 initdb 挂载”的版本，MySQL 不会对已初始化的卷重跑脚本。

两者都只能在本地清卷：`docker compose down -v && docker compose up -d`。

## 耗时（同机实测）

| 项 | 数值 |
| --- | --- |
| 空卷首次启动到 4×healthy | 约 25–30 秒（MySQL 初始化占大头） |
| 已有卷重启到 4×healthy | 约 10 秒 |
| `export-markdown`（单文档） | < 2 秒，任务轮询 6 秒时已 `success` |
| 冷构建依赖层（apt + pip，lean） | 103.5 秒（tuna pip 源） |
| Playwright 浏览器与 FFmpeg 下载 | 29.7 秒（npmmirror），Chromium 190.5 MiB + FFmpeg 119.9 MiB |
| 镜像体积 | `envoy-backend` 2.38 GB（`ENVOY_WITH_MEDIA=0`）/ 4.64 GB（`=1`，含 whisper+paddleocr+torch+paddle）、`envoy-frontend` 308 MB |
| 容器内视频转 Markdown（`local`） | 30 秒内 `success`，产物 `index.md` + `themes/转写.md` + `evidence/`，正文由容器内 Whisper `small` 转出 |
| 容器内视频转 Markdown（`celery` worker） | worker 收任务 30.5 秒 `succeeded`（`video_markdown_ready`），产物同上 |

容器内 CDN 速度是主要变量，同一台机器实测：清华 pip 源装完整应用依赖 + 媒体依赖 43 秒、单包峰值 11.6 MB/s；阿里云 pip 源 0.28 MB/s。宿主机的代理配置不作用于容器内下载，必须用构建参数指定镜像源。

## 明确没有验证的部分

- **视频转写只用 6 秒合成中文样片验证。** 样片 84,428 B / 640x360 / H.264+AAC，由宿主机 `say` 生成中文语音再用 ffmpeg 合成，容器内 Whisper `small` 直接读它。真实平台视频（B 站/抖音/YouTube 等）走的是宿主机 Envoy 抓取，不在容器链路范围内；容器内的转写结果也仍是 CPU `small` 模型的水平，转出文字有断句与错字（例：`这是Envoy容器内转写的测试音频,用于验证视频转markdown是否`），不作为 ASR 准确率验收。
- **完整媒体镜像只在这台 arm64 Mac 上构建。** `paddlepaddle` 在 arm64 没有 3.3.x 轮子（约束 `>=3.2,<3.3`）；x86_64 与多架构构建未执行。torch 必须锁 CPU 轮（`torch==2.9.1 --no-deps`），否则装到 CUDA 轮后 `import torch` 直接报 `libcublasLt.so` 缺失。
- **MinIO 模式未跑通。** `minio/minio` 已无免费 tag 且本机拉不到镜像，所以对象存储只保留 profile 与文档说明，验收走本地文件存储。
- **未在 Linux / Windows 主机上重复。**
- **Celery 并发压力未测。** 只验证了单任务在 worker 上成功；多任务并发、worker 重启后的任务恢复没有压测。
