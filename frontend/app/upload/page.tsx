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

const outputOptions = [
  { id: 'markdown', label: 'Markdown' },
  { id: 'skill', label: 'Skill' },
  { id: 'rag', label: 'RAG' },
  { id: 'samples', label: '训练样本' },
];

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
  const [selectedOutputs, setSelectedOutputs] = useState<string[]>(['markdown', 'skill', 'rag']);
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<any>(null);
  const [preprocessResult, setPreprocessResult] = useState<any>(null);
  const [message, setMessage] = useState('请选择任务/项目并上传素材');
  const [uploading, setUploading] = useState(false);
  const [preprocessing, setPreprocessing] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const projectFromQuery = params.get('project_id');
    const outputsFromQuery = params.get('outputs');
    if (projectFromQuery) {
      setProjectId(projectFromQuery);
    }
    if (outputsFromQuery) {
      const nextOutputs = outputsFromQuery.split(',').map((item) => item.trim()).filter(Boolean);
      if (nextOutputs.length) {
        setSelectedOutputs(nextOutputs);
      }
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
    if (file.size === 0) {
      setResult({ error: '上传文件为空，请选择包含内容的素材' });
      setMessage('上传文件为空，请选择包含内容的素材');
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
          input_config: { requested_outputs: selectedOutputs },
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
  const materialOutputs = selectedOutputs.filter((item) => ['markdown', 'skill', 'rag'].includes(item));
  const hasMaterialOutput = materialOutputs.length > 0;
  const materialOutputParam = hasMaterialOutput ? materialOutputs.join(',') : '';
  const primaryMaterialOutput = materialOutputs[0] || 'markdown';
  const projectQuery = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
  const exportQuery = projectId && hasMaterialOutput ? `?project_id=${encodeURIComponent(projectId)}&outputs=${encodeURIComponent(materialOutputParam)}&export_type=${encodeURIComponent(primaryMaterialOutput)}` : '';
  const taskHref = preprocessResult?.id ? `/tasks/${preprocessResult.id}` : '/tasks';
  const semanticHref = asset?.project_id ? `/semantic-units?project_id=${asset.project_id}` : `/semantic-units${projectQuery}`;
  const samplesHref = asset?.project_id ? `/generated-samples?project_id=${asset.project_id}` : `/generated-samples${projectQuery}`;
  const materialExportHref = hasMaterialOutput
    ? (asset?.project_id ? `/train-exports?project_id=${asset.project_id}&outputs=${encodeURIComponent(materialOutputParam)}&export_type=${encodeURIComponent(primaryMaterialOutput)}` : `/train-exports${exportQuery}`)
    : '#';
  const traceHref = asset?.id ? `/trace?type=asset&id=${asset.id}` : '/trace';

  const toggleOutput = (outputId: string) => {
    setSelectedOutputs((current) => {
      if (current.includes(outputId)) {
        return current.filter((item) => item !== outputId);
      }
      return [...current, outputId];
    });
  };

  return (
    <main style={{ minHeight: '100vh', background: '#ffffff', fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}>
      <section style={{ maxWidth: 960, margin: '0 auto', padding: '44px 24px' }}>
        <header style={{ paddingBottom: 22, borderBottom: '1px solid #e5e7eb' }}>
          <div style={{ color: '#6b7280', fontSize: 14, marginBottom: 8 }}>第 2 步</div>
          <h1 style={{ margin: 0, fontSize: 32, lineHeight: 1.2 }}>多媒体转换任务</h1>
          <p style={{ margin: '10px 0 0', maxWidth: 720, color: '#4b5563', lineHeight: 1.7 }}>
            视频/音频/图片/文档/长文本进入预处理转换后，可继续生成 Markdown、Skill、RAG 和训练样本等 AI 素材。
          </p>
        </header>

        <section style={{ display: 'grid', gap: 0, borderBottom: '1px solid #e5e7eb' }}>
          {['1. 确认任务/项目', '2. 上传素材', '3. 选择生成内容'].map((step) => (
            <div key={step} style={{ padding: '14px 0', borderTop: '1px solid #e5e7eb', fontWeight: 700, color: '#111827' }}>
              {step}
            </div>
          ))}
        </section>

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
              <span style={{ color: '#6b7280', fontSize: 12 }}>支持类型：视频、音频、图片、文档、Markdown、长文本</span>
            </label>
            <label style={{ display: 'grid', gap: 6 }}>
              <span style={{ color: '#4b5563', fontSize: 13 }}>文件</span>
              <input type="file" accept=".mp4,.mov,.mkv,.md,.txt,.wav,.mp3,.m4a,.pdf,.docx,.pptx,.xlsx,image/*" onChange={(event) => setFile(event.target.files?.[0] ?? null)} style={inputStyle} />
            </label>
            <button onClick={onSubmit} disabled={uploading} style={{ ...buttonStyle, width: 'fit-content', opacity: uploading ? 0.72 : 1, cursor: uploading ? 'not-allowed' : 'pointer' }}>
              {uploading ? '正在上传...' : '上传素材'}
            </button>
          </div>
        </section>

        <section style={{ marginTop: 22, paddingTop: 18, borderTop: '1px solid #e5e7eb' }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>3. 选择生成内容</h2>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 12 }}>
            {outputOptions.map((option) => (
              <label key={option.id} style={{ display: 'inline-flex', gap: 8, alignItems: 'center', padding: '8px 0', color: '#374151' }}>
                <input
                  type="checkbox"
                  checked={selectedOutputs.includes(option.id)}
                  onChange={() => toggleOutput(option.id)}
                />
                <span>{option.label}</span>
              </label>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 12, paddingTop: 12, borderTop: '1px solid #e5e7eb' }}>
            <a href="/tasks" style={{ color: '#111827', fontWeight: 700 }}>查看转换任务</a>
            <a href={`/semantic-units${projectQuery}`} style={{ color: '#111827', fontWeight: 700 }}>查看语义单元</a>
            <a href={`/train-exports${exportQuery}`} style={{ color: '#111827', fontWeight: 700 }}>训练导出</a>
          </div>
        </section>

        {result ? (
          <section style={{ marginTop: 22, paddingTop: 18, borderTop: '1px solid #e5e7eb' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', paddingBottom: 14, borderBottom: '1px solid #e5e7eb' }}>
              <div>
                <h2 style={{ margin: 0, fontSize: 18 }}>上传结果</h2>
                <p style={{ margin: '6px 0 0', color: '#6b7280', fontSize: 13 }}>
                  {asset?.id ? '素材已上传，可以启动预处理转换' : (result?.error || '素材上传结果')}
                </p>
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
            {asset?.id ? (
              <div style={{ display: 'grid', gap: 0 }}>
                <div style={{ padding: '12px 0', borderBottom: '1px solid #e5e7eb' }}>
                  <strong>素材已上传</strong>
                  <div style={{ marginTop: 6, color: '#6b7280', fontSize: 13, lineHeight: 1.6 }}>
                    素材 ID：{asset.id}；项目 ID：{asset.project_id || projectId}；类型：{asset.asset_type || assetType}；文件：{asset.file_name || file?.name || '-'}
                  </div>
                </div>
                <div style={{ padding: '12px 0', borderBottom: '1px solid #e5e7eb' }}>
                  <strong>预处理任务</strong>
                  <div style={{ marginTop: 6, color: '#6b7280', fontSize: 13, lineHeight: 1.6 }}>
                    {preprocessResult?.error ? `启动失败：${preprocessResult.error}` : preprocessResult?.id ? `任务 ID：${preprocessResult.id}；状态：${preprocessResult.status || 'created'}；进度：${preprocessResult.progress ?? 0}%` : '上传完成后点击“启动预处理”，系统会把素材转为语义单元。'}
                  </div>
                  {preprocessResult?.result_summary ? (
                    <div style={{ marginTop: 6, color: '#374151', fontSize: 13 }}>{preprocessResult.result_summary}</div>
                  ) : null}
                </div>
                <div style={{ padding: '12px 0' }}>
                  <strong>下一步操作</strong>
                  <div style={{ marginTop: 10, display: 'flex', gap: 14, flexWrap: 'wrap' }}>
                    <a href={taskHref} style={{ color: '#111827', fontWeight: 700 }}>查看预处理任务</a>
                    <a href={semanticHref} style={{ color: '#111827', fontWeight: 700 }}>查看语义单元</a>
                    <a href={samplesHref} style={{ color: '#111827', fontWeight: 700 }}>生成训练样本</a>
                    <a href={materialExportHref} aria-disabled={!hasMaterialOutput} style={{ color: hasMaterialOutput ? '#111827' : '#6b7280', fontWeight: 700, pointerEvents: hasMaterialOutput ? 'auto' : 'none' }}>导出 Markdown / Skill / RAG</a>
                    <a href={traceHref} style={{ color: '#111827', fontWeight: 700 }}>查看追溯链路</a>
                  </div>
                  {!hasMaterialOutput ? (
                    <div style={{ marginTop: 8, color: '#b45309', fontSize: 13 }}>
                      请先勾选 Markdown、Skill 或 RAG 后再导出 AI 素材。
                    </div>
                  ) : null}
                </div>
              </div>
            ) : (
              <div style={{ padding: '12px 0', color: '#b91c1c', borderBottom: '1px solid #e5e7eb' }}>
                {result?.error || '上传未返回有效素材，请重新选择文件。'}
              </div>
            )}
          </section>
        ) : null}
      </section>
    </main>
  );
}
