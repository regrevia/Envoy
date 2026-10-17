"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";

import { API_BASE, ApiError, apiJson } from "../../lib/api";

type Project = {
  id: string;
  name: string;
};

type Task = {
  id: string;
  project_id?: string;
  asset_id?: string | null;
  task_type: string;
  status: string;
  progress: number;
  input_config?: {
    checkpoints?: Record<string, { status?: string; path?: string; artifact_id?: string }>;
    library_id?: string;
    library_title?: string;
  };
  result_summary?: string | null;
  error_message?: string | null;
};

type VideoCandidate = {
  id: string;
  source_url: string;
  title: string;
  duration_sec?: number | null;
  thumbnail?: string | null;
  published_at?: string | number | null;
  extractor?: string;
};

type DiscoveryRecovery = {
  reason_code: string;
  requires_user_action: boolean;
  recommended_action?: string;
  raw_error?: string;
  platform?: string;
  source_kind?: string;
};

type DiscoverySummary = {
  creator_title?: string;
  platform?: string;
  source_kind?: string;
  discovery_method?: string;
};

type VisionSettings = {
  configured: boolean;
  base_url: string;
  model: string;
  protocol: "chat" | "messages";
  api_key_configured: boolean;
};

const stages = [
  ["download", "下载"],
  ["preprocess", "语音与画面提取"],
  ["understand", "连续画面理解"],
  ["export", "Markdown 导出"],
] as const;

function errorText(error: unknown) {
  if (error instanceof ApiError) {
    if (error.details) {
      try {
        const parsed = JSON.parse(error.details);
        if (typeof parsed.detail === "string") return parsed.detail;
      } catch {
        return error.details;
      }
    }
    return error.message;
  }
  return error instanceof Error ? error.message : String(error);
}

function errorPayload(error: unknown): DiscoveryRecovery | null {
  if (!(error instanceof ApiError) || !error.details) return null;
  try {
    const body = JSON.parse(error.details);
    return body?.detail && typeof body.detail === "object" ? body.detail as DiscoveryRecovery : null;
  } catch {
    return null;
  }
}

