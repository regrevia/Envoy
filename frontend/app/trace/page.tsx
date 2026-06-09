"use client";

import { useState } from 'react';

import { ApiError, apiJson } from '../../lib/api';

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
          <h1 style={{ margin: 0, fontSize: 32, lineHeight: 1.2 }}>追溯审查</h1>
          <p style={{ margin: '10px 0 0', maxWidth: 720, color: '#4b5563', lineHeight: 1.7 }}>
            从素材、语义单元、生成样本或处理任务出发，查看它们和导出产物之间的来源关系。
          </p>
          <div style={{ marginTop: 16, padding: '12px 0', borderTop: '1px solid #e5e7eb', color: '#374151', fontWeight: 700 }}>
            {'素材 -> 语义单元 -> 生成样本 -> AI 产物'}
          </div>
        </header>

        <div style={{ marginTop: 18, padding: '12px 0', borderBottom: '1px solid #e5e7eb', color: '#374151', display: 'flex', gap: 10, alignItems: 'center' }}>
          <strong style={{ fontSize: 14 }}>状态</strong>
          <span style={{ fontSize: 14 }}>{message}</span>
        </div>

        <section style={{ marginTop: 20, padding: '18px 0 20px', borderTop: '1px solid #e5e7eb', borderBottom: '1px solid #e5e7eb', display: 'grid', gap: 14 }}>
          <div>
            <h2 style={{ margin: 0, fontSize: 18 }}>查询追溯</h2>
            <p style={{ margin: '8px 0 0', color: '#6b7280', lineHeight: 1.6 }}>
              输入任一节点 ID，检查它是否已经串联到核心转换链路和最终 AI 产物。
            </p>
          </div>
          <label style={{ display: 'grid', gap: 6 }}>
            <span style={{ color: '#4b5563', fontSize: 13 }}>来源类型</span>
            <select value={sourceType} onChange={(event) => setSourceType(event.target.value)} style={inputStyle}>
              <option value="asset">素材 asset</option>
              <option value="semantic_unit">语义单元 semantic_unit</option>
              <option value="processing_task">处理任务 processing_task</option>
              <option value="generated_sample">生成样本 generated_sample</option>
              <option value="artifact">导出产物 artifact</option>
            </select>
          </label>
          <label style={{ display: 'grid', gap: 6 }}>
            <span style={{ color: '#4b5563', fontSize: 13 }}>来源 ID</span>
            <input placeholder="输入素材、语义单元、任务或样本 ID" value={sourceId} onChange={(event) => setSourceId(event.target.value)} style={inputStyle} />
          </label>
          <button onClick={queryTrace} style={{ ...buttonStyle, width: 'fit-content' }}>查询追溯</button>
        </section>

        <section style={{ marginTop: 20, padding: '18px 0 0', borderTop: '1px solid #e5e7eb' }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>追溯结果</h2>
          <pre style={{ marginTop: 14, padding: '12px 0 0', borderTop: '1px solid #e5e7eb', background: '#ffffff', whiteSpace: 'pre-wrap', overflowX: 'auto' }}>
            {JSON.stringify(result, null, 2)}
          </pre>
        </section>
      </section>
    </main>
  );
}
