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

const panelStyle = {
  background: '#ffffff',
  border: '1px solid #e5e7eb',
  borderRadius: 8,
  padding: 24,
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

function safeSlug(input: string) {
  return (input || 'default')
    .trim()
    .toLowerCase()
    .replace(/[^\w\u4e00-\u9fff-]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '') || 'default';
}

export default function TrainExportsPage() {
  const [exportType, setExportType] = useState<ExportType>('sft');
  const [projectId, setProjectId] = useState('');
  const [assetId, setAssetId] = useState('');
  const [taskName, setTaskName] = useState('');
  const [title, setTitle] = useState('Skill Package');
  const [sourceTaskId, setSourceTaskId] = useState('');
  const [result, setResult] = useState<any>(null);
  const [samplesText, setSamplesText] = useState('[{"instruction":"示例问题","input":"","output":"示例答案"}]');
  const [history, setHistory] = useState<ExportRecord[]>([]);
  const [message, setMessage] = useState('准备就绪');
  const [outputContext, setOutputContext] = useState('');

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
    }
    void loadHistory();
  }, []);

  const onExport = async () => {
    try {
      const payload = {
        export_type: exportType,
        project_id: projectId || undefined,
        source_asset_id: assetId || undefined,
        source_task_id: sourceTaskId || undefined,
        title,
        task_name: taskName || undefined,
        payload: JSON.parse(samplesText),
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
    }
  };

  const onAutoExport = async () => {
    try {
      const payload = {
        export_type: exportType,
        project_id: projectId || undefined,
        source_asset_id: assetId || undefined,
        source_task_id: sourceTaskId || undefined,
        title,
        task_name: taskName || undefined,
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
    }
  };

  return (
    <main style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}>
      <section style={{ maxWidth: 1160, margin: '0 auto', padding: '44px 24px' }}>
        <header style={{ paddingBottom: 22, borderBottom: '1px solid #e5e7eb' }}>
          <h1 style={{ margin: 0, fontSize: 32, lineHeight: 1.2 }}>训练导出</h1>
          <p style={{ margin: '10px 0 0', maxWidth: 720, color: '#4b5563', lineHeight: 1.7 }}>
            将项目语义单元和样本导出为 SFT、Instruction、Preference、RAG、Skill 或 Markdown 产物。
          </p>
        </header>

        <div style={{ marginTop: 18, padding: '12px 14px', border: '1px solid #e5e7eb', borderRadius: 8, background: '#ffffff', color: '#374151', display: 'flex', gap: 10, alignItems: 'center' }}>
          <strong style={{ fontSize: 14 }}>状态</strong>
          <span style={{ fontSize: 14 }}>{message}</span>
        </div>

        {outputContext ? (
          <div style={{ padding: '12px 0', borderBottom: '1px solid #e5e7eb', color: '#4b5563', fontSize: 14 }}>
            来自首页选择：{outputContext}
          </div>
        ) : null}

        <section style={{ ...panelStyle, marginTop: 20, display: 'grid', gap: 18 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 12 }}>
            <input placeholder="项目 ID" value={projectId} onChange={(event) => setProjectId(event.target.value)} style={inputStyle} />
            <input placeholder="素材 ID（可选）" value={assetId} onChange={(event) => setAssetId(event.target.value)} style={inputStyle} />
            <input placeholder="源任务 ID（可选）" value={sourceTaskId} onChange={(event) => setSourceTaskId(event.target.value)} style={inputStyle} />
            <input placeholder="任务目录名（可选）" value={taskName} onChange={(event) => setTaskName(event.target.value)} style={inputStyle} />
          </div>

          <input placeholder="导出标题" value={title} onChange={(event) => setTitle(event.target.value)} style={inputStyle} />

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
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

          <div style={{ padding: 14, background: '#f9fafb', borderRadius: 8, border: '1px dashed #d1d5db' }}>
            <strong>任务目录预览</strong>
            <div style={{ marginTop: 8, fontFamily: 'monospace', wordBreak: 'break-all', color: '#4b5563' }}>
              后端导出根目录 / {previewPath}
            </div>
          </div>

          <textarea
            value={samplesText}
            onChange={(event) => setSamplesText(event.target.value)}
            rows={6}
            style={{ ...inputStyle, fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace', minHeight: 140 }}
          />

          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <button onClick={onExport} style={{ ...buttonStyle, background: '#111827', color: '#fff', borderColor: '#111827' }}>开始导出</button>
            <button onClick={onAutoExport} style={{ ...buttonStyle, background: '#2563eb', color: '#fff', borderColor: '#2563eb' }}>自动读取导出</button>
            <Link href="/artifacts" style={{ ...buttonStyle, display: 'inline-flex', alignItems: 'center', textDecoration: 'none' }}>查看产物中心</Link>
          </div>
        </section>

        {result ? (
          <section style={{ ...panelStyle, marginTop: 20 }}>
            <h2 style={{ margin: 0, fontSize: 18 }}>导出结果</h2>
            <pre style={{ marginTop: 14, padding: 14, borderRadius: 8, border: '1px solid #e5e7eb', background: '#f9fafb', whiteSpace: 'pre-wrap', overflowX: 'auto' }}>
              {JSON.stringify(result, null, 2)}
            </pre>
          </section>
        ) : null}

        <section style={{ ...panelStyle, marginTop: 20 }}>
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
