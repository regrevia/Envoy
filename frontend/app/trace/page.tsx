"use client";

import { Suspense, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';

import { ApiError, apiJson } from '../../lib/api';

const inputStyle = {
  width: '100%',
  padding: '11px 12px',
  borderRadius: 6,
  border: '1px solid #d1d5db',
  background: '#ffffff',
  color: '#111827',
} as const;

const buttonStyle = {
  minHeight: 40,
  padding: '0 14px',
  borderRadius: 6,
  border: 0,
  background: '#111827',
  color: '#ffffff',
  fontWeight: 600,
  cursor: 'pointer',
} as const;

type TraceLink = {
  id?: string;
  source_type?: string;
  source_id?: string;
  target_type?: string;
  target_id?: string;
  relation_type?: string;
  direction?: string;
  depth?: number;
  error?: string;
};

function directionLabel(direction?: string) {
  if (direction === 'incoming') return '上游来源';
  if (direction === 'outgoing') return '下游去向';
  return '直接关系';
}

function relationTypeLabel(relationType?: string) {
  const labels: Record<string, string> = {
    derived_from: '派生自',
    supports: '支撑',
    exported_to: '导出为',
  };
  return relationType ? labels[relationType] || relationType : '-';
}

function entityTypeLabel(type?: string) {
  const labels: Record<string, string> = {
    asset: '素材',
    semantic_unit: '语义单元',
    generated_sample: '生成样本',
    processing_task: '处理任务',
    artifact: 'AI 产物',
  };
  return type ? labels[type] || type : '-';
}

function TraceWorkspace() {
  const searchParams = useSearchParams();
  const [sourceType, setSourceType] = useState('asset');
  const [sourceId, setSourceId] = useState('');
  const [result, setResult] = useState<TraceLink[]>([]);
  const [message, setMessage] = useState('输入来源 ID 后查询相关记录');

  const queryTrace = async (typeValue = sourceType, idValue = sourceId) => {
    const trimmedId = idValue.trim();
    if (!trimmedId) {
      setMessage('请输入来源 ID');
      setResult([]);
      return;
    }

    setMessage('正在查询追溯关系...');
    try {
      const data = await apiJson<TraceLink[]>(`/api/v1/trace/${typeValue}/${trimmedId}`);
      setResult(data);
      setMessage(`找到 ${data.length} 条追溯关系`);
    } catch (error) {
      const text = error instanceof ApiError || error instanceof Error ? error.message : '追溯查询失败';
      setResult([{ error: text }]);
      setMessage(`追溯查询失败：${text}`);
    }
  };

  useEffect(() => {
    const typeFromQuery = searchParams.get('type');
    const idFromQuery = searchParams.get('id');
    if (typeFromQuery) setSourceType(typeFromQuery);
    if (idFromQuery) {
      setSourceId(idFromQuery);
      setMessage('已从链接带入追溯对象，正在自动查询');
      if (typeFromQuery) {
        void queryTrace(typeFromQuery, idFromQuery);
      } else {
        void queryTrace(sourceType, idFromQuery);
      }
    }
  // Run only when URL parameters change; queryTrace reads explicit values here.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  return (
    <main style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}>
      <section style={{ maxWidth: 960, margin: '0 auto', padding: '44px 24px' }}>
        <header style={{ paddingBottom: 22, borderBottom: '1px solid #e5e7eb' }}>
          <h1 style={{ margin: 0, fontSize: 32, lineHeight: 1.2 }}>追溯审查</h1>
          <p style={{ margin: '10px 0 0', maxWidth: 720, color: '#4b5563', lineHeight: 1.7 }}>
            从素材、语义单元、生成样本或处理任务出发，查看它们和导出产物之间的来源关系。
          </p>
          <div style={{ marginTop: 16, padding: '12px 0', borderTop: '1px solid #e5e7eb', color: '#374151', fontWeight: 700 }}>
            {'素材 -> 语义单元 -> 生成样本 -> AI 产物'}
          </div>
        </header>

        <div style={{ marginTop: 18, padding: '12px 0', borderBottom: '1px solid #e5e7eb', color: '#374151', display: 'flex', gap: 10, alignItems: 'center' }}>
          <strong style={{ fontSize: 14 }}>状态</strong>
          <span style={{ fontSize: 14 }}>{message}</span>
        </div>

        <section style={{ marginTop: 20, padding: '18px 0 20px', borderTop: '1px solid #e5e7eb', borderBottom: '1px solid #e5e7eb', display: 'grid', gap: 14 }}>
          <div>
            <h2 style={{ margin: 0, fontSize: 18 }}>查询追溯</h2>
            <p style={{ margin: '8px 0 0', color: '#6b7280', lineHeight: 1.6 }}>
              输入任一节点 ID，检查它是否已经串联到核心转换链路和最终 AI 产物。
            </p>
          </div>
          <label style={{ display: 'grid', gap: 6 }}>
            <span style={{ color: '#4b5563', fontSize: 13 }}>来源类型</span>
            <select value={sourceType} onChange={(event) => setSourceType(event.target.value)} style={inputStyle}>
              <option value="asset">素材 asset</option>
              <option value="semantic_unit">语义单元 semantic_unit</option>
              <option value="processing_task">处理任务 processing_task</option>
              <option value="generated_sample">生成样本 generated_sample</option>
              <option value="artifact">导出产物 artifact</option>
            </select>
          </label>
          <label style={{ display: 'grid', gap: 6 }}>
            <span style={{ color: '#4b5563', fontSize: 13 }}>来源 ID</span>
            <input placeholder="输入素材、语义单元、任务或样本 ID" value={sourceId} onChange={(event) => setSourceId(event.target.value)} style={inputStyle} />
          </label>
          <button onClick={() => void queryTrace()} style={{ ...buttonStyle, width: 'fit-content' }}>查询追溯</button>
        </section>

        <section style={{ marginTop: 20, padding: '18px 0 0', borderTop: '1px solid #e5e7eb' }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>追溯结果</h2>
          <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid #e5e7eb', display: 'grid', gap: 0 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '120px minmax(0, 1fr) minmax(0, 1fr)', gap: 14, padding: '0 0 10px', color: '#6b7280', fontSize: 13, fontWeight: 700 }}>
              <span>链路层级</span>
              <span>上游来源</span>
              <span>下游去向</span>
            </div>
            {result.length === 0 ? <div style={{ padding: '16px 0', color: '#6b7280', borderTop: '1px solid #e5e7eb' }}>暂无追溯关系。</div> : null}
            {result.map((link, index) => (
              <div key={link.id || `${link.source_id}-${link.target_id}-${index}`} style={{ padding: '14px 0', borderTop: '1px solid #e5e7eb' }}>
                {link.error ? (
                  <div style={{ color: '#b91c1c', wordBreak: 'break-word' }}>{link.error}</div>
                ) : (
                  <>
                    <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                      <strong>第 {link.depth || 1} 层</strong>
                      <span style={{ color: link.direction === 'incoming' ? '#1d4ed8' : '#166534', fontSize: 13 }}>{directionLabel(link.direction)}</span>
                      <span style={{ color: '#6b7280', fontSize: 13 }}>{relationTypeLabel(link.relation_type)}</span>
                    </div>
                    <div style={{ marginTop: 8, display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: 14, color: '#374151', lineHeight: 1.6 }}>
                      <div>
                        <div style={{ color: '#6b7280', fontSize: 13 }}>来源</div>
                        <div style={{ wordBreak: 'break-word' }}>{entityTypeLabel(link.source_type)}: {link.source_id || '-'}</div>
                      </div>
                      <div>
                        <div style={{ color: '#6b7280', fontSize: 13 }}>去向</div>
                        <div style={{ wordBreak: 'break-word' }}>{entityTypeLabel(link.target_type)}: {link.target_id || '-'}</div>
                      </div>
                    </div>
                  </>
                )}
              </div>
            ))}
          </div>
          <h3 style={{ margin: '20px 0 0', fontSize: 15 }}>原始 JSON</h3>
          <pre style={{ marginTop: 10, padding: '12px 0 0', borderTop: '1px solid #e5e7eb', background: '#ffffff', whiteSpace: 'pre-wrap', overflowX: 'auto' }}>
            {JSON.stringify(result, null, 2)}
          </pre>
        </section>
      </section>
    </main>
  );
}

export default function TracePage() {
  return (
    <Suspense fallback={<main style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}><section style={{ maxWidth: 960, margin: '0 auto', padding: '44px 24px' }}>加载追溯工作台...</section></main>}>
      <TraceWorkspace />
    </Suspense>
  );
}
