"use client";

import { useEffect, useState } from 'react';

import { apiJson } from '../../../lib/api';

export default function ExportRecordDetailPage({ params }: { params: Promise<{ record_id: string }> }) {
  const [record, setRecord] = useState<any>(null);

  useEffect(() => {
    params.then(({ record_id }) => {
      apiJson<any[]>('/api/v1/train-exports/history')
        .then((data) => {
          const found = Array.isArray(data) ? data.find((item) => item.id === record_id) : null;
          setRecord(found || null);
        })
        .catch(() => setRecord(null));
    });
  }, [params]);

  return (
    <main style={{ minHeight: '100vh', background: '#f5f7fb', fontFamily: 'Inter, sans-serif', color: '#0f172a' }}>
      <section style={{ maxWidth: 1000, margin: '0 auto', padding: '48px 24px' }}>
        <h1 style={{ fontSize: 36, marginBottom: 8 }}>导出历史详情</h1>
        {!record ? <p>加载中或未找到记录</p> : (
          <div style={{ background: '#fff', borderRadius: 18, padding: 24, boxShadow: '0 10px 30px rgba(15,23,42,0.06)' }}>
            <div>记录 ID：{record.id}</div>
            <div>项目 ID：{record.project_id}</div>
            <div>任务 ID：{record.task_id}</div>
            <div>导出类型：{record.export_type}</div>
            <div>任务名：{record.task_name ?? '-'}</div>
            <div>根目录：{record.root_dir}</div>
            <pre style={{ marginTop: 12 }}>{record.file_list}</pre>
          </div>
        )}
      </section>
    </main>
  );
}
