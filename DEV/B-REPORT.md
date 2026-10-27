# Envoy 长 PDF 缺陷修复报告（B 线）

- 工作树：`/Users/apple/Desktop/2026/envoy-b`，分支 `wt/envoy-b`，基线 `fbdede5`（0.1.23）
- 提交：`35810fc` — B1–B5 代码 + 测试（仅本工作树，未 push、未合并）
- 状态标记：`implemented` 已改代码；`unit_checked` 有纯逻辑单测通过；`not_run` 未做（原因见末尾）

## 一、改动清单

| 项 | 文件:函数 | 状态 |
| --- | --- | --- |
| B1 本地 PDF 误走视频管线 | `packages/envoy-cli/src/envoy_cli/local_source.py`（新）`classify_local_source` / `local_read_request` / `read_local_pdf` / `validate_local_source_path` / `local_pdf_max_bytes` | implemented / unit_checked |
| B1 分发接线 | `packages/envoy-cli/src/envoy_cli/mcp.py:1834`（`envoy_read` 本地分支）按 `pdf`/`video`/其它分流；`_local_unsupported_payload`（L1428）保留 `unsupported_source`，不改原有 `source_unavailable` 语义 | implemented / unit_checked |
| B2 显式 0 下载预算被覆盖 | `reader.py:4569 _pdf_download_budget`（区分 `None` 与显式 `0`）；`reader.py:4600 _fetch_pdf_source` 重定向/重试/等待共用一个累计字节计数器 | implemented / unit_checked |
| B3 忽略共享 deadline | `reader.py:4560 _pdf_deadline`；`_parse_pdf_source` 下载与逐页解析共用同一剩余时间，每页前检查 `time.monotonic() >= deadline`，超时返回 partial 并保留已提交页 | implemented / unit_checked（真实取消未做，见 not_run） |
| B4 无逐页恢复 | `packages/envoy-cli/src/envoy_cli/pdf_store.py`（新）：`root/pdf-pages/<digest>/state.json` + `pages.jsonl`，`PARSER_VERSION`、`PAGE_STATUSES`（`text/ocr_candidate/blank/parse_error/not_processed`）、`commit_page`（落盘成功才返回 `True`）、`contiguous_cursor`、`mark_not_processed`；`reader.py:4592 _pdf_page_store`，解析循环先复用已提交页、再写盘推进游标 | implemented / unit_checked |
| B5 expand 不收敛投递缺口 | `reader.py` `_pdf_source_unresolved` / `_pdf_page_coverage_details` / `_PDF_DELIVERY_GAP_MARKERS`；expand PDF 分支在最后一页送达后把 `stored["delivery_gap"]` 置 False，搜索/覆盖详情报告 `page_count/indexed_page_count/indexed_pages/unindexed_pages/page_coverage/delivery_gap` | implemented / unit_checked |

未改：版本号、installer/workflow、`media.py`、reader ASR 段、integrations；未联网、未下载 PDF、未跑 OCR、未开浏览器、未跑全量 pytest。

## 二、验证命令与结果

解释器：`/Users/apple/Desktop/2026/Envoy/packages/envoy-cli/.venv/bin/python`（pypdf 5.9.0 / pytest / httpx 可用）。
工作树代码优先：

```bash
cd /Users/apple/Desktop/2026/envoy-b
export PYTHONPATH=/Users/apple/Desktop/2026/envoy-b/packages/envoy-cli/src:/Users/apple/Desktop/2026/envoy-b/packages/envoy-core/src
V=/Users/apple/Desktop/2026/Envoy/packages/envoy-cli/.venv/bin/python
```

| 命令 | 结果 |
| --- | --- |
| `$V -m pytest tests/cli/test_pdf_reader.py -q` | 9 passed（B2×3、B1/B3/B4/B5 各 1、B1 路由 1、B4 重启续跑 1、B5 expand 收敛 1） |
| `$V -m pytest tests/cli/test_local_source.py -q` | 8 passed（magic 分类、video 分类、显式 document 拒绝、图片 OCR 边界、本地 0 预算不生效、中文+空格路径、Windows `file:///C:/…`、未知类型 blocked 边界） |
| `$V -m pytest tests/cli/test_envoy_cli.py -q -k pdf` | 4 passed（3 处既有 `_fetch_pdf_source` stub 已加 `**_kwargs`） |
| 相关 13 个测试文件合并跑（`test_pdf_reader/test_local_source/test_envoy_cli/test_mcp_bridge/test_local_video/test_reader_public_api_path/test_read_cacheability/test_retained_material_cache/test_reader_media_binding/test_mcp_content_selfcontained/test_cache/test_public_api_fallbacks/test_fetch_fast_fail`） | **262 passed in 5.50s** |
| `$V -m py_compile reader.py mcp.py local_source.py pdf_store.py tests/...` | COMPILE_OK |

