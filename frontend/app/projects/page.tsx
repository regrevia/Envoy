"use client";

import { useEffect, useState } from 'react';

import { ApiError, apiJson } from '../../lib/api';

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
  description?: string | null;
  domain_type?: string | null;
  status?: string | null;
};

export default function ProjectsPage() {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [domainType, setDomainType] = useState('');
  const [projects, setProjects] = useState<Project[]>([]);
  const [creating, setCreating] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');
  const [message, setMessage] = useState('准备就绪');
  const [lastCreatedId, setLastCreatedId] = useState('');

  const loadProjects = async () => {
    setRefreshing(true);
    try {
      const list = await apiJson<Project[]>('/api/v1/projects');
      setProjects(list);
      setStatus('success');
      setMessage(`已加载 ${list.length} 个项目`);
    } catch (error) {
      const messageText = error instanceof ApiError || error instanceof Error ? error.message : '加载失败';
      setProjects([]);
      setStatus('error');
      setMessage(`项目列表加载失败：${messageText}`);
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    void loadProjects();
  }, []);

  const createProject = async () => {
    const trimmedName = name.trim();
    if (!trimmedName || creating) {
      setStatus('error');
      setMessage('请输入项目名');
      return;
    }

    setCreating(true);
    setStatus('loading');
    setMessage('正在创建项目...');
    try {
      const data = await apiJson<Project>('/api/v1/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: trimmedName,
          description: description.trim() || null,
          domain_type: domainType.trim() || null,
          status: 'active',
        }),
      });

      setLastCreatedId(data.id);
      setStatus('success');
      setMessage(`创建成功：${data.name}`);
      setName('');
      setDescription('');
      setDomainType('');
      await loadProjects();
      window.setTimeout(() => {
        document.getElementById(`project-${data.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 120);
    } catch (error) {
      const messageText = error instanceof ApiError || error instanceof Error ? error.message : '未知错误';
      setStatus('error');
      setMessage(`创建失败：${messageText}`);
    } finally {
      setCreating(false);
    }
  };

  const statusColor = status === 'success' ? '#166534' : status === 'error' ? '#b91c1c' : status === 'loading' ? '#1d4ed8' : '#374151';
  const statusBorder = status === 'success' ? '#bbf7d0' : status === 'error' ? '#fecaca' : status === 'loading' ? '#bfdbfe' : '#e5e7eb';
  const statusBg = status === 'success' ? '#f0fdf4' : status === 'error' ? '#fef2f2' : status === 'loading' ? '#eff6ff' : '#ffffff';

  return (
    <main style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}>
      <section style={{ maxWidth: 1180, margin: '0 auto', padding: '44px 24px' }}>
        <header style={{ paddingBottom: 22, borderBottom: '1px solid #e5e7eb' }}>
          <h1 style={{ margin: 0, fontSize: 32, lineHeight: 1.2 }}>项目管理</h1>
          <p style={{ margin: '10px 0 0', maxWidth: 680, color: '#4b5563', lineHeight: 1.7 }}>
            用项目归集视频、长文本和多模态素材，后续任务会围绕项目生成语义单元、训练数据和可追溯产物。
          </p>
        </header>

        <div style={{ marginTop: 18, padding: '12px 14px', border: `1px solid ${statusBorder}`, borderRadius: 8, background: statusBg, color: statusColor, display: 'flex', gap: 10, alignItems: 'center' }}>
          <strong style={{ fontSize: 14 }}>{refreshing ? '刷新中' : status === 'loading' ? '处理中' : status === 'error' ? '需要处理' : '状态'}</strong>
          <span style={{ fontSize: 14 }}>{message}</span>
        </div>

        <section style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 0.95fr) minmax(0, 1.05fr)', gap: 20, marginTop: 20 }}>
          <div style={panelStyle}>
            <h2 style={{ margin: 0, fontSize: 18 }}>新建项目</h2>
            <div style={{ marginTop: 18, display: 'grid', gap: 14 }}>
              <label style={{ display: 'grid', gap: 6 }}>
                <span style={{ color: '#4b5563', fontSize: 13 }}>项目名</span>
                <input placeholder="例如：售后培训视频知识库" value={name} onChange={(event) => setName(event.target.value)} style={inputStyle} />
              </label>
              <label style={{ display: 'grid', gap: 6 }}>
                <span style={{ color: '#4b5563', fontSize: 13 }}>项目描述</span>
                <input placeholder="简要说明素材来源和目标用途" value={description} onChange={(event) => setDescription(event.target.value)} style={inputStyle} />
              </label>
              <label style={{ display: 'grid', gap: 6 }}>
                <span style={{ color: '#4b5563', fontSize: 13 }}>领域类型</span>
                <input placeholder="例如：客服、教学、合规、安全生产" value={domainType} onChange={(event) => setDomainType(event.target.value)} style={inputStyle} />
              </label>
              <button onClick={createProject} disabled={creating} style={{ ...buttonStyle, width: 'fit-content', opacity: creating ? 0.72 : 1, cursor: creating ? 'not-allowed' : 'pointer' }}>
                {creating ? '创建中...' : '创建项目'}
              </button>
            </div>
          </div>

          <div style={panelStyle}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', paddingBottom: 14, borderBottom: '1px solid #e5e7eb' }}>
              <div>
                <h2 style={{ margin: 0, fontSize: 18 }}>项目列表</h2>
                <p style={{ margin: '6px 0 0', color: '#6b7280', fontSize: 13 }}>共 {projects.length} 个项目</p>
              </div>
              <button onClick={() => void loadProjects()} style={{ ...buttonStyle, background: '#ffffff', color: '#111827', border: '1px solid #d1d5db' }}>
                手动刷新
              </button>
            </div>

            <div style={{ marginTop: 0 }}>
              {projects.map((project) => (
                <div id={`project-${project.id}`} key={project.id} style={{ padding: '16px 0', borderBottom: '1px solid #e5e7eb', background: project.id === lastCreatedId ? '#f0fdf4' : 'transparent' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                    <strong>{project.name}</strong>
                    <span style={{ color: project.status === 'active' ? '#166534' : '#6b7280', fontSize: 13 }}>{project.status || 'unknown'}</span>
                  </div>
                  <div style={{ marginTop: 6, color: '#4b5563', lineHeight: 1.6 }}>{project.description || '暂无描述'}</div>
                  <div style={{ marginTop: 8, display: 'flex', gap: 10, flexWrap: 'wrap', color: '#6b7280', fontSize: 13 }}>
                    <span>ID：{project.id}</span>
                    <span>领域：{project.domain_type || '-'}</span>
                  </div>
                </div>
              ))}
              {projects.length === 0 ? <div style={{ padding: '22px 0', color: '#6b7280' }}>暂无项目</div> : null}
            </div>
          </div>
        </section>
      </section>
    </main>
  );
}
