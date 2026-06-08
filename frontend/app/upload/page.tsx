"use client";

import { useEffect, useState } from 'react';

import { ApiError, apiJson } from '../../lib/api';

const fileTypeOptions = [
  { id: 'video', label: 'Video' },
  { id: 'markdown', label: 'Markdown' },
  { id: 'text', label: 'Text' },
  { id: 'audio', label: 'Audio' },
  { id: 'document', label: 'Document' },
  { id: 'image', label: 'Image' },
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
  const [message, setMessage] = useState('Ready');
  const [uploading, setUploading] = useState(false);
  const [preprocessing, setPreprocessing] = useState(false);

  useEffect(() => {
    apiJson<Project[]>('/api/v1/projects')
      .then(setProjects)
      .catch(() => setProjects([]));
  }, []);

  const onSubmit = async () => {
    if (!file || !projectId || uploading) {
      setMessage('Choose a project and file before uploading');
      return;
    }
    const form = new FormData();
    form.append('project_id', projectId);
    form.append('asset_type', assetType);
    form.append('file', file);

    setUploading(true);
    setMessage('Uploading asset...');
    try {
      const data = await apiJson('/api/v1/assets/upload', { method: 'POST', body: form });
      setResult(data);
      setPreprocessResult(null);
      setMessage('Upload complete');
    } catch (error) {
      const errorMessage = error instanceof ApiError || error instanceof Error ? error.message : 'Upload failed';
      setResult({ error: errorMessage });
      setMessage(`Upload failed: ${errorMessage}`);
    } finally {
      setUploading(false);
    }
  };

  const onPreprocess = async () => {
    const asset = uploadedAsset(result);
    if (!asset?.id || !asset?.project_id || preprocessing) {
      setMessage('Upload an asset before preprocessing');
      return;
    }

    setPreprocessing(true);
    setMessage('Starting preprocessing...');
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
      setMessage(`Preprocessing task ${data.status || 'created'}`);
    } catch (error) {
      const errorMessage = error instanceof ApiError || error instanceof Error ? error.message : 'Preprocessing failed';
      setPreprocessResult({ error: errorMessage });
      setMessage(`Preprocessing failed: ${errorMessage}`);
    } finally {
      setPreprocessing(false);
    }
  };

  const asset = uploadedAsset(result);

  return (
    <main style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}>
      <section style={{ maxWidth: 960, margin: '0 auto', padding: '44px 24px' }}>
        <header style={{ paddingBottom: 22, borderBottom: '1px solid #e5e7eb' }}>
          <h1 style={{ margin: 0, fontSize: 32, lineHeight: 1.2 }}>Upload</h1>
          <p style={{ margin: '10px 0 0', maxWidth: 720, color: '#4b5563', lineHeight: 1.7 }}>
            Add source files to a project before preprocessing. Video, long text, Markdown, audio, PDFs, and images can all enter the material pipeline.
          </p>
        </header>

        <div style={{ marginTop: 18, padding: '12px 14px', border: '1px solid #e5e7eb', borderRadius: 8, background: '#ffffff', color: '#374151', display: 'flex', gap: 10, alignItems: 'center' }}>
          <strong style={{ fontSize: 14 }}>Status</strong>
          <span style={{ fontSize: 14 }}>{message}</span>
        </div>

        <section style={{ ...panelStyle, marginTop: 20 }}>
          <div style={{ display: 'grid', gap: 14 }}>
            <label style={{ display: 'grid', gap: 6 }}>
              <span style={{ color: '#4b5563', fontSize: 13 }}>Project</span>
              <select value={projectId} onChange={(event) => setProjectId(event.target.value)} style={inputStyle}>
                <option value="">Choose project</option>
                {projects.map((project) => <option key={project.id} value={project.id}>{project.name} ({project.id})</option>)}
              </select>
            </label>
            <label style={{ display: 'grid', gap: 6 }}>
              <span style={{ color: '#4b5563', fontSize: 13 }}>Asset type</span>
              <select value={assetType} onChange={(event) => setAssetType(event.target.value)} style={inputStyle}>
                {fileTypeOptions.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
              </select>
            </label>
            <label style={{ display: 'grid', gap: 6 }}>
              <span style={{ color: '#4b5563', fontSize: 13 }}>File</span>
              <input type="file" accept=".mp4,.md,.txt,.wav,.mp3,.pdf,image/*" onChange={(event) => setFile(event.target.files?.[0] ?? null)} style={inputStyle} />
            </label>
            <button onClick={onSubmit} disabled={uploading} style={{ ...buttonStyle, width: 'fit-content', opacity: uploading ? 0.72 : 1, cursor: uploading ? 'not-allowed' : 'pointer' }}>
              {uploading ? 'Uploading...' : 'Upload asset'}
            </button>
          </div>
        </section>

        {result ? (
          <section style={{ ...panelStyle, marginTop: 20 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', paddingBottom: 14, borderBottom: '1px solid #e5e7eb' }}>
              <div>
                <h2 style={{ margin: 0, fontSize: 18 }}>Upload result</h2>
                <p style={{ margin: '6px 0 0', color: '#6b7280', fontSize: 13 }}>{asset?.id ? `Asset ${asset.id}` : 'Upload response'}</p>
              </div>
              {asset?.id ? (
                <button
                  onClick={onPreprocess}
                  disabled={preprocessing}
                  style={{ ...buttonStyle, background: preprocessing ? '#6b7280' : '#111827', color: '#ffffff', flex: '0 0 auto', opacity: preprocessing ? 0.78 : 1, cursor: preprocessing ? 'not-allowed' : 'pointer' }}
                >
                  {preprocessing ? 'Starting...' : 'Start preprocessing'}
                </button>
              ) : null}
            </div>
            <pre style={{ marginTop: 14, padding: 14, borderRadius: 8, border: '1px solid #e5e7eb', background: '#f9fafb', whiteSpace: 'pre-wrap', overflowX: 'auto' }}>
              {JSON.stringify(result, null, 2)}
            </pre>
            {preprocessResult ? (
              <pre style={{ marginTop: 12, padding: 14, borderRadius: 8, border: '1px solid #e5e7eb', background: '#ffffff', whiteSpace: 'pre-wrap', overflowX: 'auto' }}>
                {JSON.stringify(preprocessResult, null, 2)}
              </pre>
            ) : null}
          </section>
        ) : null}
      </section>
    </main>
  );
}
