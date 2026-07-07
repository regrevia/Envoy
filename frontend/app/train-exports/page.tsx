"use client";

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import { apiJson } from '@/lib/api';

const exportTypes = [
  { id: 'sft', label: 'SFT' },
  { id: 'instruction', label: 'Instruction' },
  { id: 'preference', label: 'Preference' },
  { id: 'rag', label: 'RAG' },
  { id: 'skill', label: 'Skill' },
  { id: 'markdown', label: 'Markdown' },
] as const;

const inputStyle = {
  width: '100%',
  padding: '11px 12px',
  borderRadius: 6,
  border: '1px solid #d1d5db',
  background: '#ffffff',
  color: '#111827',
} as const;

const buttonStyle = {
  minHeight: 40,
  padding: '0 14px',
  borderRadius: 6,
  border: '1px solid #d1d5db',
  background: '#ffffff',
  color: '#111827',
  fontWeight: 600,
  cursor: 'pointer',
} as const;

type ExportType = (typeof exportTypes)[number]['id'];

type ExportRecord = {
  id: string;
  project_id: string;
  task_id: string;
  export_type: string;
  task_name?: string | null;
  root_dir: string;
  file_list: string;
  created_at?: string | null;
};

const defaultSelectedOutputs: ExportType[] = ['markdown', 'skill', 'rag'];

function exportLabel(id: ExportType | string) {
  return exportTypes.find((item) => item.id === id)?.label || id;
}

function safeSlug(input: string) {
  return (input || 'default')
    .trim()
    .toLowerCase()
    .replace(/[^\w\u4e00-\u9fff-]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '') || 'default';
}

