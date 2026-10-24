# Agent Link Reader 实施状态

更新时间：2026-10-20（Asia/Singapore）
计划基线：[`agent-link-reader-v1.md`](../product/agent-link-reader-v1.md)  
计划所依据的提交：`fb582660072bfe04c86ddb67b1810a61a3dcbd4b`  
开发分支：`dev`

当前开发基线：`dev@7b31a99`；视频媒体闭环及平台探测记录已推送至 `dev`，`main` 仍为 `f0b2fd6`，尚未合并/发行。GitHub Release 资产仍需由版本 tag 触发发行工作流。

## 状态定义

- **已验证**：本地代码或真实验收已有可复现证据。
- **进行中**：已有实现，但还未达到计划定义的稳定闭环。
- **未开始**：仅在产品方案中定义，不能作为当前已交付能力宣传。

此文档记录当前实现，不把产品方案中的安装器、Skill、性能数字或网站隔离门槛写成已完成事实。

## 当前能力

| 能力 | 状态 | 证据 / 缺口 |
| --- | --- | --- |
| 单一链接输入，后端按链接类型自动分流 | 进行中 | 视频和公开视频/图文走不同处理任务；公开视频 bundle 已开始输出统一材料协议 |
| 常见视频链接与本地视频处理 | 网站已验证，Skill 进行中 | 独立 Skill 已实现公开字幕优先、本地 Whisper ASR、预算化直链/平台下载与宿主显式请求抽帧；YouTube TED 样本当前已走通 427 条公开英文字幕和 retained-material search，Bilibili TED `BV1zj411K7zL` 无字幕 ASR 走通 0-60 秒 17 条英文 cue，Wikimedia OGV 走通真实 ASR，MDN MP4 走通真实抽帧与视觉观察复用。另一条 Bilibili 样本正确返回 `no_speech`；抖音、Vimeo、Dailymotion 与更多平台的稳定可达性仍未完成验收 |
| 抖音公开图文正文与配图 | 已验证（开发验收） | 2026-10-18 本地 Envoy 任务读取指定图文，提取 534 字符正文和 4 张原图，图片 OCR 子任务成功，产物 ZIP 校验通过 |
| 微信公众号和通用网页 | 进行中 | 通用公开页面提取已接入同一入口；微信公众号尚缺固定真实样本验收和长文/延迟资源测试 |
| PDF、Office、独立音频/图片入口 | 进行中 | 独立 Skill 支持 PDF 文本层、纯文本/Markdown/CSV/JSON 直链和公开图片视觉交接；PDF 按页定位，长纯文本按字符范围续读；无文本层扫描页明确提示 OCR 能力缺口；独立音频 URL、Office 和扫描页 OCR 仍待接入 |
| 可供 Agent 消费的 Markdown/HTML/ZIP | 已验证（开发验收） | 公共图文 ZIP 含索引、来源 Markdown、HTML、manifest、`materials.json` 和全部配图；Markdown 适合正文消费，协议 JSON 提供渐进读取和资源定位 |
| 长任务、任务状态、重试与素材检查点 | 进行中 | 现有网站任务链可恢复；Worker 租约、并发配额、取消贯通仍未达到方案门槛 |
| 独立无数据库处理核心 | 进行中 | `packages/envoy-core` 已承载第一版 `envoy.read/1` 请求/材料/覆盖/资源 schema；现有 worker 尚未整体迁出 ORM |
| CLI、宿主 Skill 与 MCP 桥 | 进行中 | `dev` 有 `packages/envoy-cli`、`envoy read/expand/doctor/mcp`；MCP 支持新版 per-request 元数据和旧版初始化协商；Skill 按目标指导 read/search/targeted expand。字幕时间定位、长行命中、跨 search/expand 区间账本、累计预算、material 文件锁、稳定视觉资源 ID、观察关联校验、租约帧交接均已实现。平台访问仍因登录/站点挑战而不稳定，未据此宣传全平台已验收 |
| 多宿主链接优先 hook | 已验证（开发验收，`dev`） | `envoy hook prompt --host {claude-code,codex,dsh}` 共享同一 URL 提取/内网过滤/回退策略；Claude Code 插件已在本机通过 marketplace 安装并在 `--debug` 日志确认 `UserPromptSubmit` 注入 668 字符 `additionalContext`；Codex 插件已安装到 `~/.codex/plugins/cache/envoy/envoy/0.1.6`，因 0.156 不再加载插件 hook，用户级 `~/.codex/hooks.json` 已由 `envoy hook install --host codex` 写入并实测注入；DSH 使用 `agent/pre-step`。策略明确不替代宿主联网搜索：无链接检索仍用原生搜索，Envoy 只处理用户链接、视频/音频和需完整来源证据的读取，并通过分段读取/search/expand 控制上下文。 |
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
- 2026-10-23 本地中文 OCR smoke：当前源码 `envoy doctor --json` 报告 Tesseract 可用、PaddleOCR 未安装；系统 Tesseract 5.5.2 含 `chi_sim`。通过完整 `read_public_page` 直链图片路径（图像源元数据/下载由本地 fixture 提供，OCR 实际调用系统 Tesseract）处理临时生成中文图，规范化空白和全/半角冒号后与预置文字完全匹配，返回 18 个 `image_region` 区域、平均置信度 0.905，bbox 均归一化有效，且保留宿主视觉 handoff。回归测试覆盖预算未返回 OCR span 的 `envoy_search` 追问及 locator 回链、无宿主图像能力时 OCR-only 可用、OCR 失败时保留视觉 handoff/正确 blocked。首次公开 Wikimedia 图片尝试返回 HTTP 404；另一个公开图片链接只验证了图像 handoff，未运行 OCR。故该结果不是公开真实来源验收，也不验证 PaddleOCR。
- 2026-10-23 OCR follow-up：直接图片 OCR 新增 `envoy_search` 对未首读回传区域的 image-region locator 回链；以本机 Tesseract 合成中文图验证了派生材料搜索和按内容哈希命中 OCR 缓存。另复现并修复 OCR 能力升级时错误复用旧后端缓存的问题：Tesseract 缓存存在后 PaddleOCR 变为可用，旧实现仍返回 Tesseract 结果；现在缓存键仅匹配当前优先后端，新增回归测试覆盖。OCR 定向测试 13 项通过，CLI/协议/发行包/Skill Bundle 及相关后端组合测试 361 项通过（9 条既有弃用/LibreSSL 警告）。合成图仅验证链路/定位/缓存，不作为 OCR 准确率或公开平台验收。
- 2026-10-23 公开 OCR 同源对照：当前 MCP `envoy_doctor` 确认为干净 Release `0.1.6`，对公开 Wikimedia 中文招牌图片请求 `ocr_profile=balanced` 只返回原图视觉 handoff，没有 OCR blocks；该 Release 尚无 direct-image OCR。隔离安装本地 preview wheel 读取同一 URL，Tesseract `eng` 返回 77 个区域、13 个零置信度；设置 `ENVOY_OCR_LANG=chi_sim` 重读返回 93 个区域、26 个零置信度，字符仍为严重噪声。两种语言都明确为 `partial` 并保留视觉 handoff，因此本样本是 OCR 困难/失败边界，不能算中文准确率验收。preview 暂时依赖运行环境变量配置 OCR 语言，MCP 请求没有逐次指定语言字段；需在后续产品体验工作中评估语言选择入口及更适合的清晰印刷体中文验收样本。
- 2026-10-23 清晰简体中文 OCR 样本：使用独立安装 wheel `0.1.6+preview.g82b8add3025a` 及 Tesseract `chi_sim` 读取 Wikimedia `SimChinesetexttest.png`，返回 32 个有序 OCR region、保留原图视觉 handoff，1 个零置信度并标记 `partial`；主要正文区域多数可读，但首行出现明显误识别。另测 Wikimedia 100 个常用汉字表，返回 196 个 region，bbox 均为 0-1 归一化有效坐标，12 个零置信度并标记 `partial`；字符与拼音区域交错，样例抽查显示 OCR 顺序/拼音识别混杂，不能当作准确提取。两例说明来源哈希、区域、顺序、置信度、partial 边界和视觉升级路径工作；不足以接受中文 OCR 准确率，且逐次 `ocr_language` 尚未加入 ReadRequest/MCP，当前通过环境变量选择语言。
- 2026-10-23 隔离 uv tool 安装端到端 smoke：将 preview wheel 安装到独立 `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR`，未覆盖正式 `/Users/apple/.local/bin/envoy`。临时 `envoy --version`/doctor 正确标记 `local-preview`、revision `82b8add3025a`、dirty build、Tesseract 可用；独立 executable 的 MCP stdio initialize + tools/list 以 0 退出、无 stderr 并返回 9 个工具。再由安装版 CLI 对上述公开简体中文样本读取，source SHA 与源码直调相同为 `61a574dd...`，返回 32 OCR regions、1 个零置信度、`partial` 和一个 inspect_visual handoff；重复读取 `cache_hit=true`。这验证独立安装产物的 CLI/MCP/OCR/cache 路径，不等于精度通过，也不等于 GitHub 多平台 CI。
- 2026-10-23 PaddleOCR 3.x 集成修复：独立 CPU 验收环境安装 PaddleOCR 3.7.0/PaddlePaddle 3.3.1，不改变正式 CLI 或仓库依赖。真实调用发现 `chi_sim`/`chi_tra` 标签直接传 Paddle 会在初始化时报 `No models are available`；按后端分别映射为 `ch`/`chinese_cht`。继续发现 PaddleOCR 3.x `predict()` 返回 `OCRResult`，数据位于 `.json["res"]`，旧代码仅接 dict 导致 0 regions；增加包装对象解包。最后发现图片缓存后缀固定 `.img` 被 PaddleX 拒绝，下载现在保留基于受限 MIME 映射的 `.png/.jpg/...` 后缀，并尽量迁移旧内容哈希 `.img` 缓存。新增 3 个语言/结果格式回归和 1 个图片后缀回归，OCR 测试 17 项通过。
- 2026-10-23 Paddle 真实 OCR reader 验收：通过独立 CPU PaddleOCR 3.7.0 环境和当前源码 reader 读取 Wikimedia 简体中文字符表，source SHA `76c4efc1...`，返回 55 个 `paddleocr-balanced` OCR blocks，0 个零置信度、bbox 均有效、PNG 内容哈希缓存正常，并保留原图 `inspect_visual` handoff。识别同时包含汉字和拼音，部分拼音乱码；`extraction=complete` 仅表示后端完成返回区域，不代表字符准确。清晰段落样本 Paddle 返回 0 区域，而 Tesseract 返回 32 区域且 1 个零置信度。小红书模拟无 OCR 区域回归现确认 `partial` 且产生视觉检查 Need。结论：Paddle 后端的语言、结果对象、输入文件扩展名集成问题已修复；中文内容准确率仍未通过，不得把 backend region coverage 当成字符准确率。
- 2026-10-23 ASR 安装版/源码 doctor 对照：正式隔离 CLI `0.1.6` 先前未安装 Whisper；本轮通过 `envoy asr install --model base` 使用实测延迟最低的清华 PyPI 镜像安装 Whisper/PyTorch，命令返回 ready，未重新下载模型。正式安装版 doctor 现在确认 Whisper 已安装且 base 模型文件存在，但它没有 `asr_configured`、摘要校验或 `asr_operational` 字段；安装版 `prepare_asr_model` 也不持久化 checkpoint 摘要。正式 Release 真实 `read-local-video` 测试通过 bounded worker 对本地合成语音完成 ASR，返回 `[00:00.000] Unvoil local speech, recognition test.`，约 1.79 秒；复测约 2.05 秒、零下载，仍有一个词误识别；这证明 runtime 可运行但不是 ASR 准确率验收。未发布源码 doctor 曾发现 `~/.envoy/asr.json` 中旧摘要 `eca4af...` 与 base 文件及当前 Whisper registry 相符的 `ed3a0b...` 不同，故报告 `asr_model_verified=false` / `asr_operational=failed`。随后用当前仓库源码调用 `asr setup --model base`，通过正式隔离 CLI 已安装的 Python/Whisper 环境重新校验同一模型并持久化注册表摘要，没有重新下载权重；复核时 `~/.envoy/asr.json` 持久值仍为 `ed3a0b...`，源码 `doctor --verify-asr` 再次返回 `asr_installed=true`、`asr_configured=true`、`asr_model_verified=true`、`asr_model_available=true`、`asr_operational=verified`。这使本机升级/源码状态验收闭环，但 Release 包本身的 doctor 字段仍旧，不能把工作区源码能力误述为已发布能力。
- 2026-10-23 双 Python standalone 验证：当前 `dev@82b8add` 的 `tests/cli tests/core_protocol tests/test_envoy_cli_distribution.py tests/test_skill_bundle.py tests/test_video_evidence_eval.py` 在 Python 3.9 后端环境 **278 passed, 1 LibreSSL warning**，在干净 uv Python 3.11 环境 **278 passed**。自包含 wheel 构建成功并隔离安装，wheel 标记 `local-preview`、`BUILD_REVISION=82b8add3025a`、`BUILD_DIRTY=true`，包含直链 OCR 和 Tesseract page-dimension pipeline revision。测试不是 GitHub Linux/Windows/macOS CI；远端 dev/main 仍为 `0d2f5f4`，候选提交未推送，故禁止据此宣称跨平台 CI 通过或发布新 Release。
- 公开中文图像 OCR 边界样本：Wikimedia 的繁体中文店铺招牌经当前源码 Envoy `read_public_page` 实际下载并调用 Tesseract 5.5.2，`chi_sim` 返回 16 区域、7 个零置信度；`chi_tra` 返回 23 区域、17 个零置信度。两个结果均标记 OCR `partial` 并保留 URI image handoff。该照片包含装饰字体，OCR 文本明显不可靠；只作为真实低质量边界样本，不算中文识别接受样本。修复了 Tesseract 无 Pillow 时 bbox 被错误归一化到 1x1 的问题，解析 TSV 页面尺寸并用 OCR cache pipeline revision 使旧错误缓存失效。
- OCR 下载诊断（本地改动，故障注入已验）：PaddleOCR 模型初始化/下载错误将保留脱敏错误、所选档位、Tesseract 回退情况和 `ask_to_use_default_ocr` 建议，并进入视频语义材料和任务摘要；真实代理故障尚未在外部网络环境复现。
- 2026-10-23 OCR 透明 PNG 边界修复：真实 Wikimedia 简体中文样本是带 alpha 的透明 PNG，原始文件直接交给 Tesseract 会返回空结果，白底预览后可见文本。Tesseract 输入现在仅在检测到透明通道时合成白底 PNG；原始 source SHA、区域坐标和视觉 handoff 不变。新增失败到通过回归测试，并新增脱敏 OCR 质量评测器 `benchmarks/ocr_quality_eval.py`，按独立转录计算 CER/WER，报告不输出原文。该修复改善输入预处理，不代表中文 OCR 准确率已接受。
- 连续证据账本与会话隔离（`dev@a6c7b1b` 实现，Windows 回归在 `be855b0`）：`search` 与 `expand` 共用 source offset 区间账本，固定 hash-striped 跨进程 material 锁序列化预算读改写且不删除活动 inode；同 session 同 URL 初读串行化，ASR 模型缓存使用跨进程单槽位。thread/子进程预算竞争、锁 inode 生命周期已测；GitHub Windows standalone job 已通过。
- Skill 平台字幕尝试：有专用 yt-dlp extractor 时只探测元数据并下载公开 VTT/JSON3/SRT 字幕，不下载视频、不读浏览器 Cookie；cue 返回 `video_time` locator。YouTube `iG9CE55wbtY` 当前真实 read 成功，返回 427 条字幕并支持搜索定位；此前同 URL 曾受网络状态影响返回 `The page needs to be reloaded`。Bilibili metadata 当前可解析但没有字幕，选定 ASR 区间返回 `no_speech`；抖音分享图文链接当前 extractor 报 unsupported；直链 MP4/OGV 与字幕解析/搜索时间定位分别有真实验收和 fixture 测试。
- 2026-10-19 独立视频媒体验收（已推送 `dev@a6c7b1b`）：clean Python 3.11 wheel 已安装 `openai-whisper==20250625` 媒体依赖并运行真实本地 Whisper `base`。Project Gutenberg/LibriVox 样本前 50 秒输出英文 8 段，耗时约 2.1 秒，抽查的开头与对应原文吻合；Wikimedia `Tear_down_this_wall.ogv` 最终 wheel 真实端到端：音频下载 94,058,123 bytes、约 163 秒，0–60 秒返回 8 条英文 cue，`video_time` 与范围 coverage 正确；Bilibili 音轨 600–660 秒的重复幻听被 no-speech 概率过滤，状态为 `no_speech`，不产生 transcript。MDN `flower.mp4` 无语音样本约 1.13 MB 下载、FFprobe 补出 5.055 秒，抽取 842x474、399,108 像素的 3.0 秒帧；帧文件在 material TTL lease 内可读，MCP visual observation 持久化并由 history 回读。host/provider token 数未知；这不代表完整视频视觉问答效果。
- 本轮检索效率基准：`benchmarks/video_evidence_eval.py` 离线两例 100% 定位预期片段，2 次工具调用返回 207 字符/52 估算 token，对照顺序扫描基线 752 字符/204 估算 token；最近一次本地检索延迟中位数约 2.44 ms（单次小样本，会随主机负载变化）。它不调用答案模型；host/provider 实际 token 数为 `unknown`，这些值只量化本地取证材料缩减。
- 本轮最终回归：全仓 `PYTHONPATH=backend backend/.venv/bin/python -m pytest -q` 为 `524 passed, 143 warnings`；CLI/MCP/protocol/video/OCR/运行时安全子集 `193 passed`；wheel clean-install/console entrypoint/doctor smoke、Skill ZIP 与视频证据 benchmark `5 passed`；最终 wheel 实际安装后的 doctor 与 MCP tools/list smoke 成功。前端生产构建成功，保留两个既有 Hook 依赖警告。告警包括 backend Python 3.9/LibreSSL urllib3 与既有 FastAPI `on_event` 弃用。
- GitHub Actions：`dev@be855b0` run `39276276218` 首次 Windows standalone job 曾失败于测试依赖/ZIP 顺序；修复后 `dev@64cc907` run `39277166622`、`dev@d8f98a9` run `39278125707`、`dev@ca8d945` run `39283200077`、`dev@2efa0ec` run `39285278677`、`dev@64542df` run `39286119485`、`dev@83543ac` run `39288217750`、`dev@c480c1d` run `39290541329`、`dev@2e1bbf9` run `39299917956`、`dev@1091e8c` run `39300829830`、`dev@fb738ce` run `39301606519`、`dev@9144280` run `39302281396`、`dev@9bdf3dc` run `39304857145` 和当前 `dev@7b31a99` run `39305458134` 的四个 job 均通过。
- 最新本机回归：全仓 `PYTHONPATH=backend backend/.venv/bin/python -m pytest -q` 为 `524 passed, 143 warnings`；CLI/MCP/protocol/video/OCR/runtime 子集最近一次为 `193 passed`；wheel clean-install/console entrypoint/doctor smoke、Skill ZIP 和视频 evidence benchmark `5 passed`；wheel 已在独立 Python 3.11 runtime 重新安装并通过 `envoy doctor --json`。前端 production build 成功，保留两个 Hook 依赖警告。Backend venv 的 Python 3.9/LibreSSL urllib3 和 FastAPI `on_event` 弃用仍有 warnings。
- GitHub 安装发行（本地改动，尚未发布）：新增自包含 CLI wheel 构建、Skill 安装说明、Release tag 版本校验及 wheel/Skill ZIP/SHA-256 发布工作流。尚未实际创建 GitHub Release 或验证宿主 MCP 配置自动写入；安装步骤仍需用户明确确认所用宿主。

## 下一次状态更新必须包括

- 当前 `dev` commit 和工作区是否干净。
- 已完成/未完成的 D 编号与 M 里程碑。
- 实际运行的测试命令及通过/失败数量。
- 真实来源、材料覆盖、图片/媒体数量和产物文件校验结果。
- 未验证能力、环境失败和外部站点限制。
