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
  model_available: boolean;
  available_models: string[];
  error?: string | null;
};

const outputTypes = [
  { id: 'markdown', label: 'Markdown', detail: '把长内容整理成可阅读、可复盘、可交付的结构化文档。' },
  { id: 'skill', label: 'Skill', detail: '沉淀为可复用的技能包、操作流程和执行说明。' },
  { id: 'rag', label: 'RAG', detail: '生成可检索、可追溯、适合接入知识库的语料。' },
  { id: 'samples', label: '训练样本', detail: '生成问答、指令和偏好数据，用于后续训练或评测。' },
  { id: 'dataset', label: '数据集', detail: '汇总为 SFT、instruction、preference 等导出格式。' },
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
  border: '1px solid #d1d5db',
  background: '#ffffff',
  color: '#111827',
  fontWeight: 600,
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

export default function HomePage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [domainType, setDomainType] = useState('knowledge');
  const [creating, setCreating] = useState(false);
  const [message, setMessage] = useState('先创建或选择一个转换任务');
  const [selectedOutputs, setSelectedOutputs] = useState<string[]>(['markdown', 'rag']);
  const [ollamaStatus, setOllamaStatus] = useState<OllamaStatus | null>(null);

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

  useEffect(() => {
    void loadProjects();
    void loadOllamaStatus();
  }, []);

  const selectedProject = useMemo(
    () => projects.find((project) => project.id === selectedProjectId),
    [projects, selectedProjectId],
  );

  const primaryExportType = useMemo(() => {
    const supported = selectedOutputs.find((item) => ['markdown', 'skill', 'rag'].includes(item));
    return supported || 'sft';
  }, [selectedOutputs]);

  const uploadHref = selectedProjectId ? `/upload?project_id=${selectedProjectId}` : '/upload';
  const exportHref = useMemo(() => {
    const params = new URLSearchParams();
    if (selectedProjectId) {
      params.set('project_id', selectedProjectId);
    }
    params.set('export_type', primaryExportType);
    params.set('outputs', selectedOutputs.join(','));
    return `/train-exports?${params.toString()}`;
  }, [primaryExportType, selectedOutputs, selectedProjectId]);

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
      setMessage(`已创建：${project.name}，下一步上传素材`);
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

  return (
    <main style={{ minHeight: '100vh', background: '#ffffff', fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}>
      <section className="home-shell">
        <header style={{ paddingBottom: 22, borderBottom: '1px solid #e5e7eb' }}>
          <div style={{ color: '#6b7280', fontSize: 14, marginBottom: 8 }}>Envoy 多媒体素材转换</div>
          <h1 style={{ margin: 0, fontSize: 34, lineHeight: 1.15 }}>创建转换任务</h1>
          <p style={{ margin: '10px 0 0', maxWidth: 820, color: '#4b5563', lineHeight: 1.7 }}>
            首页只处理一个主流程：创建任务/项目，上传视频、长文本和多模态文件，再选择要生成的 Markdown、Skill、RAG、训练样本或数据集。
          </p>
        </header>

        <section className="task-wizard primary-workflow" style={{ display: 'grid', gridTemplateColumns: '1fr', gap: 0 }}>
          <div style={{ padding: '14px 0', borderBottom: '1px solid #e5e7eb', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', color: '#374151' }}>
            <strong style={{ fontSize: 14 }}>当前状态</strong>
            <span style={{ fontSize: 14 }}>{message}</span>
          </div>

          <section style={{ padding: '14px 0', borderBottom: '1px solid #e5e7eb', display: 'grid', gap: 8 }}>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <strong style={{ fontSize: 14 }}>Ollama 本地模型</strong>
              <span style={{ color: '#374151', fontSize: 14 }}>{ollamaStatusLabel(ollamaStatus)}</span>
            </div>
            <div style={{ color: '#6b7280', fontSize: 13, lineHeight: 1.6 }}>
              模型：{ollamaStatus?.model || '未配置'}；地址：{ollamaStatus?.base_url || '未配置'}；可用模型：{ollamaStatus?.available_models?.length ? ollamaStatus.available_models.join(', ') : '-'}
            </div>
          </section>

          <article className="flow-step">
            <div style={{ color: '#6b7280', fontSize: 13 }}>1. 创建任务/项目</div>
            <h2 style={{ margin: '8px 0 0', fontSize: 20 }}>先定义本次转换目标</h2>
            <p style={{ margin: '10px 0 0', color: '#4b5563', lineHeight: 1.7 }}>
              每个任务承载一批素材和最终交付物，后续上传、预处理、语义单元、生成样本和导出都会围绕这个任务推进。
            </p>
            <div style={{ marginTop: 16, display: 'grid', gap: 12 }}>
              <label style={{ display: 'grid', gap: 6 }}>
                <span style={{ color: '#4b5563', fontSize: 13 }}>任务名称</span>
                <input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：课程视频转 RAG 知识库" style={inputStyle} />
              </label>
              <label style={{ display: 'grid', gap: 6 }}>
                <span style={{ color: '#4b5563', fontSize: 13 }}>目标说明</span>
                <input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="说明素材来源、使用场景和生成目标" style={inputStyle} />
              </label>
              <label style={{ display: 'grid', gap: 6 }}>
                <span style={{ color: '#4b5563', fontSize: 13 }}>领域类型</span>
                <input value={domainType} onChange={(event) => setDomainType(event.target.value)} placeholder="knowledge / course / support / compliance" style={inputStyle} />
              </label>
              <button onClick={createProject} disabled={creating} style={{ ...buttonStyle, width: 'fit-content', background: '#111827', color: '#ffffff', borderColor: '#111827', opacity: creating ? 0.72 : 1 }}>
                {creating ? '正在创建...' : '创建任务'}
              </button>
            </div>

            <div style={{ marginTop: 18, paddingTop: 14, borderTop: '1px solid #e5e7eb' }}>
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
                  当前任务：{selectedProject.name}，ID: {selectedProject.id}
                </div>
              ) : null}
            </div>
          </article>

          <article className="flow-step">
            <div style={{ color: '#6b7280', fontSize: 13 }}>2. 上传素材</div>
            <h2 style={{ margin: '8px 0 0', fontSize: 20 }}>把原始素材放入任务</h2>
            <p style={{ margin: '10px 0 0', color: '#4b5563', lineHeight: 1.7 }}>
              支持视频、音频、图片、PDF、Markdown、长文本和其他多模态文件。上传页会携带当前 project_id，并可直接启动预处理转换。
            </p>
            <div style={{ marginTop: 18, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <Link href={uploadHref} style={{ ...buttonStyle, background: selectedProjectId ? '#111827' : '#f3f4f6', color: selectedProjectId ? '#ffffff' : '#6b7280', borderColor: selectedProjectId ? '#111827' : '#d1d5db' }}>
                上传素材
              </Link>
              <Link href="/tasks" style={buttonStyle}>查看转换任务</Link>
            </div>
            <div style={{ marginTop: 14, color: '#6b7280', fontSize: 13, wordBreak: 'break-all' }}>
              上传入口：{uploadHref}
            </div>
          </article>

          <article className="flow-step">
            <div style={{ color: '#6b7280', fontSize: 13 }}>3. 选择生成内容</div>
            <h2 style={{ margin: '8px 0 0', fontSize: 20 }}>决定最终要交付什么</h2>
            <p style={{ margin: '10px 0 0', color: '#4b5563', lineHeight: 1.7 }}>
              先勾选目标产物，再进入语义单元审查、样本生成或训练导出页面处理细节。
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
              <Link href="/semantic-units" style={buttonStyle}>审查语义单元</Link>
              <Link href="/generated-samples" style={buttonStyle}>审查训练样本</Link>
              <Link href={exportHref} style={{ ...buttonStyle, background: '#111827', color: '#ffffff', borderColor: '#111827' }}>进入导出</Link>
            </div>
          </article>
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

        .flow-step {
          min-width: 0;
          padding: 24px 0;
          border-bottom: 1px solid #e5e7eb;
        }

        @media (max-width: 640px) {
          .home-shell {
            padding: 32px 18px;
          }
        }
      `}</style>
    </main>
  );
}
