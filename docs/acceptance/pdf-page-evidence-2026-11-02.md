# PDF 扫描页 OCR、双栏阅读顺序与 Page Evidence Index 验收（2026-11-02）

范围：核对本仓库当前代码在「扫描页 OCR」「多栏阅读顺序修正」「`envoy.page-evidence/1` 逐页证据」三件事上实际做了什么、
证据到哪一级、哪些没做；本文只记录本机能复现的测量，不外推到未测的真实文件与场景。

## 1. 证据级别

| 级别 | 含义 | 判定方式 |
| --- | --- | --- |
| `unit_checked` | 离线单元测试或合成夹具上跑出来的结论 | `tests/cli/test_pdf_evidence.py` 等离线测试通过，或本节给出的本机合成夹具实测数字 |
| `verified` | 直接读到实测产物（本机命令输出、真实文件、真实 URL 的读取结果） | 记录命令与输出；本机环境探测见 §5 |
| `not_run` | 没有执行，或执行条件不成立 | 写明缺什么（依赖 / 网络 / 文件 / 后端） |

合成夹具（`tests/fixtures/pdf/*.pdf`，由 reportlab 生成）上的任何结论最高只到 `unit_checked`，不升格为 `verified`。

## 2. 能力与证据

| 能力 | 本次做了什么 | 证据级别 | 证据来源 |
| --- | --- | --- | --- |
| 扫描页 OCR | 无文本层的页在读取时就地渲染为 PNG（200 dpi）再 OCR，识别文本按页码顺序进入正文与 `page_spans`；失败落到 `ocr_failed` 并带 `failed_reason`，不静默留空 | `unit_checked` | 代码：`packages/envoy-cli/src/envoy_cli/reader.py:6433`（`_pdf_ocr_page`）、`:6400`（`_pdf_ocr_failure`）；本机夹具实测：`tests/fixtures/pdf/scanned-text.pdf` 2 页全扫描，1.06 s，`summary.counts = {ocr: 2}`，每页 20/21 字符，后端 `tesseract 5.5.2+ocr-backends-v3`，语言 `chi_sim+eng`，profile `balanced`，dpi 200，confidence 0.674 / 0.682，`flags = ["no_text_layer","ocr_applied"]` |
| 双栏/三栏阅读顺序 | 仅对文本层页做几何重建：行按 `y` 聚类、列按起始 `x` 间距聚类，`columns >= 2` 且 `layout_score >= 0.6` 才重排；重排前后词袋必须一致，否则保留原顺序 | `unit_checked` | 代码：`pdf_layout.py:203`（`analyse_runs`）、`reader.py:6658`（`_pdf_apply_layout`，门槛 `reader.py:6249-6251`：≥600 字符、≥2 栏、score ≥0.6）；本机夹具实测：三栏夹具 1084 字符 → `columns=3, lines=9, layout_score=1.0, complex_pages=[1]`；临时合成双栏页（60 字符级填充行 ×2 栏、1410 字符）→ `columns=2, lines=12, lines_reordered=true, layout_score=1.0`，0.06 s，`flags=["layout_reordered"]`，正文首行为左栏 `L01` 而非流式交错。**注意**：仓库自带的 `two-column.pdf` 每页仅 129 字符，低于 600 字符门槛，读取结果仍是 `reading_order="stream"`、`layout=null`，故该夹具本身不构成双栏证据 |
| Page Evidence Index | `coverage.modalities.text.details.page_evidence` 给出窗口内逐页状态、字符数、文本 sha256、阅读顺序、版式摘要、OCR 摘要与 `citation` 偏移；`summary` 按整个窗口计数，`pages[]` 最多 200 条并标记截断 | `unit_checked` | 代码：`reader.py:6734`（`_pdf_page_evidence`）、`:6252-6253`（schema 与 200 上限）、`:7291`（成功路径挂载）；离线测试：`tests/cli/test_pdf_evidence.py`；本机实测：201 页窗口 0.19 s，`page_count=201, indexed_page_count=201, pages 列出 200, pages_truncated=true, summary.counts.text=201` |

## 3. `envoy.page-evidence/1` 实际字段（以代码为准）

位置：`coverage.modalities.text.details.page_evidence`（PDF 源才有；非 PDF 源的 `details` 不含该键）。
空窗口 / 解析失败分支同样挂载该对象（`reader.py` 早退路径，`returned_chars=0`）。

顶层键（`reader.py:6734` 起）：

