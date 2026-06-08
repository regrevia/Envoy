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
  const [message, setMessage] = useState('Enter a source ID to trace related records');

  const queryTrace = async () => {
    const trimmedId = sourceId.trim();
    if (!trimmedId) {
      setMessage('Enter a source ID');
      setResult([]);
      return;
    }

    setMessage('Querying trace links...');
    try {
      const data = await apiJson<any[]>(`/api/v1/trace/${sourceType}/${trimmedId}`);
      setResult(data);
      setMessage(`Found ${data.length} trace links`);
    } catch (error) {
      const text = error instanceof ApiError || error instanceof Error ? error.message : 'Trace query failed';
      setResult([{ error: text }]);
      setMessage(`Trace query failed: ${text}`);
    }
  };

  return (
    <main style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}>
      <section style={{ maxWidth: 960, margin: '0 auto', padding: '44px 24px' }}>
        <header style={{ paddingBottom: 22, borderBottom: '1px solid #e5e7eb' }}>
          <h1 style={{ margin: 0, fontSize: 32, lineHeight: 1.2 }}>Trace</h1>
          <p style={{ margin: '10px 0 0', maxWidth: 720, color: '#4b5563', lineHeight: 1.7 }}>
            Follow relationships from source assets to semantic units, generated samples, tasks, and exported artifacts.
          </p>
        </header>

        <div style={{ marginTop: 18, padding: '12px 14px', border: '1px solid #e5e7eb', borderRadius: 8, background: '#ffffff', color: '#374151', display: 'flex', gap: 10, alignItems: 'center' }}>
          <strong style={{ fontSize: 14 }}>Status</strong>
          <span style={{ fontSize: 14 }}>{message}</span>
        </div>

        <section style={{ ...panelStyle, marginTop: 20, display: 'grid', gap: 14 }}>
          <label style={{ display: 'grid', gap: 6 }}>
            <span style={{ color: '#4b5563', fontSize: 13 }}>Source type</span>
            <select value={sourceType} onChange={(event) => setSourceType(event.target.value)} style={inputStyle}>
              <option value="asset">asset</option>
              <option value="semantic_unit">semantic_unit</option>
              <option value="processing_task">processing_task</option>
              <option value="generated_sample">generated_sample</option>
            </select>
          </label>
          <label style={{ display: 'grid', gap: 6 }}>
            <span style={{ color: '#4b5563', fontSize: 13 }}>Source ID</span>
            <input placeholder="Source ID" value={sourceId} onChange={(event) => setSourceId(event.target.value)} style={inputStyle} />
          </label>
          <button onClick={queryTrace} style={{ ...buttonStyle, width: 'fit-content' }}>Query trace</button>
        </section>

        <section style={{ ...panelStyle, marginTop: 20 }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>Trace result</h2>
          <pre style={{ marginTop: 14, padding: 14, borderRadius: 8, border: '1px solid #e5e7eb', background: '#f9fafb', whiteSpace: 'pre-wrap', overflowX: 'auto' }}>
            {JSON.stringify(result, null, 2)}
          </pre>
        </section>
      </section>
    </main>
  );
}
