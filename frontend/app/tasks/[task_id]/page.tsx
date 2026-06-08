"use client";

import { useEffect, useState } from 'react';

import { apiJson } from '../../../lib/api';
import { openDownloadUrl } from '../../../lib/download';

export default function TaskDetailPage({ params }: { params: Promise<{ task_id: string }> }) {
  const [task, setTask] = useState<any>(null);

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | undefined;
    params.then(({ task_id }) => {
      const loadTask = () => {
        apiJson<any>(`/api/v1/tasks/${task_id}`)
          .then(setTask)
          .catch(() => setTask(null));
      };

      loadTask();
      timer = setInterval(loadTask, 3000);
    });
    return () => timer && clearInterval(timer);
  }, [params]);

  return (
    <main style={{ minHeight: '100vh', background: '#f5f7fb', fontFamily: 'Inter, sans-serif', color: '#0f172a' }}>
      <section style={{ maxWidth: 900, margin: '0 auto', padding: '48px 24px' }}>
        <h1 style={{ fontSize: 36, marginBottom: 8 }}>任务详情</h1>
        {!task ? (
          <p>加载中...</p>
        ) : (
          <div style={{ background: '#fff', borderRadius: 18, padding: 24, boxShadow: '0 10px 30px rgba(15,23,42,0.06)' }}>
            <div>任务 ID：{task.id}</div>
            <div>任务类型：{task.task_type}</div>
            <div>状态：{task.status}</div>
            <div>进度：{task.progress}%</div>
            <div>结果：{task.result_summary ?? '-'}</div>
            <div>错误：{task.error_message ?? '-'}</div>
            <button onClick={() => openDownloadUrl(`/api/v1/tasks/${task.id}/download`)} style={{ marginTop: 16, padding: '10px 14px', borderRadius: 10, background: '#0f172a', color: '#fff', border: 0 }}>下载结果</button>
          </div>
        )}
      </section>
    </main>
  );
}
