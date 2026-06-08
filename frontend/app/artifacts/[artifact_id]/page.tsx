"use client";

import Link from 'next/link';
import { useEffect, useState } from 'react';

import { apiJson } from '../../../lib/api';
import { openDownloadUrl } from '../../../lib/download';

export default function ArtifactDetailPage({ params }: { params: Promise<{ artifact_id: string }> }) {
  const [artifact, setArtifact] = useState<any>(null);

  useEffect(() => {
    params.then(({ artifact_id }) => {
      apiJson(`/api/v1/artifacts/${artifact_id}`)
        .then(setArtifact)
        .catch(() => setArtifact(null));
    });
  }, [params]);

  return (
    <main style={{ minHeight: '100vh', background: '#f5f7fb', fontFamily: 'Inter, sans-serif', color: '#0f172a' }}>
      <section style={{ maxWidth: 900, margin: '0 auto', padding: '48px 24px' }}>
        <h1 style={{ fontSize: 36, marginBottom: 8 }}>产物详情</h1>
        {!artifact ? (
          <p>加载中...</p>
        ) : (
          <div style={{ background: '#fff', borderRadius: 18, padding: 20, boxShadow: '0 10px 30px rgba(15,23,42,0.06)' }}>
            <div>产物 ID：{artifact.id}</div>
            <div>项目 ID：{artifact.project_id}</div>
            <div>类型：{artifact.artifact_type}</div>
            <div>名称：{artifact.name}</div>
            <div>版本：{artifact.version}</div>
            <div>文件路径：{artifact.file_path}</div>
            <button onClick={() => openDownloadUrl(`/api/v1/artifacts/${artifact.id}/download`)} style={{ marginTop: 16, padding: '10px 14px', borderRadius: 10, background: '#0f172a', color: '#fff', border: 0 }}>下载</button>
          </div>
        )}
      </section>
    </main>
  );
}
