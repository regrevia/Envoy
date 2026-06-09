"use client";

import Link from 'next/link';
import { useEffect, useState } from 'react';

import { ApiError, apiJson } from '../../../lib/api';

type Task = {
  id: string;
  project_id?: string | null;
  asset_id?: string | null;
  task_type?: string | null;
  status?: string | null;
  progress?: number | null;
  input_config?: Record<string, unknown> | null;
  result_summary?: string | null;
  error_message?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

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

function formatTime(value?: string | null) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { hour12: false });
}

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

function canCancel(task?: Task | null) {
  return task?.status === 'pending' || task?.status === 'running' || task?.status === 'dispatch_failed';
}

function canRetry(task?: Task | null) {
  return task?.status === 'failed' || task?.status === 'dispatch_failed' || task?.status === 'canceled';
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
  const [taskId, setTaskId] = useState('');
  const [canceling, setCanceling] = useState(false);
  const [retrying, setRetrying] = useState(false);

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | undefined;
    params.then(({ task_id }) => {
      setTaskId(task_id);
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

  const loadTaskOnce = async (id: string) => {
    const data = await apiJson<Task>(`/api/v1/tasks/${id}`);
    setTask(data);
    setMessage('任务已加载');
  };

  const cancelTask = async () => {
    if (!taskId || !task) return;
    setCanceling(true);
    try {
      await apiJson(`/api/v1/tasks/${taskId}/cancel`, { method: 'POST' });
      setMessage('任务已取消');
      await loadTaskOnce(taskId);
    } catch (error) {
      const text = error instanceof ApiError || error instanceof Error ? error.message : '取消任务失败';
      setMessage(text);
    } finally {
      setCanceling(false);
    }
  };

  const retryTask = async () => {
    if (!taskId || !task) return;
    setRetrying(true);
    try {
      await apiJson(`/api/v1/tasks/${taskId}/retry`, { method: 'POST' });
      setMessage('重试任务已创建');
      await loadTaskOnce(taskId);
    } catch (error) {
      const text = error instanceof ApiError || error instanceof Error ? error.message : '重试任务失败';
      setMessage(text);
    } finally {
      setRetrying(false);
    }
  };

  const progress = Math.max(0, Math.min(Number(task?.progress ?? 0), 100));

  return (
    <main style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}>
      <section style={{ maxWidth: 920, margin: '0 auto', padding: '44px 24px' }}>
        <header style={{ paddingBottom: 22, borderBottom: '1px solid #e5e7eb' }}>
          <Link href="/tasks" style={{ color: '#4b5563', fontSize: 14, textDecoration: 'none' }}>返回任务列表</Link>
          <h1 style={{ margin: '10px 0 0', fontSize: 32, lineHeight: 1.2 }}>任务详情</h1>
        </header>

        <div style={{ marginTop: 18, padding: '12px 0', borderBottom: '1px solid #e5e7eb', color: task ? statusColor(task.status) : '#374151', display: 'flex', gap: 10, alignItems: 'center' }}>
          <strong style={{ fontSize: 14 }}>状态</strong>
          <span style={{ fontSize: 14 }}>{loading ? '正在加载...' : message}</span>
        </div>

        <section style={{ marginTop: 20, paddingTop: 18, borderTop: '1px solid #e5e7eb' }}>
          {loading ? <div style={{ color: '#6b7280' }}>正在加载...</div> : null}
          {!loading && !task ? <div style={{ color: '#b91c1c' }}>任务不存在或暂不可用</div> : null}
          {task ? (
            <>
              <DetailRow label="任务 ID" value={task.id} />
              <DetailRow label="项目 ID" value={task.project_id} />
              <DetailRow label="素材 ID" value={task.asset_id} />
              <DetailRow label="任务类型" value={taskTypeLabel(task.task_type)} />
              <DetailRow label="状态" value={statusLabel(task.status)} />
              <DetailRow label="进度" value={`${progress}%`} />
              <DetailRow label="结果摘要" value={task.result_summary} />
              <DetailRow label="错误信息" value={task.error_message} />
              <DetailRow label="创建时间" value={formatTime(task.created_at)} />
              <DetailRow label="更新时间" value={formatTime(task.updated_at)} />
              <div style={{ padding: '12px 0', borderBottom: '1px solid #e5e7eb' }}>
                <div style={{ color: '#6b7280', marginBottom: 8 }}>输入配置</div>
                <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: '#374151' }}>
                  {JSON.stringify(task.input_config || {}, null, 2)}
                </pre>
              </div>
              <div style={{ marginTop: 16, height: 8, background: '#e5e7eb', borderRadius: 4, overflow: 'hidden' }}>
                <div style={{ width: `${progress}%`, height: '100%', background: '#111827' }} />
              </div>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 18 }}>
                {canCancel(task) ? (
                  <button
                    onClick={() => void cancelTask()}
                    disabled={canceling}
                    style={{ ...buttonStyle, color: '#991b1b', borderColor: '#fecaca', opacity: canceling ? 0.7 : 1 }}
                  >
                    {canceling ? '正在取消...' : '取消任务'}
                  </button>
                ) : null}
                {canRetry(task) ? (
                  <button
                    onClick={() => void retryTask()}
                    disabled={retrying}
                    style={{ ...buttonStyle, borderColor: '#9ca3af', opacity: retrying ? 0.7 : 1 }}
                  >
                    {retrying ? '正在重试...' : '重试任务'}
                  </button>
                ) : null}
                <Link href="/tasks" style={{ ...buttonStyle, display: 'inline-flex', alignItems: 'center', textDecoration: 'none' }}>
                  返回列表
                </Link>
              </div>
            </>
          ) : null}
        </section>
      </section>
    </main>
  );
}
