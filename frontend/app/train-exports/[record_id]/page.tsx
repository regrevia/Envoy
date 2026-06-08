"use client";

import Link from 'next/link';
import { useEffect, useState } from 'react';

import { apiJson } from '../../../lib/api';

type ExportRecord = {
  id: string;
  project_id: string;
  task_id: string;
  export_type: string;
  task_name?: string | null;
  root_dir: string;
  file_list: string;
  created_at?: string | null;
};

const panelStyle = {
  background: '#ffffff',
  border: '1px solid #e5e7eb',
  borderRadius: 8,
  padding: 24,
} as const;

function DetailRow({ label, value }: { label: string; value?: string | null }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '140px minmax(0, 1fr)', gap: 16, padding: '12px 0', borderBottom: '1px solid #e5e7eb' }}>
      <div style={{ color: '#6b7280' }}>{label}</div>
      <div style={{ wordBreak: 'break-all' }}>{value || '-'}</div>
    </div>
  );
}

export default function ExportRecordDetailPage({ params }: { params: Promise<{ record_id: string }> }) {
  const [record, setRecord] = useState<ExportRecord | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    params.then(({ record_id }) => {
      apiJson<ExportRecord[]>('/api/v1/train-exports/history')
        .then((data) => {
          const found = Array.isArray(data) ? data.find((item) => item.id === record_id) : null;
          setRecord(found || null);
        })
        .catch(() => setRecord(null))
        .finally(() => setLoading(false));
    });
  }, [params]);

  return (
    <main style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}>
      <section style={{ maxWidth: 1000, margin: '0 auto', padding: '44px 24px' }}>
        <header style={{ paddingBottom: 22, borderBottom: '1px solid #e5e7eb' }}>
          <Link href="/train-exports" style={{ color: '#4b5563', fontSize: 14, textDecoration: 'none' }}>返回训练导出</Link>
          <h1 style={{ margin: '10px 0 0', fontSize: 32, lineHeight: 1.2 }}>导出记录详情</h1>
        </header>

        <section style={{ ...panelStyle, marginTop: 20 }}>
          {loading ? <div style={{ color: '#6b7280' }}>加载中...</div> : null}
          {!loading && !record ? <div style={{ color: '#b91c1c' }}>未找到导出记录</div> : null}
          {record ? (
            <>
              <DetailRow label="记录 ID" value={record.id} />
              <DetailRow label="项目 ID" value={record.project_id} />
              <DetailRow label="任务 ID" value={record.task_id} />
              <DetailRow label="导出类型" value={record.export_type} />
              <DetailRow label="任务名" value={record.task_name} />
              <DetailRow label="根目录" value={record.root_dir} />
              <DetailRow label="创建时间" value={record.created_at} />
              <div style={{ paddingTop: 14 }}>
                <div style={{ color: '#6b7280', marginBottom: 8 }}>文件列表</div>
                <pre style={{ margin: 0, padding: 14, borderRadius: 8, border: '1px solid #e5e7eb', background: '#f9fafb', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                  {record.file_list}
                </pre>
              </div>
            </>
          ) : null}
        </section>
      </section>
    </main>
  );
}
