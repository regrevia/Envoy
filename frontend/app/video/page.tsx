"use client";

import Link from "next/link";
import Image from "next/image";
import { DragEvent, FormEvent, useEffect, useRef, useState } from "react";
import { AlertCircle, ArrowUpRight, Check, ChevronDown, Download, Folder, Link2, ListChecks, Settings, X } from "lucide-react";

import { API_BASE, ApiError, apiJson } from "../../lib/api";
import styles from "./video.module.css";

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
    source_url?: string;
    title?: string;
    checkpoints?: Record<string, { status?: string; path?: string; artifact_id?: string }>;
    library_id?: string;
    library_title?: string;
  };
  result_summary?: string | null;
  error_message?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  is_stale?: boolean;
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

type OutputMode = "markdown" | "html" | "both";
type Locale = "zh" | "en";

const LAST_TASK_STORAGE_KEY = "envoy:last-video-task-id";

const uiCopy = {
  zh: {
    tasks: "任务中心",
    settings: "视觉模型设置",
    title: "视频转知识库",
    subtitle: "粘贴链接，剩下的交给 Envoy",
    single: "视频链接",
    creator: "主页作品",
    local: "本地视频",
    mixed: "混合素材",
    sourcePlaceholder: "粘贴视频链接、BV号或分享链接",
    creatorPlaceholder: "粘贴创作者主页或播放列表链接",
    chooseOutput: "产物",
    both: "Markdown + HTML",
    markdown: "仅 Markdown",
    html: "HTML 预览 + Markdown 索引",
    submit: "开始整理",
    discover: "发现作品",
    upload: "上传并整理",
    workspace: "工作区",
    recent: "最近任务",
    allTasks: "查看全部",
    noTasks: "还没有任务",
    workspaceDefault: "新建视频知识库",
    workspaceSave: "保存到 Envoy 工作区",
    workspaceFirstUse: "首次处理时自动创建",
    dropVideo: "拖动视频到这里",
    chooseFile: "或点击选择",
    dropMixed: "拖动文件到这里",
    mixedTypes: "视频、图片、音频、文档",
    selectedFiles: "已选择",
    fileLimit: "最多",
    publicHint: "支持主流平台公开视频链接。",
    creatorHint: "从主页或播放列表选择作品。",
    localHint: "支持 MP4、MOV、MKV、WebM 等常见格式。",
    mixedHint: "合并视频、图片、音频和文档。",
    serviceUnavailable: "无法连接 Envoy 服务",
    modelApi: "视觉模型 API",
    savedLocally: "密钥仅加密保存在本机",
    baseUrl: "服务地址",
    modelName: "模型名",
    protocol: "协议",
    apiKeySaved: "已保存，留空保持不变",
    apiKeyPlaceholder: "输入 API Key",
    apiKeyStored: "已安全保存",
    configured: "已配置",
    transcribeOnly: "未配置；仍可进行转写和 OCR",
    selectVideos: "选择作品",
    selected: "已选",
    close: "关闭设置",
    inputMethods: "导入方式",
    chooseLocal: "选择本地视频",
    chooseMixed: "选择混合素材",
    workspaceMenu: "选择保存工作区",
    taskDetailsHint: "点击任务查看详情",
    taskFailed: "处理失败，打开详情查看下一步",
    taskProcessing: "正在处理",
    noBrowserVideo: "没有可交给浏览器接管的视频链接。",
    saveStatus: "已加密保存在本机。",
    connectSuccess: "连接成功",
    save: "保存",
    test: "测试连接",
    language: "English",
  },
  en: {
    tasks: "Tasks",
    settings: "Vision model settings",
    title: "Video to knowledge",
    subtitle: "Paste a link. Envoy handles the rest.",
    single: "Video link",
    creator: "Creator page",
    local: "Local video",
    mixed: "Mixed files",
    sourcePlaceholder: "Paste a video link, BV ID, or share link",
    creatorPlaceholder: "Paste a creator or playlist link",
    chooseOutput: "Output",
    both: "Markdown + HTML",
    markdown: "Markdown only",
    html: "HTML preview + Markdown index",
    submit: "Start",
    discover: "Find videos",
    upload: "Process files",
    workspace: "Workspace",
    recent: "Recent tasks",
    allTasks: "All tasks",
    noTasks: "No tasks yet",
    workspaceDefault: "New video library",
    workspaceSave: "Save to Envoy workspace",
    workspaceFirstUse: "Created on first use",
    dropVideo: "Drop videos here",
    chooseFile: "or click to choose",
    dropMixed: "Drop files here",
    mixedTypes: "Video, image, audio, document",
    selectedFiles: "selected",
    fileLimit: "up to",
    publicHint: "Public video links from major platforms.",
    creatorHint: "Choose videos from a creator or playlist.",
    localHint: "MP4, MOV, MKV, WebM and common video files.",
    mixedHint: "Combine video, images, audio and documents.",
    serviceUnavailable: "Envoy service is unavailable",
    modelApi: "Vision model API",
    savedLocally: "Key is encrypted and stored locally",
    baseUrl: "Base URL",
    modelName: "Model",
    protocol: "Protocol",
    apiKeySaved: "Saved; leave blank to keep",
    apiKeyPlaceholder: "Enter API key",
    apiKeyStored: "Saved securely",
    configured: "Configured",
    transcribeOnly: "Not configured; transcription and OCR remain available",
    selectVideos: "Choose videos",
    selected: "selected",
    close: "Close settings",
    inputMethods: "Import method",
    chooseLocal: "Choose local videos",
    chooseMixed: "Choose mixed files",
    workspaceMenu: "Choose workspace",
    taskDetailsHint: "Open a task for details",
    taskFailed: "Failed. Open for next steps.",
    taskProcessing: "Processing",
    noBrowserVideo: "No video link is available for browser recovery.",
    saveStatus: "Encrypted and saved locally.",
    connectSuccess: "Connected",
    save: "Save",
    test: "Test connection",
    language: "中文",
  },
} as const;

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

