"use client";

import { useEffect, useState } from 'react';

import { apiJson } from '../../lib/api';

type GeneratedSample = {
  id: string;
  project_id?: string | null;
  source_unit_id?: string | null;
  sample_type?: string | null;
  input_text?: string | null;
  output_text?: string | null;
  quality_score?: number | null;
  created_at?: string | null;
};

const panelStyle = {
  background: '#ffffff',
  border: '1px solid #e5e7eb',
  borderRadius: 8,
  padding: 24,
} as const;

const buttonStyle = {
  minHeight: 36,
  padding: '0 12px',
  borderRadius: 6,
  border: '1px solid #d1d5db',
  background: '#ffffff',
  color: '#111827',
  fontWeight: 600,
  cursor: 'pointer',
} as const;

function qualityLabel(score?: number | null) {
  if (score === null || score === undefined) return '-';
  return `${Math.round(score * 100)}%`;
}

export default function GeneratedSamplesPage() {
  const [samples, setSamples] = useState<GeneratedSample[]>([]);
  const [loading, setLoading] = useState(true);

  const loadSamples = async () => {
    setLoading(true);
    try {
      const data = await apiJson<GeneratedSample[]>('/api/v1/generated-samples');
      setSamples(data);
    } catch {
      setSamples([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadSamples();
  }, []);

  return (
    <main style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}>
      <section style={{ maxWidth: 1200, margin: '0 auto', padding: '44px 24px' }}>
        <header style={{ paddingBottom: 22, borderBottom: '1px solid #e5e7eb' }}>
          <h1 style={{ margin: 0, fontSize: 32, lineHeight: 1.2 }}>Generated Samples</h1>
          <p style={{ margin: '10px 0 0', maxWidth: 720, color: '#4b5563', lineHeight: 1.7 }}>
            Review training examples generated from semantic units before exporting datasets, RAG corpora, Skill packages, or Markdown reports.
          </p>
        </header>

        <section style={{ ...panelStyle, marginTop: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, paddingBottom: 14, borderBottom: '1px solid #e5e7eb' }}>
            <div>
              <h2 style={{ margin: 0, fontSize: 18 }}>Sample list</h2>
              <p style={{ margin: '6px 0 0', color: '#6b7280', fontSize: 13 }}>{samples.length} samples</p>
            </div>
            <button onClick={() => void loadSamples()} style={buttonStyle}>Refresh</button>
          </div>

          {loading ? <div style={{ padding: '22px 0', color: '#6b7280' }}>Loading...</div> : null}
          {!loading && samples.length === 0 ? <div style={{ padding: '22px 0', color: '#6b7280' }}>No generated samples yet</div> : null}
          {samples.map((sample) => (
            <details key={sample.id} style={{ padding: '16px 0', borderBottom: '1px solid #e5e7eb' }}>
              <summary style={{ cursor: 'pointer', fontWeight: 700 }}>
                {sample.sample_type || 'sample'} / {sample.input_text || sample.id}
              </summary>
              <div style={{ marginTop: 10, display: 'flex', gap: 10, flexWrap: 'wrap', color: '#6b7280', fontSize: 13 }}>
                <span>Project: {sample.project_id || '-'}</span>
                <span>Source unit: {sample.source_unit_id || '-'}</span>
                <span>Quality: {qualityLabel(sample.quality_score)}</span>
              </div>
              <div style={{ marginTop: 14, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 14 }}>
                <section style={{ minWidth: 0 }}>
                  <h3 style={{ margin: 0, fontSize: 14 }}>Input</h3>
                  <pre style={{ margin: '8px 0 0', padding: 14, borderRadius: 8, border: '1px solid #e5e7eb', background: '#f9fafb', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                    {sample.input_text || '-'}
                  </pre>
                </section>
                <section style={{ minWidth: 0 }}>
                  <h3 style={{ margin: 0, fontSize: 14 }}>Output</h3>
                  <pre style={{ margin: '8px 0 0', padding: 14, borderRadius: 8, border: '1px solid #e5e7eb', background: '#ffffff', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                    {sample.output_text || '-'}
                  </pre>
                </section>
              </div>
            </details>
          ))}
        </section>
      </section>
    </main>
  );
}
