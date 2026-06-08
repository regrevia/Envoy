"use client";

import Link from 'next/link';
import { useEffect, useState } from 'react';

import { apiJson } from '../../lib/api';
import { openDownloadUrl } from '../../lib/download';

export default function ArtifactsPage() {
  const [artifacts, setArtifacts] = useState<any[]>([]);

  useEffect(() => {
    apiJson<any[]>('/api/v1/artifacts')
      .then(setArtifacts)
      .catch(() => setArtifacts([]));
  }, []);

  return (
    <main style={{ minHeight: '100vh', background: '#f5f7fb', fontFamily: 'Inter, sans-serif', color: '#0f172a' }}>
      <section style={{ maxWidth: 1100, margin: '0 auto', padding: '48px 24px' }}>
        <h1 style={{ fontSize: 36, marginBottom: 8 }}>产物中心</h1>
        <p style={{ color: '#475569' }}>展示数据集、报告与导出产物。</p>

        <div style={{ display: 'grid', gap: 16, marginTop: 24 }}>
          {artifacts.length === 0 ? <div>暂无产物</div> : null}
          {artifacts.map((artifact) => (
            <div key={artifact.id ?? JSON.stringify(artifact)} style={{ background: '#fff', borderRadius: 18, padding: 20, boxShadow: '0 10px 30px rgba(15,23,42,0.06)' }}>
              <div>类型：{artifact.artifact_type ?? '-'}</div>
              <div>名称：{artifact.name ?? '-'}</div>
              <div>版本：{artifact.version ?? '-'}</div>
              <button onClick={() => openDownloadUrl(`/api/v1/artifacts/${artifact.id}/download`)} style={{ marginTop: 8, padding: '8px 12px', borderRadius: 10, background: '#0f172a', color: '#fff', border: 0 }}>下载</button>
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
