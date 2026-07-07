"use client";

import Link from 'next/link';
import { useEffect, useState } from 'react';

import { apiJson } from '../../lib/api';
import { openDownloadUrl } from '../../lib/download';

type Artifact = {
  id: string;
  project_id?: string | null;
  artifact_type?: string | null;
  name?: string | null;
  file_path?: string | null;
  version?: string | null;
  created_at?: string | null;
};

const buttonStyle = {
  minHeight: 36,
  padding: '0 12px',
  borderRadius: 999,
  border: '1px solid #0066cc',
  background: '#ffffff',
  color: '#0066cc',
  fontWeight: 400,
  cursor: 'pointer',
} as const;

function formatTime(value?: string | null) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { hour12: false });
}

export default function ArtifactsPage() {
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [loading, setLoading] = useState(true);

  const loadArtifacts = async () => {
    setLoading(true);
    try {
      const data = await apiJson<Artifact[]>('/api/v1/artifacts');
      setArtifacts(data);
    } catch {
      setArtifacts([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadArtifacts();
  }, []);

  return (
    <main style={{ minHeight: '100vh', background: '#f5f5f7', fontFamily: 'SF Pro Text, system-ui, -apple-system, sans-serif', color: '#1d1d1f' }}>
      <section style={{ maxWidth: 1120, margin: '0 auto', padding: '44px 24px' }}>
        <header style={{ paddingBottom: 22, borderBottom: '1px solid #e5e7eb' }}>
          <h1 style={{ margin: 0, fontSize: 40, lineHeight: 1.1, letterSpacing: '-0.28px', fontWeight: 600 }}>交付中心</h1>
          <p style={{ margin: '10px 0 0', maxWidth: 680, color: '#333333', fontSize: 17, lineHeight: 1.47 }}>
            查看已生成的知识库文档、流程包、检索语料和数据文件，并进入详情审查来源统计。
          </p>
        </header>

        <section style={{ marginTop: 22, paddingTop: 18, borderTop: '1px solid #e5e7eb' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, paddingBottom: 14, borderBottom: '1px solid #e5e7eb' }}>
            <div>
              <h2 style={{ margin: 0, fontSize: 18 }}>交付列表</h2>
              <p style={{ margin: '6px 0 0', color: '#7a7a7a', fontSize: 13 }}>共 {artifacts.length} 个交付物</p>
            </div>
            <button onClick={() => void loadArtifacts()} style={buttonStyle}>刷新</button>
          </div>

          <div>
            {loading ? <div style={{ padding: '22px 0', color: '#6b7280' }}>加载中...</div> : null}
            {!loading && artifacts.length === 0 ? <div style={{ padding: '22px 0', color: '#7a7a7a' }}>暂无交付物</div> : null}
            {artifacts.map((artifact) => (
              <div key={artifact.id} style={{ padding: '16px 0', borderBottom: '1px solid #e5e7eb' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'flex-start' }}>
                  <div style={{ minWidth: 0 }}>
                    <Link href={`/artifacts/${artifact.id}`} style={{ fontWeight: 700, textDecoration: 'none' }}>
                      {artifact.name || artifact.id}
                    </Link>
                    <div style={{ marginTop: 8, display: 'flex', gap: 10, flexWrap: 'wrap', color: '#6b7280', fontSize: 13 }}>
                      <span>来源任务: {artifact.project_id || '-'}</span>
                      <span>类型：{artifact.artifact_type || '-'}</span>
                      <span>版本：{artifact.version || '-'}</span>
                      <span>创建: {formatTime(artifact.created_at)}</span>
                    </div>
                    <div style={{ marginTop: 6, color: '#6b7280', fontSize: 13, wordBreak: 'break-all' }}>
                      {artifact.file_path || '-'}
                    </div>
                  </div>
                  <button onClick={() => openDownloadUrl(`/api/v1/artifacts/${artifact.id}/download`)} style={{ ...buttonStyle, background: '#0066cc', color: '#ffffff', borderColor: '#0066cc', flex: '0 0 auto' }}>
                    下载
                  </button>
                </div>
              </div>
            ))}
          </div>
        </section>
      </section>
    </main>
  );
}
