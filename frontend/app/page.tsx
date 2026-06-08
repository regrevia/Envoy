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
    { label: '项目数', value: projects.length },
    { label: '素材数', value: assets.length },
    { label: '任务数', value: tasks.length },
    { label: '产物数', value: artifacts.length },
  ];

  const entries = [
    { href: '/projects', label: '项目管理', detail: '组织项目、领域和素材归属' },
    { href: '/upload', label: '素材上传', detail: '接入视频、长文本和多模态文件' },
    { href: '/tasks', label: '任务中心', detail: '跟踪预处理、分析、生成和导出' },
    { href: '/artifacts', label: '产物中心', detail: '查看 Markdown、RAG、Skill 等结果' },
    { href: '/train-exports', label: '训练导出', detail: '生成 SFT、Instruction、Preference 数据集' },
    { href: '/trace', label: '追溯查询', detail: '查看素材到语义单元和产物的链路' },
  ];

  return (
    <main style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}>
      <section style={{ maxWidth: 1180, margin: '0 auto', padding: '44px 24px' }}>
        <header style={{ display: 'flex', justifyContent: 'space-between', gap: 20, alignItems: 'flex-start', paddingBottom: 24, borderBottom: '1px solid #e5e7eb' }}>
          <div>
            <h1 style={{ margin: 0, fontSize: 34, lineHeight: 1.15 }}>Envoy v1.0</h1>
            <p style={{ margin: '10px 0 0', maxWidth: 620, color: '#4b5563', lineHeight: 1.7 }}>
              面向视频、长文本和多模态文件的 AI 素材生产工作台，将原始材料转换为 Markdown、Skill、RAG 和训练数据。
            </p>
          </div>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            <Link href="/upload" style={{ ...actionStyle, background: '#111827', color: '#fff' }}>上传素材</Link>
            <Link href="/tasks" style={{ ...actionStyle, background: '#fff', color: '#111827', border: '1px solid #d1d5db' }}>查看任务</Link>
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

        <section style={{ display: 'grid', gridTemplateColumns: '1.15fr 0.85fr', gap: 20, marginTop: 20 }}>
          <div style={panelStyle}>
            <h2 style={{ margin: 0, fontSize: 18 }}>最近项目</h2>
            <div style={{ marginTop: 16, borderTop: '1px solid #e5e7eb' }}>
              {projects.slice(0, 5).map((project) => (
                <div key={project.id} style={{ padding: '14px 0', borderBottom: '1px solid #e5e7eb' }}>
                  <div style={{ fontWeight: 600 }}>{project.name}</div>
                  <div style={{ marginTop: 4, color: '#6b7280', fontSize: 13 }}>{project.id}</div>
                </div>
              ))}
              {projects.length === 0 ? <div style={{ padding: '18px 0', color: '#6b7280' }}>暂无项目</div> : null}
            </div>
          </div>

          <nav style={panelStyle}>
            <h2 style={{ margin: 0, fontSize: 18 }}>产品入口</h2>
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
