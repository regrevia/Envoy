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
  };
  result_summary?: string | null;
  error_message?: string | null;
};

const stages = [
  ["download", "下载"],
  ["preprocess", "语音与画面提取"],
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

export default function VideoPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [authMode, setAuthMode] = useState("auto");
  const [browser, setBrowser] = useState("chrome");
  const [cookieFile, setCookieFile] = useState<File | null>(null);
  const [task, setTask] = useState<Task | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    let alive = true;
    apiJson<Project[]>("/api/v1/projects")
      .then((items) => {
        if (!alive) return;
        setProjects(items);
        if (items[0]) setProjectId(items[0].id);
      })
      .catch((error) => {
        if (alive) setMessage(errorText(error));
      });
    return () => {
      alive = false;
    };
  }, []);

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
      let created: Task;
      if (authMode === "cookies_file") {
        if (!cookieFile) {
          setMessage("请选择 Netscape 格式的 cookies.txt");
          return;
        }
        const form = new FormData();
        form.set("project_id", selectedId);
        form.set("source_url", url);
        form.set("cookies_file", cookieFile);
        created = await apiJson<Task>("/api/v1/tasks/video-to-markdown/upload", {
          method: "POST",
          body: form,
        });
      } else {
        const auth = authMode === "auto"
          ? { mode: "auto" }
          : authMode === "browser"
            ? { mode: "browser", browser }
            : undefined;
        created = await apiJson<Task>("/api/v1/tasks/video-to-markdown", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ project_id: selectedId, source_url: url, auth }),
        });
      }
      setTask(created);
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

  const checkpoints = task?.input_config?.checkpoints ?? {};
  const exportCheckpoint = checkpoints.export;
  const active = task && ["pending", "running"].includes(task.status);

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

        <form onSubmit={submit} style={{ padding: "28px 0", borderBottom: "1px solid #d9e0dd" }}>
          <label htmlFor="video-url" style={{ display: "block", marginBottom: 9, fontWeight: 600 }}>视频链接</label>
          <div style={{ display: "flex", gap: 9, flexWrap: "wrap" }}>
            <input
              id="video-url"
              type="url"
              required
              value={sourceUrl}
              onChange={(event) => setSourceUrl(event.target.value)}
              placeholder="粘贴 B 站、抖音或其他 yt-dlp 支持的公开视频链接"
              style={{ flex: "1 1 460px", minWidth: 0, minHeight: 46, boxSizing: "border-box", padding: "0 13px", border: "1px solid #aebdb8", borderRadius: 5, background: "#fff", fontSize: 15 }}
            />
            <button disabled={busy || Boolean(active)} style={{ minHeight: 46, padding: "0 20px", border: 0, borderRadius: 5, background: busy || active ? "#9daaa6" : "#18745c", color: "white", fontSize: 15, fontWeight: 600, cursor: busy || active ? "wait" : "pointer" }}>
              {busy ? "提交中" : active ? "处理中" : "生成 Markdown"}
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
          <p style={{ margin: "11px 0 0", color: "#63716c", fontSize: 13, lineHeight: 1.55 }}>
            B站或抖音需要登录时，选择同机浏览器登录态，或上传 Netscape 格式的 cookies.txt。Cookie 只用于下载，不会写进 Markdown。
          </p>
          <div style={{ display: "grid", gridTemplateColumns: "minmax(180px, 240px) minmax(180px, 1fr)", gap: 10, marginTop: 14, alignItems: "center" }}>
            <label style={{ color: "#45534f", fontSize: 14 }}>
              访问方式
              <select value={authMode} onChange={(event) => { setAuthMode(event.target.value); setCookieFile(null); }} style={{ display: "block", width: "100%", minHeight: 36, marginTop: 6, padding: "0 8px", border: "1px solid #c4cfcb", borderRadius: 4, background: "#fff", color: "#17211f" }}>
                <option value="auto">自动读取公开或本机登录态</option>
                <option value="public">仅公开访问</option>
                <option value="browser">读取本机浏览器登录态</option>
                <option value="cookies_file">上传 cookies.txt</option>
              </select>
            </label>
            {authMode === "browser" ? (
              <label style={{ color: "#45534f", fontSize: 14 }}>
                浏览器
                <select value={browser} onChange={(event) => setBrowser(event.target.value)} style={{ display: "block", width: "100%", minHeight: 36, marginTop: 6, padding: "0 8px", border: "1px solid #c4cfcb", borderRadius: 4, background: "#fff", color: "#17211f" }}>
                  {["chrome", "edge", "firefox", "safari", "brave", "chromium"].map((item) => <option key={item} value={item}>{item}</option>)}
                </select>
              </label>
            ) : authMode === "cookies_file" ? (
              <label style={{ color: "#45534f", fontSize: 14 }}>
                Cookie 文件
                <input type="file" accept=".txt,text/plain" onChange={(event) => setCookieFile(event.target.files?.[0] || null)} style={{ display: "block", width: "100%", marginTop: 9, fontSize: 13 }} />
              </label>
            ) : (
              <span style={{ color: "#63716c", fontSize: 13 }}>先尝试公开访问，失败后自动尝试本机浏览器登录态</span>
            )}
          </div>
        </form>

        {message ? <div role="alert" style={{ padding: "14px 0", color: "#a3342c", whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{message}</div> : null}

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
                const current = !done && active && Number(task.progress || 0) < (key === "download" ? 15 : key === "preprocess" ? 72 : 100);
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
          </section>
        ) : null}
      </section>
    </main>
  );
}
