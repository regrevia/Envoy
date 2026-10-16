"use client";

import Link from 'next/link';
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
  { id: 'delivery', label: '商业交付包' },
  { id: 'markdown', label: '知识库文档' },
  { id: 'skill', label: '可复用流程包' },
  { id: 'rag', label: '检索语料包' },
  { id: 'samples', label: '训练问答' },
];
const pdfChunkingOptions = [
  { id: 'auto', label: '自动智能切片' },
  { id: 'content', label: '按内容主题切片' },
  { id: 'module', label: '按模块/章节切片' },
  { id: 'page', label: '按页切片' },
];
const outputOptionIds = new Set(outputOptions.map((option) => option.id));
const outputLabelById = Object.fromEntries(outputOptions.map((option) => [option.id, option.label]));
const fileTypeOptionIds = new Set(fileTypeOptions.map((option) => option.id));
const uploadRequestTimeoutMs = 120000;

const inputStyle = {
  width: '100%',
  padding: '11px 12px',
  borderRadius: 999,
  border: '1px solid #e0e0e0',
  background: '#ffffff',
  color: '#1d1d1f',
} as const;

const buttonStyle = {
  minHeight: 40,
  padding: '0 18px',
  borderRadius: 999,
  border: 0,
  background: '#0066cc',
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
  const [selectedOutputs, setSelectedOutputs] = useState<string[]>(['delivery', 'markdown', 'skill', 'rag']);
  const [pdfChunkingStrategy, setPdfChunkingStrategy] = useState('auto');
  const [videoTargetFrameRate, setVideoTargetFrameRate] = useState('0');
  const [videoFrameIntervalSeconds, setVideoFrameIntervalSeconds] = useState('15');
  const [videoMaxFrameCount, setVideoMaxFrameCount] = useState('240');
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<any>(null);
  const [preprocessResult, setPreprocessResult] = useState<any>(null);
  const [message, setMessage] = useState('请选择任务/项目并上传素材');
  const [uploading, setUploading] = useState(false);
  const [preprocessing, setPreprocessing] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const projectFromQuery = params.get('project_id');
    const assetTypeFromQuery = params.get('asset_type');
    const outputsFromQuery = params.get('outputs');
    if (projectFromQuery) {
      setProjectId(projectFromQuery);
    }
    if (assetTypeFromQuery && fileTypeOptionIds.has(assetTypeFromQuery)) {
      setAssetType(assetTypeFromQuery);
    }
    if (outputsFromQuery) {
      const nextOutputs = Array.from(new Set(
        outputsFromQuery
          .split(',')
          .map((item) => item.trim())
          .filter((item) => outputOptionIds.has(item)),
      ));
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
      const data = await apiJson('/api/v1/assets/upload', { method: 'POST', body: form, timeoutMs: uploadRequestTimeoutMs });
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
          input_config: {
            requested_outputs: selectedOutputs,
            pdf_chunking_strategy: pdfChunkingStrategy,
            pdf_chunk_target_chars: 2500,
            pdf_chunk_max_chars: 4500,
            pdf_preserve_page_refs: true,
            pdf_fallback_to_page_chunks: true,
            video_target_frame_rate: Number(videoTargetFrameRate) || 0,
            video_frame_interval_seconds: Number(videoFrameIntervalSeconds) || 15,
            video_max_frame_count: Number(videoMaxFrameCount) || 240,
          },
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
  const selectedProject = projects.find((project) => project.id === projectId);
  const outputContext = selectedOutputs.length
    ? selectedOutputs.map((item) => outputLabelById[item] || item).join('、')
    : '尚未选择';
  const materialOutputs = selectedOutputs.filter((item) => ['delivery', 'markdown', 'skill', 'rag'].includes(item));
  const hasMaterialOutput = materialOutputs.length > 0;
  const materialOutputParam = hasMaterialOutput ? materialOutputs.join(',') : '';
  const primaryMaterialOutput = materialOutputs[0] || 'markdown';
  const outputQueryParam = selectedOutputs.length ? `outputs=${encodeURIComponent(selectedOutputs.join(','))}` : '';
  const projectQuery = projectId ? `?project_id=${encodeURIComponent(projectId)}${outputQueryParam ? `&${outputQueryParam}` : ''}` : (outputQueryParam ? `?${outputQueryParam}` : '');
  const assetProjectQuery = asset?.project_id ? `?project_id=${encodeURIComponent(asset.project_id)}${outputQueryParam ? `&${outputQueryParam}` : ''}` : projectQuery;
  const exportQuery = projectId && hasMaterialOutput ? `?project_id=${encodeURIComponent(projectId)}&outputs=${encodeURIComponent(materialOutputParam)}&export_type=${encodeURIComponent(primaryMaterialOutput)}` : '';
  const taskHref = preprocessResult?.id ? `/tasks/${preprocessResult.id}` : '/tasks';
  const semanticHref = assetProjectQuery ? `/semantic-units${assetProjectQuery}` : '/semantic-units';
  const samplesHref = assetProjectQuery ? `/generated-samples${assetProjectQuery}` : `/generated-samples${projectQuery}`;
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
    <main style={{ minHeight: '100vh', background: '#f5f5f7', fontFamily: 'SF Pro Text, system-ui, -apple-system, sans-serif', color: '#1d1d1f' }}>
      <section style={{ maxWidth: 960, margin: '0 auto', padding: '44px 24px' }}>
        <header style={{ paddingBottom: 22, borderBottom: '1px solid #e5e7eb' }}>
          <div style={{ color: '#7a7a7a', fontSize: 14, marginBottom: 8 }}>第 2 步 · 上传素材</div>
          <h1 style={{ margin: 0, fontSize: 40, lineHeight: 1.1, letterSpacing: '-0.28px', fontWeight: 600 }}>上传素材并开始处理</h1>
          <p style={{ margin: '10px 0 0', maxWidth: 720, color: '#333333', fontSize: 17, lineHeight: 1.47 }}>
            视频、音频、图片、文档和长文本会被整理成可交付产物，后续可导出知识库文档、可复用流程包、检索语料包和训练问答。
          </p>
        </header>

        <section style={{ display: 'grid', gap: 0, borderBottom: '1px solid #e5e7eb' }}>
          {[
            ['1. 确认任务/项目', selectedProject ? selectedProject.name : '请先选择任务'],
            ['2. 上传素材', file ? file.name : '等待选择文件'],
            ['3. 选择生成内容', outputContext],
          ].map(([step, detail]) => (
            <div key={step} style={{ padding: '14px 0', borderTop: '1px solid #e5e7eb', display: 'grid', gridTemplateColumns: 'minmax(150px, 0.8fr) minmax(0, 1.2fr)', gap: 12, alignItems: 'start' }}>
              <strong style={{ color: '#111827' }}>{step}</strong>
              <span style={{ color: '#6b7280', fontSize: 13, lineHeight: 1.6, wordBreak: 'break-word' }}>{detail}</span>
            </div>
          ))}
        </section>

        <div style={{ padding: '14px 0', borderBottom: '1px solid #e5e7eb', color: '#374151', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <strong style={{ fontSize: 14 }}>状态</strong>
          <span style={{ fontSize: 14 }}>{message}</span>
        </div>

        <section style={{ padding: '18px 0', borderBottom: '1px solid #e5e7eb' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', gap: 16, alignItems: 'start' }}>
            <div>
              <h2 style={{ margin: 0, fontSize: 18 }}>当前任务上下文</h2>
              <p style={{ margin: '8px 0 0', color: '#6b7280', fontSize: 13, lineHeight: 1.7 }}>
                {selectedProject ? `任务：${selectedProject.name}` : '还没有选择任务。请从下方选择已有任务，或返回首页创建转换任务。'}
              </p>
              <p style={{ margin: '6px 0 0', color: '#6b7280', fontSize: 13, lineHeight: 1.7 }}>
                生成目标：{outputContext}
              </p>
            </div>
            <Link href="/" style={{ color: '#0066cc', fontWeight: 400, textDecoration: 'none', whiteSpace: 'nowrap' }}>返回首页创建转换任务</Link>
          </div>
        </section>

        <section style={{ padding: '22px 0', borderBottom: '1px solid #e5e7eb' }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>1. 确认任务/项目并上传素材</h2>
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

        <section style={{ padding: '22px 0', borderBottom: '1px solid #e5e7eb' }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>2. 选择生成内容</h2>
          <p style={{ margin: '8px 0 0', color: '#6b7280', fontSize: 13, lineHeight: 1.7 }}>
            这些目标会随预处理任务写入请求上下文，后续审查页和导出页会继续带着同一组选择。
          </p>
          <div style={{ display: 'grid', gap: 0, marginTop: 12 }}>
            {outputOptions.map((option) => (
              <label key={option.id} style={{ display: 'grid', gridTemplateColumns: 'auto minmax(0, 1fr)', gap: 10, alignItems: 'center', padding: '11px 0', color: '#374151', borderTop: '1px solid #e5e7eb' }}>
                <input
                  type="checkbox"
                  checked={selectedOutputs.includes(option.id)}
                  onChange={() => toggleOutput(option.id)}
                />
                <span>{option.label}</span>
              </label>
            ))}
          </div>
          <div style={{ display: 'grid', gap: 8, marginTop: 16, paddingTop: 14, borderTop: '1px solid #e5e7eb' }}>
            <label style={{ display: 'grid', gap: 6 }}>
              <span style={{ color: '#4b5563', fontSize: 13 }}>PDF 切片方式</span>
              <select value={pdfChunkingStrategy} onChange={(event) => setPdfChunkingStrategy(event.target.value)} style={inputStyle}>
                {pdfChunkingOptions.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
              </select>
            </label>
            <p style={{ margin: 0, color: '#6b7280', fontSize: 12, lineHeight: 1.7 }}>
              默认自动识别章节和主题；识别不足时保留页级切片，确保长 PDF 不漏页且 Markdown 引用可追溯。
            </p>
          </div>
          <div style={{ display: 'grid', gap: 8, marginTop: 16, paddingTop: 14, borderTop: '1px solid #e5e7eb' }}>
            <strong style={{ fontSize: 14 }}>视频抽帧设置</strong>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12 }}>
              <label style={{ display: 'grid', gap: 6 }}>
                <span style={{ color: '#4b5563', fontSize: 13 }}>目标帧率</span>
                <input type="number" min="0" value={videoTargetFrameRate} onChange={(event) => setVideoTargetFrameRate(event.target.value)} style={inputStyle} />
              </label>
              <label style={{ display: 'grid', gap: 6 }}>
                <span style={{ color: '#4b5563', fontSize: 13 }}>间隔秒数</span>
                <input type="number" min="1" value={videoFrameIntervalSeconds} onChange={(event) => setVideoFrameIntervalSeconds(event.target.value)} style={inputStyle} />
              </label>
              <label style={{ display: 'grid', gap: 6 }}>
                <span style={{ color: '#4b5563', fontSize: 13 }}>最大帧数</span>
                <input type="number" min="1" value={videoMaxFrameCount} onChange={(event) => setVideoMaxFrameCount(event.target.value)} style={inputStyle} />
              </label>
            </div>
            <p style={{ margin: 0, color: '#6b7280', fontSize: 12, lineHeight: 1.7 }}>
              目标帧率填 0 时按间隔抽帧；高帧率视频可填 24、12 或更低，减少重复画面和 OCR 负担。
            </p>
          </div>
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 12, paddingTop: 12, borderTop: '1px solid #e5e7eb' }}>
            <Link href="/tasks" style={{ color: '#0066cc', fontWeight: 400 }}>查看处理进度</Link>
            <Link href={`/semantic-units${projectQuery}`} style={{ color: '#0066cc', fontWeight: 400 }}>审查内容片段</Link>
            <Link href={`/train-exports${exportQuery}`} style={{ color: '#0066cc', fontWeight: 400 }}>交付导出</Link>
          </div>
        </section>

        {result ? (
          <section style={{ padding: '22px 0', borderBottom: '1px solid #e5e7eb' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', paddingBottom: 14, borderBottom: '1px solid #e5e7eb' }}>
              <div>
                <h2 style={{ margin: 0, fontSize: 18 }}>3. 上传结果与预处理</h2>
                <p style={{ margin: '6px 0 0', color: '#6b7280', fontSize: 13 }}>
                  {asset?.id ? '素材已上传，可以启动预处理转换' : (result?.error || '素材上传结果')}
                </p>
              </div>
              {asset?.id ? (
                <button
                  onClick={onPreprocess}
                  disabled={preprocessing}
                  style={{ ...buttonStyle, background: preprocessing ? '#7a7a7a' : '#0066cc', color: '#ffffff', flex: '0 0 auto', opacity: preprocessing ? 0.78 : 1, cursor: preprocessing ? 'not-allowed' : 'pointer' }}
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
                    文件：{asset.file_name || file?.name || '-'}；类型：{asset.asset_type || assetType}
                  </div>
                </div>
                <div style={{ padding: '12px 0', borderBottom: '1px solid #e5e7eb' }}>
                  <strong>预处理任务</strong>
                  <div style={{ marginTop: 6, color: '#6b7280', fontSize: 13, lineHeight: 1.6 }}>
                    {preprocessResult?.error ? `启动失败：${preprocessResult.error}` : preprocessResult?.id ? `处理已开始；状态：${preprocessResult.status || 'created'}；进度：${preprocessResult.progress ?? 0}%` : '上传完成后点击“启动预处理”，系统会把素材转为可审查内容片段。'}
                  </div>
                  {preprocessResult?.result_summary ? (
                    <div style={{ marginTop: 6, color: '#374151', fontSize: 13 }}>{preprocessResult.result_summary}</div>
                  ) : null}
                </div>
                <div style={{ padding: '12px 0' }}>
                  <strong>下一步操作</strong>
                  <div style={{ marginTop: 10, display: 'flex', gap: 14, flexWrap: 'wrap' }}>
                    <a href={taskHref} style={{ color: '#0066cc', fontWeight: 400 }}>查看处理进度</a>
                    <a href={semanticHref} style={{ color: '#0066cc', fontWeight: 400 }}>审查内容片段</a>
                    <a href={samplesHref} style={{ color: '#0066cc', fontWeight: 400 }}>生成训练问答</a>
                    <a href={materialExportHref} aria-disabled={!hasMaterialOutput} style={{ color: hasMaterialOutput ? '#0066cc' : '#7a7a7a', fontWeight: 400, pointerEvents: hasMaterialOutput ? 'auto' : 'none' }}>导出交付产物</a>
                    <a href={traceHref} style={{ color: '#0066cc', fontWeight: 400 }}>查看来源链路</a>
                  </div>
                  {!hasMaterialOutput ? (
                    <div style={{ marginTop: 8, color: '#b45309', fontSize: 13 }}>
                      请先勾选知识库文档、流程包或检索语料后再导出交付产物。
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
