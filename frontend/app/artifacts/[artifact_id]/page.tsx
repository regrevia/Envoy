"use client";

import Link from 'next/link';
import { useEffect, useState } from 'react';

import { apiJson } from '../../../lib/api';
import { openDownloadUrl } from '../../../lib/download';

type Artifact = {
  id: string;
  project_id?: string | null;
  artifact_type?: string | null;
  name?: string | null;
  file_path?: string | null;
  version?: string | null;
  created_at?: string | null;
  manifest?: ArtifactManifest | null;
};

type ArtifactManifest = {
  schema_version?: string | null;
  source_task_id?: string | null;
  task_name?: string | null;
  project_id?: string | null;
  row_count?: number | null;
  source_counts?: Record<string, number> | null;
  unit_type_counts?: Record<string, number> | null;
  media_processing_limits?: Array<{
    segment_seconds?: number | null;
    max_segment_count?: number | null;
    max_input_seconds?: number | null;
    unit_count?: number | null;
  }> | null;
  ollama_status?: string | null;
  ollama_error?: string | null;
  ollama_parse_status?: string | null;
  ollama_parse_error?: string | null;
  ollama_truncated?: boolean | null;
  ollama_original_prompt_chars?: number | null;
  ollama_sent_prompt_chars?: number | null;
  ollama_max_prompt_chars?: number | null;
  llm_summary?: string | null;
  llm_usage?: string | null;
  llm_keywords?: string[] | null;
  llm_risks?: string[] | null;
  manifest_path?: string | null;
  read_status?: string | null;
};

function DetailRow({ label, value }: { label: string; value?: string | null }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '140px minmax(0, 1fr)', gap: 16, padding: '12px 0', borderBottom: '1px solid #e5e7eb' }}>
      <div style={{ color: '#6b7280' }}>{label}</div>
      <div style={{ wordBreak: 'break-all' }}>{value || '-'}</div>
    </div>
  );
}

function manifestValue(value?: string | number | string[] | null) {
  if (Array.isArray(value)) return value.length ? value.join('、') : '-';
  if (value === undefined || value === null || value === '') return '-';
  return String(value);
}

function booleanValue(value?: boolean | null) {
  if (value === true) return '是';
  if (value === false) return '否';
  return '-';
}

const sourceLabels: Record<string, string> = {
  text_asset: '长文本',
  document_text: '文档文本',
  image_text: '图片 OCR',
  audio_transcript: '音频转写',
  media_transcript: '视频转写',
  media_ocr: '画面 OCR',
  media_asset: '视频元数据',
};

const unitTypeLabels: Record<string, string> = {
  text: '文本',
  document: '文档',
  image: '图片',
  audio: '音频',
  transcript: '转写',
  ocr: 'OCR',
  scene: '场景',
  rule: '规则',
};

function countsValue(counts?: Record<string, number> | null, labels: Record<string, string> = {}) {
  if (!counts || Object.keys(counts).length === 0) return '-';
  return Object.entries(counts)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, count]) => `${labels[key] || key}: ${count}`)
    .join('、');
}

function mediaLimitsValue(limits?: ArtifactManifest['media_processing_limits']) {
  if (!limits || limits.length === 0) return '-';
  return limits
    .map((item) => `分段 ${item.segment_seconds ?? '-'} 秒，最多 ${item.max_segment_count ?? '-'} 段，最大 ${item.max_input_seconds ?? '-'} 秒，来源 ${item.unit_count ?? '-'} 条`)
    .join('；');
}

