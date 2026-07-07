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
  chunk_count?: number | null;
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
  encoding?: string | null;
  files?: {
    index?: string | null;
    overview?: string | null;
    manifest?: string | null;
    sources?: string[] | null;
    chunks?: string[] | null;
    markdown?: string | null;
    corpus?: string | null;
  } | null;
  file_tree?: {
    root?: string | null;
    index?: string | null;
    overview?: string | null;
    manifest?: string | null;
    sources_dir?: string | null;
    sources?: Array<{
      source_type?: string | null;
      source_name?: string | null;
      source_path?: string | null;
      relative_path?: string | null;
      chunks_dir?: string | null;
      chunks?: Array<{
        chunk_id?: string | null;
        title?: string | null;
        summary?: string | null;
        reference_text?: string | null;
        path?: string | null;
        relative_path?: string | null;
        split_part?: number | null;
        split_total?: number | null;
      }> | null;
    }> | null;
  } | null;
  sources?: Array<{
    source_type?: string | null;
    source_name?: string | null;
    source_path?: string | null;
  }> | null;
  chunks?: Array<{
    chunk_id?: string | null;
    title?: string | null;
    summary?: string | null;
    source_name?: string | null;
    section_path?: string[] | null;
    reference_text?: string | null;
    path?: string | null;
    split_part?: number | null;
    split_total?: number | null;
  }> | null;
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
  document_ocr: 'PDF OCR',
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

function isMarkdownBundle(manifest?: ArtifactManifest | null) {
  return manifest?.schema_version === 'envoy.markdown.bundle.v1';
}

function splitValue(value?: number | null) {
  if (!value || value <= 1) return '-';
  return String(value);
}

function bundlePath(value?: string | null) {
  if (!value) return '-';
  return value.replace(/^.*?\/(index\.md|manifest\.json|00-project-overview\.md|source\.md|[^/]+\.md)$/, '$1');
}

const pageStyle = {
  minHeight: '100vh',
  background: '#f5f5f7',
  fontFamily: 'SF Pro Text, system-ui, -apple-system, sans-serif',
  color: '#1d1d1f',
} as const;

const buttonBase = {
  minHeight: 40,
  padding: '0 18px',
  borderRadius: 999,
  fontWeight: 400,
  display: 'inline-flex',
  alignItems: 'center',
  textDecoration: 'none',
  cursor: 'pointer',
} as const;

