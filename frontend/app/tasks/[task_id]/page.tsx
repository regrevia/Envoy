"use client";

import Link from 'next/link';
import { useEffect, useState } from 'react';

import { ApiError, apiJson } from '../../../lib/api';

type Task = {
  id: string;
  task_type?: string | null;
  status?: string | null;
  progress?: number | null;
  result_summary?: string | null;
  error_message?: string | null;
};

const panelStyle = {
  background: '#ffffff',
  border: '1px solid #e5e7eb',
  borderRadius: 8,
  padding: 24,
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
} as const;

function DetailRow({ label, value }: { label: string; value?: string | number | null }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '140px minmax(0, 1fr)', gap: 16, padding: '12px 0', borderBottom: '1px solid #e5e7eb' }}>
      <div style={{ color: '#6b7280' }}>{label}</div>
      <div style={{ wordBreak: 'break-word' }}>{value ?? '-'}</div>
    </div>
  );
}

function statusColor(status?: string | null) {
  if (status === 'success') return '#166534';
  if (status === 'failed' || status === 'dispatch_failed') return '#b91c1c';
  if (status === 'running') return '#1d4ed8';
  return '#4b5563';
}

export default function TaskDetailPage({ params }: { params: Promise<{ task_id: string }> }) {
  const [task, setTask] = useState<Task | null>(null);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('Loading task...');

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | undefined;
    params.then(({ task_id }) => {
      const loadTask = async () => {
        try {
          const data = await apiJson<Task>(`/api/v1/tasks/${task_id}`);
          setTask(data);
          setMessage('Task loaded');
        } catch (error) {
          const text = error instanceof ApiError || error instanceof Error ? error.message : 'Task load failed';
          setTask(null);
          setMessage(text);
        } finally {
          setLoading(false);
        }
      };

      void loadTask();
      timer = setInterval(loadTask, 3000);
    });
    return () => timer && clearInterval(timer);
  }, [params]);

  const progress = Math.max(0, Math.min(Number(task?.progress ?? 0), 100));

  return (
    <main style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}>
      <section style={{ maxWidth: 920, margin: '0 auto', padding: '44px 24px' }}>
        <header style={{ paddingBottom: 22, borderBottom: '1px solid #e5e7eb' }}>
          <Link href="/tasks" style={{ color: '#4b5563', fontSize: 14, textDecoration: 'none' }}>Back to tasks</Link>
          <h1 style={{ margin: '10px 0 0', fontSize: 32, lineHeight: 1.2 }}>Task Detail</h1>
        </header>

        <div style={{ marginTop: 18, padding: '12px 14px', border: '1px solid #e5e7eb', borderRadius: 8, background: '#ffffff', color: task ? statusColor(task.status) : '#374151', display: 'flex', gap: 10, alignItems: 'center' }}>
          <strong style={{ fontSize: 14 }}>Status</strong>
          <span style={{ fontSize: 14 }}>{loading ? 'Loading...' : message}</span>
        </div>

        <section style={{ ...panelStyle, marginTop: 20 }}>
          {loading ? <div style={{ color: '#6b7280' }}>Loading...</div> : null}
          {!loading && !task ? <div style={{ color: '#b91c1c' }}>Task not found or unavailable</div> : null}
          {task ? (
            <>
              <DetailRow label="Task ID" value={task.id} />
              <DetailRow label="Type" value={task.task_type} />
              <DetailRow label="Status" value={task.status} />
              <DetailRow label="Progress" value={`${progress}%`} />
              <DetailRow label="Summary" value={task.result_summary} />
              <DetailRow label="Error" value={task.error_message} />
              <div style={{ marginTop: 16, height: 8, background: '#e5e7eb', borderRadius: 4, overflow: 'hidden' }}>
                <div style={{ width: `${progress}%`, height: '100%', background: '#111827' }} />
              </div>
              <Link href="/tasks" style={{ ...buttonStyle, display: 'inline-flex', alignItems: 'center', textDecoration: 'none', marginTop: 18 }}>
                Back to list
              </Link>
            </>
          ) : null}
        </section>
      </section>
    </main>
  );
}
