"use client";

import { useEffect, useState } from 'react';

import { apiJson } from '../../lib/api';

type GeneratedSample = {
  id: string;
  project_id?: string | null;
  source_unit_id?: string | null;
  sample_type?: string | null;
  input_text?: string | null;
  output_text?: string | null;
  quality_score?: number | null;
  created_at?: string | null;
};

type TaskResponse = {
  id?: string;
  status?: string;
  progress?: number;
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

const supportedExportTypes = new Set(['sft', 'instruction', 'preference', 'rag', 'skill', 'markdown']);

function qualityLabel(score?: number | null) {
  if (score === null || score === undefined) return '-';
  return `${Math.round(score * 100)}%`;
}

export default function GeneratedSamplesPage() {
  const [samples, setSamples] = useState<GeneratedSample[]>([]);
  const [projectId, setProjectId] = useState('');
  const [loading, setLoading] = useState(true);
  const [generatingBatch, setGeneratingBatch] = useState(false);
  const [batchTask, setBatchTask] = useState<TaskResponse | null>(null);
  const [message, setMessage] = useState('就绪');
  const [outputContext, setOutputContext] = useState('');

  const loadSamples = async (nextProjectId = projectId) => {
    setLoading(true);
    try {
      const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
      const effectiveQuery = nextProjectId ? `?project_id=${encodeURIComponent(nextProjectId)}` : query;
      const data = await apiJson<GeneratedSample[]>(`/api/v1/generated-samples${effectiveQuery}`);
      setSamples(data);
    } catch {
      setSamples([]);
      setMessage('样本加载失败，请检查后端服务');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const projectFromQuery = params.get('project_id') || '';
    const outputsFromQuery = params.get('outputs') || '';
    if (projectFromQuery) {
      setProjectId(projectFromQuery);
    }
    if (outputsFromQuery) {
      setOutputContext(outputsFromQuery);
    }
    void loadSamples(projectFromQuery);
  }, []);

  const exportOutputs = outputContext
    .split(',')
    .map((item) => item.trim())
    .filter((item) => supportedExportTypes.has(item));
  const fallbackExportOutputs = ['markdown', 'skill', 'rag'];
  const selectedExportOutputs = exportOutputs.length ? exportOutputs : fallbackExportOutputs;
  const primaryExportType = selectedExportOutputs[0] || 'markdown';
  const outputQueryParam = outputContext ? `outputs=${encodeURIComponent(outputContext)}` : '';
  const semanticHref = projectId ? `/semantic-units?project_id=${projectId}${outputQueryParam ? `&${outputQueryParam}` : ''}` : '/semantic-units';
  const exportHref = projectId ? `/train-exports?project_id=${projectId}&export_type=${primaryExportType}&outputs=${encodeURIComponent(selectedExportOutputs.join(','))}` : '/train-exports';

  const onGenerateProjectSamples = async () => {
    if (!projectId || generatingBatch) {
      setMessage('请先从上传页或语义单元页带入项目 ID');
      return;
    }

    setGeneratingBatch(true);
    setMessage('正在创建样本生成任务...');
    try {
      const task = await apiJson<TaskResponse>('/api/v1/tasks/generate-samples', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId,
          task_type: 'generate_samples',
          input_config: { limit: 50 },
        }),
      });
      setBatchTask(task);
      setMessage(`批量生成任务已创建：${task.status || 'created'}`);
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : '批量生成样本失败';
      setBatchTask({ error_message: errorMessage });
      setMessage(`批量生成样本失败：${errorMessage}`);
    } finally {
      setGeneratingBatch(false);
    }
  };

  return (
    <main style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}>
      <section style={{ maxWidth: 1200, margin: '0 auto', padding: '44px 24px' }}>
        <header style={{ paddingBottom: 22, borderBottom: '1px solid #e5e7eb' }}>
          <h1 style={{ margin: 0, fontSize: 32, lineHeight: 1.2 }}>生成样本</h1>
          <p style={{ margin: '10px 0 0', maxWidth: 720, color: '#4b5563', lineHeight: 1.7 }}>
            {'语义单元 -> 生成样本 -> 训练导出。确认质量后再导出数据集、RAG 语料、Skill 包或 Markdown 报告。'}
          </p>
        </header>

        <section style={{ marginTop: 22, paddingTop: 18, borderTop: '1px solid #e5e7eb' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, paddingBottom: 14, borderBottom: '1px solid #e5e7eb' }}>
            <div>
              <h2 style={{ margin: 0, fontSize: 18 }}>样本列表</h2>
              <p style={{ margin: '6px 0 0', color: '#6b7280', fontSize: 13 }}>
                共 {samples.length} 条样本{projectId ? `；当前项目：${projectId}` : ''}
                {outputContext ? `；生成目标：${outputContext}` : ''}
              </p>
            </div>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
              <button onClick={() => void onGenerateProjectSamples()} disabled={generatingBatch} style={{ ...buttonStyle, background: projectId ? '#111827' : '#f3f4f6', color: projectId ? '#ffffff' : '#6b7280', borderColor: projectId ? '#111827' : '#d1d5db', opacity: generatingBatch ? 0.72 : 1, cursor: generatingBatch ? 'not-allowed' : 'pointer' }}>
                {generatingBatch ? '正在创建...' : '批量生成样本'}
              </button>
              <button onClick={() => void loadSamples()} style={buttonStyle}>刷新</button>
            </div>
          </div>
          <div style={{ padding: '12px 0', borderBottom: '1px solid #e5e7eb', color: '#374151', display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
            <strong style={{ fontSize: 14 }}>状态</strong>
            <span style={{ fontSize: 14 }}>{message}</span>
            {batchTask?.id ? <a href={`/tasks/${batchTask.id}`} style={{ color: '#111827', fontWeight: 700 }}>查看批量任务</a> : null}
            {batchTask?.error_message ? <span style={{ color: '#b91c1c', fontSize: 13 }}>{batchTask.error_message}</span> : null}
          </div>
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', padding: '12px 0', borderBottom: '1px solid #e5e7eb' }}>
            <a href={semanticHref} style={{ color: '#111827', fontWeight: 700 }}>返回语义单元</a>
            <a href={exportHref} style={{ color: '#111827', fontWeight: 700 }}>训练导出</a>
          </div>

          {loading ? <div style={{ padding: '22px 0', color: '#6b7280' }}>正在加载...</div> : null}
          {!loading && samples.length === 0 ? <div style={{ padding: '22px 0', color: '#6b7280' }}>暂无生成样本。请先在素材转换工作台选择语义单元并生成样本。</div> : null}
          {samples.map((sample) => (
            <details key={sample.id} style={{ padding: '16px 0', borderBottom: '1px solid #e5e7eb' }}>
              <summary style={{ cursor: 'pointer', fontWeight: 700 }}>
                {sample.sample_type || '样本'} / {sample.input_text || sample.id}
              </summary>
              <div style={{ marginTop: 10, display: 'flex', gap: 10, flexWrap: 'wrap', color: '#6b7280', fontSize: 13 }}>
                <span>项目: {sample.project_id || '-'}</span>
                <span>来源语义单元: {sample.source_unit_id || '-'}</span>
                <span>质量: {qualityLabel(sample.quality_score)}</span>
              </div>
              <div style={{ marginTop: 14, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 14 }}>
                <section style={{ minWidth: 0 }}>
                  <h3 style={{ margin: 0, fontSize: 14 }}>输入</h3>
                  <pre style={{ margin: '8px 0 0', padding: '12px 0 0', borderTop: '1px solid #e5e7eb', background: '#ffffff', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                    {sample.input_text || '-'}
                  </pre>
                </section>
                <section style={{ minWidth: 0 }}>
                  <h3 style={{ margin: 0, fontSize: 14 }}>输出</h3>
                  <pre style={{ margin: '8px 0 0', padding: '12px 0 0', borderTop: '1px solid #e5e7eb', background: '#ffffff', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                    {sample.output_text || '-'}
                  </pre>
                </section>
              </div>
            </details>
          ))}
        </section>
      </section>
    </main>
  );
}
