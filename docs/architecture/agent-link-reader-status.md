# Agent Link Reader 实施状态

更新时间：2026-10-19（Asia/Shanghai）
计划基线：[`agent-link-reader-v1.md`](../product/agent-link-reader-v1.md)  
计划所依据的提交：`fb582660072bfe04c86ddb67b1810a61a3dcbd4b`  
开发分支：`dev`

当前开发基线：`dev` 的 `4b4b076`；已合并发布基线：`main` 的 `7cb77b8`。当前工作树在完成验证后保持干净。GitHub Release 资产仍需由版本 tag 触发发行工作流。

## 状态定义

- **已验证**：本地代码或真实验收已有可复现证据。
- **进行中**：已有实现，但还未达到计划定义的稳定闭环。
- **未开始**：仅在产品方案中定义，不能作为当前已交付能力宣传。

此文档记录当前实现，不把产品方案中的安装器、Skill、性能数字或网站隔离门槛写成已完成事实。

## 当前能力

| 能力 | 状态 | 证据 / 缺口 |
| --- | --- | --- |
| 单一链接输入，后端按链接类型自动分流 | 进行中 | 视频和公开视频/图文走不同处理任务；公开视频 bundle 已开始输出统一材料协议 |
| 常见视频链接与本地视频处理 | 网站已验证，Skill 进行中 | 网站复用 yt-dlp、浏览器恢复、FFmpeg、转写/OCR 和原有导出链路；独立 Skill 工作树新增 yt-dlp 公开字幕时间定位与原生视频宿主交接，但本地真实 YouTube 测试返回需重载提示、本地真实 Bilibili 测试返回 HTTP 412，平台成功样本尚未获得；平台像素/抽帧、通用 ASR 和平台视频闭环仍未完成 |
| 抖音公开图文正文与配图 | 已验证（开发验收） | 2026-10-18 本地 Envoy 任务读取指定图文，提取 534 字符正文和 4 张原图，图片 OCR 子任务成功，产物 ZIP 校验通过 |
| 微信公众号和通用网页 | 进行中 | 通用公开页面提取已接入同一入口；微信公众号尚缺固定真实样本验收和长文/延迟资源测试 |
| PDF、Office、独立音频/图片入口 | 进行中 | 独立 Skill 支持 PDF 文本层、纯文本/Markdown/CSV/JSON 直链和公开图片视觉交接；PDF 按页定位，长纯文本按字符范围续读；无文本层扫描页明确提示 OCR 能力缺口；Office/音视频和扫描页 OCR 仍待接入 |
| 可供 Agent 消费的 Markdown/HTML/ZIP | 已验证（开发验收） | 公共图文 ZIP 含索引、来源 Markdown、HTML、manifest、`materials.json` 和全部配图；Markdown 适合正文消费，协议 JSON 提供渐进读取和资源定位 |
| 长任务、任务状态、重试与素材检查点 | 进行中 | 现有网站任务链可恢复；Worker 租约、并发配额、取消贯通仍未达到方案门槛 |
| 独立无数据库处理核心 | 进行中 | `packages/envoy-core` 已承载第一版 `envoy.read/1` 请求/材料/覆盖/资源 schema；现有 worker 尚未整体迁出 ORM |
| CLI、宿主 Skill 与 MCP 桥 | 进行中 | 已有开发版 `packages/envoy-cli`、`envoy read/expand/doctor/mcp`；MCP 支持新版 per-request 元数据和旧版初始化协商；Skill 按目标指导 read/search/targeted expand。工作树新增字幕解析/时间检索测试、长行命中窗口、跨 search/expand 区间账本、绝对 offset 续读、稳定图像资源 ID、视觉观察关联拒绝和公开视频直链条件交接。平台 extractor 真实链路暂受站点响应限制，不能据此宣称站点字幕支持已验收 |
| 跨来源质量/费用基准 | 未开始 | 尚无固定 60 来源/120 任务集，也未测得全链路费用改善数字 |

## 近期执行顺序

按方案 M0/M1 优先，网站现有链路保持可运行：

