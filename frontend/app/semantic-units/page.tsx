"use client";

import { useEffect, useState } from 'react';

import { apiJson } from '../../lib/api';

type SemanticUnit = {
  id: string;
  project_id?: string | null;
  asset_id?: string | null;
  unit_type?: string | null;
  title?: string | null;
  content?: string | null;
  labels?: Record<string, unknown> | null;
  confidence?: number | null;
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

export default function SemanticUnitsPage() {
  const [units, setUnits] = useState<SemanticUnit[]>([]);
  const [loading, setLoading] = useState(true);

  const loadUnits = async () => {
    setLoading(true);
    try {
      const data = await apiJson<SemanticUnit[]>('/api/v1/semantic-units');
      setUnits(data);
    } catch {
      setUnits([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadUnits();
  }, []);

  return (
    <main style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}>
      <section style={{ maxWidth: 1200, margin: '0 auto', padding: '44px 24px' }}>
        <header style={{ paddingBottom: 22, borderBottom: '1px solid #e5e7eb' }}>
          <h1 style={{ margin: 0, fontSize: 32, lineHeight: 1.2 }}>Semantic Units</h1>
          <p style={{ margin: '10px 0 0', maxWidth: 720, color: '#4b5563', lineHeight: 1.7 }}>
            Review extracted text chunks, scenes, rules, concepts, labels, and confidence before exporting AI materials.
          </p>
        </header>

        <section style={{ ...panelStyle, marginTop: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, paddingBottom: 14, borderBottom: '1px solid #e5e7eb' }}>
            <div>
              <h2 style={{ margin: 0, fontSize: 18 }}>Unit list</h2>
              <p style={{ margin: '6px 0 0', color: '#6b7280', fontSize: 13 }}>{units.length} units</p>
            </div>
            <button onClick={() => void loadUnits()} style={buttonStyle}>Refresh</button>
          </div>

          {loading ? <div style={{ padding: '22px 0', color: '#6b7280' }}>Loading...</div> : null}
          {!loading && units.length === 0 ? <div style={{ padding: '22px 0', color: '#6b7280' }}>No semantic units yet</div> : null}
          {units.map((unit) => (
            <details key={unit.id} style={{ padding: '16px 0', borderBottom: '1px solid #e5e7eb' }}>
              <summary style={{ cursor: 'pointer', fontWeight: 700 }}>
                {unit.unit_type || '-'} / {unit.title || unit.id}
              </summary>
              <div style={{ marginTop: 10, display: 'flex', gap: 10, flexWrap: 'wrap', color: '#6b7280', fontSize: 13 }}>
                <span>Project: {unit.project_id || '-'}</span>
                <span>Asset: {unit.asset_id || '-'}</span>
                <span>Confidence: {unit.confidence ?? '-'}</span>
              </div>
              <pre style={{ marginTop: 12, padding: 14, borderRadius: 8, border: '1px solid #e5e7eb', background: '#f9fafb', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                {unit.content || '-'}
              </pre>
              <pre style={{ marginTop: 10, padding: 14, borderRadius: 8, border: '1px solid #e5e7eb', background: '#ffffff', whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: '#4b5563' }}>
                {JSON.stringify(unit.labels || {}, null, 2)}
              </pre>
            </details>
          ))}
        </section>
      </section>
    </main>
  );
}
