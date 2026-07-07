# Markdown-First Export Design

## Goal

把长 PDF、长视频、长文本和多个 `md/txt` 素材统一导出为高质量 Markdown 产物，优先输出可直接导入其他大模型软件的主文档和分片文档，同时保持长任务稳定、可降级、不会因 Ollama 不可用而阻塞主链。

## Scope

In scope:
- 长 PDF、长视频、长文本、多个 `md/txt` 的 Markdown 优先导出链路
- `主文档 + 分片文档` 的目录结构、命名规范、编码规范
- 前端输出目录配置与 Markdown 导出体验优化
- Ollama 作为可选辅助层参与摘要、标题优化和视频片段语义整理
- 保留可追溯来源信息和长任务降级信息

Out of scope:
- 先把 RAG 作为主目标重新设计
- 强依赖云模型或外部服务
- 大规模 UI 重设计
- 把所有现有导出类型统一重构

## Design Principles

- Markdown 主链必须稳定、可复现、可离线使用
- Ollama 只能增强，不能成为必经路径
- 所有导出文件统一使用 `UTF-8` 无 BOM
- 文件名必须安全、稳定、可跨平台；中文信息保留在正文和 manifest 中
- 分片遵循“单主题、单层级、完整闭环”
- 过长内容必须分层处理，避免单文档不可读、不可导入、不可维护

## Output Model

导出根目录由前端指定，系统在根目录下生成固定结构：

```text
<output_root>/
  <date>/
    <task_slug>_<task_id>/
      manifest.json
      index.md
      overview/
        00-project-overview.md
      sources/
        01-pdf-<source_slug>/
          source.md
          chunks/
            001-<chunk_slug>.md
            002-<chunk_slug>.md
        02-video-<source_slug>/
          source.md
          chunks/
            001-<chunk_slug>.md
        03-text-<source_slug>/
          source.md
          chunks/
            001-<chunk_slug>.md
```

说明：
- `manifest.json`：机器可读索引，记录来源、分片关系、导出状态、编码、降级与 Ollama 增强状态
- `index.md`：总入口文档，提供导航、素材概览、处理说明、分来源索引、异常说明
- `overview/00-project-overview.md`：主文档，适合直接导入聊天类模型软件
- `sources/*/source.md`：单来源聚合文档
- `sources/*/chunks/*.md`：单主题分片文档，适合知识库或长上下文工具导入

## Naming and Encoding

- 所有文件内容统一用 `UTF-8` 编码写入
- 文件名只使用安全 slug、数字序号和 ASCII 连字符
- manifest 保存：
  - 原始来源名
  - 中文标题
  - 规范化 slug
  - 文件路径映射
- 系统不依赖中文文件名作为主索引，避免不同软件导入时乱码或路径兼容问题

## Segmentation Strategy

采用 `混合规则`：

1. 先按来源切分
2. 来源内优先按结构切分
3. 结构块过长时再按长度与自然边界做二次拆分

按来源结构切分规则：
- PDF：优先按标题层级、页区段、明显章节边界切分
- 视频：优先按转写片段、时间段、场景/段落边界切分
- 长文本：优先按 Markdown 标题、空行段、章节标记切分
- 多个 `md/txt`：先按文件切分，再按文件内层级切分

二次拆分规则：
- 不做简单的固定字符硬切
- 保留标题层级完整性
- 超长块仅在自然段、列表组、子标题边界拆分
- 每个分片保留完整主题闭环，不把不相关内容混到同一个分片里

## Markdown Shapes

### Main document

```md
# <任务主标题>

## 导航目录
## 素材概览
## 处理说明
## 分来源总览
## 分片索引
## 异常与降级说明
```

### Source document

```md
# <来源标题>

> 来源类型：PDF / 视频 / 文本
> 原始文件：<原始来源名>
> 来源ID：<source_id>

## 内容摘要
## 结构目录
## 分片索引
## 异常与降级说明
```

### Chunk document

```md
# <分片标题>

> 摘要：<1-3 句摘要>
> 来源：<原始文件名 / 视频名>
> 引用：<页码范围 / 时间范围 / 章节路径>
> 分片ID：<chunk_id>

## 正文
...

## 来源说明
- 原始来源：...
- 章节路径：...
- 页码/时间段：...
```

## Ollama Assistance Layer

Ollama 仅作为增强层，不参与主切分和主导出决策。

允许的增强点：
- 分片摘要生成
- 分片标题优化
- 视频片段语义整理
- 主文档总览摘要

执行顺序：
1. 先生成稳定基础 Markdown
2. 再按分片尝试 Ollama 增强
3. 任意 Ollama 步骤失败，不影响主产物落盘

约束：
- 长 PDF 逐分片调用，不整本调用
- 长视频逐片段或逐转写块调用，不整段调用
- 超出 prompt 上限时直接截断并记录
- 需要显式超时控制和失败状态记录

建议配置项：
- `markdown_enhance_with_ollama`
- `markdown_ollama_timeout_sec`
- `markdown_chunk_target_chars`
- `markdown_chunk_max_chars`

## Failure and Degradation Model

Markdown 导出必须显式记录以下状态：
- `success`
- `partial_success`
- `failed`

常见降级：
- 视频转写部分失败，但保留成功分段与 OCR
- PDF 章节结构提取不足，但保留页区段文本
- Ollama 增强失败，但基础 Markdown 正常导出

manifest 中需要记录：
- 分片总数
- 降级分片数
- 失败片段数
- 失败原因摘要
- Ollama 增强成功/失败/跳过统计

## Backend Changes

核心实现方向：
- 扩展现有 Markdown 导出服务，增加 `index/source/chunks/manifest` 多文件输出
- 建立统一中间结构：
  - `source document`
  - `structural section`
  - `markdown chunk`
- 对 PDF、视频、文本分别提供结构切分器
- 统一 slug、编码写入、目录布局和 manifest 生成
- Ollama 增强在独立步骤执行，并写回 chunk metadata

## Frontend Changes

目标不是重做视觉，而是减少卡片感、提高导出工作台的清晰度。

范围：
- 首页
- 训练导出页
- 产物查看相关入口
- 全局样式的卡片/阴影/边框策略

调整原则：
- 减少卡片墙和大阴影
- 使用分隔线、列表、表格式信息组织替代重卡片布局
- 输出目录选择、导出模式、Ollama 增强开关应集中成单一工作流
- Markdown 优先相关选项前置，RAG/其他导出弱化

## Acceptance Criteria

- 用户可在前端指定 Markdown 导出根目录
- 导出生成 `index.md`、`overview/00-project-overview.md`、`sources/*/source.md`、`sources/*/chunks/*.md`、`manifest.json`
- 所有 Markdown 与 JSON 文件使用 `UTF-8`
- 长 PDF、长视频、长文本、多 `md/txt` 输入均能走同一 Markdown 主链
- 分片文档保持单主题、带标题、摘要、来源引用
- Ollama 不可用时，基础 Markdown 仍可完整导出
- 前端页面减少卡片感，Markdown 导出流程更聚焦

## Risks

- PDF 结构切分质量受原始文档标题信息影响较大
- 视频结构切分质量受转写质量限制
- Ollama 增强在长任务中可能带来额外延迟，需要严格超时和并发控制
- 前端如果继续保留过多平行导出入口，会削弱 Markdown 主链的操作清晰度

## Recommended Execution Order

1. 先重构 Markdown 后端输出结构
2. 再补 PDF/视频/长文本/多文件的统一分片逻辑
3. 再接 Ollama 可选增强
4. 最后收前端去卡片感和导出工作流