1. **M0 / D01**：冻结真实来源 fixture、行为和验收结果；记录视频与公开图文基准。
2. **M1 / D02**：版本化请求、材料块、定位、覆盖、错误和使用量 schema；网站现有公开网页流程先生成协议兼容快照。
3. **M1 / D03**：创建不依赖 FastAPI、SQLAlchemy、业务数据库的核心包边界、CLI 原型与本地状态接口。
4. **M2 / D04–D06**：完善通用网页、微信公众号、图片、PDF 和 Office 的真实结构提取。
5. **M3 / D07–D10**：媒体解析/获取分层、真实时间定位、视觉宿主桥、累计预算、缓存和资源租约。
6. **M4 / D11**：在目标系统验证标准运行包、安装器、doctor、升级和回滚。
7. **M5 / D12–D16**：网站独立 Worker、租约/配额/取消恢复、产物版本和身份隔离。
8. **M6 / D17**：固定数据集、真实宿主端到端、成本/质量及发行矩阵。

每个阶段单独验收，不因一个抖音图文样本通过就宣称通用网页、Skill 或跨模态全量能力已经完成。

## 当前开发验收记录

### 抖音图文：`为什么 AI 给的命令总报错？`

- 来源类型：公开图文（不登录、不使用浏览器 Cookie）。
- 页内正文：534 个字符。
- 配图：页面显示 4 张，4 张均已下载并进入 OCR 子任务。
- 产物：Markdown、HTML、manifest、原图和来源索引组成 ZIP。
- 产物 Artifact ID：`1f88552a-7566-40aa-a07f-3c09f1952a87`。
- 协议材料：bundle 根目录新增 `materials.json`，正文标记为 `source_text`，图片标记为 `source_image`，资源 URI 指向 `assets/images/`；协议快照由针对性测试校验。
- 失败/恢复：一次 CDN 图片请求发生 TLS EOF；增加有限重试后，续跑复用已经落盘的素材并成功完成。
- 边界：这是单样本公开访问验收，不证明抖音所有图文状态都可获取；登录、验证码、过期签名 URL 和地区限制仍需如实报告。

### 开发环境限制

- 当前本机 backend venv 是 Python 3.9/LibreSSL；全量测试需用本地要求的 `PYTHONPATH=backend` 调用方式。
- Docker Registry 拉取 `python:3.11-slim` 曾返回 EOF，因此该轮没有完成新 Docker 镜像构建验证。
- 页面真实验收使用隔离 headless Chromium 和 `/tmp` 下的 SQLite/本地存储，不使用个人 Chrome profile、Cookie 或模型凭据。

### 本轮验证

