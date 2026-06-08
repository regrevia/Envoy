"use client";

import { useEffect, useState } from 'react';

import { apiJson } from '../../lib/api';
import { apiUrl } from '@/lib/api';

export default function SemanticUnitsPage() {
  const [units, setUnits] = useState<any[]>([]);

  useEffect(() => {
    apiJson<any[]>('/api/v1/semantic-units')
      .then(setUnits)
      .catch(() => setUnits([]));
  }, []);

  return (
    <main style={{ minHeight: '100vh', background: '#f5f7fb', fontFamily: 'Inter, sans-serif', color: '#0f172a' }}>
      <section style={{ maxWidth: 1200, margin: '0 auto', padding: '48px 24px' }}>
        <h1 style={{ fontSize: 36, marginBottom: 8 }}>语义单元</h1>
        <p style={{ color: '#475569' }}>展示场景、规则、概念等结构化结果。</p>

        <div style={{ display: 'grid', gap: 16, marginTop: 24 }}>
          {units.length === 0 ? <div>暂无语义单元</div> : null}
          {units.map((unit) => (
            <details key={unit.id} style={{ background: '#fff', borderRadius: 18, padding: 20, boxShadow: '0 10px 30px rgba(15,23,42,0.06)' }}>
              <summary style={{ cursor: 'pointer', fontWeight: 700 }}>{unit.unit_type ?? '-'} · {unit.title ?? '-'}</summary>
              <div style={{ marginTop: 12, color: '#334155' }}>置信度：{unit.confidence ?? '-'}</div>
              <pre style={{ marginTop: 12, whiteSpace: 'pre-wrap' }}>{unit.content ?? '-'}</pre>
            </details>
          ))}
        </div>
      </section>
    </main>
  );
}
