# OCR 后端与中英文识别

更新时间：2026-10-24
涉及代码：`packages/envoy-cli/src/envoy_cli/ocr.py`、`packages/envoy-cli/src/envoy_cli/reader.py`
协议字段：`envoy.read/1` 的 `ocr_backend`（可选，向后兼容）

## 目标

页面文字不稳定时，用户以图片为准。此前的 OCR 只能依赖 PaddleOCR/Tesseract，且默认语言硬编码为 `eng`，中文截图识别质量差，旧英文缓存还会污染后续中文读取。本次改造：

1. 新增 macOS 原生 Vision OCR 后端（accurate 模式 + 语言纠正），中文识别质量优先；
2. 新增跨平台 RapidOCR 后端（中文模型，无需系统依赖）；
3. 默认语言从 `eng` 改为 `auto`，按后端解析为“简体中文 + 英文”，同时保留英文兼容；
4. 缓存键包含解析后的语言与实际后端，旧 `eng` 缓存自然失效；对做 traineddata 适配的 Tesseract，键固定使用配置语言（如 `chi_sim+eng`），不随本机语言包变化而漂移；
5. 缺失可选依赖时给出明确安装提示，而不是静默降级或崩溃。

## 后端矩阵

| 后端 | 平台 | 可选依赖 | 中文效果 | 默认语言（`auto`） | 备注 |
| --- | --- | --- | --- | --- | --- |
| `vision` | macOS | `pyobjc-framework-Vision`、`pyobjc-framework-Quartz` | 最好，系统级 accurate 模式 + 语言纠正 | `zh-Hans+en-US` | macOS 默认首选；Linux/Windows 自动跳过 |
| `paddleocr` | 跨平台 | 自行安装 `paddleocr` | 好 | `ch` | 原先唯一中文后端，模型下载较重 |
| `rapidocr` | 跨平台 | `rapidocr-onnxruntime` | 好 | `ch` | 新增，ONNX 运行时，无需系统库 |
| `tesseract` | 跨平台 | 系统 `tesseract` 可执行文件 | 一般，取决于 traineddata | `chi_sim+eng` | 原后备方案，保留 |

`auto` 的选择顺序为：`vision` > `paddleocr` > `rapidocr` > `tesseract`。不可用的后端会被跳过；若一个都不可用，报错信息会同时列出各后端的安装方式。

安装示例：

```bash
# macOS 原生 Vision（推荐）
pip install 'envoy-cli[ocr-vision]'

# 跨平台 RapidOCR
pip install 'envoy-cli[ocr-rapid]'
```

默认安装仍保持轻量：不安装可选依赖时，只有系统已装 Tesseract 才可用，否则读取图片会得到带安装提示的明确错误。`envoy doctor` 的 `runtime.ocr` 会列出四个后端的实时可用性，便于先确认环境再读图。

## 语言解析

请求可传 `ocr_language`（`envoy.read/1` 已有字段），新增全局环境变量 `ENVOY_OCR_LANG`。两层都未设置时为 `auto`。

规范化分组：

- 简体中文：`ch`、`zh`、`zh-CN`、`zh_Hans`、`chi_sim`、`chs`、`cn`、`chinese` → `zh-Hans`
- 繁体中文：`chi_tra`、`zh-TW`、`zh_Hant`、`chinese_cht`、`cht` → `zh-Hant`
- 英文：`eng`、`en-US`、`en_us`、`english` → `en`
- 其他标签原样透传，避免破坏已有的后端专用标签

每个后端再把规范语言解析成其原生形式：

| 规范语言 | `vision` | `tesseract` | `paddleocr` / `rapidocr` |
| --- | --- | --- | --- |
| `auto` | `zh-Hans+en-US` | `chi_sim+eng` | `ch` |
| `zh-Hans` | `zh-Hans+en-US` | `chi_sim+eng` | `ch` |
| `zh-Hant` | `zh-Hant+en-US` | `chi_tra+eng` | `chinese_cht` |
| `en` | `en-US` | `eng` | `en` |

解析后的语言写入 OCR 结果（`language` 字段）与 coverage details，便于排查“为什么这次识别效果不同”。

对 `tesseract` 还有一层环境适配：`auto` 会读取本机 `tesseract --list-langs`，缺少 `chi_sim` 时退回可用的 `eng`，部分缺失时用剩余语言；这样未安装中文语言包的旧环境不会因为默认语言变化而整体失败。显式指定的语言不做替换，缺语言包会得到包含 stderr 的明确错误。

## 请求与配置入口

优先级：请求字段 > 环境变量 > `auto`。

| 设置 | 请求字段 | 环境变量 | 取值 |
| --- | --- | --- | --- |
| 后端 | `ocr_backend`（新增，可选） | `ENVOY_OCR_BACKEND` | `auto`、`vision`、`paddleocr`、`rapidocr`、`tesseract` |
| 语言 | `ocr_language`（已有） | `ENVOY_OCR_LANG` | `auto` 或上表的别名 |

`ocr_backend` 为可选字段，旧请求不传时行为不变（走 `auto` 自动选择），因此协议保持向后兼容。MCP `envoy_read` 工具的 `inputSchema` 同步暴露该字段。

