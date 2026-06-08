"use client";

import { useEffect, useState } from 'react';

import { ApiError, apiJson } from '../../lib/api';

export default function ProjectsPage() {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [domainType, setDomainType] = useState('');
  const [projects, setProjects] = useState<any[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string>('');
  const [logs, setLogs] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');
  const [message, setMessage] = useState('');
  const [lastCreatedId, setLastCreatedId] = useState('');

  const loadProjects = async () => {
    setRefreshing(true);
    setStatus('loading');
    try {
      const list = await apiJson<any[]>('/api/v1/projects');
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
    const timer = setInterval(() => {
      if (selectedProjectId) {
        setLogs((prev) => [...prev.slice(-19), `[${new Date().toLocaleTimeString()}] 项目 ${selectedProjectId} 心跳刷新`]);
      }
      void loadProjects();
    }, 3000);
    return () => clearInterval(timer);
  }, [selectedProjectId]);

  const createProject = async () => {
    if (!name.trim() || creating) return;
    setCreating(true);
    setStatus('loading');
    setMessage('正在创建项目...');
    setLogs((prev) => [...prev, `[${new Date().toLocaleTimeString()}] 正在创建项目...`]);
    try {
      const data = await apiJson<{ id: string }>('/api/v1/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, description, domain_type: domainType, status: 'active' }),
      });
      setLastCreatedId(data.id);
      setStatus('success');
      setMessage(`创建成功：${data.id}`);
      setLogs((prev) => [...prev, `[${new Date().toLocaleTimeString()}] 创建项目成功：${data.id}`]);
      setName('');
      setDescription('');
      setDomainType('');
      await loadProjects();
      setTimeout(() => {
        const el = document.getElementById(`project-${data.id}`);
        el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 150);
    } catch (error) {
      const messageText = error instanceof ApiError || error instanceof Error ? error.message : '未知错误';
      setStatus('error');
      setMessage(`创建失败：${messageText}`);
      setLogs((prev) => [...prev, `[${new Date().toLocaleTimeString()}] 创建项目失败：${messageText}`]);
    } finally {
      setCreating(false);
    }
  };

  return (
    <main style={{ minHeight: '100vh', background: '#f5f7fb', fontFamily: 'Inter, sans-serif', color: '#0f172a' }}>
      <section style={{ maxWidth: 1200, margin: '0 auto', padding: '48px 24px' }}>
        <h1 style={{ fontSize: 36, marginBottom: 8 }}>项目管理</h1>
        <p style={{ color: '#475569' }}>只输入项目名，系统自动分配项目 ID。每个项目可接收多种素材输入。</p>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 16, padding: '12px 16px', borderRadius: 12, background: status === 'success' ? '#ecfdf5' : status === 'error' ? '#fef2f2' : status === 'loading' ? '#eff6ff' : '#f8fafc', color: status === 'success' ? '#166534' : status === 'error' ? '#b91c1c' : status === 'loading' ? '#1d4ed8' : '#334155', border: '1px solid ' + (status === 'success' ? '#bbf7d0' : status === 'error' ? '#fecaca' : status === 'loading' ? '#bfdbfe' : '#e2e8f0') }}>
          <strong>状态：</strong>
          <span>{refreshing ? '正在刷新项目列表...' : status === 'idle' ? '就绪' : status === 'loading' ? '处理中' : status === 'success' ? '成功' : '失败'}</span>
          <span style={{ marginLeft: 8 }}>{message || '—'}</span>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1.1fr 0.9fr', gap: 20, marginTop: 24 }}>
          <div style={{ background: '#fff', borderRadius: 18, padding: 24, boxShadow: '0 10px 30px rgba(15,23,42,0.06)' }}>
            <h2 style={{ marginTop: 0 }}>新建项目</h2>
            <div style={{ display: 'grid', gap: 12 }}>
              <input placeholder="项目名" value={name} onChange={(e) => setName(e.target.value)} style={{ padding: 12, borderRadius: 10, border: '1px solid #cbd5e1', boxShadow: 'inset 0 1px 2px rgba(0,0,0,0.04)' }} />
              <input placeholder="项目描述" value={description} onChange={(e) => setDescription(e.target.value)} style={{ padding: 12, borderRadius: 10, border: '1px solid #cbd5e1', boxShadow: 'inset 0 1px 2px rgba(0,0,0,0.04)' }} />
              <input placeholder="领域类型" value={domainType} onChange={(e) => setDomainType(e.target.value)} style={{ padding: 12, borderRadius: 10, border: '1px solid #cbd5e1', boxShadow: 'inset 0 1px 2px rgba(0,0,0,0.04)' }} />
              <button onClick={createProject} disabled={creating} style={{ padding: '12px 16px', borderRadius: 10, background: creating ? '#334155' : '#0f172a', color: '#fff', border: 0, width: 'fit-content', cursor: creating ? 'not-allowed' : 'pointer', transform: creating ? 'none' : 'translateY(0)', boxShadow: creating ? 'none' : '0 8px 18px rgba(15,23,42,0.12)' }}>{creating ? '创建中...' : '创建项目'}</button>
            </div>
          </div>

          <div style={{ background: '#fff', borderRadius: 18, padding: 24, boxShadow: '0 10px 30px rgba(15,23,42,0.06)' }}>
            <h2 style={{ marginTop: 0 }}>实时日志</h2>
            <select value={selectedProjectId} onChange={(e) => setSelectedProjectId(e.target.value)} style={{ padding: 12, borderRadius: 10, border: '1px solid #cbd5e1', width: '100%' }}>
              <option value="">选择项目</option>
              {projects.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.id})</option>)}
            </select>
            <div style={{ marginTop: 12, height: 280, overflowY: 'auto', background: '#0f172a', color: '#dbeafe', padding: 16, borderRadius: 12, fontFamily: 'monospace', fontSize: 13 }}>
              {logs.length === 0 ? <div>暂无日志</div> : logs.map((line, idx) => <div key={idx}>{line}</div>)}
            </div>
          </div>
        </div>

        <div style={{ marginTop: 24, background: '#fff', borderRadius: 18, padding: 24, boxShadow: '0 10px 30px rgba(15,23,42,0.06)' }}>
          <h2 style={{ marginTop: 0 }}>项目列表</h2>
          <div style={{ display: 'flex', gap: 10, marginBottom: 12 }}>
            <button onClick={() => void loadProjects()} style={{ padding: '8px 12px', borderRadius: 10, background: '#e2e8f0', color: '#0f172a', border: 0 }}>手动刷新</button>
            {lastCreatedId ? <span style={{ alignSelf: 'center', color: '#475569' }}>最近创建：{lastCreatedId}</span> : null}
          </div>
          <div style={{ display: 'grid', gap: 12 }}>
            {projects.map((project) => (
              <div id={`project-${project.id}`} key={project.id} style={{ padding: 12, borderRadius: 10, border: `1px solid ${project.id === lastCreatedId ? '#22c55e' : '#e2e8f0'}`, background: project.id === lastCreatedId ? '#f0fdf4' : '#fff' }}>
                <strong>{project.name}</strong>
                <div>ID：{project.id}</div>
                <div>描述：{project.description ?? '-'}</div>
                <div>领域：{project.domain_type ?? '-'}</div>
              </div>
            ))}
            {projects.length === 0 ? <div>暂无项目</div> : null}
          </div>
        </div>
      </section>
    </main>
  );
}
