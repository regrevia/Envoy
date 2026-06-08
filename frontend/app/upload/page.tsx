"use client";

import { useEffect, useState } from 'react';

import { ApiError, apiJson } from '../../lib/api';

const fileTypeOptions = [
  { id: 'video', label: '视频' },
  { id: 'markdown', label: 'Markdown' },
  { id: 'text', label: '长文本' },
  { id: 'audio', label: '音频' },
  { id: 'document', label: '文档' },
  { id: 'image', label: '图片' },
];

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

type Project = {
  id: string;
  name: string;
};

type TaskResponse = {
  id?: string;
  status?: string;
  task_type?: string;
  progress?: number;
  result_summary?: string | null;
  error_message?: string | null;
};

function uploadedAsset(result: any) {
  return result && typeof result === 'object' && 'asset' in result ? result.asset : null;
}

export default function UploadPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState('');
  const [assetType, setAssetType] = useState('video');
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<any>(null);
  const [preprocessResult, setPreprocessResult] = useState<any>(null);
  const [message, setMessage] = useState('请选择任务/项目并上传素材');
  const [uploading, setUploading] = useState(false);
  const [preprocessing, setPreprocessing] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const projectFromQuery = params.get('project_id');
    if (projectFromQuery) {
      setProjectId(projectFromQuery);
    }

    apiJson<Project[]>('/api/v1/projects')
      .then(setProjects)
      .catch(() => setProjects([]));
  }, []);

  const onSubmit = async () => {
    if (!file || !projectId || uploading) {
      setMessage('请先选择任务/项目和文件');
      return;
    }
    const form = new FormData();
    form.append('project_id', projectId);
    form.append('asset_type', assetType);
    form.append('file', file);

    setUploading(true);
    setMessage('正在上传素材...');
    try {
      const data = await apiJson('/api/v1/assets/upload', { method: 'POST', body: form });
      setResult(data);
      setPreprocessResult(null);
      setMessage('上传完成，可以启动预处理');
    } catch (error) {
      const errorMessage = error instanceof ApiError || error instanceof Error ? error.message : '上传失败';
      setResult({ error: errorMessage });
      setMessage(`上传失败：${errorMessage}`);
    } finally {
      setUploading(false);
    }
  };

  const onPreprocess = async () => {
    const asset = uploadedAsset(result);
    if (!asset?.id || !asset?.project_id || preprocessing) {
      setMessage('请先上传素材再启动预处理');
      return;
    }

    setPreprocessing(true);
    setMessage('正在启动预处理转换...');
    try {
      const data = await apiJson<TaskResponse>('/api/v1/tasks/preprocess', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: asset.project_id,
          asset_id: asset.id,
          task_type: 'preprocess',
          input_config: {},
        }),
      });
      setPreprocessResult(data);
      setMessage(`预处理任务已创建：${data.status || 'created'}`);
    } catch (error) {
      const errorMessage = error instanceof ApiError || error instanceof Error ? error.message : '预处理失败';
      setPreprocessResult({ error: errorMessage });
      setMessage(`预处理失败：${errorMessage}`);
    } finally {
      setPreprocessing(false);
    }
  };

  const asset = uploadedAsset(result);

  return (
    <main style={{ minHeight: '100vh', background: '#ffffff', fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}>
      <section style={{ maxWidth: 960, margin: '0 auto', padding: '44px 24px' }}>
        <header style={{ paddingBottom: 22, borderBottom: '1px solid #e5e7eb' }}>
          <div style={{ color: '#6b7280', fontSize: 14, marginBottom: 8 }}>第 2 步</div>
          <h1 style={{ margin: 0, fontSize: 32, lineHeight: 1.2 }}>上传素材</h1>
          <p style={{ margin: '10px 0 0', maxWidth: 720, color: '#4b5563', lineHeight: 1.7 }}>
            将视频、音频、图片、文档、Markdown 或长文本放入当前任务，上传后可直接启动预处理转换。
          </p>
        </header>

        <div style={{ padding: '14px 0', borderBottom: '1px solid #e5e7eb', color: '#374151', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <strong style={{ fontSize: 14 }}>状态</strong>
          <span style={{ fontSize: 14 }}>{message}</span>
        </div>

        <section style={{ marginTop: 22, paddingTop: 18, borderTop: '1px solid #e5e7eb' }}>
          <div style={{ display: 'grid', gap: 14 }}>
            <label style={{ display: 'grid', gap: 6 }}>
              <span style={{ color: '#4b5563', fontSize: 13 }}>选择任务/项目</span>
              <select value={projectId} onChange={(event) => setProjectId(event.target.value)} style={inputStyle}>
                <option value="">请选择任务/项目</option>
                {projects.map((project) => <option key={project.id} value={project.id}>{project.name} ({project.id})</option>)}
              </select>
            </label>
            <label style={{ display: 'grid', gap: 6 }}>
              <span style={{ color: '#4b5563', fontSize: 13 }}>素材类型</span>
              <select value={assetType} onChange={(event) => setAssetType(event.target.value)} style={inputStyle}>
                {fileTypeOptions.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
              </select>
            </label>
            <label style={{ display: 'grid', gap: 6 }}>
              <span style={{ color: '#4b5563', fontSize: 13 }}>文件</span>
              <input type="file" accept=".mp4,.md,.txt,.wav,.mp3,.pdf,image/*" onChange={(event) => setFile(event.target.files?.[0] ?? null)} style={inputStyle} />
            </label>
            <button onClick={onSubmit} disabled={uploading} style={{ ...buttonStyle, width: 'fit-content', opacity: uploading ? 0.72 : 1, cursor: uploading ? 'not-allowed' : 'pointer' }}>
              {uploading ? '正在上传...' : '上传素材'}
            </button>
          </div>
        </section>

        {result ? (
          <section style={{ marginTop: 22, paddingTop: 18, borderTop: '1px solid #e5e7eb' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', paddingBottom: 14, borderBottom: '1px solid #e5e7eb' }}>
              <div>
                <h2 style={{ margin: 0, fontSize: 18 }}>上传结果</h2>
                <p style={{ margin: '6px 0 0', color: '#6b7280', fontSize: 13 }}>{asset?.id ? `素材 ${asset.id}` : '上传响应'}</p>
              </div>
              {asset?.id ? (
                <button
                  onClick={onPreprocess}
                  disabled={preprocessing}
                  style={{ ...buttonStyle, background: preprocessing ? '#6b7280' : '#111827', color: '#ffffff', flex: '0 0 auto', opacity: preprocessing ? 0.78 : 1, cursor: preprocessing ? 'not-allowed' : 'pointer' }}
                >
                  {preprocessing ? '正在启动...' : '启动预处理'}
                </button>
              ) : null}
            </div>
            <pre style={{ marginTop: 14, padding: '12px 0 0', borderTop: '1px solid #e5e7eb', background: '#ffffff', whiteSpace: 'pre-wrap', overflowX: 'auto' }}>
              {JSON.stringify(result, null, 2)}
            </pre>
            {preprocessResult ? (
              <pre style={{ marginTop: 12, padding: '12px 0 0', borderTop: '1px solid #e5e7eb', background: '#ffffff', whiteSpace: 'pre-wrap', overflowX: 'auto' }}>
                {JSON.stringify(preprocessResult, null, 2)}
              </pre>
            ) : null}
          </section>
        ) : null}
      </section>
    </main>
  );
}
