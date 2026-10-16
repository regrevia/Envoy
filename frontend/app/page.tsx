"use client";

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import { ApiError, apiJson } from '../lib/api';

type Project = {
  id: string;
  name: string;
  description?: string | null;
  domain_type?: string | null;
  status?: string | null;
};

type OllamaStatus = {
  enabled: boolean;
  status: string;
  base_url: string;
  model: string;
  timeout_sec: number;
  max_prompt_chars?: number;
  configured_from?: string;
  model_available: boolean;
  available_models: string[];
  error?: string | null;
};

type MediaStatus = {
  segment_seconds: number;
  max_segment_count: number;
  max_input_seconds: number;
  frame_interval_seconds: number;
  max_frame_count: number;
  frame_extraction_enabled: boolean;
  ocr_enabled: boolean;
  transcription_timeout_sec: number;
  ocr_timeout_sec: number;
};

const outputTypes = [
  { id: 'delivery', label: '商业交付包', detail: '把可读文档、RAG 语料、训练种子和 agent 导入清单打包为统一交付目录。' },
  { id: 'markdown', label: '知识库文档', detail: '把长内容整理成总览文档和分片文档，适合直接交付或导入知识库。' },
  { id: 'skill', label: '可复用流程包', detail: '沉淀操作步骤、检查清单和执行说明，方便团队复用。' },
  { id: 'rag', label: '检索语料包', detail: '生成可检索、可追溯的内容片段，方便问答工具引用来源。' },
  { id: 'samples', label: '训练问答', detail: '生成可审查的问答样本，用于后续训练或评测。' },
  { id: 'dataset', label: '数据文件', detail: '汇总为后续模型训练或评估可以使用的数据格式。' },
];

const assetTypeOptions = [
  { id: 'video', label: '视频' },
  { id: 'text', label: '长文本' },
  { id: 'document', label: '文档' },
  { id: 'audio', label: '音频' },
  { id: 'image', label: '图片' },
  { id: 'markdown', label: 'Markdown' },
];

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
  border: '1px solid #0066cc',
  background: '#ffffff',
  color: '#0066cc',
  fontWeight: 400,
  cursor: 'pointer',
  textDecoration: 'none',
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
} as const;

function ollamaStatusLabel(status?: OllamaStatus | null) {
  if (!status) {
    return '检测中';
  }
  if (!status.enabled) {
    return '未配置';
  }
  if (status.status === 'available') {
    return '可用';
  }
  if (status.status === 'model_missing') {
    return '模型缺失';
  }
  return '不可达';
}

function enabledLabel(value?: boolean) {
  if (value === true) {
    return '开启';
  }
  if (value === false) {
    return '关闭';
  }
  return '检测中';
}