| 键 | 类型 | 说明 |
| --- | --- | --- |
| `schema` | str | 固定 `"envoy.page-evidence/1"` |
| `page_count` | int | 文档总页数 |
| `window` | object | `{page_start, page_end}`，本次读取的页窗口 |
| `indexed_page_count` | int | 窗口页数（可超过 200；本机 201 页窗口实测为 201） |
| `pages` | list | 逐页条目，按页码升序，最多 200 条 |
| `pages_truncated` | bool | `pages` 被截断时为 `true` |
| `summary` | object | 按**整个窗口**聚合，不受 `pages` 截断影响 |

`pages[]` 条目键：

| 键 | 类型 | 说明 |
| --- | --- | --- |
| `page_number` | int | 页码，从 1 开始 |
| `status` | str | `text` / `ocr` / `ocr_failed` / `ocr_candidate` / `blank` / `parse_error` / `unprocessed`；未知值被归一为 `unprocessed` |
| `extraction_mode` | str | 正常取值为 `text` / `ocr` / `layout` / `none` / `error`（见 §6 第 4 条） |
| `chars` | int | 该页文本字符数 |
| `text_sha256` | str | 该页文本的 sha256（空文本也有值） |
| `reading_order` | str | `stream` 或 `columns` |
| `layout` | object / null | `{columns, lines, lines_reordered, table_like_rows, layout_score}`，可选 `notes`（≤5 条）与 `header_footer_lines`；未做版式分析时为 `null` |
| `ocr` | object / null | `{backend, language, engine_version, profile, confidence, regions, failed_reason, cache_hit, dpi}`；非 OCR 页为 `null` |
| `citation` | object | `{text_offset_start, text_offset_end, block_ids, page_number}`；偏移是 `full_text` 内绝对位置，与留存材料里的 `page_spans[].start/end` 一致；`details` 本身不重复给出 `page_spans`（只有留存材料带），`block_ids` 为该页已投递时的 `blk-pdf-page-NNNN` |
| `flags` | list[str] | 例如 `no_text_layer`、`ocr_applied`、`ocr_failed`、`ocr_page_cap`、`ocr_budget_exhausted`、`layout_reordered`、`header_footer_like`、`layout_skipped` |

`summary` 键：

- `counts`：七个 `status` 各自计数（顺序即 `_PDF_PAGE_STATUSES`，`reader.py:6254`）；
- `ocr_pages` / `ocr_failed_pages` / `scanned_pages`（`ocr` + `ocr_failed` + `ocr_candidate`）/ `complex_pages`（`layout.columns >= 2`）/ `parse_error_pages` / `not_processed_pages`。

上限与预算（`reader.py:6245-6275`、`6280` 起）：
OCR 默认最多 40 页或窗口页数取小，可用环境变量 `ENVOY_PDF_OCR_MAX_PAGES` 覆盖；读取的墙钟 deadline 到点后剩余页记 `unprocessed` +
`ocr_budget_exhausted`，不会在无提示的情况下丢掉页；版式分析可用 `ENVOY_PDF_LAYOUT=off` 关闭。

## 4. 扫描页 OCR 的后端选择与失败语义

- 渲染：`render_page_to_png(pdf_bytes, page_number, *, dpi=200)`（`pdf_render.py:196`），后端顺序 `pypdfium2`（可选 extra `pdf-render`）→ 系统 `pdftoppm` → `mutool`；都没有时抛 `PdfRenderUnavailable`，安装提示为 `pip install 'envoy-cli[pdf-render]'`（`pdf_render.py:24`）。
- OCR：`available_ocr_backends()`（`ocr.py:169`）探测 `vision`（macOS）→ `paddleocr` → `rapidocr` → `tesseract`，选中后按 profile / language 出结果；`OCR_PROFILES = {fast, balanced, quality, pro}`（`ocr.py:19`），引擎版本串带 `ocr-backends-v3`（`ocr.py:21`）。
- 缓存：`ocr-pages.jsonl`，键由 `sha256(source_digest | page | dpi | render_backend_version | ocr_backend | ocr_engine_version | ocr_language | ocr_profile | PARSER_VERSION)` 组成（`reader.py:6340`），命中时 `ocr.cache_hit=true`。
- 页缓存版本：`PARSER_VERSION = "pdf-page-store/2"`（`pdf_store.py:30`），因为旧记录没有 OCR 元数据；`ocr_failed` / `ocr_candidate` 不入可复用集（`reader.py:6265`），每次重读重试。

## 5. 后端矩阵（本机实测，2026-11-02）

`available_ocr_backends()` 实测输出：`{"vision": false, "paddleocr": false, "rapidocr": false, "tesseract": true}`；
`render_backend_status()` 实测：`{"available": true, "backend": "pypdfium2", "backend_version": "5.14.0"}`。

