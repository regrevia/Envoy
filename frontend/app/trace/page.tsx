"use client";

import { useState } from 'react';

import { ApiError, apiJson } from '../../lib/api';

const panelStyle = {
  background: '#ffffff',
  border: '1px solid #e5e7eb',
  borderRadius: 8,
  padding: 24,
} as const;

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
  border: 0,
  background: '#111827',
  color: '#ffffff',
  fontWeight: 600,
  cursor: 'pointer',
} as const;

export default function TracePage() {
  const [sourceType, setSourceType] = useState('asset');
  const [sourceId, setSourceId] = useState('');
  const [result, setResult] = useState<any[]>([]);
  const [message, setMessage] = useState('输入来源 ID 后查询相关记录');

  const queryTrace = async () => {
    const trimmedId = sourceId.trim();
    if (!trimmedId) {
      setMessage('请输入来源 ID');
      setResult([]);
      return;
    }

    setMessage('正在查询追溯关系...');
    try {
      const data = await apiJson<any[]>(`/api/v1/trace/${sourceType}/${trimmedId}`);
      setResult(data);
      setMessage(`找到 ${data.length} 条追溯关系`);
    } catch (error) {
      const text = error instanceof ApiError || error instanceof Error ? error.message : '追溯查询失败';
      setResult([{ error: text }]);
      setMessage(`追溯查询失败：${text}`);
    }
  };

  return (
    <main style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}>
      <section style={{ maxWidth: 960, margin: '0 auto', padding: '44px 24px' }}>
        <header style={{ paddingBottom: 22, borderBottom: '1px solid #e5e7eb' }}>
          <h1 style={{ margin: 0, fontSize: 32, lineHeight: 1.2 }}>追溯</h1>
          <p style={{ margin: '10px 0 0', maxWidth: 720, color: '#4b5563', lineHeight: 1.7 }}>
            从素材、语义单元、生成样本或任务出发，查看它们和导出产物之间的来源关系。
          </p>
        </header>

        <div style={{ marginTop: 18, padding: '12px 14px', border: '1px solid #e5e7eb', borderRadius: 8, background: '#ffffff', color: '#374151', display: 'flex', gap: 10, alignItems: 'center' }}>
          <strong style={{ fontSize: 14 }}>状态</strong>
          <span style={{ fontSize: 14 }}>{message}</span>
        </div>

        <section style={{ ...panelStyle, marginTop: 20, display: 'grid', gap: 14 }}>
          <label style={{ display: 'grid', gap: 6 }}>
            <span style={{ color: '#4b5563', fontSize: 13 }}>来源类型</span>
            <select value={sourceType} onChange={(event) => setSourceType(event.target.value)} style={inputStyle}>
              <option value="asset">素材 asset</option>
              <option value="semantic_unit">语义单元 semantic_unit</option>
              <option value="processing_task">处理任务 processing_task</option>
              <option value="generated_sample">生成样本 generated_sample</option>
            </select>
          </label>
          <label style={{ display: 'grid', gap: 6 }}>
            <span style={{ color: '#4b5563', fontSize: 13 }}>来源 ID</span>
            <input placeholder="输入素材、语义单元、任务或样本 ID" value={sourceId} onChange={(event) => setSourceId(event.target.value)} style={inputStyle} />
          </label>
          <button onClick={queryTrace} style={{ ...buttonStyle, width: 'fit-content' }}>查询追溯</button>
        </section>

        <section style={{ ...panelStyle, marginTop: 20 }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>追溯结果</h2>
          <pre style={{ marginTop: 14, padding: 14, borderRadius: 8, border: '1px solid #e5e7eb', background: '#f9fafb', whiteSpace: 'pre-wrap', overflowX: 'auto' }}>
            {JSON.stringify(result, null, 2)}
          </pre>
        </section>
      </section>
    </main>
  );
}
