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

function statusLabel(status?: string | null) {
  if (status === 'success') return '已完成';
  if (status === 'failed') return '失败';
  if (status === 'dispatch_failed') return '投递失败';
  if (status === 'running') return '运行中';
  if (status === 'pending') return '等待中';
  return status || '-';
}

function taskTypeLabel(taskType?: string | null) {
  const labels: Record<string, string> = {
    preprocess: '素材预处理',
    analyze: '语义分析',
    sample: '样本生成',
    generate_samples: '样本生成',
    export: '数据集导出',
    export_skill: 'Skill 导出',
    export_markdown: 'Markdown 导出',
  };
  return taskType ? labels[taskType] || taskType : '-';
}

export default function TaskDetailPage({ params }: { params: Promise<{ task_id: string }> }) {
  const [task, setTask] = useState<Task | null>(null);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('正在加载任务...');

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | undefined;
    params.then(({ task_id }) => {
      const loadTask = async () => {
        try {
          const data = await apiJson<Task>(`/api/v1/tasks/${task_id}`);
          setTask(data);
          setMessage('任务已加载');
        } catch (error) {
          const text = error instanceof ApiError || error instanceof Error ? error.message : '任务加载失败';
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
          <Link href="/tasks" style={{ color: '#4b5563', fontSize: 14, textDecoration: 'none' }}>返回任务列表</Link>
          <h1 style={{ margin: '10px 0 0', fontSize: 32, lineHeight: 1.2 }}>任务详情</h1>
        </header>

        <div style={{ marginTop: 18, padding: '12px 14px', border: '1px solid #e5e7eb', borderRadius: 8, background: '#ffffff', color: task ? statusColor(task.status) : '#374151', display: 'flex', gap: 10, alignItems: 'center' }}>
          <strong style={{ fontSize: 14 }}>状态</strong>
          <span style={{ fontSize: 14 }}>{loading ? '正在加载...' : message}</span>
        </div>

        <section style={{ ...panelStyle, marginTop: 20 }}>
          {loading ? <div style={{ color: '#6b7280' }}>正在加载...</div> : null}
          {!loading && !task ? <div style={{ color: '#b91c1c' }}>任务不存在或暂不可用</div> : null}
          {task ? (
            <>
              <DetailRow label="任务 ID" value={task.id} />
              <DetailRow label="任务类型" value={taskTypeLabel(task.task_type)} />
              <DetailRow label="状态" value={statusLabel(task.status)} />
              <DetailRow label="进度" value={`${progress}%`} />
              <DetailRow label="结果摘要" value={task.result_summary} />
              <DetailRow label="错误信息" value={task.error_message} />
              <div style={{ marginTop: 16, height: 8, background: '#e5e7eb', borderRadius: 4, overflow: 'hidden' }}>
                <div style={{ width: `${progress}%`, height: '100%', background: '#111827' }} />
              </div>
              <Link href="/tasks" style={{ ...buttonStyle, display: 'inline-flex', alignItems: 'center', textDecoration: 'none', marginTop: 18 }}>
                返回列表
              </Link>
            </>
          ) : null}
        </section>
      </section>
    </main>
  );
}
