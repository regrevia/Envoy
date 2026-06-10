"use client";

import Link from 'next/link';
import { useEffect, useState } from 'react';

import { apiJson } from '../../lib/api';

type Task = {
  id: string;
  project_id?: string | null;
  asset_id?: string | null;
  task_type?: string | null;
  status?: string | null;
  progress?: number | null;
  created_at?: string | null;
  updated_at?: string | null;
  is_stale?: boolean;
  stale_seconds?: number;
  stale_after_seconds?: number;
  result_summary?: string | null;
  error_message?: string | null;
};

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
  if (status === 'canceled') return '#6b7280';
  return '#4b5563';
}

function statusLabel(status?: string | null) {
  if (status === 'success') return '已完成';
  if (status === 'failed') return '失败';
  if (status === 'dispatch_failed') return '投递失败';
  if (status === 'running') return '运行中';
  if (status === 'pending') return '等待中';
  if (status === 'canceled') return '已取消';
  return status || '-';
}

function canCancel(task: Task) {
  return task.status === 'pending' || task.status === 'running' || task.status === 'dispatch_failed';
}

function canRetry(task: Task) {
  return task.status === 'failed' || task.status === 'dispatch_failed' || task.status === 'canceled';
}

function staleLabel(task: Task) {
  if (!task.is_stale) return '';
  const seconds = Math.max(Number(task.stale_seconds ?? 0), 0);
  const minutes = Math.floor(seconds / 60);
  return minutes > 0 ? `疑似卡住：${minutes} 分钟未更新` : '疑似卡住：长时间未更新';
}

function formatDurationSeconds(value?: number | null) {
  const seconds = Math.max(Number(value ?? 0), 0);
  if (seconds >= 60) return `${Math.floor(seconds / 60)} 分钟`;
  return `${seconds} 秒`;
}

function isMediaLimitSummary(summary?: string | null) {
  const text = summary || '';
  return text.includes('segment(s)') && text.includes('frame(s)') && text.includes('max');
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

function formatTime(value?: string | null) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { hour12: false });
}