| 环节 | 后端 | 本机状态 | 证据级别 | 缺失依赖 / 备注 |
| --- | --- | --- | --- | --- |
| 渲染 | `pypdfium2` 5.14.0 + Pillow 13.0.0 | 可用，扫描夹具 2 页 1.06 s 端到端 | `verified` | 本仓库开发 venv 已装 `pdf-render` extra；未安装该 extra 的用户会得到 `PdfRenderUnavailable` 与安装命令 |
| 渲染 | `pdftoppm` | 未安装（`command -v` 无输出） | `not_run` | 需要 poppler |
| 渲染 | `mutool` | 未安装（`command -v` 无输出） | `not_run` | 需要 mupdf-tools |
| OCR | `tesseract` 5.5.2 | 可用，`chi_sim+eng` 中文夹具识别出 20/21 字符，confidence ≈0.68 | `verified` | 需要 `tesseract` 可执行文件与对应语言包 |
| OCR | macOS `Vision` | 不可用 | `not_run` | 缺 `pyobjc-framework-Vision` / `Quartz`（extra `ocr-vision`） |
| OCR | `paddleocr` | 不可用 | `not_run` | 缺 `paddleocr` 包 |
| OCR | `rapidocr` | 不可用 | `not_run` | 缺 `rapidocr_onnxruntime` 或 `rapidocr` 包 |

## 6. 已知缺口（均为 `not_run` 或明确不支持）

1. 真实超大文件（>1000 页）未测：本机只测到 201 页合成窗口（0.19 s）。真实文件的大小、加密、损坏、字体缺失均 `not_run`。
2. 真实公开 PDF 未测：本轮无可用网络出口，未读取任何线上 PDF；未对真实扫描书、真实论文做测量，全部 `not_run`。
3. 旋转页 / 竖排文字：不做检测与纠正，`not_run`；旋转页可能被 OCR 识别为乱序文本，未验证。
4. `extraction_mode` 与 `reading_order` 没有做枚举校验：代码按 `str(record.get(...) or 默认值)` 透传（`reader.py:6818` 附近），只校验 `status`。内部记录只会产生合法值，但外部若有人构造记录，收到的是原样字符串——与契约把两者列为枚举不完全一致，已上报 Lead，未改动 `reader.py`。
5. 表格重建不支持：只统计 `table_like_rows`（≥3 段、单元格短文本的行），不重排表格、不输出单元格结构。
6. 扫描页的版式分析不支持：OCR 结果固定 `reading_order="stream"`、`layout=null`；多栏扫描页的阅读顺序未处理。
7. 加密文件、表单、批注、附件提取不支持；加密 PDF 的行为未验证（`not_run`）。
8. OCR 缓存命中与失效未实测（`not_run`）；同一材料复用路径是否命中缓存本轮没有单独测量。
9. 页级 deadline 截断后的续读行为只在代码上确认（`unprocessed` + `ocr_budget_exhausted`），未在真实长文件上实测（`not_run`）。
10. 双栏夹具缺口：仓库 `two-column.pdf` 每页 129 字符，低于 600 字符门槛，不能作为双栏证据；双栏证据来自临时合成的长页（见 §2）。
11. `page_spans` 的位置：代码把它写进留存材料（供 `expand` / 检索复用），读取回执的 `details` 里没有该键；契约若按「回执 details」理解则不成立，已上报 Lead，未改代码。

## 7. 非目标（本轮不涉及，也不作为承诺）

- 不新增 OCR / 渲染之外的依赖，不引入 pdfminer / pdfplumber / fitz；
- 不改变 `envoy.read/1` 的 pydantic 模型，不新增 `coverage` 顶层键；`page_coverage` / `page_spans` / `scanned_pages` 保留；
- 不做表格结构重建、不做扫描页版式分析、不做加密/表单/批注提取；
- 不改动 CLI 既有输出字段语义，`page_evidence` 只在 `details` 内新增。

## 8. 复现

```bash
cd <repo>
packages/envoy-cli/.venv/bin/python -m pytest tests/cli/test_pdf_evidence.py -q
```

- 测试全部离线，夹具为合成 PDF，无需网络、无需 OCR / 渲染额外依赖即可在无后端机器上跑通：
  OCR 形状用例直接构造页记录，不触发真实后端；
- 本机实测数字来自 `local_source.read_local_pdf` 与 `reader._parse_pdf_source` 的一次性脚本（夹具与 201 页合成 PDF），未纳入测试套件；
- 结论上限：**部分 PDF 的扫描页可识别、部分双栏页阅读顺序已修正**；更广的文件类型与版面未测。
