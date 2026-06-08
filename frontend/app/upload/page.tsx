"use client";

import { useEffect, useState } from 'react';

import { ApiError, apiJson } from '../../lib/api';

const fileTypeOptions = [
  { id: 'video', label: 'MP4 视频' },
  { id: 'markdown', label: 'Markdown' },
  { id: 'text', label: 'TXT 文本' },
];

export default function UploadPage() {
  const [projects, setProjects] = useState<any[]>([]);
  const [projectId, setProjectId] = useState('');
  const [assetType, setAssetType] = useState('video');
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<any>(null);

  useEffect(() => {
    apiJson<any[]>('/api/v1/projects')
      .then(setProjects)
      .catch(() => setProjects([]));
  }, []);

  const onSubmit = async () => {
    if (!file || !projectId) return;
    const form = new FormData();
    form.append('project_id', projectId);
    form.append('asset_type', assetType);
    form.append('file', file);
    try {
      const data = await apiJson('/api/v1/assets/upload', { method: 'POST', body: form });
      setResult(data);
    } catch (error) {
      const message = error instanceof ApiError ? error.message : error instanceof Error ? error.message : '上传失败';
      setResult({ error: message });
    }
  };

  return (
    <main style={{ minHeight: '100vh', background: '#f5f7fb', fontFamily: 'Inter, sans-serif', color: '#0f172a' }}>
      <section style={{ maxWidth: 900, margin: '0 auto', padding: '48px 24px' }}>
        <h1 style={{ fontSize: 36, marginBottom: 8 }}>素材上传</h1>
        <p style={{ color: '#475569' }}>一个项目支持上传 MP4、MD、TXT 等多种输入。</p>

        <div style={{ background: '#fff', borderRadius: 18, padding: 24, boxShadow: '0 10px 30px rgba(15,23,42,0.06)', marginTop: 24 }}>
          <div style={{ display: 'grid', gap: 16 }}>
            <select value={projectId} onChange={(e) => setProjectId(e.target.value)} style={{ padding: 12, borderRadius: 10, border: '1px solid #cbd5e1' }}>
              <option value="">选择项目</option>
              {projects.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.id})</option>)}
            </select>
            <select value={assetType} onChange={(e) => setAssetType(e.target.value)} style={{ padding: 12, borderRadius: 10, border: '1px solid #cbd5e1' }}>
              {fileTypeOptions.map((opt) => <option key={opt.id} value={opt.id}>{opt.label}</option>)}
            </select>
            <input type="file" accept=".mp4,.md,.txt,.wav,.mp3,.pdf,image/*" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            <button onClick={onSubmit} style={{ padding: '12px 16px', borderRadius: 10, background: '#0f172a', color: '#fff', border: 0 }}>上传并入库</button>
          </div>
        </div>

        {result ? (
          <pre style={{ marginTop: 24, background: '#fff', padding: 20, borderRadius: 18, overflowX: 'auto' }}>
            {JSON.stringify(result, null, 2)}
          </pre>
        ) : null}
      </section>
    </main>
  );
}
