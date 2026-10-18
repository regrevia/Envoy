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
  is_stale?: boolean;
  stale_seconds?: number;
  stale_after_seconds?: number;
};

type TaskDiagnostics = {
  task_id: string;
  status?: string | null;
  severity?: string | null;
  summary?: string | null;
  recommended_action?: string | null;
  next_actions?: string[];
  can_cancel?: boolean;
  can_retry?: boolean;
  is_stale?: boolean;
  stale_seconds?: number;
  stale_after_seconds?: number;
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

function staleLabel(task?: Task | null) {
  if (!task?.is_stale) return '运行正常';
  const seconds = Math.max(Number(task.stale_seconds ?? 0), 0);
  const minutes = Math.floor(seconds / 60);
  return minutes > 0 ? `可能无响应：${minutes} 分钟未更新` : '可能无响应：长时间未更新';
}

function actionLabel(action?: string | null) {
  const labels: Record<string, string> = {
    monitor: '继续观察',
    cancel_then_retry: '先取消再重试',
    retry: '重试任务',
    create_new_task: '重新创建任务',
    review_outputs: '审查产物',
  };
  return action ? labels[action] || action : '-';
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

function taskInputSummary(inputConfig?: Record<string, unknown> | null) {
  const config = inputConfig || {};
  const requestedOutputs = Array.isArray(config.requested_outputs)
    ? config.requested_outputs.map((item) => String(item)).filter(Boolean).join('、')
    : '';
  const rows = [
    { label: '生成目标', value: requestedOutputs || '-' },
    { label: '来源语义单元', value: typeof config.source_unit_id === 'string' ? config.source_unit_id : '-' },
    { label: '来源文件', value: typeof config.source_path === 'string' ? config.source_path : '-' },
    { label: '重试来源', value: typeof config.retry_of_task_id === 'string' ? config.retry_of_task_id : '-' },
  ];
  return rows.filter((row) => row.value !== '-');
}

export default function TaskDetailPage({ params }: { params: Promise<{ task_id: string }> }) {
  const [task, setTask] = useState<Task | null>(null);
  const [diagnostics, setDiagnostics] = useState<TaskDiagnostics | null>(null);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('正在加载任务...');
  const [taskId, setTaskId] = useState('');
  const [canceling, setCanceling] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [browserSessionId, setBrowserSessionId] = useState('');
  const [browserBusy, setBrowserBusy] = useState(false);
  const [browserMessage, setBrowserMessage] = useState('');

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | undefined;
    params.then(({ task_id }) => {
      setTaskId(task_id);
      const loadTask = async () => {
        try {
          const [data, diagnosticData] = await Promise.all([
            apiJson<Task>(`/api/v1/tasks/${task_id}`),
            apiJson<TaskDiagnostics>(`/api/v1/tasks/${task_id}/diagnostics`),
          ]);
          setTask(data);
          setDiagnostics(diagnosticData);
          setMessage('任务已加载');
        } catch (error) {
          const text = error instanceof ApiError || error instanceof Error ? error.message : '任务加载失败';
          setTask(null);
          setDiagnostics(null);
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
    const [data, diagnosticData] = await Promise.all([
      apiJson<Task>(`/api/v1/tasks/${id}`),
      apiJson<TaskDiagnostics>(`/api/v1/tasks/${id}/diagnostics`),
    ]);
    setTask(data);
    setDiagnostics(diagnosticData);
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

  const openBrowserForTask = async () => {
    const sourceUrl = typeof task?.input_config?.source_url === 'string'
      ? task.input_config.source_url
      : '';
    if (!sourceUrl) {
      setBrowserMessage('当前任务没有可打开的来源链接。');
      return;
    }
    setBrowserBusy(true);
    setBrowserMessage('');
    try {
      const result = await apiJson<{ session_id: string; message?: string }>('/api/v1/video-playlists/browser-sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_url: sourceUrl, limit: 1 }),
        timeoutMs: 120000,
      });
      setBrowserSessionId(result.session_id);
      setBrowserMessage(result.message || '浏览器已打开，请完成登录或验证。');
    } catch (error) {
      setBrowserMessage(error instanceof Error ? error.message : '浏览器接管失败');
    } finally {
      setBrowserBusy(false);
    }
  };

  const retryWithBrowserSession = async () => {
    if (!task || !browserSessionId || task.task_type !== 'video_to_markdown') {
      setBrowserMessage('当前任务类型暂不支持直接复用浏览器会话，请使用普通重试。');
      return;
    }
    const sourceUrl = typeof task.input_config?.source_url === 'string'
      ? task.input_config.source_url
      : '';
    setBrowserBusy(true);
    setBrowserMessage('');
    try {
      await apiJson('/api/v1/tasks/video-to-markdown', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: task.project_id,
          source_url: sourceUrl,
          title: typeof task.input_config?.title === 'string' ? task.input_config.title : '',
          auth: { mode: 'browser_session', session_id: browserSessionId },
          input_config: task.input_config || {},
        }),
      });
      setBrowserMessage('已使用当前浏览器会话创建重试任务。');
    } catch (error) {
      setBrowserMessage(error instanceof Error ? error.message : '浏览器会话重试失败');
    } finally {
      setBrowserBusy(false);
    }
  };

  const progress = Math.max(0, Math.min(Number(task?.progress ?? 0), 100));
  const inputSummary = taskInputSummary(task?.input_config);

  return (
    <main style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}>
      <section style={{ maxWidth: 920, margin: '0 auto', padding: '44px 24px' }}>
        <header style={{ paddingBottom: 22, borderBottom: '1px solid #e5e7eb' }}>
          <Link href="/tasks" style={{ color: '#4b5563', fontSize: 14, textDecoration: 'none' }}>返回任务列表</Link>
          <h1 style={{ margin: '10px 0 0', fontSize: 32, lineHeight: 1.2 }}>任务详情</h1>
        </header>

        <section style={{ padding: '14px 0', borderBottom: '1px solid #e5e7eb' }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>长任务诊断</h2>
          <div style={{ marginTop: 8, display: 'flex', gap: 14, flexWrap: 'wrap', color: '#4b5563', fontSize: 13 }}>
            <span>自动刷新: 3 秒</span>
            <span>无响应判定: {formatDurationSeconds(diagnostics?.stale_after_seconds ?? task?.stale_after_seconds)} 无更新</span>
            <span>最近更新: {formatDurationSeconds(diagnostics?.stale_seconds ?? task?.stale_seconds)} 前</span>
            <span>{staleLabel(task)}</span>
            <span>超过无响应判定后，可取消并重试</span>
          </div>
          <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid #e5e7eb', color: '#374151', display: 'grid', gap: 8 }}>
            <div><strong>后端诊断</strong>: {diagnostics?.summary || '等待诊断结果'}</div>
            <div><strong>建议动作</strong>: {actionLabel(diagnostics?.recommended_action)}</div>
            <div><strong>诊断结论</strong>: {(diagnostics?.next_actions || ['继续观察任务进度']).join(' / ')}</div>
          </div>
        </section>

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
              <DetailRow label="任务摘要" value={task.result_summary} />
              <DetailRow label="媒体处理上限" value={isMediaLimitSummary(task.result_summary) ? "已按配置限制媒体处理范围" : "未触发媒体处理上限摘要"} />
              <DetailRow label="错误信息" value={task.error_message} />
              <DetailRow label="创建时间" value={formatTime(task.created_at)} />
              <DetailRow label="更新时间" value={formatTime(task.updated_at)} />
              <DetailRow label="无响应判定" value={`${formatDurationSeconds(task.stale_after_seconds)} 无更新`} />
              <DetailRow label="最近更新" value={`${formatDurationSeconds(task.stale_seconds)} 前`} />
              <DetailRow label="运行状态" value={staleLabel(task)} />
              <DetailRow label="后端诊断" value={diagnostics?.summary} />
              <DetailRow label="建议动作" value={actionLabel(diagnostics?.recommended_action)} />
              <DetailRow label="诊断结论" value={(diagnostics?.next_actions || []).join(' / ')} />
              <div style={{ padding: '12px 0', borderBottom: '1px solid #e5e7eb' }}>
                <div style={{ color: '#6b7280', marginBottom: 8 }}>输入配置</div>
                <div style={{ display: 'grid', gap: 0, borderTop: '1px solid #e5e7eb' }}>
                  <div style={{ padding: '10px 0', borderBottom: '1px solid #e5e7eb', fontWeight: 700 }}>输入摘要</div>
                  {inputSummary.length ? inputSummary.map((row) => (
                    <div key={row.label} style={{ display: 'grid', gridTemplateColumns: '140px minmax(0, 1fr)', gap: 16, padding: '10px 0', borderBottom: '1px solid #e5e7eb' }}>
                      <span style={{ color: '#6b7280' }}>{row.label}</span>
                      <span style={{ wordBreak: 'break-word' }}>{row.value}</span>
                    </div>
                  )) : (
                    <div style={{ padding: '10px 0', borderBottom: '1px solid #e5e7eb', color: '#6b7280' }}>没有额外输入配置</div>
                  )}
                </div>
                <details style={{ marginTop: 12 }}>
                  <summary style={{ cursor: 'pointer', color: '#111827', fontWeight: 700 }}>查看原始输入配置</summary>
                  <pre style={{ margin: '12px 0 0', padding: '12px 0 0', borderTop: '1px solid #e5e7eb', whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: '#374151' }}>
                    {JSON.stringify(task.input_config || {}, null, 2)}
                  </pre>
                </details>
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
                {task.status === 'failed' && typeof task.input_config?.source_url === 'string' ? (
                  <button
                    onClick={() => void openBrowserForTask()}
                    disabled={browserBusy}
                    style={{ ...buttonStyle, borderColor: '#17694f', color: '#17694f', opacity: browserBusy ? 0.7 : 1 }}
                  >
                    {browserBusy && !browserSessionId ? '正在打开浏览器...' : '打开浏览器处理'}
                  </button>
                ) : null}
                {browserSessionId ? (
                  <button
                    onClick={() => void retryWithBrowserSession()}
                    disabled={browserBusy}
                    style={{ ...buttonStyle, background: '#17694f', borderColor: '#17694f', color: '#ffffff', opacity: browserBusy ? 0.7 : 1 }}
                  >
                    {browserBusy ? '正在创建重试...' : '使用当前会话重试'}
                  </button>
                ) : null}
                <Link href="/tasks" style={{ ...buttonStyle, display: 'inline-flex', alignItems: 'center', textDecoration: 'none' }}>
                  返回列表
                </Link>
              </div>
              {browserMessage ? (
                <div style={{ marginTop: 12, color: browserMessage.includes('失败') ? '#b91c1c' : '#17694f' }}>
                  {browserMessage}
                </div>
              ) : null}
            </>
          ) : null}
        </section>
      </section>
    </main>
  );
}
