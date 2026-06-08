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
  const [message, setMessage] = useState('Ready');
  const [lastCreatedId, setLastCreatedId] = useState('');

  const loadProjects = async () => {
    setRefreshing(true);
    try {
      const list = await apiJson<Project[]>('/api/v1/projects');
      setProjects(list);
      setStatus('success');
      setMessage(`Loaded ${list.length} projects`);
    } catch (error) {
      const messageText = error instanceof ApiError || error instanceof Error ? error.message : 'Load failed';
      setProjects([]);
      setStatus('error');
      setMessage(`Project list failed: ${messageText}`);
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
      setMessage('Enter a project name');
      return;
    }

    setCreating(true);
    setStatus('loading');
    setMessage('Creating project...');
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
      setMessage(`Created ${data.name}`);
      setName('');
      setDescription('');
      setDomainType('');
      await loadProjects();
      window.setTimeout(() => {
        document.getElementById(`project-${data.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 120);
    } catch (error) {
      const messageText = error instanceof ApiError || error instanceof Error ? error.message : 'Unknown error';
      setStatus('error');
      setMessage(`Create failed: ${messageText}`);
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
          <h1 style={{ margin: 0, fontSize: 32, lineHeight: 1.2 }}>Projects</h1>
          <p style={{ margin: '10px 0 0', maxWidth: 700, color: '#4b5563', lineHeight: 1.7 }}>
            Group video, long text, and multimodal assets before preprocessing, analysis, export, and trace review.
          </p>
        </header>

        <div style={{ marginTop: 18, padding: '12px 14px', border: `1px solid ${statusBorder}`, borderRadius: 8, background: statusBg, color: statusColor, display: 'flex', gap: 10, alignItems: 'center' }}>
          <strong style={{ fontSize: 14 }}>{refreshing ? 'Refreshing' : status === 'loading' ? 'Working' : status === 'error' ? 'Needs attention' : 'Status'}</strong>
          <span style={{ fontSize: 14 }}>{message}</span>
        </div>

        <section style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 0.95fr) minmax(0, 1.05fr)', gap: 20, marginTop: 20 }}>
          <div style={panelStyle}>
            <h2 style={{ margin: 0, fontSize: 18 }}>New project</h2>
            <div style={{ marginTop: 18, display: 'grid', gap: 14 }}>
              <label style={{ display: 'grid', gap: 6 }}>
                <span style={{ color: '#4b5563', fontSize: 13 }}>Project name</span>
                <input placeholder="Example: support training videos" value={name} onChange={(event) => setName(event.target.value)} style={inputStyle} />
              </label>
              <label style={{ display: 'grid', gap: 6 }}>
                <span style={{ color: '#4b5563', fontSize: 13 }}>Description</span>
                <input placeholder="Source material and target use" value={description} onChange={(event) => setDescription(event.target.value)} style={inputStyle} />
              </label>
              <label style={{ display: 'grid', gap: 6 }}>
                <span style={{ color: '#4b5563', fontSize: 13 }}>Domain type</span>
                <input placeholder="support, teaching, compliance, safety" value={domainType} onChange={(event) => setDomainType(event.target.value)} style={inputStyle} />
              </label>
              <button onClick={createProject} disabled={creating} style={{ ...buttonStyle, width: 'fit-content', opacity: creating ? 0.72 : 1, cursor: creating ? 'not-allowed' : 'pointer' }}>
                {creating ? 'Creating...' : 'Create project'}
              </button>
            </div>
          </div>

          <div style={panelStyle}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', paddingBottom: 14, borderBottom: '1px solid #e5e7eb' }}>
              <div>
                <h2 style={{ margin: 0, fontSize: 18 }}>Project list</h2>
                <p style={{ margin: '6px 0 0', color: '#6b7280', fontSize: 13 }}>{projects.length} projects</p>
              </div>
              <button onClick={() => void loadProjects()} style={{ ...buttonStyle, background: '#ffffff', color: '#111827', border: '1px solid #d1d5db' }}>
                Refresh
              </button>
            </div>

            <div>
              {projects.map((project) => (
                <div id={`project-${project.id}`} key={project.id} style={{ padding: '16px 0', borderBottom: '1px solid #e5e7eb', background: project.id === lastCreatedId ? '#f0fdf4' : 'transparent' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                    <strong>{project.name}</strong>
                    <span style={{ color: project.status === 'active' ? '#166534' : '#6b7280', fontSize: 13 }}>{project.status || 'unknown'}</span>
                  </div>
                  <div style={{ marginTop: 6, color: '#4b5563', lineHeight: 1.6 }}>{project.description || 'No description'}</div>
                  <div style={{ marginTop: 8, display: 'flex', gap: 10, flexWrap: 'wrap', color: '#6b7280', fontSize: 13 }}>
                    <span>ID: {project.id}</span>
                    <span>Domain: {project.domain_type || '-'}</span>
                  </div>
                </div>
              ))}
              {projects.length === 0 ? <div style={{ padding: '22px 0', color: '#6b7280' }}>No projects yet</div> : null}
            </div>
          </div>
        </section>
      </section>
    </main>
  );
}
