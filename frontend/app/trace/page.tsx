"use client";

import { useState } from 'react';

import { ApiError, apiJson } from '../../lib/api';

export default function TracePage() {
  const [sourceType, setSourceType] = useState('asset');
  const [sourceId, setSourceId] = useState('');
  const [result, setResult] = useState<any[]>([]);

  const queryTrace = async () => {
    try {
      const data = await apiJson<any[]>(`/api/v1/trace/${sourceType}/${sourceId}`);
      setResult(data);
    } catch (error) {
      const message = error instanceof ApiError ? error.message : '查询失败';
      setResult([{ error: message }]);
    }
  };

  return (
    <main style={{ minHeight: '100vh', background: '#f5f7fb', fontFamily: 'Inter, sans-serif', color: '#0f172a' }}>
      <section style={{ maxWidth: 900, margin: '0 auto', padding: '48px 24px' }}>
        <h1 style={{ fontSize: 36, marginBottom: 8 }}>追溯查询</h1>
        <p style={{ color: '#475569' }}>按源类型和源 ID 查询追溯链。</p>

        <div style={{ background: '#fff', borderRadius: 18, padding: 24, boxShadow: '0 10px 30px rgba(15,23,42,0.06)', marginTop: 24, display: 'grid', gap: 16 }}>
          <select value={sourceType} onChange={(e) => setSourceType(e.target.value)} style={{ padding: 12, borderRadius: 10, border: '1px solid #cbd5e1' }}>
            <option value="asset">asset</option>
            <option value="semantic_unit">semantic_unit</option>
            <option value="processing_task">processing_task</option>
          </select>
          <input placeholder="源 ID" value={sourceId} onChange={(e) => setSourceId(e.target.value)} style={{ padding: 12, borderRadius: 10, border: '1px solid #cbd5e1' }} />
          <button onClick={queryTrace} style={{ padding: '12px 16px', borderRadius: 10, background: '#0f172a', color: '#fff', border: 0 }}>查询</button>
        </div>

        <div style={{ marginTop: 24 }}>
          <pre style={{ background: '#fff', padding: 20, borderRadius: 18, overflowX: 'auto' }}>{JSON.stringify(result, null, 2)}</pre>
        </div>
      </section>
    </main>
  );
}
