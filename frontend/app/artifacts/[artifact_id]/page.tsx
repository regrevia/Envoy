"use client";

import Link from 'next/link';
import { useEffect, useState } from 'react';

import { apiJson } from '../../../lib/api';
import { openDownloadUrl } from '../../../lib/download';

type Artifact = {
  id: string;
  project_id?: string | null;
  artifact_type?: string | null;
  name?: string | null;
  file_path?: string | null;
  version?: string | null;
  created_at?: string | null;
};

const panelStyle = {
  background: '#ffffff',
  border: '1px solid #e5e7eb',
  borderRadius: 8,
  padding: 24,
} as const;

function DetailRow({ label, value }: { label: string; value?: string | null }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '140px minmax(0, 1fr)', gap: 16, padding: '12px 0', borderBottom: '1px solid #e5e7eb' }}>
      <div style={{ color: '#6b7280' }}>{label}</div>
      <div style={{ wordBreak: 'break-all' }}>{value || '-'}</div>
    </div>
  );
}

export default function ArtifactDetailPage({ params }: { params: Promise<{ artifact_id: string }> }) {
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    params.then(({ artifact_id }) => {
      apiJson<Artifact>(`/api/v1/artifacts/${artifact_id}`)
        .then(setArtifact)
        .catch(() => setArtifact(null))
        .finally(() => setLoading(false));
    });
  }, [params]);

  return (
    <main style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}>
      <section style={{ maxWidth: 920, margin: '0 auto', padding: '44px 24px' }}>
        <header style={{ paddingBottom: 22, borderBottom: '1px solid #e5e7eb' }}>
          <Link href="/artifacts" style={{ color: '#4b5563', fontSize: 14, textDecoration: 'none' }}>返回产物中心</Link>
          <h1 style={{ margin: '10px 0 0', fontSize: 32, lineHeight: 1.2 }}>产物详情</h1>
        </header>

        <section style={{ ...panelStyle, marginTop: 20 }}>
          {loading ? <div style={{ color: '#6b7280' }}>加载中...</div> : null}
          {!loading && !artifact ? <div style={{ color: '#b91c1c' }}>未找到产物</div> : null}
          {artifact ? (
            <>
              <DetailRow label="产物 ID" value={artifact.id} />
              <DetailRow label="项目 ID" value={artifact.project_id} />
              <DetailRow label="类型" value={artifact.artifact_type} />
              <DetailRow label="名称" value={artifact.name} />
              <DetailRow label="版本" value={artifact.version} />
              <DetailRow label="文件路径" value={artifact.file_path} />
              <DetailRow label="创建时间" value={artifact.created_at} />
              <button onClick={() => openDownloadUrl(`/api/v1/artifacts/${artifact.id}/download`)} style={{ marginTop: 18, minHeight: 40, padding: '0 14px', borderRadius: 6, background: '#111827', color: '#fff', border: 0, fontWeight: 600, cursor: 'pointer' }}>
                下载文件
              </button>
            </>
          ) : null}
        </section>
      </section>
    </main>
  );
}
