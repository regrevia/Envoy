"use client";

import Link from 'next/link';
import { useEffect, useState } from 'react';

import { apiJson } from '../../lib/api';

type SemanticUnit = {
  id: string;
  project_id?: string | null;
  asset_id?: string | null;
  segment_id?: string | null;
  unit_type?: string | null;
  title?: string | null;
  content?: string | null;
  labels?: Record<string, unknown> | null;
  confidence?: number | null;
  created_at?: string | null;
};

type TaskResponse = {
  id?: string;
  status?: string;
  task_type?: string;
  progress?: number;
  result_summary?: string | null;
  error_message?: string | null;
};

const pipelineSteps = [
  { title: '多媒体/长文本', detail: '视频、音频、PDF、图片、Markdown、网页摘录和长文本统一进入项目素材池。' },
  { title: '预处理转换', detail: '执行抽帧、转写、OCR、文本切块和元数据清洗，避免长任务卡住主界面。' },
  { title: '语义单元', detail: '沉淀场景、规则、概念、文本块和标签，作为后续生成的可追溯中间层。' },
  { title: '生成样本', detail: '从单个语义单元或项目批量生成可审查的训练样本。' },
  { title: 'Markdown / Skill / RAG', detail: '导出为 md、skill 包、RAG 语料和训练数据集。' },
];

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