显式指定不可用的后端不会被静默替换：会返回包含安装命令的 `RuntimeError`，避免“以为在用 Vision，实际用了 Tesseract”。

## 缓存

OCR 缓存位于本地数据目录 `ocr-cache/`，键由以下内容组成：

- 图片 SHA-256；
- OCR profile（`fast`/`balanced`/`quality`/`pro`）；
- **解析后的语言**（不是原始请求语言）；
- **实际使用的后端**与运行时引擎版本；
- `OCR_PIPELINE_REVISION` 管线版本。

因此：

- 同一张图从 `eng` 切换到 `auto` 时，中文缓存与旧英文缓存互不覆盖；
- PaddleOCR 升级或 Vision 依赖变化不会复用旧结果；
- 管线算法修订时整体失效（当前 revision 为 `ocr-backends-v3`）。

## 失败行为

| 场景 | 行为 |
| --- | --- |
| macOS 未安装 Vision/Quartz | Vision 不可用，`auto` 继续尝试其他后端 |
| Linux/Windows | Vision 自动跳过，不影响其他后端 |
| `rapidocr` 未安装 | 自动跳过；显式指定时报 `RapidOCR is not installed` 并给安装提示 |
| 无任何可用后端 | 报 `No ... OCR backend ...`，列出 `ocr-vision`、`ocr-rapid`、PaddleOCR、Tesseract 的安装方式 |
| 图片无法解码或 Vision 请求执行失败 | 抛出明确 `RuntimeError`，不会被误判为“无文字” |
| 后端返回空结果 | 正常返回空 regions，不影响读取流程 |

## 验证

新增测试：`tests/cli/test_ocr_backends.py`，覆盖可用性探测、`auto` 优先级、缺依赖时的安装提示、未知后端拒绝、语言规范化/解析矩阵、Vision bbox 坐标系转换（左下 → 左上）、Vision 请求参数（accurate/language correction/languages）、RapidOCR 多种返回结构、reader 的请求/环境变量优先级、解析语言写入 coverage、旧 `eng` 缓存不污染 `auto`、`ReadRequest` 与 MCP schema 字段。

既有测试：`tests/cli/test_ocr.py`（Tesseract/PaddleOCR/缓存/直链图片读取）保持通过；所有外部 OCR 调用均被 mock，测试不下载模型。

```bash
PYTHONPATH=packages/envoy-core/src:packages/envoy-cli/src \
  uv run --python 3.11 --with pytest --with pydantic --with httpx --with pypdf --with Pillow \
  pytest tests/cli/test_ocr.py tests/cli/test_ocr_backends.py -q
```

## 命令行入口

- `envoy ocr status [--json]`：打印各后端可用性、引擎版本、`auto` 当前会选择的后端以及 `ENVOY_OCR_BACKEND` / `ENVOY_OCR_LANG` 的生效值。
- `envoy ocr test <image> [--backend auto|vision|paddleocr|rapidocr|tesseract] [--language auto|zh-Hans|zh-Hant|en] [--profile balanced]`：对一张本地图片跑完整 OCR 并输出后端、解析语言、region 数与文本，用于在你自己的截图上比较后端。可选依赖安装示例见上文；`envoy doctor --json` 的 `runtime.ocr` 仍提供等价的可用性信息。

设置示例：

```bash
export ENVOY_OCR_BACKEND=vision     # 固定后端，避免自动选择漂移
export ENVOY_OCR_LANG=zh-Hans       # 默认 auto；也可显式 en / zh-Hant / chi_sim 等别名
```

## 隐私

- 所有识别在本地进程内完成：Vision 走 macOS 系统框架，RapidOCR/PaddleOCR 走本地 ONNX/Paddle 模型，Tesseract 走本机可执行文件；图片内容不会上传到任何服务。
- OCR 结果（含识别文本与 bbox）缓存在本地数据目录的 `ocr-cache/`，可直接删除以强制重新识别；缓存键含图片哈希、profile、语言、后端与引擎版本，不会跨图复用。

## 质量取舍与实测

同一张含中英混排文字的截图（44px 字号，2026-10-24 在 macOS 26.5.2 arm64 实测）：

| 后端 | 结果 | 结论 |
| --- | --- | --- |
| `vision`（auto 语言） | 两行分别完整识别为 `Envoy OCR 中文识别测试 2026`、`Hello World, mixed 中英 text.` | 默认首选，行聚合准确，中英混排友好 |
| `tesseract`（auto=`chi_sim+eng`） | 中文可识别但被拆成单字/短词多行，英文正确 | 兜底可用；比旧默认 `eng` 明显改善中文 |
| `paddleocr` / `rapidocr` | 中文质量介于两者之间，取决于模型版本 | 跨平台备选，首次使用可能下载模型 |

fallback 规则：`auto` 依 `vision > paddleocr > rapidocr > tesseract` 选择第一个“已安装”的后端；显式指定后端时不做静默替换，缺依赖直接报带安装命令的错误。若识别的行顺序或 bbox 不符合预期，可先用 `ocr_profile` 调整处理档位，再切换 `ocr_backend` 对比结果。
