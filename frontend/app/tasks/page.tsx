"use client";

import Link from 'next/link';
import { useEffect, useState } from 'react';

import { apiJson } from '../../lib/api';
import { apiUrl } from '@/lib/api';

export default function TasksPage() {
  const [tasks, setTasks] = useState<any[]>([]);

  const loadTasks = () => {
    apiJson<any[]>('/api/v1/tasks')
      .then(setTasks)
      .catch(() => setTasks([]));
  };

  useEffect(() => {
    loadTasks();
    const timer = setInterval(loadTasks, 4000);
    return () => clearInterval(timer);
  }, []);

  return (
    <main style={{ minHeight: '100vh', background: '#f5f7fb', fontFamily: 'Inter, sans-serif', color: '#0f172a' }}>
      <section style={{ maxWidth: 1100, margin: '0 auto', padding: '48px 24px' }}>
        <h1 style={{ fontSize: 36, marginBottom: 8 }}>任务中心</h1>
        <p style={{ color: '#475569' }}>用于查看预处理、分析、样本生成与导出任务。</p>

        <div style={{ display: 'grid', gap: 16, marginTop: 24 }}>
          {tasks.length === 0 ? <div>暂无任务</div> : null}
          {tasks.map((task) => (
            <div key={task.id ?? JSON.stringify(task)} style={{ background: '#fff', borderRadius: 18, padding: 20, boxShadow: '0 10px 30px rgba(15,23,42,0.06)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                <strong>{task.task_type ?? '-'}</strong>
                <span>{task.status ?? '-'}</span>
              </div>
              <div style={{ height: 8, background: '#e2e8f0', borderRadius: 999, overflow: 'hidden' }}>
                <div style={{ width: `${task.progress ?? 0}%`, height: '100%', background: '#0f172a' }} />
              </div>
              <div style={{ marginTop: 8 }}>进度：{task.progress ?? 0}%</div>
              <Link href={`/tasks/${task.id}`}>查看详情</Link>
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