function formatCreatedAt(value?: string | null) {
  if (!value) {
    return '-';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString('zh-CN', { hour12: false });
}

function labelSummary(labels?: Record<string, unknown> | null) {
  const entries = Object.entries(labels || {}).slice(0, 4);
  if (entries.length === 0) {
    return '-';
  }
  return entries.map(([key, value]) => `${key}: ${String(value)}`).join('；');
}

export default function SemanticUnitsPage() {
  const [units, setUnits] = useState<SemanticUnit[]>([]);
  const [loading, setLoading] = useState(true);
  const [generatingUnitId, setGeneratingUnitId] = useState('');
  const [message, setMessage] = useState('就绪');
  const [generationResult, setGenerationResult] = useState<TaskResponse | null>(null);

  const loadUnits = async () => {
    setLoading(true);
    try {
      const data = await apiJson<SemanticUnit[]>('/api/v1/semantic-units');
      setUnits(data);
    } catch {
      setUnits([]);
      setMessage('语义单元加载失败，请检查后端服务');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadUnits();
  }, []);

  const generateSample = async (unit: SemanticUnit) => {
    if (!unit.id || !unit.project_id || generatingUnitId) {
      setMessage('请选择带有项目上下文的语义单元');
      return;
    }

    setGeneratingUnitId(unit.id);
    setMessage('正在创建样本生成任务...');
    try {
      const data = await apiJson<TaskResponse>('/api/v1/tasks/generate-samples', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: unit.project_id,
          asset_id: unit.asset_id || undefined,
          task_type: 'generate_samples',
          input_config: { source_unit_id: unit.id },
        }),
      });
      setGenerationResult(data);
      setMessage(`样本生成任务已创建：${data.status || 'created'}`);
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : '样本生成失败';
      setGenerationResult({ error_message: errorMessage });
      setMessage(`样本生成失败：${errorMessage}`);
    } finally {
      setGeneratingUnitId('');
    }
  };

  return (
    <main style={{ minHeight: '100vh', background: '#ffffff', fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}>
      <section className="workspace-shell">
        <header style={{ paddingBottom: 22, borderBottom: '1px solid #e5e7eb' }}>
          <div style={{ color: '#6b7280', fontSize: 14, marginBottom: 8 }}>Envoy 核心链路 / 这是第 3 步</div>
          <h1 style={{ margin: 0, fontSize: 32, lineHeight: 1.2 }}>素材转换工作台</h1>
          <p style={{ margin: '10px 0 0', maxWidth: 720, color: '#4b5563', lineHeight: 1.7 }}>
            这里是语义单元审查页，不是首页。先从首页创建转换任务，再上传素材，最后在这里选择生成内容并进入 Markdown、Skill、RAG 或训练导出。
          </p>
        </header>

        <section aria-label="转换链路" style={{ borderBottom: '1px solid #e5e7eb' }}>
          <div className="pipeline-grid">
            {pipelineSteps.map((step, index) => (
              <div key={step.title} style={{ padding: '16px 0' }}>
                <div style={{ color: '#6b7280', fontSize: 12 }}>0{index + 1}</div>
                <div style={{ fontWeight: 700, marginTop: 6 }}>{step.title}</div>
                <div style={{ color: '#4b5563', fontSize: 13, lineHeight: 1.6, marginTop: 6 }}>{step.detail}</div>
              </div>
            ))}
          </div>
        </section>

        <nav aria-label="核心操作" className="quick-actions">
          <Link href="/" style={{ ...buttonStyle, display: 'inline-flex', alignItems: 'center', textDecoration: 'none', background: '#111827', color: '#ffffff', borderColor: '#111827' }}>返回首页创建转换任务</Link>
          <Link href="/upload" style={{ ...buttonStyle, display: 'inline-flex', alignItems: 'center', textDecoration: 'none', background: '#111827', color: '#ffffff', borderColor: '#111827' }}>上传素材</Link>
          <Link href="/tasks" style={{ ...buttonStyle, display: 'inline-flex', alignItems: 'center', textDecoration: 'none' }}>转换任务</Link>
          <Link href="/generated-samples" style={{ ...buttonStyle, display: 'inline-flex', alignItems: 'center', textDecoration: 'none' }}>样本审查</Link>
          <Link href="/train-exports" style={{ ...buttonStyle, display: 'inline-flex', alignItems: 'center', textDecoration: 'none' }}>训练导出</Link>
          <Link href="/artifacts" style={{ ...buttonStyle, display: 'inline-flex', alignItems: 'center', textDecoration: 'none' }}>成果文件</Link>
        </nav>

        <div style={{ padding: '14px 0', borderBottom: '1px solid #e5e7eb', color: '#374151', display: 'flex', gap: 10, alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <strong style={{ fontSize: 14 }}>状态</strong>
            <span style={{ fontSize: 14 }}>{message}</span>
          </div>
          <button onClick={() => void loadUnits()} style={buttonStyle}>刷新语义单元</button>
        </div>

        <section style={{ marginTop: 22, borderTop: '1px solid #e5e7eb' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, padding: '16px 0', borderBottom: '1px solid #e5e7eb' }}>
            <div>
              <h2 style={{ margin: 0, fontSize: 18 }}>语义单元</h2>
              <p style={{ margin: '6px 0 0', color: '#6b7280', fontSize: 13 }}>当前 {units.length} 条，可继续生成样本或进入导出链路</p>
            </div>
            <Link href="/upload" style={{ ...buttonStyle, display: 'inline-flex', alignItems: 'center', textDecoration: 'none' }}>新增多模态素材</Link>
          </div>

          {loading ? <div style={{ padding: '22px 0', color: '#6b7280' }}>正在加载...</div> : null}
          {!loading && units.length === 0 ? <div style={{ padding: '22px 0', color: '#6b7280' }}>还没有语义单元，请先上传素材并启动预处理转换。</div> : null}
          {units.map((unit) => (
            <article key={unit.id} style={{ padding: '18px 0', borderBottom: '1px solid #e5e7eb' }}>
              <div className="unit-row">
                <div>
                  <div style={{ fontWeight: 700 }}>
                    {unit.unit_type || '-'} / {unit.title || unit.id}
                  </div>
                  <div style={{ marginTop: 8, display: 'flex', gap: 10, flexWrap: 'wrap', color: '#6b7280', fontSize: 13 }}>
                    <span>项目: {unit.project_id || '-'}</span>
                    <span>素材: {unit.asset_id || '-'}</span>
                    <span>分片: {unit.segment_id || '-'}</span>
                    <span>创建: {formatCreatedAt(unit.created_at)}</span>
                    <span>置信度: {unit.confidence ?? '-'}</span>
                  </div>
                  <div style={{ marginTop: 8, color: '#6b7280', fontSize: 13, lineHeight: 1.6 }}>
                    标签摘要：{labelSummary(unit.labels)}
                  </div>
                </div>
                <div className="unit-actions">
                  <button
                    onClick={() => void generateSample(unit)}
                    disabled={Boolean(generatingUnitId)}
                    style={{ ...buttonStyle, background: generatingUnitId === unit.id ? '#6b7280' : '#111827', color: '#ffffff', borderColor: generatingUnitId === unit.id ? '#6b7280' : '#111827', opacity: generatingUnitId && generatingUnitId !== unit.id ? 0.6 : 1, cursor: generatingUnitId ? 'not-allowed' : 'pointer' }}
                  >
                    {generatingUnitId === unit.id ? '正在启动...' : '生成样本'}
                  </button>
                  <Link href="/generated-samples" style={{ ...buttonStyle, display: 'inline-flex', alignItems: 'center', textDecoration: 'none' }}>查看样本</Link>
                  <Link href="/train-exports" style={{ ...buttonStyle, display: 'inline-flex', alignItems: 'center', textDecoration: 'none' }}>导出 md/Skill/RAG</Link>
                </div>
              </div>
              <pre style={{ margin: '14px 0 0', padding: '12px 0 0', borderTop: '1px solid #e5e7eb', background: '#ffffff', whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: '#374151' }}>
                {unit.content || '-'}
              </pre>
              <pre style={{ margin: '10px 0 0', padding: '10px 0 0', borderTop: '1px solid #e5e7eb', background: '#ffffff', whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: '#6b7280' }}>
                {JSON.stringify(unit.labels || {}, null, 2)}
              </pre>
            </article>
          ))}
        </section>

        {generationResult ? (
          <section style={{ marginTop: 22, padding: '16px 0 0', borderTop: '1px solid #e5e7eb' }}>
            <h2 style={{ margin: 0, fontSize: 18 }}>生成任务</h2>
            <pre style={{ margin: '14px 0 0', padding: '12px 0 0', borderTop: '1px solid #e5e7eb', background: '#ffffff', whiteSpace: 'pre-wrap', overflowX: 'auto' }}>
              {JSON.stringify(generationResult, null, 2)}
            </pre>
          </section>
        ) : null}
      </section>
      <style jsx>{`
        .workspace-shell {
          max-width: 1200px;
          margin: 0 auto;
          padding: 44px 24px;
        }

        .pipeline-grid {
          display: grid;
          grid-template-columns: repeat(5, minmax(0, 1fr));
          column-gap: 22px;
        }

        .pipeline-grid > div + div {
          border-left: 1px solid #e5e7eb;
          padding-left: 22px;
        }

        .quick-actions {
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          padding: 14px 0;
          border-bottom: 1px solid #e5e7eb;
        }

        .unit-row {
          display: grid;
          grid-template-columns: minmax(0, 1fr) minmax(260px, auto);
          gap: 16px;
          align-items: start;
        }

        .unit-actions {
          display: flex;
          gap: 10px;
          flex-wrap: wrap;
          justify-content: flex-end;
          max-width: 100%;
        }

        @media (max-width: 900px) {
          .pipeline-grid {
            grid-template-columns: 1fr;
          }

          .pipeline-grid > div + div {
            border-left: 0;
            border-top: 1px solid #e5e7eb;
            padding-left: 0;
          }

          .unit-row {
            grid-template-columns: 1fr;
          }

          .unit-actions {
            justify-content: flex-start;
          }
        }
      `}</style>
    </main>
  );
}