export default function ArtifactDetailPage({ params }: { params: Promise<{ artifact_id: string }> }) {
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    params.then(({ artifact_id }) => {
      apiJson<Artifact>(`/api/v1/artifacts/${artifact_id}`)
        .then(setArtifact)
        .catch(() => setArtifact(null))
        .finally(() => setLoading(false));
    });
  }, [params]);

  return (
    <main style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}>
      <section style={{ maxWidth: 920, margin: '0 auto', padding: '44px 24px' }}>
        <header style={{ paddingBottom: 22, borderBottom: '1px solid #e5e7eb' }}>
          <Link href="/artifacts" style={{ color: '#4b5563', fontSize: 14, textDecoration: 'none' }}>返回产物中心</Link>
          <h1 style={{ margin: '10px 0 0', fontSize: 32, lineHeight: 1.2 }}>产物详情</h1>
        </header>

        <section style={{ marginTop: 20, paddingTop: 2, borderTop: '1px solid #e5e7eb' }}>
          {loading ? <div style={{ color: '#6b7280' }}>加载中...</div> : null}
          {!loading && !artifact ? <div style={{ color: '#b91c1c' }}>未找到产物</div> : null}
          {artifact ? (
            <>
              <DetailRow label="产物 ID" value={artifact.id} />
              <DetailRow label="项目 ID" value={artifact.project_id} />
              <DetailRow label="类型" value={artifact.artifact_type} />
              <DetailRow label="名称" value={artifact.name} />
              <DetailRow label="版本" value={artifact.version} />
              <DetailRow label="文件路径" value={artifact.file_path} />
              <DetailRow label="创建时间" value={artifact.created_at} />
              {artifact.manifest ? (
                <section style={{ marginTop: 18, paddingTop: 18, borderTop: '1px solid #e5e7eb' }}>
                  <h2 style={{ margin: 0, fontSize: 18 }}>质量元数据</h2>
                  <p style={{ margin: '6px 0 10px', color: '#6b7280', fontSize: 13 }}>来自导出 Manifest，用于审查 AI 素材来源、Ollama 状态和生成质量。</p>
                  <DetailRow label="Manifest 路径" value={artifact.manifest.manifest_path} />
                  <DetailRow label="Schema" value={artifact.manifest.schema_version} />
                  <DetailRow label="来源任务" value={artifact.manifest.source_task_id} />
                  <DetailRow label="任务名称" value={artifact.manifest.task_name} />
                  <DetailRow label="行数" value={manifestValue(artifact.manifest.row_count)} />
                  <DetailRow label="素材来源统计" value={countsValue(artifact.manifest.source_counts, sourceLabels)} />
                  <DetailRow label="语义类型统计" value={countsValue(artifact.manifest.unit_type_counts, unitTypeLabels)} />
                  <DetailRow label="媒体处理上限" value={mediaLimitsValue(artifact.manifest.media_processing_limits)} />
                  <DetailRow label="Ollama 状态" value={artifact.manifest.ollama_status} />
                  <DetailRow label="Ollama 错误" value={artifact.manifest.ollama_error} />
                  <DetailRow label="结构解析" value={artifact.manifest.ollama_parse_status} />
                  <DetailRow label="解析错误" value={artifact.manifest.ollama_parse_error} />
                  <DetailRow label="Ollama 截断" value={booleanValue(artifact.manifest.ollama_truncated)} />
                  <DetailRow label="提示词长度" value={manifestValue(artifact.manifest.ollama_original_prompt_chars)} />
                  <DetailRow label="发送长度" value={manifestValue(artifact.manifest.ollama_sent_prompt_chars)} />
                  <DetailRow label="最大长度" value={manifestValue(artifact.manifest.ollama_max_prompt_chars)} />
                  <DetailRow label="AI 摘要" value={artifact.manifest.llm_summary} />
                  <DetailRow label="用途" value={artifact.manifest.llm_usage} />
                  <DetailRow label="关键词" value={manifestValue(artifact.manifest.llm_keywords)} />
                  <DetailRow label="风险" value={manifestValue(artifact.manifest.llm_risks)} />
                </section>
              ) : (
                <section style={{ marginTop: 18, paddingTop: 18, borderTop: '1px solid #e5e7eb', color: '#6b7280' }}>
                  暂无 Manifest 质量元数据
                </section>
              )}
              <div style={{ marginTop: 18, paddingTop: 16, borderTop: '1px solid #e5e7eb', display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                <Link href={`/trace?type=artifact&id=${artifact.id}`} style={{ minHeight: 40, padding: '0 14px', borderRadius: 6, background: '#ffffff', color: '#111827', border: '1px solid #d1d5db', fontWeight: 600, display: 'inline-flex', alignItems: 'center', textDecoration: 'none' }}>
                  查看来源链路
                </Link>
                <button onClick={() => openDownloadUrl(`/api/v1/artifacts/${artifact.id}/download`)} style={{ minHeight: 40, padding: '0 14px', borderRadius: 6, background: '#111827', color: '#fff', border: 0, fontWeight: 600, cursor: 'pointer' }}>
                  下载文件
                </button>
              </div>
            </>
          ) : null}
        </section>
      </section>
    </main>
  );
}
