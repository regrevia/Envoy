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

function DetailRow({ label, value }: { label: string; value?: string | null }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '140px minmax(0, 1fr)', gap: 16, padding: '12px 0', borderBottom: '1px solid #e0e0e0' }}>
      <div style={{ color: '#7a7a7a' }}>{label}</div>
      <div style={{ wordBreak: 'break-all' }}>{value || '-'}</div>
    </div>
  );
}

function sourceNameFromPath(path: string) {
  const match = path.match(/\/sources\/([^/]+)\/source\.md$/);
  return match?.[1] || path.split('/').slice(-2, -1)[0] || 'source';
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

  const parsedFiles = (() => {
    if (!record?.file_list) return [];
    try {
      const parsed = JSON.parse(record.file_list);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return record.file_list.split(',').map((item) => item.trim()).filter(Boolean);
    }
  })();

  const markdownFiles = parsedFiles.filter((item) => typeof item === 'string' && item.endsWith('.md'));
  const indexFile = markdownFiles.find((item) => item.endsWith('/index.md') || item.endsWith('index.md'));
  const overviewFile = markdownFiles.find((item) => item.includes('/overview/00-project-overview.md'));
  const manifestFile = parsedFiles.find((item) => typeof item === 'string' && item.endsWith('/manifest.json'));
  const sourceFiles = markdownFiles.filter((item) => item.includes('/sources/') && item.endsWith('/source.md'));
  const chunkFiles = markdownFiles.filter((item) => item.includes('/sources/') && item.includes('/chunks/'));
  const sourceTrees = sourceFiles.map((sourceFile) => {
    const sourceDir = sourceFile.replace(/\/source\.md$/, '');
    return {
      sourceFile,
      sourceName: sourceNameFromPath(sourceFile),
      chunks: chunkFiles.filter((item) => item.startsWith(`${sourceDir}/chunks/`)),
    };
  });

  return (
    <main style={{ minHeight: '100vh', background: '#f5f5f7', fontFamily: 'SF Pro Text, system-ui, -apple-system, sans-serif', color: '#1d1d1f' }}>
      <div style={{ height: 44, background: '#000000', color: '#ffffff', display: 'flex', alignItems: 'center', padding: '0 24px', fontSize: 12 }}>
        Envoy · Export
      </div>
      <section style={{ maxWidth: 1100, margin: '0 auto', padding: '44px 24px 72px' }}>
        <header style={{ paddingBottom: 24, borderBottom: '1px solid #e0e0e0' }}>
          <Link href="/train-exports" style={{ color: '#0066cc', fontSize: 14, textDecoration: 'none' }}>返回训练导出</Link>
          <h1 style={{ margin: '12px 0 0', fontSize: 40, lineHeight: 1.1, letterSpacing: '-0.28px', fontWeight: 600 }}>导出记录详情</h1>
        </header>

        <section style={{ marginTop: 22 }}>
          {loading ? <div style={{ color: '#7a7a7a' }}>加载中...</div> : null}
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
              <div style={{ padding: '14px 0', borderBottom: '1px solid #e0e0e0' }}>
                <div style={{ color: '#7a7a7a', marginBottom: 8 }}>文件列表</div>
                <pre style={{ margin: 0, padding: '12px 0 0', borderTop: '1px solid #e0e0e0', whiteSpace: 'pre-wrap', wordBreak: 'break-all', fontSize: 12, lineHeight: 1.5 }}>
                  {record.file_list}
                </pre>
              </div>
              {parsedFiles.length ? (
                <section style={{ marginTop: 28, paddingTop: 24, borderTop: '1px solid #e0e0e0' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'baseline', flexWrap: 'wrap' }}>
                    <h2 style={{ margin: 0, fontSize: 28, lineHeight: 1.14, letterSpacing: '-0.2px', fontWeight: 600 }}>Markdown 目录树</h2>
                    <span style={{ color: '#7a7a7a', fontSize: 14 }}>来源 {sourceFiles.length} 个 · 分片 {chunkFiles.length} 个</span>
                  </div>
                  <DetailRow label="Index" value={indexFile} />
                  <DetailRow label="Overview" value={overviewFile} />
                  <DetailRow label="Manifest" value={manifestFile} />
                  <DetailRow label="来源文件数" value={sourceFiles.length ? String(sourceFiles.length) : '-'} />
                  <DetailRow label="分片文件数" value={chunkFiles.length ? String(chunkFiles.length) : '-'} />
                  {sourceTrees.length ? (
                    <div style={{ marginTop: 18 }}>
                      {sourceTrees.map((source, sourceIndex) => (
                        <section key={`${source.sourceFile || sourceIndex}`} style={{ padding: '18px 0', borderTop: '1px solid #e0e0e0' }}>
                          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', gap: 16, alignItems: 'baseline' }}>
                            <div>
                              <h3 style={{ margin: 0, fontSize: 21, lineHeight: 1.19, fontWeight: 600 }}>{source.sourceName}</h3>
                              <div style={{ marginTop: 5, color: '#7a7a7a', fontSize: 13, wordBreak: 'break-all' }}>{source.sourceFile}</div>
                            </div>
                            <div style={{ color: '#0066cc', fontSize: 14 }}>{source.chunks.length} chunks</div>
                          </div>
                          <div style={{ marginTop: 14, color: '#7a7a7a', fontSize: 13 }}>分片索引</div>
                          <div style={{ marginTop: 8, borderTop: '1px solid #f0f0f0' }}>
                            {source.chunks.slice(0, 24).map((item, index) => (
                              <div key={`${item || index}`} style={{ display: 'grid', gridTemplateColumns: '44px minmax(0, 1fr)', gap: 12, padding: '12px 0', borderBottom: '1px solid #f0f0f0' }}>
                                <div style={{ color: '#7a7a7a', fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>{String(index + 1).padStart(2, '0')}</div>
                                <div>
                                  <div style={{ fontWeight: 600 }}>{item.split('/').pop() || '-'}</div>
                                  <div style={{ marginTop: 4, color: '#7a7a7a', fontSize: 13, wordBreak: 'break-all' }}>{item}</div>
                                </div>
                              </div>
                            ))}
                          </div>
                        </section>
                      ))}
                    </div>
                  ) : null}
                </section>
              ) : null}
            </>
          ) : null}
        </section>
      </section>
    </main>
  );
}