单测覆盖的验收点：① 显式 0 预算不下载且不被改写；② 本地 PDF 不构造 `LocalVideoReadRequest`；③ deadline 耗尽返回 partial 且已完成页保留、重启只解析未提交页；④ 全量送达后投递缺口归零、真实缺口保留；⑤ 页范围 1-based 闭区间。

## 三、未完成 / not_run

- 真实长 PDF（200/500 页）端到端：`not_run`（约束禁止真实下载；只用 2–3 页自造文本层 PDF 验证逻辑）。
- OCR / 扫描页真实识别、模型下载：`not_run`（禁止视觉推理与模型下载；只验证 `ocr_candidate` 状态与 `missing_capability` 边界已有测试）。
- B3「可取消 worker」的真实取消：`not_run`（当前实现是逐页 deadline 检查 + 已提交页复用；没有线程/进程中断机制，接口留待 Lead 决定是否值得加）。
- 真实网络重定向/慢网络下的累计字节计数：`unit_checked` 逻辑层面，真实网络 `not_run`。
- 真机/Windows 路径：`unit_checked` 仅逻辑（`file:///C:/…` 解码）；真实 Windows 运行 `not_run`。
- 未跑 `scripts/dev_test.sh` 全量、未跑浏览器/集成测试（约束禁止）。

## 四、明天需用户在场验收（请 Lead 对齐 D01–D12 编号）

建议按以下场景验收（编号以 Lead 的 D01–D12 为准，此处给判定口径）：

1. 本地下载的中文/空格路径 PDF 用 `envoy_read` 直接读出正文（不再报 “not a readable video”）。
2. `budget.max_download_bytes = 0` 的本地 PDF 仍可读，且覆盖详情 `downloaded_bytes = 0`。
3. 远程 PDF 显式 0 预算返回 blocked（`download_budget_zero`），不发起请求。
4. 小 `max_work_seconds` 下 PDF 返回 partial，已完成页在响应里、后续 expand 从断点继续。
5. 同一 PDF 第二次读取命中页存储，不重复解析已提交页（重启续跑）。
6. 分多次 expand 把 PDF 全部取完后，`delivery_gap=false`、`source_coverage_unresolved=[]`、`next_offset=null`。
7. 扫描页 PDF 仍返回 `missing_capability` 且提示 OCR，不假装已读。
8. 页定位始终是 1-based `pdf_page`，跨页 expand 不丢页号。
9. 未知本地类型（xlsx/png 等）返回 `unsupported_source`，`uploaded=false`，文案说明边界。
10. 500 页上限、64 MiB 上限行为不变（超限明确报错）。
11. 加密 PDF 仍 `auth_required`。
12. 既有本地视频 `.mp4`（`ftyp`）与缺失文件（`source_unavailable`/`not_found`）行为不变。

## 五、Lead 在共享文件里需要做的集成

1. **合并本工作树提交 `35810fc`**（或 cherry-pick）：新增 `local_source.py`、`pdf_store.py`、`tests/cli/test_pdf_reader.py`、`tests/cli/test_local_source.py`；修改 `reader.py`、`mcp.py`。
2. **既有测试 stub 适配**：`tests/cli/test_envoy_cli.py` 中 3 处 `monkeypatch.setattr(reader, "_fetch_pdf_source", lambda _url, _max_bytes, _cache: {...})` 已改为 `lambda ..., **_kwargs: {...}`（`_read_pdf_source` 现在传 `deadline=`）。合并时保留这个改动，否则 3 个既有 PDF 测试会 TypeError。
3. **material 存储字段**：新写入 `delivery_gap`、`page_coverage`、`source_coverage_unresolved`、`page_spans`；旧 material JSON 缺字段时按默认值处理（`_pdf_source_unresolved` / `_pdf_page_coverage_details` 已容错），无需迁移脚本。
4. **页存储目录**：`_material_root()/pdf-pages/<sha256>/`，受 `ENVOY_LOCAL_DATA_DIR` 控制；清理策略与 material 过期一致即可，未加自动 GC。
5. **文档/变更日志**：`local_source` 的“本地只支持 PDF 与视频/音频，其它类型需公网 URL 或宿主转换”属于能力边界，建议同步到 README/能力表（未改文档）。
6. **版本号**：未动，发布时由 Lead 统一 bump。