function isNetworkError(error: unknown) {
  return error instanceof TypeError || (error instanceof Error && /failed to fetch|networkerror|load failed/i.test(error.message));
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

function browserSessionIdFromError(message?: string | null) {
  const match = String(message || "").match(/browser session retained:\s*([0-9a-f-]{20,})/i);
  return match?.[1] || "";
}

function taskStatusLabel(status?: string | null, locale: Locale = "zh") {
  const labels = locale === "en"
    ? { success: "Done", failed: "Failed", running: "Processing", pending: "Queued", canceled: "Canceled", unknown: "Unknown" }
    : { success: "已完成", failed: "失败", running: "处理中", pending: "排队中", canceled: "已取消", unknown: "未知" };
  if (status === "success") return labels.success;
  if (status === "failed" || status === "dispatch_failed") return labels.failed;
  if (status === "running") return labels.running;
  if (status === "pending") return labels.pending;
  if (status === "canceled") return labels.canceled;
  return status || labels.unknown;
}

function taskTypeLabel(taskType?: string | null, locale: Locale = "zh") {
  if (locale === "en") {
    if (taskType === "video_to_markdown") return "Video to Markdown";
    if (taskType === "video_batch_to_markdown") return "Batch video library";
    if (taskType === "mixed_assets_to_markdown") return "Mixed materials";
    return taskType || "Processing task";
  }
  if (taskType === "video_to_markdown") return "视频转 Markdown";
  if (taskType === "video_batch_to_markdown") return "批量视频整理";
  if (taskType === "mixed_assets_to_markdown") return "混合素材整理";
  return taskType || "处理任务";
}

function shortTaskTime(value?: string | null) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function VideoPage() {
  const [locale, setLocale] = useState<Locale>('zh');
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [mode, setMode] = useState<"single" | "creator" | "local" | "mixed">("single");
  const [outputMode, setOutputMode] = useState<OutputMode>("both");
  const [localFiles, setLocalFiles] = useState<File[]>([]);
  const [mixedFiles, setMixedFiles] = useState<File[]>([]);
  const [dragActive, setDragActive] = useState(false);
  const [candidates, setCandidates] = useState<VideoCandidate[]>([]);
  const [discoverySummary, setDiscoverySummary] = useState<DiscoverySummary | null>(null);
  const [selectedVideoUrls, setSelectedVideoUrls] = useState<string[]>([]);
  const [discovering, setDiscovering] = useState(false);
  const [discoveryRecovery, setDiscoveryRecovery] = useState<DiscoveryRecovery | null>(null);
  const [browserSessionId, setBrowserSessionId] = useState("");
  const [browserBusy, setBrowserBusy] = useState(false);
  const [task, setTask] = useState<Task | null>(null);
  const [recentTasks, setRecentTasks] = useState<Task[]>([]);
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
  const visionApiKeyRef = useRef<HTMLInputElement>(null);
  const [visionBusy, setVisionBusy] = useState(false);
  const [visionMessage, setVisionMessage] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const settingsRef = useRef<HTMLDivElement>(null);
  const workspaceRef = useRef<HTMLDivElement>(null);

  const outputFormats = outputMode === "both" ? ["markdown", "html"] : [outputMode];
  const copy = uiCopy[locale];

  const loadRecentTasks = async () => {
    try {
      const tasks = await apiJson<Task[]>("/api/v1/tasks");
      setRecentTasks(tasks.slice(0, 5));
    } catch {
      setRecentTasks([]);
    }
  };

  useEffect(() => {
    const savedLocale = window.localStorage.getItem("envoy:locale");
    if (savedLocale === "en") setLocale("en");
  }, []);

  useEffect(() => {
    document.documentElement.lang = locale === "zh" ? "zh-CN" : "en";
    window.localStorage.setItem("envoy:locale", locale);
  }, [locale]);

  useEffect(() => {
    const closeOnOutside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!settingsRef.current?.contains(target)) setSettingsOpen(false);
      if (!workspaceRef.current?.contains(target)) setWorkspaceOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSettingsOpen(false);
        setWorkspaceOpen(false);
      }
    };
    document.addEventListener("pointerdown", closeOnOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, []);

  useEffect(() => {
    let alive = true;
    const savedTaskId = window.localStorage.getItem(LAST_TASK_STORAGE_KEY);
    if (!savedTaskId) return () => {
      alive = false;
    };
    apiJson<Task>(`/api/v1/tasks/${savedTaskId}`)
      .then((savedTask) => {
        if (alive) setTask(savedTask);
      })
      .catch(() => {
        window.localStorage.removeItem(LAST_TASK_STORAGE_KEY);
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (task?.id) window.localStorage.setItem(LAST_TASK_STORAGE_KEY, task.id);
    const retainedSessionId = browserSessionIdFromError(task?.error_message);
    if (retainedSessionId) setBrowserSessionId(retainedSessionId);
  }, [task?.id, task?.error_message]);

  useEffect(() => {
    let alive = true;
    Promise.all([
      apiJson<Project[]>("/api/v1/projects"),
      apiJson<VisionSettings>("/api/v1/health/vision"),
      apiJson<Task[]>("/api/v1/tasks"),
    ])
      .then(([items, settings, tasks]) => {
        if (!alive) return;
        setProjects(items);
        if (items[0]) setProjectId(items[0].id);
        setVisionSettings(settings);
        setRecentTasks(tasks.slice(0, 5));
      })
      .catch((error) => {
        if (alive) {
          setMessage(isNetworkError(error)
            ? uiCopy[locale].serviceUnavailable
            : errorText(error));
        }
      });
    return () => {
      alive = false;
    };
  }, [locale]);

  useEffect(() => {
    const timer = window.setInterval(() => void loadRecentTasks(), 5000);
    return () => window.clearInterval(timer);
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
          api_key: visionApiKeyRef.current?.value || visionApiKey,
        }),
      });
      setVisionSettings(settings);
      setVisionApiKey("");
      if (visionApiKeyRef.current) visionApiKeyRef.current.value = "";
      setVisionMessage(copy.saveStatus);
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
      const enteredApiKey = visionApiKeyRef.current?.value || visionApiKey;
      if (!visionSettings.api_key_configured && !enteredApiKey.trim()) {
        setVisionMessage(locale === "zh" ? "请填写 API Key 后测试连接。" : "Enter an API key to test the connection.");
        return;
      }
      if (enteredApiKey) {
        const settings = await apiJson<VisionSettings>("/api/v1/health/vision/config", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            base_url: visionSettings.base_url,
            model: visionSettings.model,
            protocol: visionSettings.protocol,
            api_key: enteredApiKey,
          }),
        });
        setVisionSettings(settings);
        setVisionApiKey("");
        if (visionApiKeyRef.current) visionApiKeyRef.current.value = "";
      }
      const result = await apiJson<{ status: string; model: string; response?: string }>("/api/v1/health/vision/test", {
        method: "POST",
      });
      setVisionMessage(`${copy.connectSuccess}: ${result.model}`);
    } catch (error) {
      setVisionMessage(errorText(error));
    } finally {
      setVisionBusy(false);
    }
  };

  const activeTaskId = task?.id;
  const activeTaskStatus = task?.status;

  useEffect(() => {
    if (!activeTaskId || !["pending", "running"].includes(activeTaskStatus || "")) return;
    let alive = true;
    const timer = window.setInterval(() => {
      apiJson<Task>(`/api/v1/tasks/${activeTaskId}`)
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
  }, [activeTaskId, activeTaskStatus]);

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
        body: JSON.stringify({
          project_id: selectedId,
          source_url: url,
          auth: { mode: "auto" },
          input_config: { output_formats: outputFormats },
        }),
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
        body: JSON.stringify({
          project_id: selectedId,
          title: "本地视频知识库",
          items,
          input_config: { output_formats: outputFormats },
        }),
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

  const acceptDroppedFiles = (files: File[]) => {
    if (mode === "local") {
      setLocalFiles(files.filter((file) => mixedAssetType(file) === "video").slice(0, 20));
    } else if (mode === "mixed") {
      setMixedFiles(files.slice(0, 50));
    }
    setDragActive(false);
  };

  const handleDrop = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    acceptDroppedFiles(Array.from(event.dataTransfer.files));
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
      const append = task?.status === "success" && [
        "mixed_assets_to_markdown",
        "video_to_markdown",
        "video_batch_to_markdown",
      ].includes(task.task_type);
      const endpoint = append && task
        ? `/api/v1/tasks/${task.id}/append-assets`
        : "/api/v1/tasks/mixed-library-to-markdown";
      const body = append
        ? { asset_ids: assetIds, title: task?.input_config?.library_title }
        : {
            project_id: selectedId,
            title: "混合素材知识库",
            asset_ids: assetIds,
            input_config: { output_formats: outputFormats },
          };
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
    setBrowserSessionId("");
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

  const startBrowserDiscovery = async () => {
    setBrowserBusy(true);
    setMessage("");
    try {
      const browserSourceUrl = sourceUrl.trim() || task?.input_config?.source_url || "";
      if (!browserSourceUrl) {
        setMessage("没有可交给浏览器接管的视频链接。");
        return;
      }
      const result = await apiJson<{ session_id: string; message?: string }>("/api/v1/video-playlists/browser-sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_url: browserSourceUrl, limit: 100 }),
        timeoutMs: 120000,
      });
      setBrowserSessionId(result.session_id);
      setMessage(result.message || "本机浏览器已打开，请完成登录或验证后继续。");
    } catch (error) {
      setMessage(errorText(error));
    } finally {
      setBrowserBusy(false);
    }
  };

  const retryWithBrowserSession = async () => {
    if (!browserSessionId || !task) return;
    setBusy(true);
    setMessage("");
    try {
      const selectedId = await ensureProjectId();
      const created = await apiJson<Task>("/api/v1/tasks/video-to-markdown", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: selectedId,
          source_url: task.input_config?.source_url || sourceUrl.trim(),
          title: task.input_config?.title || "",
          auth: { mode: "browser_session", session_id: browserSessionId },
        }),
      });
      setTask(created);
      setMessage("已使用浏览器当前会话重新开始处理。");
    } catch (error) {
      setMessage(errorText(error));
    } finally {
      setBusy(false);
    }
  };

  const discoverFromBrowser = async () => {
    if (!browserSessionId) return;
    setBrowserBusy(true);
    setMessage("");
    try {
      const result = await apiJson<DiscoverySummary & {
        items: VideoCandidate[];
        status: string;
        requires_user_action: boolean;
      }>(`/api/v1/video-playlists/browser-sessions/${browserSessionId}/discover`, {
        method: "POST",
        timeoutMs: 120000,
      });
      if (result.requires_user_action || !result.items.length) {
        setDiscoveryRecovery((current) => ({
          ...(current || { reason_code: "challenge_required", requires_user_action: true }),
          requires_user_action: true,
          raw_error: "页面仍需要完成登录、验证码或安全确认。",
        }));
        setMessage("页面仍需要完成登录或安全确认。");
        return;
      }
      setCandidates(result.items);
      setSelectedVideoUrls(result.items.slice(0, 20).map((item) => item.source_url));
      setDiscoverySummary(result);
      setDiscoveryRecovery(null);
      setMessage(`浏览器已读取 ${result.items.length} 条可见作品。`);
    } catch (error) {
      setMessage(errorText(error));
    } finally {
      setBrowserBusy(false);
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
        ? { items, title: task?.input_config?.library_title, input_config: { output_formats: outputFormats } }
        : {
            project_id: selectedId,
            title: "视频知识库",
            items,
            input_config: { output_formats: outputFormats },
          };
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
  const selectedProject = projects.find((project) => project.id === projectId);
  const visionConfigured = visionSettings.configured && visionSettings.api_key_configured;
  const modeHint = mode === "single"
    ? copy.publicHint
    : mode === "creator"
      ? copy.creatorHint
      : mode === "local"
        ? copy.localHint
        : copy.mixedHint;

  return (
    <main className={styles.app}>
      <header className={styles.topbar}>
        <Link href="/video" className={styles.brand} aria-label="Envoy">
          <Image src="/brand/envoy-bird.png" alt="" width={28} height={28} className={styles.brandMark} priority />
          <span>ENVOY</span>
        </Link>
        <div className={styles.topActions}>
          <Link href="/tasks" className={styles.tasksLink}>
            <ListChecks size={17} aria-hidden="true" />
            <span>{copy.tasks}</span>
            <ArrowUpRight size={14} aria-hidden="true" />
          </Link>
          <button
            type="button"
            className={styles.languageButton}
            onClick={() => setLocale(locale === 'zh' ? 'en' : 'zh')}
            aria-label={locale === "zh" ? "Switch to English" : "切换为中文"}
          >
            {copy.language}
          </button>
          <div className={styles.settingsAnchor} ref={settingsRef}>
            <button
              type="button"
              className={styles.iconButton}
              aria-label={copy.settings}
              aria-expanded={settingsOpen}
              aria-haspopup="dialog"
              title={visionConfigured ? (locale === "zh" ? "视觉模型已配置" : "Vision model ready") : copy.settings}
              onClick={() => setSettingsOpen((open) => !open)}
            >
              <Settings size={19} aria-hidden="true" />
              <span className={visionConfigured ? styles.configReady : styles.configWarning} aria-hidden="true">
                {visionConfigured ? <Check size={11} strokeWidth={3} /> : <AlertCircle size={12} strokeWidth={3} />}
              </span>
            </button>
            {settingsOpen ? (
              <section className={styles.settingsPanel} role="dialog" aria-label={copy.settings}>
                <header className={styles.panelHeader}>
                  <div>
                    <h2 className={styles.panelTitle}>{copy.modelApi}</h2>
                    <p className={styles.panelNote}>{copy.savedLocally}</p>
                  </div>
                  <button type="button" className={styles.panelClose} onClick={() => setSettingsOpen(false)} aria-label={copy.close}>
                    <X size={18} aria-hidden="true" />
                  </button>
                </header>
                <div className={styles.settingsFields}>
                  <label className={`${styles.field} ${styles.fieldFull}`}>
                    {copy.baseUrl}
                    <input
                      value={visionSettings.base_url}
                      onChange={(event) => setVisionSettings((old) => ({ ...old, base_url: event.target.value }))}
                      placeholder="https://api.example.com/v1"
                      autoComplete="url"
                      className={styles.input}
                    />
                  </label>
                  <label className={styles.field}>
                    {copy.modelName}
                    <input
                      value={visionSettings.model}
                      onChange={(event) => setVisionSettings((old) => ({ ...old, model: event.target.value }))}
                      placeholder={locale === "zh" ? "服务商模型 ID" : "Provider model ID"}
                      autoComplete="off"
                      className={styles.input}
                    />
                  </label>
                  <label className={styles.field}>
                    {copy.protocol}
                    <select
                      value={visionSettings.protocol}
                      onChange={(event) => setVisionSettings((old) => ({ ...old, protocol: event.target.value as "chat" | "messages" }))}
                      className={styles.input}
                    >
                      <option value="chat">Chat Completions</option>
                      <option value="messages">Anthropic Messages</option>
                    </select>
                  </label>
                  <label className={`${styles.field} ${styles.fieldFull}`}>
                    API Key {visionSettings.api_key_configured ? `· ${copy.apiKeySaved}` : ""}
                    <input
                      ref={visionApiKeyRef}
                      type="password"
                      autoComplete="new-password"
                      value={visionApiKey}
                      onChange={(event) => setVisionApiKey(event.target.value)}
                      placeholder={visionSettings.api_key_configured ? copy.apiKeyStored : copy.apiKeyPlaceholder}
                      className={styles.input}
                    />
                  </label>
                </div>
                <div className={styles.panelActions}>
                  <button type="button" className={styles.primaryButton} onClick={() => void saveVisionSettings()} disabled={visionBusy}>
                    {visionBusy ? (locale === "zh" ? "保存中..." : "Saving...") : copy.save}
                  </button>
                  <button type="button" className={styles.secondaryButton} onClick={() => void testVisionSettings()} disabled={visionBusy}>
                    {visionBusy ? (locale === "zh" ? "连接中..." : "Testing...") : copy.test}
                  </button>
                  <span
                    role="status"
                    className={`${styles.status} ${visionMessage ? (visionMessage.startsWith(copy.connectSuccess) || visionMessage.startsWith(copy.saveStatus) ? styles.statusSuccess : styles.statusError) : ""}`}
                  >
                    {visionMessage || (visionConfigured
                      ? `${copy.configured} · ${visionSettings.model}`
                      : copy.transcribeOnly)}
                  </span>
                </div>
              </section>
            ) : null}
          </div>
        </div>
      </header>

      <section className={styles.workspace}>
        <div className={styles.intro}>
          <h1 className={styles.heading}>{copy.title}</h1>
          <p className={styles.subtitle}>{copy.subtitle}</p>
        </div>
        <div className={styles.modeSwitch} role="tablist" aria-label={copy.inputMethods}>
          {([
            ["single", copy.single],
            ["creator", copy.creator],
            ["local", copy.local],
          ] as const).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={mode === value}
              className={`${styles.modeButton} ${mode === value ? styles.modeButtonActive : ""}`}
              onClick={() => {
                setMode(value);
                setCandidates([]);
                setDiscoverySummary(null);
                setSelectedVideoUrls([]);
                setDiscoveryRecovery(null);
                setMessage("");
              }}
            >
              {label}
            </button>
          ))}
        </div>

        <form
          className={styles.sourceForm}
          onSubmit={(event) => {
            event.preventDefault();
            if (mode === "single") void submit(event);
            else if (mode === "creator") void discoverVideos();
            else if (mode === "local") void submitLocalVideos();
            else void submitMixedFiles();
          }}
        >
          {mode === "single" || mode === "creator" ? (
            <div className={styles.inputWithIcon}>
              <Link2 className={styles.inputIcon} size={18} aria-hidden="true" />
              <input
                id="video-url"
                type="text"
                required
                value={sourceUrl}
                onChange={(event) => setSourceUrl(event.target.value)}
                placeholder={mode === "single" ? copy.sourcePlaceholder : copy.creatorPlaceholder}
                aria-label={mode === "single" ? copy.single : copy.creator}
                className={`${styles.input} ${styles.sourceInput}`}
              />
            </div>
          ) : mode === "local" ? (
            <label
              htmlFor="local-videos"
              className={`${styles.dropZone} ${dragActive ? styles.dropZoneActive : ""}`}
              onDragEnter={(event) => {
                event.preventDefault();
                setDragActive(true);
              }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={(event) => {
                if (event.currentTarget === event.target) setDragActive(false);
              }}
              onDrop={handleDrop}
            >
              <input
                id="local-videos"
                type="file"
                multiple
                accept=".3gp,.avi,.flv,.m2ts,.m4v,.mkv,.mov,.mp4,.mpeg,.mpg,.mts,.mxf,.ogv,.ts,.webm,.wmv,video/*"
                onChange={(event) => acceptDroppedFiles(Array.from(event.target.files ?? []))}
                aria-label={copy.chooseLocal}
                className={styles.fileInputHidden}
              />
              <strong>{copy.dropVideo}</strong>
              <span>{copy.chooseFile} · {copy.fileLimit} 20</span>
              {localFiles.length ? <em>{localFiles.length} {locale === "zh" ? "个视频" : "videos"} {copy.selectedFiles}</em> : null}
            </label>
          ) : (
            <label
              htmlFor="mixed-files"
              className={`${styles.dropZone} ${dragActive ? styles.dropZoneActive : ""}`}
              onDragEnter={(event) => {
                event.preventDefault();
                setDragActive(true);
              }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={(event) => {
                if (event.currentTarget === event.target) setDragActive(false);
              }}
              onDrop={handleDrop}
            >
              <input
                id="mixed-files"
                type="file"
                multiple
                accept=".3gp,.avi,.flv,.m2ts,.m4v,.mkv,.mov,.mp4,.mpeg,.mpg,.mts,.mxf,.ogv,.ts,.webm,.wmv,.png,.jpg,.jpeg,.webp,.gif,.bmp,.tif,.tiff,.mp3,.wav,.m4a,.aac,.ogg,.flac,.pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.md,.markdown,.txt,video/*,audio/*,image/*,text/plain,application/pdf"
                onChange={(event) => acceptDroppedFiles(Array.from(event.target.files ?? []))}
                aria-label={copy.chooseMixed}
                className={styles.fileInputHidden}
              />
              <strong>{copy.dropMixed}</strong>
              <span>{copy.mixedTypes} · {copy.fileLimit} 50</span>
              {mixedFiles.length ? <em>{mixedFiles.length} {copy.selectedFiles}</em> : null}
            </label>
          )}
          <button type="submit" className={styles.primaryButton} disabled={busy || Boolean(active) || discovering}>
            {busy
              ? (locale === "zh" ? "处理中..." : "Processing...")
              : active
                ? (locale === "zh" ? "任务处理中" : "In progress")
                : mode === "single"
                  ? copy.submit
                  : mode === "creator"
                    ? discovering ? (locale === "zh" ? "正在发现..." : "Finding...") : copy.discover
                    : mode === "local"
                      ? `${copy.upload}${localFiles.length ? ` (${localFiles.length})` : ""}`
                      : `${copy.upload}${mixedFiles.length ? ` (${mixedFiles.length})` : ""}`}
          </button>
          <label className={styles.outputSelect}>
            <span>{copy.chooseOutput}</span>
            <select value={outputMode} onChange={(event) => setOutputMode(event.target.value as OutputMode)}>
              <option value="both">{copy.both}</option>
              <option value="markdown">{copy.markdown}</option>
              <option value="html">{copy.html}</option>
            </select>
          </label>
        </form>

        <div className={styles.belowForm}>
          <div className={styles.workspaceAnchor} ref={workspaceRef}>
            <button
              type="button"
              className={styles.workspaceButton}
              aria-expanded={workspaceOpen}
              aria-haspopup="menu"
              onClick={() => setWorkspaceOpen((open) => !open)}
            >
              <Folder size={16} aria-hidden="true" />
              <span>{copy.workspace}</span>
              <strong className={styles.workspaceName}>{selectedProject?.name || copy.workspaceDefault}</strong>
              <ChevronDown size={14} aria-hidden="true" />
            </button>
            {workspaceOpen ? (
              <div className={styles.workspaceMenu} role="menu" aria-label={copy.workspaceMenu}>
                <div className={styles.menuHeading}>{copy.workspaceSave}</div>
                {projects.map((project) => (
                  <button
                    key={project.id}
                    type="button"
                    role="menuitem"
                    aria-current={project.id === projectId ? "true" : undefined}
                    className={`${styles.menuItem} ${project.id === projectId ? styles.menuItemSelected : ""}`}
                    onClick={() => {
                      setProjectId(project.id);
                      setWorkspaceOpen(false);
                    }}
                  >
                    <span>{project.name}</span>
                    {project.id === projectId ? <Check size={16} aria-hidden="true" /> : null}
                  </button>
                ))}
                {!projects.length ? <div className={styles.menuHeading}>{copy.workspaceFirstUse}</div> : null}
              </div>
            ) : null}
          </div>
          <span className={styles.modeHint}>{modeHint}</span>
        </div>

        {mode === "creator" && candidates.length > 0 ? (
          <section className={styles.candidateSection} aria-label={copy.selectVideos}>
            <div className={styles.taskHeader}>
              <h2 className={styles.sectionHeading}>
                {discoverySummary?.creator_title || copy.selectVideos}
              </h2>
              <span className={styles.sectionMeta}>{selectedVideoUrls.length}/20 {copy.selected}</span>
            </div>
            <p className={styles.sectionMeta}>
              {discoverySummary?.platform || "视频平台"} · {discoverySummary?.discovery_method || "作品发现"}
            </p>
            <div className={styles.candidateList}>
              {candidates.map((item) => {
                const selected = selectedVideoUrls.includes(item.source_url);
                const disabled = !selected && selectedVideoUrls.length >= 20;
                return (
                  <label key={item.source_url} className={styles.candidateRow}>
                    <input
                      type="checkbox"
                      checked={selected}
                      disabled={disabled}
                      onChange={(event) => setSelectedVideoUrls((current) => event.target.checked
                        ? [...current, item.source_url]
                        : current.filter((url) => url !== item.source_url))}
                    />
                    <span>
                      <span className={styles.candidateTitle}>{item.title}</span>
                      <span className={styles.candidateUrl}>{item.source_url}</span>
                    </span>
                  </label>
                );
              })}
            </div>
            <button type="button" className={styles.primaryButton} onClick={() => void submitBatch(appendToCompleted)} disabled={busy || !selectedVideoUrls.length}>
              {appendToCompleted ? "追加到知识库" : "整理所选作品"}
            </button>
          </section>
        ) : null}

        {message ? (
          <div role="status" className={`${styles.message} ${message.includes("完成") || message.includes("成功") || message.includes("已打开") || message.includes("已读取") ? styles.messageSuccess : ""}`}>
            {message}
          </div>
        ) : null}

        <section className={styles.recentTasks} aria-label="最近任务">
          <div className={styles.recentTasksHeader}>
            <div>
              <h2 className={styles.sectionHeading}>{copy.recent}</h2>
          <p className={styles.sectionMeta}>{copy.taskDetailsHint}</p>
            </div>
            <Link href="/tasks" className={styles.secondaryButton}>
              {copy.allTasks}
              <ArrowUpRight size={15} aria-hidden="true" />
            </Link>
          </div>
          {recentTasks.length ? (
            <div className={styles.recentTaskList}>
              {recentTasks.map((recentTask) => {
                const progress = Math.max(0, Math.min(Number(recentTask.progress ?? 0), 100));
                return (
                  <Link key={recentTask.id} href={`/tasks/${recentTask.id}`} className={styles.recentTaskRow}>
                    <div className={styles.recentTaskMain}>
                      <div className={styles.recentTaskTitle}>
                        <strong>{taskTypeLabel(recentTask.task_type, locale)}</strong>
                        <span className={`${styles.recentTaskStatus} ${recentTask.status === "failed" ? styles.recentTaskStatusFailed : recentTask.status === "success" ? styles.recentTaskStatusSuccess : ""}`}>
                          {taskStatusLabel(recentTask.status, locale)}
                        </span>
                      </div>
                      <div className={styles.recentTaskProgress}>
                        <span style={{ width: `${progress}%` }} />
                      </div>
                      <span className={styles.recentTaskSummary}>
                        {recentTask.result_summary || (recentTask.status === "failed" ? copy.taskFailed : copy.taskProcessing)}
                      </span>
                    </div>
                    <div className={styles.recentTaskMeta}>
                      <span>{shortTaskTime(recentTask.updated_at || recentTask.created_at)}</span>
                      <ArrowUpRight size={16} aria-hidden="true" />
                    </div>
                  </Link>
                );
              })}
            </div>
          ) : (
            <div className={styles.recentTasksEmpty}>{copy.noTasks}</div>
          )}
        </section>
      </section>
    </main>
  );
}