export default function VideoPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [mode, setMode] = useState<"single" | "creator" | "local" | "mixed">("single");
  const [localFiles, setLocalFiles] = useState<File[]>([]);
  const [mixedFiles, setMixedFiles] = useState<File[]>([]);
  const [candidates, setCandidates] = useState<VideoCandidate[]>([]);
  const [discoverySummary, setDiscoverySummary] = useState<DiscoverySummary | null>(null);
  const [selectedVideoUrls, setSelectedVideoUrls] = useState<string[]>([]);
  const [discovering, setDiscovering] = useState(false);
  const [discoveryRecovery, setDiscoveryRecovery] = useState<DiscoveryRecovery | null>(null);
  const [task, setTask] = useState<Task | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [visionSettings, setVisionSettings] = useState<VisionSettings>({
    configured: false,
    base_url: "",
    model: "",
    protocol: "chat",
    api_key_configured: false,
  });
  const [visionApiKey, setVisionApiKey] = useState("");
  const [visionBusy, setVisionBusy] = useState(false);
  const [visionMessage, setVisionMessage] = useState("");

  useEffect(() => {
    let alive = true;
    Promise.all([
      apiJson<Project[]>("/api/v1/projects"),
      apiJson<VisionSettings>("/api/v1/health/vision"),
    ])
      .then(([items, settings]) => {
        if (!alive) return;
        setProjects(items);
        if (items[0]) setProjectId(items[0].id);
        setVisionSettings(settings);
      })
      .catch((error) => {
        if (alive) setMessage(errorText(error));
      });
    return () => {
      alive = false;
    };
  }, []);

  const saveVisionSettings = async () => {
    setVisionBusy(true);
    setVisionMessage("");
    try {
      const settings = await apiJson<VisionSettings>("/api/v1/health/vision/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          base_url: visionSettings.base_url,
          model: visionSettings.model,
          protocol: visionSettings.protocol,
          api_key: visionApiKey,
        }),
      });
      setVisionSettings(settings);
      setVisionApiKey("");
      setVisionMessage("配置已加密保存在本机。");
    } catch (error) {
      setVisionMessage(errorText(error));
    } finally {
      setVisionBusy(false);
    }
  };

  const testVisionSettings = async () => {
    setVisionBusy(true);
    setVisionMessage("");
    try {
      if (visionApiKey) {
        const settings = await apiJson<VisionSettings>("/api/v1/health/vision/config", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            base_url: visionSettings.base_url,
            model: visionSettings.model,
            protocol: visionSettings.protocol,
            api_key: visionApiKey,
          }),
        });
        setVisionSettings(settings);
        setVisionApiKey("");
      }
      const result = await apiJson<{ status: string; model: string; response?: string }>("/api/v1/health/vision/test", {
        method: "POST",
      });
      setVisionMessage(`连接成功：${result.model}`);
    } catch (error) {
      setVisionMessage(errorText(error));
    } finally {
      setVisionBusy(false);
    }
  };

  useEffect(() => {
    if (!task || !["pending", "running"].includes(task.status)) return;
    let alive = true;
    const timer = window.setInterval(() => {
      apiJson<Task>(`/api/v1/tasks/${task.id}`)
        .then((next) => {
          if (alive) setTask(next);
        })
        .catch((error) => {
          if (alive) setMessage(errorText(error));
        });
    }, 2500);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [task?.id, task?.status]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const url = sourceUrl.trim();
    if (!url) {
      setMessage("请输入视频链接");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      let selectedId = projectId;
      if (!selectedId) {
        const project = await apiJson<Project>("/api/v1/projects", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: "视频知识库",
            description: "从公开视频链接整理的知识文档",
            domain_type: "video",
            status: "active",
          }),
        });
        selectedId = project.id;
        setProjects([project]);
        setProjectId(project.id);
      }
      const created = await apiJson<Task>("/api/v1/tasks/video-to-markdown", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: selectedId, source_url: url, auth: { mode: "auto" } }),
      });
      setTask(created);
    } catch (error) {
      setMessage(errorText(error));
    } finally {
      setBusy(false);
    }
  };

  const ensureProjectId = async () => {
    if (projectId) return projectId;
    const project = await apiJson<Project>("/api/v1/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: "视频知识库",
        description: "从视频素材整理的知识文档",
        domain_type: "video",
        status: "active",
      }),
    });
    setProjects((current) => [...current, project]);
    setProjectId(project.id);
    return project.id;
  };

  const submitLocalVideos = async () => {
    if (!localFiles.length || localFiles.length > 20) {
      setMessage("请选择 1 到 20 个本地视频");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const selectedId = await ensureProjectId();
      const items: { asset_id: string; title: string }[] = [];
      for (const file of localFiles) {
        const form = new FormData();
        form.append("project_id", selectedId);
        form.append("asset_type", "video");
        form.append("file", file);
        const uploaded = await apiJson<{ asset: { id: string; file_name: string } }>("/api/v1/assets/upload", {
          method: "POST",
          body: form,
          timeoutMs: Math.max(120000, Math.ceil(file.size / (1024 * 1024) * 5000)),
        });
        items.push({ asset_id: uploaded.asset.id, title: uploaded.asset.file_name });
      }
      const created = await apiJson<Task>("/api/v1/tasks/video-batch-to-markdown", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: selectedId, title: "本地视频知识库", items }),
      });
      setTask(created);
      setLocalFiles([]);
    } catch (error) {
      setMessage(errorText(error));
    } finally {
      setBusy(false);
    }
  };

  const mixedAssetType = (file: File) => {
    const name = file.name.toLowerCase();
    if (file.type.startsWith("video/") || /\.(3gp|avi|flv|m2ts|m4v|mkv|mov|mp4|mpeg|mpg|mts|mxf|ogv|ts|webm|wmv)$/.test(name)) return "video";
    if (file.type.startsWith("image/") || /\.(png|jpe?g|webp|gif|bmp|tiff?)$/.test(name)) return "image";
    if (file.type.startsWith("audio/") || /\.(mp3|wav|m4a|aac|ogg|flac|wma)$/.test(name)) return "audio";
    if (/\.(pdf|doc|docx|ppt|pptx|xls|xlsx)$/.test(name) || file.type.includes("pdf") || file.type.includes("document")) return "document";
    if (/\.(md|markdown)$/.test(name)) return "markdown";
    return "text";
  };

  const submitMixedFiles = async () => {
    if (!mixedFiles.length || mixedFiles.length > 50) {
      setMessage("请选择 1 到 50 个混合素材");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const selectedId = await ensureProjectId();
      const assetIds: string[] = [];
      for (const file of mixedFiles) {
        const form = new FormData();
        form.append("project_id", selectedId);
        form.append("asset_type", mixedAssetType(file));
        form.append("file", file);
        const uploaded = await apiJson<{ asset: { id: string } }>("/api/v1/assets/upload", {
          method: "POST",
          body: form,
          timeoutMs: Math.max(120000, Math.ceil(file.size / (1024 * 1024) * 5000)),
        });
        assetIds.push(uploaded.asset.id);
      }
      const append = task?.status === "success" && task.task_type === "mixed_assets_to_markdown";
      const endpoint = append && task
        ? `/api/v1/tasks/${task.id}/append-assets`
        : "/api/v1/tasks/mixed-library-to-markdown";
      const body = append
        ? { asset_ids: assetIds, title: task?.input_config?.library_title }
        : { project_id: selectedId, title: "混合素材知识库", asset_ids: assetIds };
      const created = await apiJson<Task>(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setTask(created);
      setMixedFiles([]);
    } catch (error) {
      setMessage(errorText(error));
    } finally {
      setBusy(false);
    }
  };

  const retry = async () => {
    if (!task) return;
    setBusy(true);
    setMessage("");
    try {
      const retried = await apiJson<Task>(`/api/v1/tasks/${task.id}/retry`, { method: "POST" });
      setTask(retried);
    } catch (error) {
      setMessage(errorText(error));
    } finally {
      setBusy(false);
    }
  };

  const discoverVideos = async () => {
    setDiscovering(true);
    setMessage("");
    setDiscoveryRecovery(null);
    setCandidates([]);
    setDiscoverySummary(null);
    setSelectedVideoUrls([]);
    try {
      const result = await apiJson<DiscoverySummary & { items: VideoCandidate[] }>("/api/v1/video-playlists/discover", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_url: sourceUrl.trim(), limit: 100 }),
      });
      setCandidates(result.items);
      setDiscoverySummary(result);
      setSelectedVideoUrls(result.items.slice(0, 20).map((item) => item.source_url));
      if (result.items.length > 20) setMessage("发现列表最多显示 100 条；单次最多选择 20 条，剩余视频可追加处理。");
    } catch (error) {
      const payload = errorPayload(error);
      if (payload?.requires_user_action) {
        setDiscoveryRecovery(payload);
        setMessage(payload.raw_error || "来源页面需要先完成登录或人工验证。");
      } else {
        setMessage(errorText(error));
      }
    } finally {
      setDiscovering(false);
    }
  };

  const submitBatch = async (append = false) => {
    if (!selectedVideoUrls.length || selectedVideoUrls.length > 20) {
      setMessage("请先选择 1 到 20 条视频");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const selectedId = await ensureProjectId();
      const items = candidates
        .filter((item) => selectedVideoUrls.includes(item.source_url))
        .map((item) => ({ source_url: item.source_url, title: item.title }));
      const endpoint = append && task
        ? `/api/v1/tasks/${task.id}/append-videos`
        : "/api/v1/tasks/video-batch-to-markdown";
      const body = append
        ? { items, title: task?.input_config?.library_title }
        : { project_id: selectedId, title: "视频知识库", items };
      const created = await apiJson<Task>(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setTask(created);
      setCandidates([]);
      setSelectedVideoUrls([]);
      setSourceUrl("");
    } catch (error) {
      setMessage(errorText(error));
    } finally {
      setBusy(false);
    }
  };

  const checkpoints = task?.input_config?.checkpoints ?? {};
  const exportCheckpoint = checkpoints.collection_export ?? checkpoints.export;
  const active = task && ["pending", "running"].includes(task.status);
  const appendToCompleted = Boolean(
    task?.status === "success" && ["video_batch_to_markdown", "video_to_markdown"].includes(task.task_type),
  );

  return (
    <main style={{ minHeight: "100vh", background: "#f7f8f8", color: "#17211f", fontFamily: "system-ui, sans-serif" }}>
      <section style={{ maxWidth: 820, margin: "0 auto", padding: "48px 22px" }}>
        <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, borderBottom: "1px solid #d9e0dd", paddingBottom: 18 }}>
          <div>
            <p style={{ margin: 0, color: "#27715e", fontSize: 13, fontWeight: 650 }}>ENVOY / VIDEO</p>
            <h1 style={{ margin: "8px 0 0", fontSize: 30, lineHeight: 1.2 }}>视频转知识文档</h1>
          </div>
          <Link href="/tasks" style={{ color: "#27715e" }}>任务列表</Link>
        </header>

        <div role="group" aria-label="导入方式" style={{ display: "flex", gap: 4, padding: "18px 0 0", borderBottom: "1px solid #d9e0dd" }}>
          {([["single", "单个链接"], ["creator", "主页/播放列表"], ["local", "本地视频"], ["mixed", "混合素材"]] as const).map(([value, label]) => (
            <button key={value} type="button" onClick={() => { setMode(value); setCandidates([]); setDiscoverySummary(null); setSelectedVideoUrls([]); setMessage(""); }} aria-pressed={mode === value} style={{ minHeight: 38, padding: "0 12px", border: 0, borderBottom: mode === value ? "2px solid #18745c" : "2px solid transparent", background: "transparent", color: mode === value ? "#18745c" : "#63716c", fontWeight: mode === value ? 650 : 450, cursor: "pointer" }}>{label}</button>
          ))}
        </div>

        <section style={{ padding: "22px 0", borderBottom: "1px solid #d9e0dd" }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>视觉模型 API</h2>
          <p style={{ margin: "7px 0 14px", color: "#63716c", fontSize: 13, lineHeight: 1.5 }}>
            填写服务商文档中的 API Base URL、API Key 和原始模型 ID。密钥在后端加密保存，不会返回到浏览器或进入模型提示词。
          </p>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            <label style={{ color: "#45534f", fontSize: 13 }}>
              Base URL
              <input value={visionSettings.base_url} onChange={(event) => setVisionSettings((old) => ({ ...old, base_url: event.target.value }))} placeholder="https://api.example.com/v1" style={{ display: "block", boxSizing: "border-box", width: "100%", minHeight: 38, marginTop: 5, padding: "0 9px", border: "1px solid #aebdb8", borderRadius: 4 }} />
            </label>
            <label style={{ color: "#45534f", fontSize: 13 }}>
              模型名
              <input value={visionSettings.model} onChange={(event) => setVisionSettings((old) => ({ ...old, model: event.target.value }))} placeholder="按服务商文档填写完整 model id" style={{ display: "block", boxSizing: "border-box", width: "100%", minHeight: 38, marginTop: 5, padding: "0 9px", border: "1px solid #aebdb8", borderRadius: 4 }} />
            </label>
            <label style={{ color: "#45534f", fontSize: 13 }}>
              API Key {visionSettings.api_key_configured ? "· 已保存，留空则保持不变" : ""}
              <input type="password" autoComplete="new-password" value={visionApiKey} onChange={(event) => setVisionApiKey(event.target.value)} placeholder={visionSettings.api_key_configured ? "已配置" : "API Key"} style={{ display: "block", boxSizing: "border-box", width: "100%", minHeight: 38, marginTop: 5, padding: "0 9px", border: "1px solid #aebdb8", borderRadius: 4 }} />
            </label>
            <label style={{ color: "#45534f", fontSize: 13 }}>
              协议
              <select value={visionSettings.protocol} onChange={(event) => setVisionSettings((old) => ({ ...old, protocol: event.target.value as "chat" | "messages" }))} style={{ display: "block", width: "100%", minHeight: 38, marginTop: 5, padding: "0 8px", border: "1px solid #aebdb8", borderRadius: 4, background: "#fff" }}>
                <option value="chat">OpenAI Chat Completions</option>
                <option value="messages">Anthropic Messages</option>
              </select>
            </label>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginTop: 12 }}>
            <button type="button" onClick={() => void saveVisionSettings()} disabled={visionBusy} style={{ minHeight: 36, padding: "0 12px", border: "1px solid #18745c", borderRadius: 4, background: "#18745c", color: "#fff", cursor: "pointer" }}>{visionBusy ? "处理中..." : "保存配置"}</button>
            <button type="button" onClick={() => void testVisionSettings()} disabled={visionBusy || (!visionApiKey && !visionSettings.api_key_configured)} style={{ minHeight: 36, padding: "0 12px", border: "1px solid #18745c", borderRadius: 4, background: "#fff", color: "#18745c", cursor: "pointer" }}>测试 API 连接</button>
            <span role="status" style={{ color: visionMessage.startsWith("连接成功") || visionMessage.startsWith("配置已") ? "#18745c" : "#a3342c", fontSize: 13 }}>{visionMessage || (visionSettings.configured ? "已配置" : "未配置；视频仍可执行语音转写和 OCR")}</span>
          </div>
        </section>

        <form onSubmit={(event) => { event.preventDefault(); if (mode === "single") void submit(event); else if (mode === "creator") void discoverVideos(); else if (mode === "local") void submitLocalVideos(); else void submitMixedFiles(); }} style={{ padding: "28px 0", borderBottom: "1px solid #d9e0dd" }}>
          {mode === "local" ? (
            <label htmlFor="local-videos" style={{ display: "block", marginBottom: 9, fontWeight: 600 }}>选择本地视频（最多 20 个）</label>
          ) : mode === "mixed" ? (
            <label htmlFor="mixed-files" style={{ display: "block", marginBottom: 9, fontWeight: 600 }}>选择混合素材（最多 50 个）</label>
          ) : (
            <label htmlFor="video-url" style={{ display: "block", marginBottom: 9, fontWeight: 600 }}>{mode === "single" ? "视频链接" : "创作者主页或播放列表链接"}</label>
          )}
          <div style={{ display: "flex", gap: 9, flexWrap: "wrap" }}>
            {mode === "local" ? (
              <input
                id="local-videos"
                type="file"
                multiple
                accept=".3gp,.avi,.flv,.m2ts,.m4v,.mkv,.mov,.mp4,.mpeg,.mpg,.mts,.mxf,.ogv,.ts,.webm,.wmv,video/*"
                onChange={(event) => setLocalFiles(Array.from(event.target.files ?? []).slice(0, 20))}
                style={{ flex: "1 1 460px", minWidth: 0, minHeight: 46, boxSizing: "border-box", padding: 8, border: "1px solid #aebdb8", borderRadius: 5, background: "#fff", fontSize: 14 }}
              />
            ) : mode === "mixed" ? (
              <input
                id="mixed-files"
                type="file"
                multiple
                accept=".3gp,.avi,.flv,.m2ts,.m4v,.mkv,.mov,.mp4,.mpeg,.mpg,.mts,.mxf,.ogv,.ts,.webm,.wmv,.png,.jpg,.jpeg,.webp,.gif,.bmp,.tif,.tiff,.mp3,.wav,.m4a,.aac,.ogg,.flac,.pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.md,.markdown,.txt,video/*,audio/*,image/*,text/plain,application/pdf"
                onChange={(event) => setMixedFiles(Array.from(event.target.files ?? []).slice(0, 50))}
                style={{ flex: "1 1 460px", minWidth: 0, minHeight: 46, boxSizing: "border-box", padding: 8, border: "1px solid #aebdb8", borderRadius: 5, background: "#fff", fontSize: 14 }}
              />
            ) : (
            <input
              id="video-url"
              type="url"
              required
              value={sourceUrl}
              onChange={(event) => setSourceUrl(event.target.value)}
              placeholder={mode === "single" ? "粘贴单个视频链接" : "粘贴 YouTube、B 站、抖音、Vimeo、Twitch 等主页/列表链接"}
              style={{ flex: "1 1 460px", minWidth: 0, minHeight: 46, boxSizing: "border-box", padding: "0 13px", border: "1px solid #aebdb8", borderRadius: 5, background: "#fff", fontSize: 15 }}
            />
            )}
            <button type="submit" disabled={busy || Boolean(active) || discovering} style={{ minHeight: 46, padding: "0 20px", border: 0, borderRadius: 5, background: busy || active || discovering ? "#9daaa6" : "#18745c", color: "white", fontSize: 15, fontWeight: 600, cursor: busy || active || discovering ? "wait" : "pointer" }}>
              {busy ? "提交中" : active ? "处理中" : mode === "single" ? "生成 Markdown" : mode === "local" ? "上传并生成主题库" : mode === "mixed" ? "上传并生成混合库" : discovering ? "发现中..." : "发现视频"}
            </button>
          </div>
          {projects.length > 0 ? (
            <label style={{ display: "flex", alignItems: "center", gap: 9, marginTop: 14, color: "#45534f", fontSize: 14 }}>
              保存到
              <select value={projectId} onChange={(event) => setProjectId(event.target.value)} style={{ maxWidth: "100%", minHeight: 34, padding: "0 8px", border: "1px solid #c4cfcb", borderRadius: 4, background: "#fff", color: "#17211f" }}>
                {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
              </select>
            </label>
          ) : null}
          {mode === "mixed" ? <p style={{ margin: "11px 0 0", color: "#63716c", fontSize: 13, lineHeight: 1.55 }}>图片、PDF、文字、音频和视频会分别提取，再汇总为一份带索引和来源追踪的 Markdown。默认不调用额外 LLM，减少 token 消耗。</p> : mode !== "local" ? <p style={{ margin: "11px 0 0", color: "#63716c", fontSize: 13, lineHeight: 1.55 }}>
            B站或抖音需要登录时，选择同机浏览器登录态，或上传 Netscape 格式的 cookies.txt。Cookie 只用于下载，不会写进 Markdown。
          </p> : <p style={{ margin: "11px 0 0", color: "#63716c", fontSize: 13, lineHeight: 1.55 }}>支持 MP4、MOV、MKV、WebM、AVI、M4V、TS 等常见格式；多个文件会分别处理并汇总到同一主题库。</p>}
          {mode !== "local" && mode !== "mixed" ? <p style={{ margin: "12px 0 0", color: "#63716c", fontSize: 13 }}>
            Envoy 会先尝试公开访问，失败后自动尝试本机浏览器登录态。请确保运行 Envoy 的电脑上已登录 B 站或抖音。
          </p> : null}
        </form>

        {mode === "creator" && candidates.length > 0 ? (
          <section style={{ padding: "20px 0", borderBottom: "1px solid #d9e0dd" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
              <h2 style={{ margin: 0, fontSize: 17 }}>选择要导入的视频</h2>
              <span style={{ color: "#63716c", fontSize: 13 }}>{selectedVideoUrls.length}/20 已选</span>
            </div>
            <p style={{ margin: "7px 0 0", color: "#63716c", fontSize: 13 }}>
              {discoverySummary?.creator_title ? `${discoverySummary.creator_title} · ` : ""}
              {discoverySummary?.platform || "unknown"} · {discoverySummary?.source_kind || "unknown"} · {discoverySummary?.discovery_method || "discovery"}
            </p>
            <div style={{ maxHeight: 380, overflow: "auto", marginTop: 10, borderTop: "1px solid #d9e0dd" }}>
              {candidates.map((item) => {
                const selected = selectedVideoUrls.includes(item.source_url);
                const disabled = !selected && selectedVideoUrls.length >= 20;
                return (
                  <label key={item.source_url} style={{ display: "flex", alignItems: "flex-start", gap: 10, padding: "11px 4px", borderBottom: "1px solid #e4e9e7", opacity: disabled ? 0.5 : 1 }}>
                    <input type="checkbox" checked={selected} disabled={disabled} onChange={(event) => setSelectedVideoUrls((current) => event.target.checked ? [...current, item.source_url] : current.filter((url) => url !== item.source_url))} style={{ marginTop: 3 }} />
                    <span style={{ minWidth: 0 }}>
                      <span style={{ display: "block", fontSize: 14, fontWeight: 550 }}>{item.title}</span>
                      <span style={{ display: "block", marginTop: 3, color: "#71807a", fontSize: 12, overflowWrap: "anywhere" }}>{item.duration_sec ? `${Math.round(item.duration_sec)} 秒 · ` : ""}{item.source_url}</span>
                    </span>
                  </label>
                );
              })}
            </div>
            <button type="button" onClick={() => void submitBatch(appendToCompleted)} disabled={busy || !selectedVideoUrls.length} style={{ minHeight: 40, marginTop: 14, padding: "0 14px", border: 0, borderRadius: 4, background: "#18745c", color: "white", fontWeight: 600, cursor: "pointer" }}>{appendToCompleted ? "追加到现有主题库" : "生成主题知识库"}</button>
          </section>
        ) : null}

        {message ? <div role="alert" style={{ padding: "14px 0", color: "#a3342c", whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{message}</div> : null}
        {discoveryRecovery?.requires_user_action ? (
          <section role="status" style={{ padding: "14px 0 18px", borderBottom: "1px solid #d9e0dd" }}>
            <strong style={{ display: "block", color: "#7c4a12", fontSize: 14 }}>需要在来源网站完成一次人工处理</strong>
            <p style={{ margin: "7px 0 12px", color: "#63716c", fontSize: 13, lineHeight: 1.55 }}>
              请在新页面完成登录、验证码或安全确认。Envoy 不读取或提交你的验证码；完成后回到这里点击重试。
            </p>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              <a href={sourceUrl.trim()} target="_blank" rel="noreferrer" style={{ display: "inline-flex", alignItems: "center", minHeight: 38, padding: "0 12px", border: "1px solid #b7791f", borderRadius: 4, color: "#7c4a12", background: "#fffaf0", textDecoration: "none", fontWeight: 600 }}>
                打开来源页面
              </a>
              <button type="button" onClick={() => void discoverVideos()} disabled={discovering} style={{ minHeight: 38, padding: "0 12px", border: 0, borderRadius: 4, color: "#fff", background: "#18745c", cursor: discovering ? "wait" : "pointer", fontWeight: 600 }}>
                {discovering ? "重新发现中..." : "我已处理，重试发现"}
              </button>
            </div>
          </section>
        ) : null}

        {task ? (
          <section aria-live="polite" style={{ paddingTop: 24 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12 }}>
              <h2 style={{ margin: 0, fontSize: 19 }}>处理任务</h2>
              <span style={{ color: task.status === "failed" ? "#a3342c" : task.status === "success" ? "#18745c" : "#586761" }}>
                {task.status === "success" ? "已完成" : task.status === "failed" ? "失败" : task.status === "pending" ? "等待中" : "处理中"}
              </span>
            </div>
            <p style={{ color: "#63716c", fontSize: 13, overflowWrap: "anywhere" }}>任务 ID：{task.id}</p>
            <div style={{ height: 7, background: "#dfe6e3", borderRadius: 4, overflow: "hidden" }}>
              <div style={{ width: `${Math.max(0, Math.min(task.progress || 0, 100))}%`, height: "100%", background: "#18745c", transition: "width 250ms ease" }} />
            </div>
            <p style={{ color: "#63716c", fontSize: 13 }}>进度 {task.progress || 0}%{task.result_summary ? ` · ${task.result_summary}` : ""}</p>
            <ol style={{ margin: "20px 0", padding: 0, listStyle: "none", borderTop: "1px solid #d9e0dd" }}>
              {stages.map(([key, label]) => {
                const done = checkpoints[key]?.status === "success";
                const current = !done && active && Number(task.progress || 0) < (
                  key === "download" ? 15 : key === "preprocess" ? 68 : key === "understand" ? 72 : 100
                );
                return <li key={key} style={{ padding: "11px 0", borderBottom: "1px solid #d9e0dd", color: done ? "#18745c" : "#63716c" }}>{done ? "完成" : current ? "进行中" : "等待"} · {label}</li>;
              })}
            </ol>
            {task.error_message ? (
              <div style={{ padding: 14, borderLeft: "3px solid #b13a30", background: "#fff1ef", color: "#81271f", whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
                <strong>原始错误</strong>
                <div style={{ marginTop: 7 }}>{task.error_message}</div>
              </div>
            ) : null}
            {task.status === "failed" ? <button onClick={() => void retry()} disabled={busy} style={{ marginTop: 16, minHeight: 38, padding: "0 14px", border: "1px solid #18745c", borderRadius: 4, background: "white", color: "#18745c", cursor: "pointer" }}>从已完成阶段重试</button> : null}
            {task.status === "success" && exportCheckpoint?.artifact_id ? (
              <a href={`${API_BASE}/api/v1/artifacts/${exportCheckpoint.artifact_id}/download`} style={{ display: "inline-flex", alignItems: "center", minHeight: 42, marginTop: 16, padding: "0 16px", borderRadius: 4, background: "#18745c", color: "white", textDecoration: "none", fontWeight: 600 }}>下载 Markdown</a>
            ) : null}
            {task.status === "success" && ["video_batch_to_markdown", "video_to_markdown", "mixed_assets_to_markdown"].includes(task.task_type) ? (
              <button type="button" onClick={() => setMode(task.task_type === "mixed_assets_to_markdown" ? "mixed" : "creator")} style={{ display: "block", minHeight: 38, marginTop: 12, padding: "0 12px", border: "1px solid #18745c", borderRadius: 4, background: "white", color: "#18745c", cursor: "pointer" }}>{task.task_type === "mixed_assets_to_markdown" ? "追加图片、文字或视频" : "发现并追加更多视频"}</button>
            ) : null}
            {task.status === "success" && ["video_batch_to_markdown", "video_to_markdown", "mixed_assets_to_markdown"].includes(task.task_type) ? (
              <button type="button" onClick={() => setMode("mixed")} style={{ display: "block", minHeight: 38, marginTop: 8, padding: "0 12px", border: "1px solid #7a8f87", borderRadius: 4, background: "white", color: "#45534f", cursor: "pointer" }}>追加其他格式素材</button>
            ) : null}
          </section>
        ) : null}
      </section>
    </main>
  );
}