export default function HomePage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [domainType, setDomainType] = useState('knowledge');
  const [selectedAssetType, setSelectedAssetType] = useState('video');
  const [creating, setCreating] = useState(false);
  const [message, setMessage] = useState('先创建或选择一个转换任务');
  const [selectedOutputs, setSelectedOutputs] = useState<string[]>(['delivery', 'markdown', 'rag']);
  const [ollamaStatus, setOllamaStatus] = useState<OllamaStatus | null>(null);
  const [ollamaConfig, setOllamaConfig] = useState({
    base_url: 'http://127.0.0.1:11434',
    model: '',
    timeout_sec: '120',
    max_prompt_chars: '12000',
  });
  const [savingOllama, setSavingOllama] = useState(false);
  const [mediaStatus, setMediaStatus] = useState<MediaStatus | null>(null);

  const loadProjects = async () => {
    try {
      const list = await apiJson<Project[]>('/api/v1/projects');
      setProjects(list);
      if (!selectedProjectId && list.length > 0) {
        setSelectedProjectId(list[0].id);
      }
    } catch {
      setProjects([]);
      setMessage('项目列表加载失败，请检查后端服务');
    }
  };

  const loadOllamaStatus = async () => {
    try {
      const status = await apiJson<OllamaStatus>('/api/v1/health/ollama');
      setOllamaStatus(status);
      setOllamaConfig({
        base_url: status.base_url || 'http://127.0.0.1:11434',
        model: status.model || '',
        timeout_sec: String(status.timeout_sec || 120),
        max_prompt_chars: String(status.max_prompt_chars || 12000),
      });
    } catch {
      setOllamaStatus({
        enabled: false,
        status: 'unreachable',
        base_url: '',
        model: '',
        timeout_sec: 0,
        model_available: false,
        available_models: [],
        error: '后端状态接口不可用',
      });
    }
  };

  const loadMediaStatus = async () => {
    try {
      const status = await apiJson<MediaStatus>('/api/v1/health/media');
      setMediaStatus(status);
    } catch {
      setMediaStatus(null);
    }
  };

  useEffect(() => {
    void loadProjects();
    void loadOllamaStatus();
    void loadMediaStatus();
  }, []);

  const selectedProject = useMemo(
    () => projects.find((project) => project.id === selectedProjectId),
    [projects, selectedProjectId],
  );

  const materialOutputs = selectedOutputs.filter((item) => ['delivery', 'markdown', 'skill', 'rag'].includes(item));
  const datasetOutputs = selectedOutputs.includes('dataset') ? ['sft'] : [];
  const exportOutputs = [...materialOutputs, ...datasetOutputs];
  const hasExportableOutput = exportOutputs.length > 0;
  const primaryExportType = exportOutputs[0] || 'sft';

  const canProceed = Boolean(selectedProjectId);
  const disabledActionStyle = {
    opacity: 0.58,
    cursor: 'not-allowed',
  } as const;
  const uploadOutputsParam = selectedOutputs.join(',');
  const uploadHref = canProceed
    ? `/upload?project_id=${selectedProjectId}&asset_type=${encodeURIComponent(selectedAssetType)}${uploadOutputsParam ? `&outputs=${encodeURIComponent(uploadOutputsParam)}` : ''}`
    : '#';
  const semanticHref = canProceed ? `/semantic-units?project_id=${selectedProjectId}` : '#';
  const samplesHref = canProceed && selectedOutputs.includes('samples') ?
    `/generated-samples?project_id=${selectedProjectId}` :
    '#';
  const exportHref = useMemo(() => {
    if (!canProceed || !hasExportableOutput) {
      return '#';
    }
    const params = new URLSearchParams();
    if (selectedProjectId) {
      params.set('project_id', selectedProjectId);
    }
    params.set('export_type', primaryExportType);
    params.set('outputs', exportOutputs.join(','));
    return `/train-exports?${params.toString()}`;
  }, [canProceed, exportOutputs, hasExportableOutput, primaryExportType, selectedProjectId]);

  const createProject = async () => {
    const trimmedName = name.trim();
    if (!trimmedName || creating) {
      setMessage('请输入任务/项目名称');
      return;
    }

    setCreating(true);
    setMessage('正在创建转换任务...');
    try {
      const project = await apiJson<Project>('/api/v1/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: trimmedName,
          description: description.trim() || null,
          domain_type: domainType.trim() || null,
          status: 'active',
        }),
      });
      setProjects((current) => [project, ...current.filter((item) => item.id !== project.id)]);
      setSelectedProjectId(project.id);
      setName('');
      setDescription('');
      setMessage(`已创建：${project.name}，下一步上传${assetTypeOptions.find((item) => item.id === selectedAssetType)?.label || '素材'}`);
    } catch (error) {
      const errorMessage = error instanceof ApiError || error instanceof Error ? error.message : '创建失败';
      setMessage(`创建失败：${errorMessage}`);
    } finally {
      setCreating(false);
    }
  };

  const toggleOutput = (id: string) => {
    setSelectedOutputs((current) => (
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id]
    ));
  };

  const saveOllamaConfig = async () => {
    if (savingOllama) {
      return;
    }
    setSavingOllama(true);
    setMessage('正在保存 Ollama 配置...');
    try {
      const status = await apiJson<OllamaStatus>('/api/v1/health/ollama/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          base_url: ollamaConfig.base_url,
          model: ollamaConfig.model,
          timeout_sec: Number(ollamaConfig.timeout_sec || 120),
          max_prompt_chars: Number(ollamaConfig.max_prompt_chars || 12000),
        }),
      });
      setOllamaStatus(status);
      setMessage('Ollama 配置已保存，后续样本生成和导出会使用该本地模型');
    } catch (error) {
      const errorMessage = error instanceof ApiError || error instanceof Error ? error.message : '保存失败';
      setMessage(`Ollama 配置保存失败：${errorMessage}`);
    } finally {
      setSavingOllama(false);
    }
  };

  return (
    <main style={{ minHeight: '100vh', background: '#f5f5f7', fontFamily: 'SF Pro Text, system-ui, -apple-system, sans-serif', color: '#1d1d1f' }}>
      <section className="home-shell">
        <header style={{ paddingBottom: 22, borderBottom: '1px solid #e5e7eb' }}>
          <div style={{ color: '#7a7a7a', fontSize: 14, marginBottom: 8 }}>Envoy 内容交付工作台</div>
          <h1 style={{ margin: 0, fontSize: 40, lineHeight: 1.1, letterSpacing: '-0.28px', fontWeight: 600 }}>创建转换任务</h1>
          <p style={{ margin: '10px 0 0', maxWidth: 820, color: '#333333', fontSize: 17, lineHeight: 1.47 }}>
            先确定素材类型和交付目标，再上传视频、长文本或文档，最后生成知识库文档、可复用流程包、检索语料包和训练问答。
          </p>
        </header>

        <section className="task-wizard primary-workflow" style={{ display: 'grid', gridTemplateColumns: '1fr', gap: 0 }}>
          <div style={{ padding: '14px 0', borderBottom: '1px solid #e5e7eb', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', color: '#374151' }}>
            <strong style={{ fontSize: 14 }}>当前状态</strong>
            <span style={{ fontSize: 14 }}>{message}</span>
          </div>

          <section className="workflow-strip" aria-label="转换流程">
            {[
              ['1', '创建任务', '确定素材类型和生成目标'],
              ['2', '上传素材', '文件进入当前任务素材池'],
              ['3', '生成交付产物', '输出文档、流程包、检索语料或训练问答'],
            ].map(([index, title, detail]) => (
              <div key={index} className="workflow-strip-item">
                <span className="workflow-index">{index}</span>
                <span>
                  <strong>{title}</strong>
                  <span>{detail}</span>
                </span>
              </div>
            ))}
          </section>

          <article className="flow-step first-step">
            <div className="step-heading">
              <div>
                <div style={{ color: '#7a7a7a', fontSize: 13 }}>1. 创建任务</div>
                <h2 style={{ margin: '8px 0 0', fontSize: 22 }}>先定义本次转换目标</h2>
              </div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <Link href="/video" style={{ ...buttonStyle, background: '#0066cc', color: '#ffffff', textDecoration: 'none' }}>粘贴视频链接转 Markdown</Link>
                <Link href="/projects" style={{ ...buttonStyle, textDecoration: 'none' }}>项目管理</Link>
              </div>
            </div>
            <p style={{ margin: '10px 0 0', color: '#4b5563', lineHeight: 1.7 }}>
              每个任务承载一批素材和最终交付物，后续上传、处理、审查和导出都会围绕这个任务推进。
            </p>
            <section style={{ marginTop: 16, padding: '14px 0', borderTop: '1px solid #e5e7eb', borderBottom: '1px solid #e5e7eb' }}>
              <div style={{ color: '#4b5563', fontSize: 13, marginBottom: 10 }}>素材类型</div>
              <div className="segmented-line">
                {assetTypeOptions.map((item) => {
                  const active = selectedAssetType === item.id;
                  return (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => setSelectedAssetType(item.id)}
                      style={{
                        ...buttonStyle,
                        minHeight: 34,
                        background: active ? '#0066cc' : '#ffffff',
                        color: active ? '#ffffff' : '#1d1d1f',
                        borderColor: active ? '#0066cc' : '#e0e0e0',
                      }}
                    >
                      {item.label}
                    </button>
                  );
                })}
              </div>
            </section>
            <div className="create-grid">
              <label style={{ display: 'grid', gap: 6 }}>
                <span style={{ color: '#4b5563', fontSize: 13 }}>任务名称</span>
                <input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：数字电路课程知识库" style={inputStyle} />
              </label>
              <label style={{ display: 'grid', gap: 6 }}>
                <span style={{ color: '#4b5563', fontSize: 13 }}>目标说明</span>
                <input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="说明素材来源、使用场景和生成目标" style={inputStyle} />
              </label>
              <label style={{ display: 'grid', gap: 6 }}>
                <span style={{ color: '#4b5563', fontSize: 13 }}>领域类型</span>
                <input value={domainType} onChange={(event) => setDomainType(event.target.value)} placeholder="knowledge / course / support / compliance" style={inputStyle} />
              </label>
              <button onClick={createProject} disabled={creating} style={{ ...buttonStyle, width: 'fit-content', background: '#0066cc', color: '#ffffff', borderColor: '#0066cc', opacity: creating ? 0.72 : 1 }}>
                {creating ? '正在创建...' : '创建任务'}
              </button>
            </div>

            <div style={{ marginTop: 18, paddingTop: 16, borderTop: '1px solid #e5e7eb' }}>
              <label style={{ display: 'grid', gap: 6 }}>
                <span style={{ color: '#4b5563', fontSize: 13 }}>或选择已有任务</span>
                <select value={selectedProjectId} onChange={(event) => setSelectedProjectId(event.target.value)} style={inputStyle}>
                  <option value="">请选择任务/项目</option>
                  {projects.map((project) => (
                    <option key={project.id} value={project.id}>{project.name}</option>
                  ))}
                </select>
              </label>
              {selectedProject ? (
                <div style={{ marginTop: 10, color: '#6b7280', fontSize: 13, lineHeight: 1.6 }}>
                  当前任务：{selectedProject.name}
                </div>
              ) : null}
            </div>
          </article>

          <article className="flow-step">
            <div style={{ color: '#6b7280', fontSize: 13 }}>2. 上传素材</div>
            <h2 style={{ margin: '8px 0 0', fontSize: 20 }}>把原始素材放入任务</h2>
            <p style={{ margin: '10px 0 0', color: '#4b5563', lineHeight: 1.7 }}>
              支持视频、音频、图片、PDF、Markdown、长文本和其他多模态文件。上传页会自动带入当前任务，并可直接启动内容处理。
            </p>
            <div style={{ marginTop: 18, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <Link href={uploadHref} aria-disabled={!canProceed} style={{ ...buttonStyle, background: canProceed ? '#0066cc' : '#fafafc', color: canProceed ? '#ffffff' : '#7a7a7a', borderColor: canProceed ? '#0066cc' : '#e0e0e0', ...(!canProceed ? disabledActionStyle : {}) }}>
                上传素材
              </Link>
              <Link href="/tasks" style={buttonStyle}>查看转换任务</Link>
            </div>
            {!canProceed ? (
              <div style={{ marginTop: 10, color: '#b45309', fontSize: 13 }}>
                请先创建或选择任务后继续，上传素材需要明确归属的任务/项目。
              </div>
            ) : null}
            <div style={{ marginTop: 14, color: '#6b7280', fontSize: 13, wordBreak: 'break-all' }}>
              上传入口：{uploadHref}
            </div>
          </article>

          <article className="flow-step">
            <div style={{ color: '#6b7280', fontSize: 13 }}>3. 选择生成内容</div>
            <h2 style={{ margin: '8px 0 0', fontSize: 20 }}>决定最终要交付什么</h2>
            <p style={{ margin: '10px 0 0', color: '#4b5563', lineHeight: 1.7 }}>
              先勾选目标产物，再进入内容审查、训练问答或交付导出页面处理细节。
            </p>
            <div style={{ marginTop: 14, display: 'grid', gap: 0 }}>
              {outputTypes.map((item) => {
                const checked = selectedOutputs.includes(item.id);
                return (
                  <label key={item.id} style={{ display: 'grid', gridTemplateColumns: 'auto minmax(0, 1fr)', gap: 10, alignItems: 'start', padding: '12px 0', borderTop: '1px solid #e5e7eb' }}>
                    <input type="checkbox" checked={checked} onChange={() => toggleOutput(item.id)} style={{ marginTop: 3 }} />
                    <span>
                      <strong>{item.label}</strong>
                      <span style={{ display: 'block', color: '#6b7280', fontSize: 13, lineHeight: 1.6, marginTop: 4 }}>{item.detail}</span>
                    </span>
                  </label>
                );
              })}
            </div>
            <div style={{ marginTop: 18, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <Link href={semanticHref} aria-disabled={!canProceed} style={{ ...buttonStyle, ...(!canProceed ? disabledActionStyle : {}) }}>审查内容片段</Link>
              <Link href={samplesHref} aria-disabled={!canProceed || !selectedOutputs.includes('samples')} style={{ ...buttonStyle, ...(!canProceed || !selectedOutputs.includes('samples') ? disabledActionStyle : {}) }}>审查训练问答</Link>
              <Link href={exportHref} aria-disabled={!canProceed || !hasExportableOutput} style={{ ...buttonStyle, background: canProceed && hasExportableOutput ? '#0066cc' : '#fafafc', color: canProceed && hasExportableOutput ? '#ffffff' : '#7a7a7a', borderColor: canProceed && hasExportableOutput ? '#0066cc' : '#e0e0e0', ...(!canProceed || !hasExportableOutput ? disabledActionStyle : {}) }}>进入交付导出</Link>
            </div>
            {!canProceed ? (
              <div style={{ marginTop: 10, color: '#b45309', fontSize: 13 }}>
                请先创建或选择任务后继续，导出需要明确来源任务。
              </div>
            ) : null}
            {canProceed && !hasExportableOutput ? (
              <div style={{ marginTop: 10, color: '#b45309', fontSize: 13 }}>
                请先勾选知识库文档、流程包、检索语料或数据文件后再进入导出。
              </div>
            ) : null}
          </article>

          <article className="flow-step">
            <div style={{ color: '#6b7280', fontSize: 13 }}>完整转换链路</div>
            <h2 style={{ margin: '8px 0 0', fontSize: 20 }}>从原始素材到可交付内容</h2>
            <p style={{ margin: '10px 0 0', color: '#4b5563', lineHeight: 1.7 }}>
              {'上传素材 -> 内容处理 -> 内容片段审查 -> 生成训练问答 -> 自动整理交付物 -> 来源追溯。'}
            </p>
            <div style={{ marginTop: 14, display: 'grid', gap: 0 }}>
              {[
                ['内容处理', '视频、音频、图片、文档和长文本会先转成可审查内容片段，长任务可在任务页查看进度、取消、重试和疑似卡住状态。'],
                ['长任务诊断', '任务列表会持续展示自动刷新、卡住阈值和未更新时间，便于处理长视频或大文件。'],
                ['自动整理交付物', '交付导出页可基于任务自动读取内容片段和训练问答，输出文档、流程包、检索语料或数据文件。'],
                ['来源追溯', '交付中心和追溯审查会展示素材、内容片段、训练问答和交付产物之间的链路。'],
              ].map(([title, detail]) => (
                <div key={title} style={{ padding: '12px 0', borderTop: '1px solid #e5e7eb' }}>
                  <strong>{title}</strong>
                  <div style={{ marginTop: 4, color: '#6b7280', fontSize: 13, lineHeight: 1.6 }}>{detail}</div>
                </div>
              ))}
            </div>
            <div style={{ marginTop: 18, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <Link href="/tasks" style={buttonStyle}>查看转换任务</Link>
              <Link href="/artifacts" style={buttonStyle}>查看交付中心</Link>
              <Link href="/trace" style={buttonStyle}>查看追溯链路</Link>
            </div>
          </article>

          <details className="flow-step runtime-section">
            <summary style={{ cursor: 'pointer', listStyle: 'none' }}>
              <div className="step-heading">
                <div>
                  <div style={{ color: '#6b7280', fontSize: 13 }}>运行环境</div>
                  <h2 style={{ margin: '8px 0 0', fontSize: 20 }}>本地模型与媒体处理状态</h2>
                </div>
                <span style={{ color: '#374151', fontSize: 14 }}>Ollama：{ollamaStatusLabel(ollamaStatus)}</span>
              </div>
            </summary>
            <div style={{ marginTop: 10, color: '#6b7280', fontSize: 13, lineHeight: 1.6 }}>
              展开后可配置 Ollama，并查看长视频分段、转写超时、抽帧和 OCR 上限。
            </div>
            <div className="runtime-grid">
              <section className="runtime-pane">
                <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                  <strong style={{ fontSize: 14 }}>Ollama 本地模型</strong>
                  <span style={{ color: '#374151', fontSize: 14 }}>{ollamaStatusLabel(ollamaStatus)}</span>
                </div>
                <div style={{ marginTop: 8, color: '#6b7280', fontSize: 13, lineHeight: 1.6 }}>
                  模型：{ollamaStatus?.model || '未配置'}；地址：{ollamaStatus?.base_url || '未配置'}；可用模型：{ollamaStatus?.available_models?.length ? ollamaStatus.available_models.join(', ') : '-'}
                </div>
                <div style={{ marginTop: 14, paddingTop: 14, borderTop: '1px solid #e5e7eb', display: 'grid', gap: 10 }}>
                  <strong style={{ fontSize: 14 }}>Ollama 配置</strong>
                  <div className="ollama-config-grid">
                    <label style={{ display: 'grid', gap: 6 }}>
                      <span style={{ color: '#4b5563', fontSize: 13 }}>服务地址</span>
                      <input value={ollamaConfig.base_url} onChange={(event) => setOllamaConfig((current) => ({ ...current, base_url: event.target.value }))} placeholder="http://127.0.0.1:11434" style={inputStyle} />
                    </label>
                    <label style={{ display: 'grid', gap: 6 }}>
                      <span style={{ color: '#4b5563', fontSize: 13 }}>模型名称</span>
                      <input value={ollamaConfig.model} onChange={(event) => setOllamaConfig((current) => ({ ...current, model: event.target.value }))} placeholder="qwen2.5:7b" style={inputStyle} />
                    </label>
                    <label style={{ display: 'grid', gap: 6 }}>
                      <span style={{ color: '#4b5563', fontSize: 13 }}>超时秒数</span>
                      <input value={ollamaConfig.timeout_sec} onChange={(event) => setOllamaConfig((current) => ({ ...current, timeout_sec: event.target.value }))} placeholder="120" style={inputStyle} />
                    </label>
                    <label style={{ display: 'grid', gap: 6 }}>
                      <span style={{ color: '#4b5563', fontSize: 13 }}>最大提示词长度</span>
                      <input value={ollamaConfig.max_prompt_chars} onChange={(event) => setOllamaConfig((current) => ({ ...current, max_prompt_chars: event.target.value }))} placeholder="12000" style={inputStyle} />
                    </label>
                  </div>
                  <button onClick={saveOllamaConfig} disabled={savingOllama} style={{ ...buttonStyle, width: 'fit-content', background: '#0066cc', color: '#ffffff', borderColor: '#0066cc', opacity: savingOllama ? 0.72 : 1 }}>
                    {savingOllama ? '正在保存...' : '保存配置'}
                  </button>
                  <div style={{ color: '#6b7280', fontSize: 13, lineHeight: 1.6 }}>
                    配置会写入本地运行时配置文件，服务重启后继续生效。
                  </div>
                </div>
              </section>

              <section className="runtime-pane">
                <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                  <strong style={{ fontSize: 14 }}>媒体处理模式</strong>
                  <span style={{ color: '#374151', fontSize: 14 }}>最大处理 {mediaStatus?.max_input_seconds ?? '-'} 秒</span>
                </div>
                <div style={{ marginTop: 8, color: '#6b7280', fontSize: 13, lineHeight: 1.7 }}>
                  分段：{mediaStatus?.segment_seconds ?? '-'} 秒 x {mediaStatus?.max_segment_count ?? '-'} 段；单段转写超时：{mediaStatus?.transcription_timeout_sec ?? '-'} 秒；OCR 超时：{mediaStatus?.ocr_timeout_sec ?? '-'} 秒；抽帧：{enabledLabel(mediaStatus?.frame_extraction_enabled)}，每 {mediaStatus?.frame_interval_seconds ?? '-'} 秒 1 帧，最多 {mediaStatus?.max_frame_count ?? '-'} 帧；OCR：{enabledLabel(mediaStatus?.ocr_enabled)}
                </div>
                <div style={{ marginTop: 14, paddingTop: 14, borderTop: '1px solid #e5e7eb', color: '#4b5563', fontSize: 13, lineHeight: 1.7 }}>
                  长视频会按配置分段处理，任务页会展示进度、最后更新时间、疑似卡住状态，并支持取消和重试。
                </div>
              </section>
            </div>
          </details>
        </section>
      </section>

      <style jsx>{`
        .home-shell {
          max-width: 960px;
          margin: 0 auto;
          padding: 44px 24px;
        }

        .task-wizard {
          margin-top: 0;
        }

        .workflow-strip {
          display: grid;
          border-bottom: 1px solid #e5e7eb;
        }

        .workflow-strip-item {
          min-width: 0;
          display: grid;
          grid-template-columns: auto minmax(0, 1fr);
          gap: 10px;
          padding: 14px 0;
          border-bottom: 1px solid #e5e7eb;
        }

        .workflow-strip-item:last-child {
          border-bottom: 0;
        }

        .workflow-strip-item strong,
        .workflow-strip-item span span {
          display: block;
        }

        .workflow-strip-item span span {
          margin-top: 4px;
          color: #6b7280;
          font-size: 13px;
          line-height: 1.5;
        }

        .workflow-index {
          width: 26px;
          height: 26px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          border: 1px solid #d1d5db;
          border-radius: 50%;
          color: #111827;
          font-size: 13px;
          font-weight: 700;
        }

        .segmented-line {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
        }

        .step-heading {
          display: flex;
          justify-content: space-between;
          gap: 16px;
          align-items: flex-start;
        }

        .create-grid {
          margin-top: 16px;
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 12px;
        }

        .create-grid label:first-child,
        .create-grid label:nth-child(2) {
          grid-column: span 2;
        }

        .runtime-grid {
          margin-top: 18px;
          display: grid;
          grid-template-columns: minmax(0, 1.2fr) minmax(0, 0.8fr);
          gap: 22px;
        }

        .runtime-pane {
          min-width: 0;
          padding-top: 16px;
          border-top: 1px solid #e5e7eb;
        }

        .ollama-config-grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 10px;
        }

        .flow-step {
          min-width: 0;
          padding: 24px 0;
          border-bottom: 1px solid #e5e7eb;
        }

        .first-step {
          padding-top: 26px;
        }

        @media (max-width: 640px) {
          .home-shell {
            padding: 32px 18px;
          }

          .workflow-strip,
          .create-grid,
          .runtime-grid,
          .ollama-config-grid {
            grid-template-columns: 1fr;
          }

          .workflow-strip-item {
            padding-right: 0;
            border-right: 0;
            border-bottom: 1px solid #e5e7eb;
          }

          .workflow-strip-item:last-child {
            border-bottom: 0;
          }

          .create-grid label:first-child,
          .create-grid label:nth-child(2) {
            grid-column: auto;
          }

          .step-heading {
            display: grid;
          }
        }
      `}</style>
    </main>
  );
}