- 当前基线：`dev`，本轮提交后更新；工作区状态以 Git 为准，本轮未合并 `main`。
- 协议与公开页面针对性测试：`49 passed, 1 warning`。
- 仓库根目录全量测试：`434 passed, 143 warnings`。
- 前端生产构建：`npm run build` 成功；仅有 `generated-samples` 和 `semantic-units` 两处既有 Hook 依赖警告。
- 静态编译检查：`web_content_service.py`、`jobs.py`、`read_v1.py` 通过 `py_compile`。
- 本轮新增覆盖：`materials.json` 生成、`envoy.read/1` 版本字段、正文/图片/OCR 来源标记和图片资源 URI。
- 真实抖音页面回归：隔离 Chromium 读取到标题、作者“抖个不停”、531 字正文、4 张图片和 0 个视频 URL；未使用 Cookie 或个人浏览器配置。
- 独立 CLI、MCP 与协议针对性测试：`31 passed`；OCR 档位专项 `5 passed`；MCP bridge 单测 `14 passed`，覆盖新版发现/工具列表/工具调用、短状态文本、旧版初始化协商、坏 JSON 和工具错误；读取器测试覆盖预算续读、同一读取内 DNS 复用、网络重试/字节上限、私网阻断、缓存 TTL/容量及符号链接保护、敏感 URL 脱敏和签名图片省略。
- Agent 能力发现：`envoy doctor --json` 与 MCP `envoy_doctor` 共用 `envoy.capabilities/1` 目录；正常读链接直接调用 `envoy_read`，仅在能力不明确时调用 doctor；MCP schema 暴露受限的宿主 `image_input` 声明，Agent 按目标和 coverage 决定是否续读或交接视觉资源；视觉宿主可通过 `envoy_observe_visual` 回传带来源的有限观察。
- 目标驱动证据提示：网页读取保留原始正文坐标不变，根据 `goal` 返回有限的文本候选 offset 和图片相关性排序；候选只是定向续读提示，不能替代实际展开。
- 本轮 CLI/MCP 回归：`backend/.venv/bin/python -m pytest -q tests/cli/test_envoy_cli.py tests/cli/test_mcp_bridge.py`，`38 passed`；源码 doctor smoke 输出能力目录，标明独立运行时不支持视频/音频、扫描 PDF OCR 与 Office，并声明不访问凭据。
- 本轮全仓回归：`PYTHONPATH=backend backend/.venv/bin/python -m pytest -q`，`434 passed, 143 warnings`；警告包括 LibreSSL/urllib3 与既有 FastAPI `on_event` 弃用提示。
- 本轮证据闭环定向回归：`63 passed, 1 warning`；覆盖视觉观察工具、来源 URL 脱敏、重复/重叠续读拒绝、零续读轮次、图片纯视觉页面、挑战页、视频未读取覆盖、OCR `pro` 传递和网页材料 OCR 块。
- 本轮 goal 证据选择回归：新增文本候选 offset 与图片排序测试；不改变原文坐标或续读账本。
- 本轮目标选择定向回归：`backend/.venv/bin/python -m pytest -q tests/cli/test_envoy_cli.py tests/cli/test_mcp_bridge.py tests/core_protocol/test_read_protocol.py`，`51 passed`；无 `goal` 时相关扩展字段省略，保持 MCP 精简结果体积。
- 本轮视觉观察定向回归：`backend/.venv/bin/python -m pytest -q tests/cli/test_envoy_cli.py tests/cli/test_mcp_bridge.py tests/core_protocol/test_read_protocol.py`，`52 passed`；`envoy_observe_visual` 接收原响应的 `material_id` 时，将最多 32 条去重后的观察写入本地材料快照；不保存图片字节，观察 URL 继续脱敏。
- Skill 产品化：`scripts/package_envoy_skill.py` 生成可重复的 `envoy-skill.zip`，含 `envoy-skill.json`、`SKILL.md`、协议参考和通用 `hosts/mcp.json` 配置；包内不含凭据、本地配置、浏览器状态或素材，Skill bundle 测试通过。
- 普适化与 token 调度：无扩展名图片按响应 `Content-Type` 自动进入视觉交接；视频/音频响应明确返回独立运行时能力缺口；文本覆盖按 ASCII 四字符/非 ASCII 单码点提供 tokenizer 无关估算；Hook 标记为可选宿主外层能力，不影响核心协议。
- Token 预算代理：`Budget.max_response_tokens`、`Budget.max_total_response_tokens` 和对应 expand 限制可选启用；initial read 与 expansion 共用累计估算账本，字符/Token 代理取更严格上限。预算按文本逐码点近似计数，不代表供应商 tokenizer 精确用量或计费，且总额度可以小于单次响应 ceiling。
- 本轮预算/多语言专项：CLI、MCP 和协议测试 `60 passed`，新增总额度低于单次上限、仅设置累计上限、连续续读耗尽以及中文/阿拉伯语预算用例。
- 视觉复用缓存：包含图片的短网页和直链图片都会登记已交付视觉资源，以接收宿主观察；观察必须匹配材料中的 resource/block/source，防止错绑；同版本材料续写时保留仍有效的观察。`envoy_visual_history` 可按材料/资源 ID 有界读取已存观察，支持追问复用。零保留或快照不可用时，观察仍作为当前结果返回，但明确不持久化。
- 短材料多轮检索：完整且不超过 256K 字符的来源按本地 TTL 缓存；`envoy_search` 对保留正文按新问题检索，返回原始偏移/页面定位，并扣减共享累计预算，避免追问时重新下载或顺序展开全文。
- 本轮视觉追问复用：MCP `envoy_visual_history` 支持资源过滤及最多 16 条/20K 字符上限，返回带来源 locator 的 `host_visual` blocks；不重新下载图片，也不触发视觉模型。
- 本轮材料追问搜索：新增 MCP/CLI `envoy_search`；短文本材料（<=256K 字符）按保留 TTL 缓存，长材料复用续读快照；支持中日韩词项拆分、原文 offset/PDF 页定位、受限结果数/字符/Token，检索结果扣减与 expand 相同的累计账本。
- 独立 CLI 发行包验证：使用本机虚拟环境普通 wheel 安装成功；`envoy doctor --json` 正常，实际读取 `https://example.org/` 返回 `ready`。
- MCP 发行进程 smoke：已安装 wheel 通过 `envoy mcp` stdin/stdout 完成新版发现、工具列表和 `2025-11-25` 旧版初始化；独立网页读取工具 schema 从约 4.7 KB 缩至约 1.2 KB；新版工具调用只返回短状态文本，完整材料仅放在结构化字段中。
- 图片视觉协作专项：直接图片 URL 在宿主具备 URI 图片输入时返回 `waiting_for_host/needs_host`、资源 URI 和 `inspect_visual` 动作；无图片能力时结构化返回 `missing_capability`，不内联 base64。
- 链接读取适配：PDF 扩展名直链和按 `Content-Type` 识别的无扩展名 PDF，均按页提取并生成 `pdf_page` 定位；长 PDF 从本机快照续读；纯文本/Markdown/CSV/JSON 链接按响应类型或文件后缀自动识别，长文本用本机快照按范围续读；扫描版 PDF 明确报告 OCR 能力缺口。真实 IETF RFC 9110 文本链路抽取约 503K 字符来源，首响应遵循预算并提供续读句柄。
- 真实 PDF 验收：读取公开 W3C `dummy.pdf`，得到 1 页、文本 `Dummy PDF file`、状态 `ready`；最新 wheel 安装后的 PDF smoke 同样返回 `ready` 和 `pdf_page` 定位。
- 真实长文本验收：IETF RFC 9110 约 503K 字符经已安装 CLI 读取，返回 `partial` 与续读句柄。
- 长材料与稳定性：首读遵循响应字符预算并明确返回 `partial` 覆盖；来源硬上限也会显式报告；`expand` 从本地快照返回非重叠范围，累计返回范围受 `max_total_response_chars` 约束；网络请求有有限重试与流式字节上限；快照权限为用户私有并遵循请求 TTL（最多 7 天）/100 项清理策略；来源 URL 凭据参数脱敏，签名图片不向宿主暴露。网页内容在 Skill 指令中明确作为不可信数据，不能覆盖 Agent 指令。
- OCR 配置：当前后端支持 `fast/balanced/quality/pro` 四档 OCR 模型选择，默认 `balanced`；`quality` 使用 PP-OCRv5 server，`pro` 使用 PP-OCRv6 medium，作为当前 Envoy 的最高通用精度档，模型按首次使用按需下载；旧版 PaddleOCR 自动兼容回退，Tesseract 仍作为降级路径；Skill/MCP 的 `ReadRequest` 支持 `ocr_profile`。OCR 进入派生材料协议，不作为默认全量 Agent 上下文。当前本机 PaddleX OCR 模型缓存约 177 MB，实际部署大小随档位和运行时版本变化。
- OCR 下载诊断（本地改动，故障注入已验）：PaddleOCR 模型初始化/下载错误将保留脱敏错误、所选档位、Tesseract 回退情况和 `ask_to_use_default_ocr` 建议，并进入视频语义材料和任务摘要；真实代理故障尚未在外部网络环境复现。
- 连续证据账本与会话隔离（本地改动，专项回归已验）：`search` 与 `expand` 共用 source offset 区间账本，visual history/observation/search/expand 校验 session；相同 URL 与 revision 的独立 session 得到不同 material ID。当前文件存储仍无跨进程事务锁/原子预算扣账，不能宣称同 session 并发写入已完全串行化。
- Skill 平台字幕尝试（本地改动）：有专用 yt-dlp extractor 时只探测元数据并下载公开 VTT/JSON3/SRT 字幕，不下载视频、不读浏览器 Cookie；cue 返回 `video_time` locator。2026-10-19 本地真实 YouTube 返回 `The page needs to be reloaded`，Bilibili 返回 HTTP 412，因此真实平台字幕成功尚未验收；直链 MP4 与字幕解析/搜索时间定位分别有真实 HTTP/离线 fixture 测试。
- GitHub 安装发行（本地改动，尚未发布）：新增自包含 CLI wheel 构建、Skill 安装说明、Release tag 版本校验及 wheel/Skill ZIP/SHA-256 发布工作流。尚未实际创建 GitHub Release 或验证宿主 MCP 配置自动写入；安装步骤仍需用户明确确认所用宿主。

## 下一次状态更新必须包括

- 当前 `dev` commit 和工作区是否干净。
- 已完成/未完成的 D 编号与 M 里程碑。
- 实际运行的测试命令及通过/失败数量。
- 真实来源、材料覆盖、图片/媒体数量和产物文件校验结果。
- 未验证能力、环境失败和外部站点限制。
