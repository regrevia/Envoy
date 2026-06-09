"use client";

import Link from 'next/link';
import { useEffect, useState } from 'react';

import { apiJson } from '../../lib/api';

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
  minHeight: 36,
  padding: '0 12px',
  borderRadius: 6,
  border: '1px solid #d1d5db',
  background: '#ffffff',
  color: '#111827',
  fontWeight: 600,
  cursor: 'pointer',
} as const;

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

export default function TasksPage() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);

  const loadTasks = async () => {
    try {
      const data = await apiJson<Task[]>('/api/v1/tasks');
      setTasks(data);
    } catch {
      setTasks([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadTasks();
    const timer = setInterval(loadTasks, 4000);
    return () => clearInterval(timer);
  }, []);

  return (
    <main style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}>
      <section style={{ maxWidth: 1120, margin: '0 auto', padding: '44px 24px' }}>
        <header style={{ paddingBottom: 22, borderBottom: '1px solid #e5e7eb' }}>
          <h1 style={{ margin: 0, fontSize: 32, lineHeight: 1.2 }}>任务</h1>
          <p style={{ margin: '10px 0 0', maxWidth: 720, color: '#4b5563', lineHeight: 1.7 }}>
            查看预处理、语义分析、样本生成和导出任务的执行状态。列表会自动刷新，便于发现长任务是否卡住或失败。
          </p>
        </header>

        <section style={{ ...panelStyle, marginTop: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, paddingBottom: 14, borderBottom: '1px solid #e5e7eb' }}>
            <div>
              <h2 style={{ margin: 0, fontSize: 18 }}>任务列表</h2>
              <p style={{ margin: '6px 0 0', color: '#6b7280', fontSize: 13 }}>共 {tasks.length} 个任务</p>
            </div>
            <button onClick={() => void loadTasks()} style={buttonStyle}>刷新</button>
          </div>

          {loading ? <div style={{ padding: '22px 0', color: '#6b7280' }}>正在加载...</div> : null}
          {!loading && tasks.length === 0 ? <div style={{ padding: '22px 0', color: '#6b7280' }}>暂无任务。请先创建项目、上传素材并启动转换。</div> : null}
          {tasks.map((task) => {
            const progress = Math.max(0, Math.min(Number(task.progress ?? 0), 100));
            return (
              <div key={task.id} style={{ padding: '16px 0', borderBottom: '1px solid #e5e7eb' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'flex-start' }}>
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                      <strong>{taskTypeLabel(task.task_type)}</strong>
                      <span style={{ color: statusColor(task.status), fontSize: 13 }}>{statusLabel(task.status)}</span>
                    </div>
                    <div style={{ marginTop: 10, height: 8, background: '#e5e7eb', borderRadius: 4, overflow: 'hidden' }}>
                      <div style={{ width: `${progress}%`, height: '100%', background: '#111827' }} />
                    </div>
                    <div style={{ marginTop: 8, color: '#6b7280', fontSize: 13 }}>进度: {progress}%</div>
                    {task.error_message ? <div style={{ marginTop: 8, color: '#b91c1c', wordBreak: 'break-word' }}>{task.error_message}</div> : null}
                    {task.result_summary ? <div style={{ marginTop: 8, color: '#4b5563', wordBreak: 'break-word' }}>{task.result_summary}</div> : null}
                  </div>
                  <Link href={`/tasks/${task.id}`} style={{ ...buttonStyle, display: 'inline-flex', alignItems: 'center', textDecoration: 'none', flex: '0 0 auto' }}>详情</Link>
                </div>
              </div>
            );
          })}
        </section>
      </section>
    </main>
  );
}