export default function TrainExportsPage() {
  const [exportType, setExportType] = useState<ExportType>('markdown');
  const [projectId, setProjectId] = useState('');
  const [assetId, setAssetId] = useState('');
  const [taskName, setTaskName] = useState('');
  const [title, setTitle] = useState('Markdown Bundle');
  const [sourceTaskId, setSourceTaskId] = useState('');
  const [outputDir, setOutputDir] = useState('');
  const [markdownEnhanceWithOllama, setMarkdownEnhanceWithOllama] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [samplesText, setSamplesText] = useState('[{"instruction":"示例问题","input":"","output":"示例答案"}]');
  const [history, setHistory] = useState<ExportRecord[]>([]);
  const [message, setMessage] = useState('准备就绪');
  const [outputContext, setOutputContext] = useState('');
  const [selectedOutputs, setSelectedOutputs] = useState<ExportType[]>(defaultSelectedOutputs);
  const [batchResults, setBatchResults] = useState<any[]>([]);
  const [exporting, setExporting] = useState(false);
  const [autoExporting, setAutoExporting] = useState(false);

  const previewPath = useMemo(() => {
    const folder = `${safeSlug(taskName || exportType)}_${safeSlug(sourceTaskId || assetId || projectId || 'manual')}`;
    const date = new Date().toISOString().slice(0, 10).replaceAll('-', '');
    return `${date}/${folder}`;
  }, [assetId, exportType, projectId, sourceTaskId, taskName]);

  const loadHistory = async () => {
    try {
      const data = await apiJson<ExportRecord[]>('/api/v1/train-exports/history');
      setHistory(data);
    } catch {
      setHistory([]);
    }
  };

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const projectFromQuery = params.get('project_id');
    const outputFromQuery = params.get('export_type') as ExportType | null;
    const outputsFromQuery = params.get('outputs') || '';

    if (projectFromQuery) {
      setProjectId(projectFromQuery);
      setMessage('已载入首页选择的任务上下文');
    }
    if (outputFromQuery && exportTypes.some((item) => item.id === outputFromQuery)) {
      setExportType(outputFromQuery);
    }
    if (outputsFromQuery) {
      setOutputContext(outputsFromQuery);
      const parsedOutputs = outputsFromQuery.split(',')
        .map((item) => item.trim())
        .filter((item): item is ExportType => exportTypes.some((type) => type.id === item));
      if (parsedOutputs.length) {
        setSelectedOutputs(parsedOutputs);
        if (!outputFromQuery) {
          setExportType(parsedOutputs[0]);
        }
      }
    }
    void loadHistory();
  }, []);

  const hasSourceContext = () => Boolean(projectId.trim() || assetId.trim() || sourceTaskId.trim());

  const toggleSelectedOutput = (item: ExportType) => {
    setSelectedOutputs((current) => {
      if (current.includes(item)) {
        return current.filter((value) => value !== item);
      }
      return [...current, item];
    });
    setExportType(item);
  };

  const parseSamplesPayload = () => {
    try {
      const parsed = JSON.parse(samplesText);
      if (!Array.isArray(parsed)) {
        setMessage('样本 JSON 格式错误：顶层必须是数组');
        return null;
      }
      return parsed;
    } catch {
      setMessage('样本 JSON 格式错误：请检查括号、逗号和引号');
      return null;
    }
  };

  const onExport = async () => {
    if (exporting) {
      return;
    }
    if (!hasSourceContext()) {
      setMessage('请填写项目 ID 或来源上下文');
      return;
    }

    setMessage('正在解析样本 JSON...');
    const parsedSamples = parseSamplesPayload();
    if (!parsedSamples) {
      return;
    }

    setExporting(true);
    setMessage('正在导出...');
    try {
      const payload = {
        export_type: exportType,
        project_id: projectId || undefined,
        source_asset_id: assetId || undefined,
        source_task_id: sourceTaskId || undefined,
        title,
        task_name: taskName || undefined,
        out_dir: outputDir.trim() || undefined,
        markdown_enhance_with_ollama: exportType === 'markdown' ? markdownEnhanceWithOllama : false,
        payload: parsedSamples,
      };
      const data = await apiJson('/api/v1/train-exports', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      setResult(data);
      setMessage('导出完成');
      void loadHistory();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '导出失败');
    } finally {
      setExporting(false);
    }
  };

  const onAutoExport = async () => {
    if (autoExporting) {
      return;
    }
    if (!hasSourceContext()) {
      setMessage('请填写项目 ID 或来源上下文');
      return;
    }

    setAutoExporting(true);
    setMessage('正在自动读取并导出...');
    try {
      const payload = {
        export_type: exportType,
        project_id: projectId || undefined,
        source_asset_id: assetId || undefined,
        source_task_id: sourceTaskId || undefined,
        title,
        task_name: taskName || undefined,
        out_dir: outputDir.trim() || undefined,
        markdown_enhance_with_ollama: exportType === 'markdown' ? markdownEnhanceWithOllama : false,
      };
      const data = await apiJson('/api/v1/train-exports/auto', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      setResult(data);
      setMessage('自动导出完成');
      void loadHistory();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '自动导出失败');
    } finally {
      setAutoExporting(false);
    }
  };

  const onAutoExportSelected = async () => {
    if (autoExporting) {
      return;
    }
    if (!hasSourceContext()) {
      setMessage('请填写项目 ID 或来源上下文');
      return;
    }

    const outputs = selectedOutputs.length ? selectedOutputs : [exportType];
    setAutoExporting(true);
    setBatchResults([]);
    setMessage('正在批量自动导出...');
    try {
      const results = await Promise.all(outputs.map((item) => apiJson('/api/v1/train-exports/auto', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          export_type: item,
          project_id: projectId || undefined,
          source_asset_id: assetId || undefined,
          source_task_id: sourceTaskId || undefined,
          title,
          task_name: taskName ? `${taskName}-${item}` : undefined,
          out_dir: outputDir.trim() || undefined,
          markdown_enhance_with_ollama: item === 'markdown' ? markdownEnhanceWithOllama : false,
        }),
      })));
      setBatchResults(results);
      setResult(results[results.length - 1] || null);
      setMessage(`批量自动导出完成：${outputs.length} 个产物`);
      void loadHistory();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '批量自动导出失败');
    } finally {
      setAutoExporting(false);
    }
  };

  return (
    <main style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}>
      <section style={{ maxWidth: 1160, margin: '0 auto', padding: '44px 24px' }}>
        <header style={{ paddingBottom: 22, borderBottom: '1px solid #e5e7eb' }}>
          <h1 style={{ margin: 0, fontSize: 32, lineHeight: 1.2 }}>训练导出</h1>
          <p style={{ margin: '10px 0 0', maxWidth: 720, color: '#4b5563', lineHeight: 1.7 }}>
            从语义单元到 AI 素材，将项目内容导出为 SFT、Instruction、Preference、RAG、Skill 或 Markdown 产物。
          </p>
        </header>

        <section style={{ padding: '14px 0', borderBottom: '1px solid #e5e7eb', color: '#374151', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <strong style={{ fontSize: 14 }}>状态</strong>
          <span style={{ fontSize: 14 }}>{message}</span>
        </section>

        <section aria-label="导出链路" style={{ borderBottom: '1px solid #e5e7eb' }}>
          {['1. 确认来源', '2. 选择产物', '3. 导出并审查'].map((step) => (
            <div key={step} style={{ padding: '14px 0', borderTop: '1px solid #e5e7eb', fontWeight: 700, color: '#111827' }}>
              {step}
            </div>
          ))}
        </section>

        {outputContext ? (
          <div style={{ padding: '12px 0', borderBottom: '1px solid #e5e7eb', color: '#4b5563', fontSize: 14 }}>
            来自首页选择：{outputContext}
            <div style={{ marginTop: 8, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {selectedOutputs.map((item) => (
                <span key={item} style={{ color: '#111827', fontWeight: 700 }}>{exportLabel(item)}</span>
              ))}
            </div>
          </div>
        ) : null}

        <section style={{ marginTop: 22, paddingTop: 18, borderTop: '1px solid #e5e7eb', display: 'grid', gap: 18 }}>
          <div style={{ display: 'grid', gap: 8, paddingBottom: 18, borderBottom: '1px solid #e5e7eb' }}>
            <div style={{ color: '#6b7280', fontSize: 13 }}>Markdown 优先工作台</div>
            <h2 style={{ margin: 0, fontSize: 24 }}>主文档 + 分片文档导出</h2>
            <p style={{ margin: 0, color: '#4b5563', lineHeight: 1.7 }}>
              优先生成 `index.md`、主文档、来源文档和分片文档。输出内容统一 UTF-8 编码，适合直接导入其他模型软件。
            </p>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 12, paddingBottom: 18, borderBottom: '1px solid #e5e7eb' }}>
            <input placeholder="项目 ID" value={projectId} onChange={(event) => setProjectId(event.target.value)} style={inputStyle} />
            <input placeholder="素材 ID（可选）" value={assetId} onChange={(event) => setAssetId(event.target.value)} style={inputStyle} />
            <input placeholder="源任务 ID（可选）" value={sourceTaskId} onChange={(event) => setSourceTaskId(event.target.value)} style={inputStyle} />
            <input placeholder="任务目录名（可选）" value={taskName} onChange={(event) => setTaskName(event.target.value)} style={inputStyle} />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: 12, paddingBottom: 18, borderBottom: '1px solid #e5e7eb' }}>
            <input placeholder="导出标题" value={title} onChange={(event) => setTitle(event.target.value)} style={inputStyle} />
            <input placeholder="输出根目录（可选）" value={outputDir} onChange={(event) => setOutputDir(event.target.value)} style={inputStyle} />
          </div>

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', padding: '12px 0', borderTop: '1px solid #e5e7eb', borderBottom: '1px solid #e5e7eb' }}>
            {exportTypes.map((item) => (
              <button
                key={item.id}
                onClick={() => setExportType(item.id)}
                style={{
                  ...buttonStyle,
                  background: exportType === item.id ? '#111827' : '#ffffff',
                  color: exportType === item.id ? '#ffffff' : '#111827',
                  borderColor: exportType === item.id ? '#111827' : '#d1d5db',
                }}
              >
                {item.label}
              </button>
            ))}
          </div>

          {exportType === 'markdown' ? (
            <div style={{ display: 'grid', gap: 10, padding: '12px 0', borderBottom: '1px solid #e5e7eb' }}>
              <label style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                <input
                  type="checkbox"
                  checked={markdownEnhanceWithOllama}
                  onChange={(event) => setMarkdownEnhanceWithOllama(event.target.checked)}
                />
                <span>使用 Ollama 优化摘要与标题（失败不阻塞主导出）</span>
              </label>
              <div style={{ color: '#6b7280', fontSize: 13 }}>
                输出目录：{outputDir.trim() || '后端默认导出根目录'} / {previewPath}
              </div>
            </div>
          ) : null}

          <div style={{ padding: '12px 0', borderBottom: '1px solid #e5e7eb' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              <strong>导出所选产物</strong>
              <span style={{ color: '#6b7280', fontSize: 13 }}>优先勾选 Markdown，其次再补 Skill、RAG</span>
            </div>
            <div style={{ marginTop: 10, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {exportTypes.map((item) => {
                const active = selectedOutputs.includes(item.id);
                return (
                  <button
                    key={`selected-${item.id}`}
                    onClick={() => toggleSelectedOutput(item.id)}
                    style={{
                      ...buttonStyle,
                      background: active ? '#111827' : '#ffffff',
                      color: active ? '#ffffff' : '#111827',
                      borderColor: active ? '#111827' : '#d1d5db',
                    }}
                  >
                    {item.label}
                  </button>
                );
              })}
            </div>
          </div>

          <div style={{ padding: '12px 0', borderBottom: '1px solid #e5e7eb' }}>
            <strong>任务目录预览</strong>
            <div style={{ marginTop: 8, fontFamily: 'monospace', wordBreak: 'break-all', color: '#4b5563' }}>
              {(outputDir.trim() || '后端导出根目录')} / {previewPath}
            </div>
          </div>

          <textarea
            value={samplesText}
            onChange={(event) => setSamplesText(event.target.value)}
            rows={6}
            style={{ ...inputStyle, fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace', minHeight: 140 }}
          />

          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <button
              onClick={onExport}
              disabled={exporting}
              style={{ ...buttonStyle, background: '#111827', color: '#fff', borderColor: '#111827', opacity: exporting ? 0.72 : 1, cursor: exporting ? 'not-allowed' : 'pointer' }}
            >
              {exporting ? '导出中...' : '开始导出'}
            </button>
            <button
              onClick={onAutoExport}
              disabled={autoExporting}
              style={{ ...buttonStyle, background: '#2563eb', color: '#fff', borderColor: '#2563eb', opacity: autoExporting ? 0.72 : 1, cursor: autoExporting ? 'not-allowed' : 'pointer' }}
            >
              {autoExporting ? '自动导出中...' : '自动读取导出'}
            </button>
            <button
              onClick={onAutoExportSelected}
              disabled={autoExporting}
              style={{ ...buttonStyle, background: '#0f766e', color: '#fff', borderColor: '#0f766e', opacity: autoExporting ? 0.72 : 1, cursor: autoExporting ? 'not-allowed' : 'pointer' }}
            >
              {autoExporting ? '批量导出中...' : '批量自动导出'}
            </button>
            <Link href="/artifacts" style={{ ...buttonStyle, display: 'inline-flex', alignItems: 'center', textDecoration: 'none' }}>查看产物中心</Link>
            <Link href="/artifacts" style={{ ...buttonStyle, display: 'inline-flex', alignItems: 'center', textDecoration: 'none' }}>查看产物来源统计</Link>
          </div>
        </section>

        {batchResults.length ? (
          <section style={{ marginTop: 22, paddingTop: 18, borderTop: '1px solid #e5e7eb' }}>
            <h2 style={{ margin: 0, fontSize: 18 }}>批量导出结果</h2>
            {batchResults.map((item, index) => (
              <div key={`${item?.path || item?.root || index}`} style={{ padding: '12px 0', borderBottom: '1px solid #e5e7eb' }}>
                <div style={{ fontWeight: 700 }}>{exportLabel(item?.export_type || selectedOutputs[index] || '产物')}</div>
                <div style={{ marginTop: 6, color: '#4b5563', wordBreak: 'break-all' }}>目录：{item?.root || item?.path || item?.record?.root_dir || '后端未返回目录'}</div>
                {item?.record?.id ? (
                  <Link href={`/train-exports/${item.record.id}`} style={{ display: 'inline-flex', marginTop: 8, color: '#2563eb', textDecoration: 'none', fontWeight: 700 }}>
                    查看导出记录
                  </Link>
                ) : null}
              </div>
            ))}
          </section>
        ) : null}

        {result ? (
          <section style={{ marginTop: 22, paddingTop: 18, borderTop: '1px solid #e5e7eb' }}>
            <h2 style={{ margin: 0, fontSize: 18 }}>导出结果</h2>
            <pre style={{ marginTop: 14, padding: '12px 0 0', borderTop: '1px solid #e5e7eb', background: '#ffffff', whiteSpace: 'pre-wrap', overflowX: 'auto' }}>
              {JSON.stringify(result, null, 2)}
            </pre>
          </section>
        ) : null}

        <section style={{ marginTop: 22, paddingTop: 18, borderTop: '1px solid #e5e7eb' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, paddingBottom: 14, borderBottom: '1px solid #e5e7eb' }}>
            <div>
              <h2 style={{ margin: 0, fontSize: 18 }}>导出历史</h2>
              <p style={{ margin: '6px 0 0', color: '#6b7280', fontSize: 13 }}>共 {history.length} 条记录</p>
            </div>
            <button onClick={loadHistory} style={buttonStyle}>刷新历史</button>
          </div>

          {history.length === 0 ? <div style={{ padding: '22px 0', color: '#6b7280' }}>暂无历史记录</div> : null}
          {history.map((item) => (
            <div key={item.id} style={{ padding: '16px 0', borderBottom: '1px solid #e5e7eb' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                <Link href={`/train-exports/${item.id}`} style={{ fontWeight: 700, textDecoration: 'none' }}>
                  {item.export_type}
                </Link>
                <span style={{ color: '#6b7280', fontSize: 13 }}>{item.task_name || '未命名'}</span>
              </div>
              <div style={{ marginTop: 8, color: '#4b5563', wordBreak: 'break-all' }}>目录：{item.root_dir}</div>
              <div style={{ marginTop: 6, color: '#6b7280', fontSize: 13, wordBreak: 'break-all' }}>文件：{item.file_list}</div>
            </div>
          ))}
        </section>
      </section>
    </main>
  );
}
