"use client";

import Link from 'next/link';
import { useMemo, useState } from 'react';

import { apiJson } from '@/lib/api';
import { downloadFile, openDownloadUrl } from '@/lib/download';

const exportTypes = [
  { id: 'sft', label: 'SFT' },
  { id: 'instruction', label: 'Instruction' },
  { id: 'preference', label: 'Preference' },
  { id: 'rag', label: 'RAG' },
  { id: 'skill', label: 'Skill' },
  { id: 'markdown', label: 'Markdown' },
] as const;

const BASE_EXPORT_DIR = 'e:/2026/Envoy/tmp/exports';

function safeSlug(input: string) {
  return (input || 'default')
    .trim()
    .toLowerCase()
    .replace(/[^\w\u4e00-\u9fff-]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '') || 'default';
}

export default function TrainExportsPage() {
  const [exportType, setExportType] = useState<(typeof exportTypes)[number]['id']>('sft');
  const [projectId, setProjectId] = useState('');
  const [assetId, setAssetId] = useState('');
  const [taskName, setTaskName] = useState('');
  const [title, setTitle] = useState('Skill Package');
  const [sourceTaskId, setSourceTaskId] = useState('');
  const [result, setResult] = useState<any>(null);
  const [samplesText, setSamplesText] = useState('[{"instruction":"示例问题","input":"","output":"示例答案"}]');
  const [history, setHistory] = useState<any[]>([]);

  const previewPath = useMemo(() => {
    const folder = `${safeSlug(taskName || exportType)}_${safeSlug(sourceTaskId || assetId || projectId || 'manual')}`;
    const date = new Date().toISOString().slice(0, 10).replaceAll('-', '');
    return `${BASE_EXPORT_DIR}/${date}/${folder}`;
  }, [assetId, exportType, projectId, sourceTaskId, taskName]);

  const loadHistory = async () => {
    const data = await apiJson<any[]>('/api/v1/train-exports/history');
    setHistory(data);
  };

  const onExport = async () => {
    const payload = {
      export_type: exportType,
      project_id: projectId,
      source_asset_id: assetId,
      source_task_id: sourceTaskId,
      title,
      task_name: taskName,
      payload: JSON.parse(samplesText),
      out_dir: BASE_EXPORT_DIR,
    };
    const data = await apiJson('/api/v1/train-exports', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    setResult(data);
    loadHistory();
  };

  const onAutoExport = async () => {
    const payload = {
      export_type: exportType,
      source_asset_id: assetId || projectId,
      source_task_id: sourceTaskId,
      title,
      task_name: taskName,
      out_dir: BASE_EXPORT_DIR,
    };
    const data = await apiJson('/api/v1/train-exports/auto', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    setResult(data);
    loadHistory();
  };

  const openDir = async (path?: string) => {
    const target = path || result?.root || previewPath;
    await downloadFile(`/api/v1/train-exports/open?path=${encodeURIComponent(String(target))}`, 'open-result.txt');
  };

  return (
    <main style={{ minHeight: '100vh', background: '#f5f7fb', fontFamily: 'Inter, sans-serif', color: '#0f172a' }}>
      <section style={{ maxWidth: 1100, margin: '0 auto', padding: '48px 24px' }}>
        <h1 style={{ fontSize: 36, marginBottom: 8 }}>训练导出</h1>
        <p style={{ color: '#475569' }}>一键选择导出格式，生成训练资产或 Skill / Markdown 产物。</p>

        <div style={{ background: '#fff', borderRadius: 18, padding: 24, boxShadow: '0 10px 30px rgba(15,23,42,0.06)', marginTop: 24, display: 'grid', gap: 16 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
            <input placeholder="项目 ID" value={projectId} onChange={(e) => setProjectId(e.target.value)} style={{ padding: 12, borderRadius: 10, border: '1px solid #cbd5e1' }} />
            <input placeholder="素材 ID（可选）" value={assetId} onChange={(e) => setAssetId(e.target.value)} style={{ padding: 12, borderRadius: 10, border: '1px solid #cbd5e1' }} />
            <input placeholder="源任务 ID（可选）" value={sourceTaskId} onChange={(e) => setSourceTaskId(e.target.value)} style={{ padding: 12, borderRadius: 10, border: '1px solid #cbd5e1' }} />
            <input placeholder="任务目录名（可重命名）" value={taskName} onChange={(e) => setTaskName(e.target.value)} style={{ padding: 12, borderRadius: 10, border: '1px solid #cbd5e1' }} />
          </div>

          <input placeholder="导出标题" value={title} onChange={(e) => setTitle(e.target.value)} style={{ padding: 12, borderRadius: 10, border: '1px solid #cbd5e1' }} />

          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            {exportTypes.map((item) => (
              <button
                key={item.id}
                onClick={() => setExportType(item.id)}
                style={{
                  padding: '10px 14px',
                  borderRadius: 999,
                  border: exportType === item.id ? '1px solid #0f172a' : '1px solid #cbd5e1',
                  background: exportType === item.id ? '#0f172a' : '#fff',
                  color: exportType === item.id ? '#fff' : '#0f172a',
                }}
              >
                {item.label}
              </button>
            ))}
          </div>

          <div style={{ padding: 14, background: '#f8fafc', borderRadius: 12, border: '1px dashed #cbd5e1' }}>
            <strong>任务目录预览</strong>
            <div style={{ marginTop: 8, fontFamily: 'monospace', wordBreak: 'break-all' }}>{previewPath}</div>
          </div>

          <textarea
            value={samplesText}
            onChange={(e) => setSamplesText(e.target.value)}
            rows={6}
            style={{ padding: 12, borderRadius: 10, border: '1px solid #cbd5e1', fontFamily: 'monospace' }}
          />

          <div style={{ display: 'flex', gap: 12 }}>
            <button onClick={onExport} style={{ padding: '12px 16px', borderRadius: 10, background: '#0f172a', color: '#fff', border: 0, width: 'fit-content' }}>开始导出</button>
            <button onClick={onAutoExport} style={{ padding: '12px 16px', borderRadius: 10, background: '#1d4ed8', color: '#fff', border: 0, width: 'fit-content' }}>自动读取导出</button>
            <button onClick={() => openDir()} style={{ padding: '12px 16px', borderRadius: 10, background: '#e2e8f0', color: '#0f172a', border: 0, width: 'fit-content' }}>打开输出目录</button>
          </div>
        </div>

        {result ? (
          <div style={{ marginTop: 24, background: '#fff', borderRadius: 18, padding: 20, boxShadow: '0 10px 30px rgba(15,23,42,0.06)' }}>
            <h2>导出结果</h2>
            <pre style={{ whiteSpace: 'pre-wrap', overflowX: 'auto' }}>{JSON.stringify(result, null, 2)}</pre>
            {result.root ? <div style={{ marginTop: 12 }}>目录：{result.root}</div> : null}
            {result.path ? <div style={{ marginTop: 12 }}>文件：{result.path}</div> : null}
            {result.paths ? <pre style={{ marginTop: 12 }}>{JSON.stringify(result.paths, null, 2)}</pre> : null}
            {result.root ? <button onClick={() => void openDir(result.root)} style={{ marginTop: 12, padding: '10px 14px', borderRadius: 10, background: '#0f172a', color: '#fff', border: 0 }}>点击打开结果目录</button> : null}
          </div>
        ) : null}

        <div style={{ marginTop: 32, background: '#fff', borderRadius: 18, padding: 20, boxShadow: '0 10px 30px rgba(15,23,42,0.06)' }}>
          <h2>导出历史记录</h2>
          <button onClick={loadHistory} style={{ marginBottom: 12, padding: '10px 14px', borderRadius: 10, background: '#e2e8f0', color: '#0f172a', border: 0 }}>刷新历史</button>
          <div style={{ display: 'grid', gap: 12 }}>
            {history.length === 0 ? <div>暂无历史记录</div> : null}
            {history.map((item) => (
              <div key={item.id} style={{ border: '1px solid #e2e8f0', borderRadius: 14, padding: 14 }}>
                <div><strong>{item.export_type}</strong> · {item.task_name ?? '未命名'}</div>
                <div>目录：{item.root_dir}</div>
                <div style={{ marginTop: 6, whiteSpace: 'pre-wrap' }}>文件：{item.file_list}</div>
                <div style={{ marginTop: 8, display: 'flex', gap: 10 }}>
                  <button onClick={() => void openDir(item.root_dir)} style={{ padding: '8px 12px', borderRadius: 10, background: '#0f172a', color: '#fff', border: 0 }}>打开目录</button>
                </div>
              </div>
            ))}
          </div>
        </div>

      </section>
    </main>
  );
}