export default function TasksPage() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [cancelingId, setCancelingId] = useState<string | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [message, setMessage] = useState('');

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

  const cancelTask = async (taskId: string) => {
    setCancelingId(taskId);
    setMessage('');
    try {
      await apiJson(`/api/v1/tasks/${taskId}/cancel`, { method: 'POST' });
      setMessage('任务已取消');
      await loadTasks();
    } catch (error) {
      const text = error instanceof Error ? error.message : '取消任务失败';
      setMessage(text);
    } finally {
      setCancelingId(null);
    }
  };

  const retryTask = async (taskId: string) => {
    setRetryingId(taskId);
    setMessage('');
    try {
      await apiJson(`/api/v1/tasks/${taskId}/retry`, { method: 'POST' });
      setMessage('重试任务已创建');
      await loadTasks();
    } catch (error) {
      const text = error instanceof Error ? error.message : '重试任务失败';
      setMessage(text);
    } finally {
      setRetryingId(null);
    }
  };

  return (
    <main style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}>
      <section style={{ maxWidth: 1120, margin: '0 auto', padding: '44px 24px' }}>
        <header style={{ paddingBottom: 22, borderBottom: '1px solid #e5e7eb' }}>
          <h1 style={{ margin: 0, fontSize: 32, lineHeight: 1.2 }}>任务</h1>
          <p style={{ margin: '10px 0 0', maxWidth: 720, color: '#4b5563', lineHeight: 1.7 }}>
            查看预处理、语义分析、样本生成和导出任务的执行状态。列表会自动刷新，便于发现长任务是否卡住或失败。
          </p>
        </header>

        <section style={{ padding: '14px 0', borderBottom: '1px solid #e5e7eb' }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>长任务诊断</h2>
          <div style={{ marginTop: 8, display: 'flex', gap: 14, flexWrap: 'wrap', color: '#4b5563', fontSize: 13 }}>
            <span>自动刷新: 4 秒</span>
            <span>卡住阈值: 使用任务 stale_after_seconds</span>
            <span>未更新时间: 使用任务 stale_seconds</span>
            <span>疑似卡住后可先取消任务，再重试任务</span>
          </div>
        </section>

        <section style={{ marginTop: 20, paddingTop: 18, borderTop: '1px solid #e5e7eb' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, paddingBottom: 14, borderBottom: '1px solid #e5e7eb' }}>
            <div>
              <h2 style={{ margin: 0, fontSize: 18 }}>任务列表</h2>
              <p style={{ margin: '6px 0 0', color: '#6b7280', fontSize: 13 }}>共 {tasks.length} 个任务</p>
            </div>
            <button onClick={() => void loadTasks()} style={buttonStyle}>刷新</button>
          </div>
          {message ? <div style={{ padding: '12px 0', borderBottom: '1px solid #e5e7eb', color: message.includes('失败') ? '#b91c1c' : '#166534' }}>{message}</div> : null}

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
                    <div style={{ marginTop: 8, display: 'flex', gap: 10, flexWrap: 'wrap', color: '#6b7280', fontSize: 13 }}>
                      <span>进度: {progress}%</span>
                      <span>项目: {task.project_id || '-'}</span>
                      <span>素材: {task.asset_id || '-'}</span>
                      <span>创建: {formatTime(task.created_at)}</span>
                      <span>最后更新: {formatTime(task.updated_at)}</span>
                    </div>
                    <div style={{ marginTop: 8, display: 'flex', gap: 10, flexWrap: 'wrap', color: '#6b7280', fontSize: 13 }}>
                      <span>卡住阈值: {formatDurationSeconds(task.stale_after_seconds)}</span>
                      <span>未更新时间: {formatDurationSeconds(task.stale_seconds)}</span>
                    </div>
                    {task.is_stale ? <div style={{ marginTop: 8, color: '#b91c1c', fontWeight: 600 }}>{staleLabel(task)}</div> : null}
                    {task.error_message ? <div style={{ marginTop: 8, color: '#b91c1c', wordBreak: 'break-word' }}>{task.error_message}</div> : null}
                    {task.result_summary ? (
                      <div style={{ marginTop: 8, color: '#4b5563', wordBreak: 'break-word' }}>
                        <span style={{ color: '#6b7280' }}>任务摘要: </span>
                        {task.result_summary}
                        {isMediaLimitSummary(task.result_summary) ? <span style={{ marginLeft: 10, color: '#1f2937', fontWeight: 600 }}>媒体处理上限</span> : null}
                      </div>
                    ) : null}
                  </div>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end', flex: '0 0 auto' }}>
                    {canCancel(task) ? (
                      <button
                        onClick={() => void cancelTask(task.id)}
                        disabled={cancelingId === task.id}
                        style={{ ...buttonStyle, color: '#991b1b', borderColor: '#fecaca', opacity: cancelingId === task.id ? 0.7 : 1 }}
                      >
                        {cancelingId === task.id ? '正在取消...' : '取消任务'}
                      </button>
                    ) : null}
                    {canRetry(task) ? (
                      <button
                        onClick={() => void retryTask(task.id)}
                        disabled={retryingId === task.id}
                        style={{ ...buttonStyle, color: '#111827', borderColor: '#9ca3af', opacity: retryingId === task.id ? 0.7 : 1 }}
                      >
                        {retryingId === task.id ? '正在重试...' : '重试任务'}
                      </button>
                    ) : null}
                    <Link href={`/tasks/${task.id}`} style={{ ...buttonStyle, display: 'inline-flex', alignItems: 'center', textDecoration: 'none' }}>详情</Link>
                  </div>
                </div>
              </div>
            );
          })}
        </section>
      </section>
    </main>
  );
}
