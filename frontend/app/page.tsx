"use client";

import Link from 'next/link';
import { useEffect, useState } from 'react';

import { apiJson } from '../lib/api';

const panelStyle = {
  background: '#ffffff',
  border: '1px solid #e5e7eb',
  borderRadius: 8,
  padding: 24,
} as const;

const actionStyle = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  minHeight: 40,
  padding: '0 14px',
  borderRadius: 6,
  textDecoration: 'none',
  fontWeight: 600,
  fontSize: 14,
} as const;

export default function HomePage() {
  const [projects, setProjects] = useState<any[]>([]);
  const [assets, setAssets] = useState<any[]>([]);
  const [tasks, setTasks] = useState<any[]>([]);
  const [artifacts, setArtifacts] = useState<any[]>([]);

  useEffect(() => {
    Promise.all([
      apiJson<any[]>('/api/v1/projects').catch(() => []),
      apiJson<any[]>('/api/v1/assets').catch(() => []),
      apiJson<any[]>('/api/v1/tasks').catch(() => []),
      apiJson<any[]>('/api/v1/artifacts').catch(() => []),
    ]).then(([p, a, t, ar]) => {
      setProjects(p);
      setAssets(a);
      setTasks(t);
      setArtifacts(ar);
    });
  }, []);

  const metrics = [
    { label: 'Projects', value: projects.length },
    { label: 'Assets', value: assets.length },
    { label: 'Tasks', value: tasks.length },
    { label: 'Artifacts', value: artifacts.length },
  ];

  const entries = [
    { href: '/projects', label: 'Projects', detail: 'Organize domains, source material, and generated work.' },
    { href: '/upload', label: 'Upload', detail: 'Ingest video, long text, markdown, audio, and other source files.' },
    { href: '/tasks', label: 'Tasks', detail: 'Track preprocessing, analysis, sample generation, and exports.' },
    { href: '/semantic-units', label: 'Semantic Units', detail: 'Inspect extracted text chunks, scenes, rules, and concepts.' },
    { href: '/artifacts', label: 'Artifacts', detail: 'Open Markdown, RAG, Skill, and dataset outputs.' },
    { href: '/train-exports', label: 'Training Exports', detail: 'Create SFT, instruction, preference, RAG, Skill, and Markdown files.' },
    { href: '/trace', label: 'Trace', detail: 'Follow links from assets to semantic units and artifacts.' },
  ];

  return (
    <main style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}>
      <section style={{ maxWidth: 1180, margin: '0 auto', padding: '44px 24px' }}>
        <header style={{ display: 'flex', justifyContent: 'space-between', gap: 20, alignItems: 'flex-start', paddingBottom: 24, borderBottom: '1px solid #e5e7eb' }}>
          <div>
            <h1 style={{ margin: 0, fontSize: 34, lineHeight: 1.15 }}>Envoy Workspace</h1>
            <p style={{ margin: '10px 0 0', maxWidth: 680, color: '#4b5563', lineHeight: 1.7 }}>
              Convert raw video, long text, and multimodal files into structured AI materials: Markdown, Skill packages, RAG corpora, and training datasets.
            </p>
          </div>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            <Link href="/upload" style={{ ...actionStyle, background: '#111827', color: '#fff' }}>Upload asset</Link>
            <Link href="/tasks" style={{ ...actionStyle, background: '#fff', color: '#111827', border: '1px solid #d1d5db' }}>View tasks</Link>
          </div>
        </header>

        <section style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', border: '1px solid #e5e7eb', borderRadius: 8, overflow: 'hidden', marginTop: 24, background: '#fff' }}>
          {metrics.map((metric, index) => (
            <div key={metric.label} style={{ padding: 20, borderLeft: index === 0 ? 'none' : '1px solid #e5e7eb' }}>
              <div style={{ color: '#6b7280', fontSize: 13 }}>{metric.label}</div>
              <div style={{ fontSize: 32, fontWeight: 700, marginTop: 8 }}>{metric.value}</div>
            </div>
          ))}
        </section>

        <section style={{ display: 'grid', gridTemplateColumns: '1.1fr 0.9fr', gap: 20, marginTop: 20 }}>
          <div style={panelStyle}>
            <h2 style={{ margin: 0, fontSize: 18 }}>Recent projects</h2>
            <div style={{ marginTop: 16, borderTop: '1px solid #e5e7eb' }}>
              {projects.slice(0, 5).map((project) => (
                <div key={project.id} style={{ padding: '14px 0', borderBottom: '1px solid #e5e7eb' }}>
                  <div style={{ fontWeight: 600 }}>{project.name}</div>
                  <div style={{ marginTop: 4, color: '#6b7280', fontSize: 13 }}>{project.id}</div>
                </div>
              ))}
              {projects.length === 0 ? <div style={{ padding: '18px 0', color: '#6b7280' }}>No projects yet</div> : null}
            </div>
          </div>

          <nav style={panelStyle}>
            <h2 style={{ margin: 0, fontSize: 18 }}>Workspace areas</h2>
            <div style={{ marginTop: 16, borderTop: '1px solid #e5e7eb' }}>
              {entries.map((entry) => (
                <Link key={entry.href} href={entry.href} style={{ display: 'block', padding: '13px 0', borderBottom: '1px solid #e5e7eb', textDecoration: 'none' }}>
                  <div style={{ fontWeight: 600 }}>{entry.label}</div>
                  <div style={{ marginTop: 4, color: '#6b7280', fontSize: 13 }}>{entry.detail}</div>
                </Link>
              ))}
            </div>
          </nav>
        </section>
      </section>
    </main>
  );
}