function MarkdownTree({ manifest }: { manifest: ArtifactManifest }) {
  const fileTree = manifest.file_tree;
  if (!fileTree?.sources?.length) return null;
  return (
    <section style={{ marginTop: 28, borderTop: '1px solid #e0e0e0', paddingTop: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'baseline', flexWrap: 'wrap' }}>
        <h2 style={{ margin: 0, fontSize: 28, lineHeight: 1.14, letterSpacing: '-0.2px', fontWeight: 600 }}>Markdown 目录树</h2>
        <span style={{ color: '#7a7a7a', fontSize: 14 }}>来源 {fileTree.sources.length} 个 · 分片 {manifest.chunk_count ?? manifest.files?.chunks?.length ?? 0} 个</span>
      </div>
      <div style={{ marginTop: 18, borderTop: '1px solid #e0e0e0' }}>
        <DetailRow label="Index" value={fileTree.index || manifest.files?.index} />
        <DetailRow label="Overview" value={fileTree.overview || manifest.files?.overview} />
        <DetailRow label="Manifest" value={fileTree.manifest || manifest.files?.manifest} />
      </div>
      <div style={{ marginTop: 18 }}>
        {fileTree.sources.map((source, sourceIndex) => (
          <section key={`${source.source_name || sourceIndex}`} style={{ padding: '18px 0', borderTop: '1px solid #e0e0e0' }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', gap: 16, alignItems: 'baseline' }}>
              <div>
                <h3 style={{ margin: 0, fontSize: 21, lineHeight: 1.19, fontWeight: 600 }}>{source.source_name || '未命名来源'}</h3>
                <div style={{ marginTop: 5, color: '#7a7a7a', fontSize: 14, wordBreak: 'break-all' }}>{source.relative_path || source.source_path || '-'}</div>
              </div>
              <div style={{ color: '#0066cc', fontSize: 14 }}>{source.chunks?.length || 0} chunks</div>
            </div>
            {source.chunks?.length ? (
              <div style={{ marginTop: 14, borderTop: '1px solid #f0f0f0' }}>
                {source.chunks.slice(0, 24).map((chunk, chunkIndex) => (
                  <div key={`${chunk.chunk_id || chunk.relative_path || chunkIndex}`} style={{ display: 'grid', gridTemplateColumns: '44px minmax(0, 1fr)', gap: 12, padding: '12px 0', borderBottom: '1px solid #f0f0f0' }}>
                    <div style={{ color: '#7a7a7a', fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>{String(chunkIndex + 1).padStart(2, '0')}</div>
                    <div>
                      <div style={{ fontWeight: 600, lineHeight: 1.35 }}>{chunk.title || '-'}</div>
                      <div style={{ marginTop: 4, color: '#7a7a7a', fontSize: 13 }}>{chunk.reference_text || '未标注引用'}</div>
                      <div style={{ marginTop: 4, color: '#7a7a7a', fontSize: 13, wordBreak: 'break-all' }}>{chunk.relative_path || chunk.path || '-'}</div>
                    </div>
                  </div>
                ))}
              </div>
            ) : null}
          </section>
        ))}
      </div>
    </section>
  );
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
    <main style={pageStyle}>
      <div style={{ height: 44, background: '#000000', color: '#ffffff', display: 'flex', alignItems: 'center', padding: '0 24px', fontSize: 12 }}>
        Envoy · Artifact
      </div>
      <section style={{ maxWidth: 1100, margin: '0 auto', padding: '44px 24px 72px' }}>
        <header style={{ paddingBottom: 24, borderBottom: '1px solid #e0e0e0' }}>
          <Link href="/artifacts" style={{ color: '#0066cc', fontSize: 14, textDecoration: 'none' }}>返回产物中心</Link>
          <h1 style={{ margin: '12px 0 0', fontSize: 40, lineHeight: 1.1, letterSpacing: '-0.28px', fontWeight: 600 }}>产物详情</h1>
        </header>

        <section style={{ marginTop: 22 }}>
          {loading ? <div style={{ color: '#7a7a7a' }}>加载中...</div> : null}
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
              {isMarkdownBundle(artifact.manifest) ? (
                <div style={{ marginTop: 14, padding: '10px 0', borderTop: '1px solid #e0e0e0', color: '#0066cc', fontWeight: 600 }}>
                  Markdown bundle 产物
                </div>
              ) : null}
              {artifact.manifest?.file_tree ? <MarkdownTree manifest={artifact.manifest} /> : null}
              {artifact.manifest?.files && !artifact.manifest.file_tree ? (
                <section style={{ marginTop: 18, paddingTop: 18, borderTop: '1px solid #e0e0e0' }}>
                  <h2 style={{ margin: 0, fontSize: 21, fontWeight: 600 }}>Markdown 目录树</h2>
                  <p style={{ margin: '6px 0 10px', color: '#7a7a7a', fontSize: 13 }}>优先查看 index、overview、source 和 chunks，直接适配别的模型软件导入。</p>
                  <DetailRow label="编码" value={artifact.manifest.encoding} />
                  <DetailRow label="Index" value={bundlePath(artifact.manifest.files.index)} />
                  <DetailRow label="Overview" value={bundlePath(artifact.manifest.files.overview)} />
                  <DetailRow label="Manifest" value={bundlePath(artifact.manifest.files.manifest)} />
                  <DetailRow label="来源文件数" value={manifestValue(artifact.manifest.files.sources?.length)} />
                  <DetailRow label="分片文件数" value={manifestValue(artifact.manifest.files.chunks?.length)} />
                  <DetailRow label="主文档" value={bundlePath(artifact.manifest.files.markdown)} />
                  <DetailRow label="语料文件" value={bundlePath(artifact.manifest.files.corpus)} />
                  <DetailRow label="Index 路径" value={artifact.manifest.files.index} />
                  <DetailRow label="Overview 路径" value={artifact.manifest.files.overview} />
                  <DetailRow label="Manifest 路径" value={artifact.manifest.files.manifest} />
                </section>
              ) : null}
              {artifact.manifest ? (
                <section style={{ marginTop: 28, paddingTop: 24, borderTop: '1px solid #e0e0e0' }}>
                  <h2 style={{ margin: 0, fontSize: 28, lineHeight: 1.14, fontWeight: 600 }}>质量元数据</h2>
                  <p style={{ margin: '6px 0 10px', color: '#7a7a7a', fontSize: 13 }}>
                    {isMarkdownBundle(artifact.manifest)
                      ? '来自 Markdown bundle Manifest，可直接审查来源目录、分片索引和导出质量。'
                      : '来自导出 Manifest，用于审查 AI 素材来源、Ollama 状态和生成质量。'}
                  </p>
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
                  {artifact.manifest.sources?.length ? (
                    <div style={{ marginTop: 14, paddingTop: 14, borderTop: '1px solid #e0e0e0' }}>
                      <div style={{ color: '#7a7a7a', marginBottom: 8 }}>来源目录</div>
                      {artifact.manifest.sources.map((item, index) => (
                        <div key={`${item.source_name || index}`} style={{ padding: '8px 0', borderTop: '1px solid #f0f0f0' }}>
                          <div style={{ fontWeight: 700 }}>{item.source_name || '-'}</div>
                          <div style={{ color: '#7a7a7a', fontSize: 13, wordBreak: 'break-all' }}>{item.source_path || '-'}</div>
                        </div>
                      ))}
                    </div>
                  ) : null}
                  {artifact.manifest.chunks?.length && !artifact.manifest.file_tree ? (
                    <div style={{ marginTop: 14, paddingTop: 14, borderTop: '1px solid #e5e7eb' }}>
                      <div style={{ color: '#6b7280', marginBottom: 8 }}>分片索引</div>
                      {artifact.manifest.chunks.slice(0, 10).map((item, index) => (
                        <div key={`${item.chunk_id || index}`} style={{ padding: '8px 0', borderTop: '1px solid #e5e7eb' }}>
                          <div style={{ fontWeight: 700 }}>{item.title || '-'}</div>
                          <div style={{ color: '#6b7280', fontSize: 13, wordBreak: 'break-all' }}>{item.reference_text || '-'}</div>
                          <div style={{ color: '#6b7280', fontSize: 13, wordBreak: 'break-all' }}>{item.path || '-'}</div>
                          <div style={{ color: '#6b7280', fontSize: 12 }}>
                            分片 {splitValue(item.split_part)} / {splitValue(item.split_total)}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </section>
              ) : (
                <section style={{ marginTop: 18, paddingTop: 18, borderTop: '1px solid #e0e0e0', color: '#7a7a7a' }}>
                  暂无 Manifest 质量元数据
                </section>
              )}
              <div style={{ marginTop: 24, paddingTop: 18, borderTop: '1px solid #e0e0e0', display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                <Link href={`/trace?type=artifact&id=${artifact.id}`} style={{ ...buttonBase, background: '#ffffff', color: '#0066cc', border: '1px solid #0066cc' }}>
                  查看来源链路
                </Link>
                <button onClick={() => openDownloadUrl(`/api/v1/artifacts/${artifact.id}/download`)} style={{ ...buttonBase, background: '#0066cc', color: '#ffffff', border: 0 }}>
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
