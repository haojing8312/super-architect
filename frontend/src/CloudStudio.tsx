import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  BookOpen,
  Box,
  CheckCircle2,
  Cloud,
  Download,
  FileText,
  Image,
  Layers3,
  Loader2,
  Monitor,
  Plug,
  RefreshCw,
  Share2,
  Square,
  Upload,
  Video,
  Wrench,
  X,
} from "lucide-react";
import { api, ApiError, fileUrl, type Row } from "./api";
import "./CloudStudio.css";

export interface CloudStudioProps {
  toast: (message: string) => void;
  onError?: (message: string) => void;
  initialCapability?: string;
  onLocal?: (kind: string) => void;
  onRestore?: (item: Row) => void;
}
// Usage: <CloudStudio toast={toast} onError={setError} />
const text = (value: unknown): string =>
  typeof value === "string"
    ? value
    : typeof value === "number"
      ? String(value)
      : "";
const rows = (value: Row): Row[] =>
  Array.isArray(value.items) ? value.items : [];
const pretty = (value: unknown) => JSON.stringify(value ?? {}, null, 2);
const stamp = (value: unknown) =>
  value ? new Date(Number(value)).toLocaleString("zh-CN") : "—";
const errorText = (value: unknown) =>
  value instanceof Error ? value.message : "操作未完成，请刷新连接后重试";
const projectLabels: Record<string, string> = {
  knowledge: "知识库",
  canvas: "设计画布",
  documents: "文档与汇报",
  interior: "室内方案",
  creative: "创作项目",
};
const stateLabels: Record<string, string> = {
  queued: "已排队",
  running: "执行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};
const icons: Record<string, typeof Cloud> = {
  tools: Wrench,
  dialogue: BookOpen,
  quick: Image,
  canvas: Layers3,
  render: Image,
  video: Video,
  image_to_model: Box,
  plugin: Plug,
  knowledge: BookOpen,
  documents: FileText,
  interior: Layers3,
};
const originalCapabilities = [
  "tools",
  "dialogue",
  "quick",
  "canvas",
  "render",
  "video",
  "image_to_model",
  "plugin",
];
const activeJob = (job: Row) => ["queued", "running"].includes(job.state);
function objectInput(value: string, label: string): Row {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error(`${label}格式无效，请填写有效的 JSON 对象`);
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
    throw new Error(`${label}须为对象`);
  return parsed as Row;
}

export default function CloudStudio({
  toast,
  onError,
  initialCapability = "dialogue",
  onLocal,
  onRestore,
}: CloudStudioProps) {
  const [status, setStatus] = useState<Row | null>(null),
    [catalog, setCatalog] = useState<Row[]>([]),
    [projects, setProjects] = useState<Row[]>([]),
    [jobs, setJobs] = useState<Row[]>([]),
    [assets, setAssets] = useState<Row[]>([]),
    [devices, setDevices] = useState<Row[]>([]),
    [localObjects, setLocalObjects] = useState<Row[]>([]),
    [runtimes, setRuntimes] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(""),
    [error, setError] = useState(""),
    [section, setSection] = useState("create");
  const [pendingWorker, setPendingWorker] = useState<boolean | null>(null);
  const [restoredProject, setRestoredProject] = useState<Row | null>(null);
  const [capability, setCapability] = useState(initialCapability),
    [prompt, setPrompt] = useState(""),
    [provider, setProvider] = useState(
      initialCapability === "video" ? "codebuddy" : "codex",
    ),
    [model, setModel] = useState(""),
    [preset, setPreset] = useState(""),
    [options, setOptions] = useState<Row>({}),
    [advanced, setAdvanced] = useState("{}");
  const [projectId, setProjectId] = useState(""),
    [inputAssetIds, setInputAssetIds] = useState<string[]>([]),
    [projectName, setProjectName] = useState(""),
    [projectType, setProjectType] = useState("documents"),
    [documentDraft, setDocumentDraft] = useState("{}"),
    [editingRevision, setEditingRevision] = useState<number | null>(null),
    [conflict, setConflict] = useState(false),
    [versions, setVersions] = useState<Row[]>([]),
    [shares, setShares] = useState<Row[]>([]),
    [shareExpiry, setShareExpiry] = useState("86400"),
    [localObject, setLocalObject] = useState("");
  const [jobState, setJobState] = useState(""),
    [jobCapability, setJobCapability] = useState(""),
    [jobQuery, setJobQuery] = useState(""),
    [selectedJob, setSelectedJob] = useState<Row | null>(null);
  const alive = useRef(true),
    pendingRefresh = useRef<Promise<void> | null>(null),
    mutating = useRef(false),
    generation = useRef(0),
    fileInput = useRef<HTMLInputElement>(null),
    dialog = useRef<HTMLElement>(null),
    submittedRequest = useRef({ fingerprint: "", id: "" }),
    requestedCounts = useRef<Record<string, number>>({
      projects: 50,
      jobs: 50,
      assets: 50,
      devices: 50,
    }),
    callbacks = useRef({ toast, onError });
  callbacks.current = { toast, onError };
  const loggedIn = status?.logged_in === true,
    connected = loggedIn && status?.connected === true;
  const selectedProject = projects.find(
    (project) => text(project.id) === projectId,
  );
  const selectedCapability = catalog.find((item) => item.id === capability);
  const projectDocument = useMemo((): Row | null => {
    try {
      const value: unknown = JSON.parse(documentDraft);
      return value && typeof value === "object" && !Array.isArray(value)
        ? (value as Row)
        : null;
    } catch {
      return null;
    }
  }, [documentDraft]);
  const documentContentKey =
    ["content", "brief", "description"].find(
      (key) => typeof projectDocument?.[key] === "string",
    ) || "content";
  const referencedAssetIds = new Set<string>(
    Array.isArray(selectedProject?.asset_ids) ? selectedProject.asset_ids.map(text) : [],
  );
  const projectAssets = assets.filter(
    (asset) => text(asset.project_id) === projectId || referencedAssetIds.has(text(asset.id)),
  );
  const visibleJobs = useMemo(
    () =>
      jobs.filter(
        (job) =>
          (!jobState || job.state === jobState) &&
          (!jobCapability || job.capability === jobCapability) &&
          (!jobQuery ||
            `${text(job.prompt)} ${text(job.id)} ${text(job.summary)}`
              .toLowerCase()
              .includes(jobQuery.toLowerCase())),
      ),
    [jobs, jobState, jobCapability, jobQuery],
  );
  const report = useCallback((value: unknown) => {
    const message = errorText(value);
    if (alive.current) {
      setError(message);
      callbacks.current.onError?.(message);
    }
  }, []);
  const refresh = useCallback(
    (manual = false): Promise<void> => {
      if (pendingRefresh.current) return pendingRefresh.current;
      const request = (async () => {
        const ticket = generation.current;
        if (manual) {
          setLoading(true);
          setError("");
        }
        try {
          const connection = await api("/cloud/status");
          if (!alive.current || ticket !== generation.current) return;
          setStatus(connection);
          if (connection.logged_in !== true) {
            setCatalog([]);
            setProjects([]);
            setJobs([]);
            setAssets([]);
            setDevices([]);
            setLocalObjects([]);
            setSelectedJob(null);
            setShares([]);
            setVersions([]);
            setProjectId("");
            setProjectName("");
            setDocumentDraft("{}");
            setEditingRevision(null);
            setInputAssetIds([]);
            setLocalObject("");
            setConflict(false);
            setPrompt("");
            setRestoredProject(null);
            submittedRequest.current = { fingerprint: "", id: "" };
            return;
          }
          const paths = [
            "/cloud/catalog",
            "/cloud/projects",
            "/cloud/jobs",
            "/cloud/assets",
            "/cloud/devices",
            "/cloud/local-objects",
            "/runtimes",
          ];
          const results = await Promise.allSettled(
            paths.map(async (path) => {
              const count =
                requestedCounts.current[path.split("/").pop() || ""];
              if (!count) return api(path);
              const pages = await Promise.all(
                Array.from({ length: Math.ceil(count / 100) }, (_, index) =>
                  api(
                    `${path}?offset=${index * 100}&limit=${Math.min(100, count - index * 100)}`,
                  ),
                ),
              );
              return { items: pages.flatMap(rows) };
            }),
          );
          if (!alive.current || ticket !== generation.current) return;
          const setters = [
            setCatalog,
            setProjects,
            setJobs,
            setAssets,
            setDevices,
            setLocalObjects,
            setRuntimes,
          ];
          results.forEach((result, index) => {
            if (result.status === "fulfilled")
              setters[index](rows(result.value));
            else if (manual || index < 5) report(result.reason);
          });
          if (
            results.slice(0, 5).some((result) => result.status === "rejected")
          )
            setStatus({ ...connection, connected: false });
        } catch (failure) {
          if (alive.current && ticket === generation.current) {
            setStatus(null);
            report(failure);
          }
        } finally {
          if (alive.current && ticket === generation.current) setLoading(false);
        }
      })();
      pendingRefresh.current = request;
      void request.finally(() => {
        if (pendingRefresh.current === request) pendingRefresh.current = null;
      });
      return request;
    },
    [report],
  );
  useEffect(() => {
    alive.current = true;
    void refresh(true);
    const timer = window.setInterval(() => {
      if (!document.hidden && !mutating.current) void refresh();
    }, 5000);
    return () => {
      alive.current = false;
      generation.current++;
      window.clearInterval(timer);
    };
  }, [refresh]);
  useEffect(() => {
    if (
      originalCapabilities.includes(initialCapability) ||
      ["knowledge", "documents", "interior"].includes(initialCapability)
    ) {
      setCapability(initialCapability);
      setPreset("");
      setOptions({});
      setAdvanced("{}");
      if (initialCapability === "video") setProvider("codebuddy");
      setSection("create");
    }
  }, [initialCapability]);
  useEffect(() => {
    if (selectedJob) {
      const latest = jobs.find((job) => job.id === selectedJob.id);
      if (latest) setSelectedJob(latest);
    }
  }, [jobs, selectedJob]);
  useEffect(() => {
    if (!selectedJob) return;
    const previous =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const focusable = () =>
      Array.from(
        dialog.current?.querySelectorAll<HTMLElement>(
          'button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),summary,[tabindex="0"]',
        ) || [],
      );
    focusable()[0]?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setSelectedJob(null);
      }
      if (event.key === "Tab") {
        const elements = focusable(),
          first = elements[0],
          last = elements[elements.length - 1];
        if (!first) {
          event.preventDefault();
          return;
        }
        if (
          event.shiftKey &&
          (document.activeElement === first ||
            !dialog.current?.contains(document.activeElement))
        ) {
          event.preventDefault();
          last.focus();
        } else if (
          !event.shiftKey &&
          (document.activeElement === last ||
            !dialog.current?.contains(document.activeElement))
        ) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      previous?.focus();
    };
  }, [selectedJob?.id]);
  const run = async (label: string, operation: () => Promise<void>) => {
    if (mutating.current) return;
    if (!connected) {
      report(new Error("请先在账号与管理端登录会员，并刷新云端连接"));
      return;
    }
    mutating.current = true;
    generation.current++;
    setBusy(label);
    setError("");
    try {
      await operation();
      if (pendingRefresh.current) await pendingRefresh.current;
      await refresh();
    } catch (failure) {
      report(failure);
    } finally {
      mutating.current = false;
      if (alive.current) setBusy("");
    }
  };
  const chooseProject = async (id: string) => {
    setProjectId(id);
    setInputAssetIds([]);
    setVersions([]);
    setShares([]);
    setConflict(false);
    setRestoredProject(null);
    const project = projects.find((item) => text(item.id) === id);
    setProjectName(project?.name || "");
    setProjectType(project?.type || "documents");
    setDocumentDraft(pretty(project?.document));
    setEditingRevision(project ? Number(project.revision) : null);
  };
  const chooseCapability = (item: Row) => {
    setCapability(item.id);
    setPreset("");
    setOptions({});
    setAdvanced("{}");
    if (item.id === "video") setProvider("codebuddy");
    setSection("create");
  };
  const applyTemplate = () => {
    if (!selectedCapability?.template) return;
    setPrompt(text(selectedCapability.template.prompt));
    setOptions(selectedCapability.template.options || {});
    setPreset(selectedCapability.id);
  };
  const createJob = async () => {
    if (!selectedProject) throw new Error("先创建或选择云项目，再提交任务");
    if (!selectedCapability?.enabled)
      throw new Error("此能力当前未启用，请选择可用能力");
    if (!prompt.trim()) throw new Error("请填写任务描述");
    if (capability === "video" && provider !== "codebuddy")
      throw new Error("视频任务请选择 CodeBuddy 执行设备");
    const extra = objectInput(advanced, "补充参数");
    const payload = {
      project_id: projectId,
      capability,
      prompt: prompt.trim(),
      input_asset_ids: inputAssetIds,
      options: { ...options, ...extra, provider, model: model.trim(), preset },
    };
    const fingerprint = JSON.stringify(payload);
    if (submittedRequest.current.fingerprint !== fingerprint)
      submittedRequest.current = { fingerprint, id: crypto.randomUUID() };
    const submitted = await api("/cloud/jobs", {
      ...payload,
      request_id: submittedRequest.current.id,
    });
    if (!text(submitted.id) || !stateLabels[submitted.state])
      throw new Error("任务没有返回有效记录，请刷新任务列表核对");
    submittedRequest.current = { fingerprint: "", id: "" };
    setSelectedJob(submitted);
    setSection("jobs");
    callbacks.current.toast(
      submitted.state === "queued"
        ? "任务已排队，等待已启用的执行设备"
        : `任务状态：${stateLabels[submitted.state]}`,
    );
  };
  const saveProject = async () => {
    if (!projectName.trim()) throw new Error("请填写项目名称");
    const document = objectInput(documentDraft, "项目内容");
    try {
      const saved =
        editingRevision !== null && projectId
          ? await api(
              `/cloud/projects/${encodeURIComponent(projectId)}`,
              {
                base_revision: editingRevision,
                name: projectName.trim(),
                document,
                asset_ids: selectedProject?.asset_ids || [],
              },
              "PUT",
            )
          : await api("/cloud/projects", {
              name: projectName.trim(),
              type: projectType,
              document,
              asset_ids: [],
            });
      if (!saved.id || !Number.isFinite(Number(saved.revision)))
        throw new Error("项目未返回有效版本，请刷新核对");
      setProjectId(text(saved.id));
      setEditingRevision(Number(saved.revision));
      setDocumentDraft(pretty(saved.document));
      setConflict(false);
      callbacks.current.toast(`项目已保存 · 版本 ${saved.revision}`);
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 409) {
        setConflict(true);
        throw new Error(
          "项目已被其他设备更新。你的草稿已保留；先查看最新版本，再合并后保存。",
        );
      }
      throw failure;
    }
  };
  const uploadAsset = async (file: File) => {
    if (!projectId) throw new Error("先选择云项目，再上传素材");
    const data = new FormData();
    data.append("file", file);
    data.append("project_id", projectId);
    data.append("role", "reference");
    const saved = await api("/cloud/assets", data);
    if (!saved.id) throw new Error("素材未返回有效记录，请刷新核对");
    setInputAssetIds((previous) => [...previous, text(saved.id)]);
    callbacks.current.toast("参考素材已上传，可以选择它创建任务");
  };
  const download = (asset: Row) =>
    void run("下载成果", async () => {
      const url = await fileUrl(
        `/api/cloud/assets/${encodeURIComponent(text(asset.id))}/content`,
      );
      const link = document.createElement("a");
      link.href = url;
      link.download = text(asset.filename);
      link.click();
    });
  const loadVersions = () =>
    void run("项目版本", async () => {
      if (!projectId) throw new Error("请先选择项目");
      setVersions(
        rows(
          await api(
            `/cloud/projects/${encodeURIComponent(projectId)}/versions`,
          ),
        ),
      );
    });
  const loadShares = () =>
    void run("分享列表", async () => {
      if (!projectId) throw new Error("请先选择项目");
      setShares(
        rows(
          await api(`/cloud/projects/${encodeURIComponent(projectId)}/shares`),
        ),
      );
    });
  const runtimeLabel = (runtime: Row) =>
    runtime.installed
      ? `${runtime.provider === "codebuddy" ? "CodeBuddy" : "Codex"} · 已检测到`
      : `${runtime.provider === "codebuddy" ? "CodeBuddy" : "Codex"} · 本机未检测到`;
  const configure = (key: string, value: unknown) =>
    setOptions((previous) => ({ ...previous, [key]: value }));
  const loadMore = (resource: string) =>
    void run("加载更多", async () => {
      requestedCounts.current[resource] += 50;
    });
  const progress = (job: Row) =>
    typeof job.progress === "number" && Number.isFinite(job.progress)
      ? Math.max(0, Math.min(100, job.progress))
      : null;
  const renderAsset = (asset: Row) => (
    <div className="cloud-asset" key={asset.id}>
      <FileText size={17} />
      <div>
        <strong>{text(asset.filename) || text(asset.id)}</strong>
        <span>
          {text(asset.format || asset.mime)} ·{" "}
          {Number.isFinite(Number(asset.size_bytes))
            ? `${(Number(asset.size_bytes) / 1024).toFixed(1)} KB`
            : "大小未提供"}
        </span>
      </div>
      <button
        type="button"
        className="cloud-button"
        disabled={!!busy || !connected}
        onClick={() => download(asset)}
      >
        <Download size={14} />
        下载
      </button>
    </div>
  );
  return (
    <div className="studio cloud-studio">
      <header className="cloud-heading">
        <div>
          <div className="cloud-eyebrow">
            <Cloud size={15} />
            项目与成果，跨设备接续
          </div>
          <h2>云端项目</h2>
          <p>保存设计项目，把任务交给你已启用的设备，完成后在这里取回成果。</p>
        </div>
        <div className="cloud-heading-actions">
          {onLocal && originalCapabilities.includes(capability) && (
            <button
              className="cloud-button"
              disabled={!!busy}
              onClick={() => onLocal(capability)}
            >
              本机直接创作
            </button>
          )}
          <button
            className="cloud-button"
            disabled={loading || !!busy}
            onClick={() => void refresh(true)}
          >
            <RefreshCw size={15} className={loading ? "spin" : ""} />
            {loading ? "连接中…" : "刷新"}
          </button>
        </div>
      </header>
      <section className="cloud-connection" aria-label="云端连接状态">
        <div
          className={"cloud-connection-state " + (connected ? "connected" : "")}
        >
          <span />
          <strong>
            {loading && status === null
              ? "正在检查连接"
              : !loggedIn
                ? "会员会话未连接"
                : connected
                  ? "云端已连接"
                  : "云端连接暂不可用"}
          </strong>
        </div>
        <p>
          {!loggedIn
            ? "先到“账号与管理端”登录会员，再使用云项目与任务。"
            : "只有你主动启用的执行设备才会领取任务。你的本机 Agent 登录信息保留在此设备。"}
        </p>
        <label className="cloud-switch">
          <input
            type="checkbox"
            checked={pendingWorker ?? status?.worker?.enabled === true}
            disabled={!connected || !!busy}
            onChange={(event) => {
              const enabled = event.target.checked;
              setPendingWorker(enabled);
              void run("执行设备", async () => {
                await api("/cloud/worker", { enabled }, "PUT");
                callbacks.current.toast(
                  enabled ? "本机执行设备已启用" : "本机执行设备已关闭",
                );
              }).finally(() => {
                if (alive.current) setPendingWorker(null);
              });
            }}
          />
          <span>允许本机执行云任务</span>
        </label>
        {status?.worker?.last_error && (
          <div className="cloud-note cloud-worker-error">
            {text(status.worker.last_error)}
          </div>
        )}
      </section>
      {error && (
        <div className="cloud-error" role="alert">
          <AlertCircle size={16} />
          <span>{error}</span>
          <button aria-label="关闭错误提示" onClick={() => setError("")}>
            <X size={15} />
          </button>
        </div>
      )}
      <nav className="cloud-tabs" aria-label="云端工作区">
        {[
          ["create", "创建任务"],
          ["jobs", `任务记录${jobs.length ? ` · ${jobs.length}` : ""}`],
          ["projects", "云项目"],
          ["devices", "执行设备"],
        ].map(([id, name]) => (
          <button
            key={id}
            aria-current={section === id ? "page" : undefined}
            className={section === id ? "active" : ""}
            onClick={() => setSection(id)}
          >
            {name}
          </button>
        ))}
      </nav>
      {section === "create" && (
        <>
          <div className="cloud-capabilities">
            {originalCapabilities.map((id) => {
              const item = catalog.find((entry) => entry.id === id),
                Icon = icons[id] || Cloud;
              return item ? (
                <button
                  key={id}
                  className={capability === id ? "selected" : ""}
                  disabled={!connected || !item.enabled || !!busy}
                  onClick={() => chooseCapability(item)}
                >
                  <Icon size={20} />
                  <strong>{text(item.name)}</strong>
                  <span>{item.enabled ? "选择能力" : "暂未启用"}</span>
                </button>
              ) : null;
            })}
          </div>
          {!loading && catalog.length === 0 && (
            <div className="cloud-empty">
              {loggedIn
                ? "能力目录尚未加载。请刷新连接，或联系管理员启用能力。"
                : "登录会员后显示可用能力。"}
            </div>
          )}
          <section className="cloud-card">
            <div className="cloud-card-heading">
              <h3>{text(selectedCapability?.name) || "创建云端任务"}</h3>
              <span className="cloud-note">提交后等待执行设备领取</span>
            </div>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void run("创建任务", createJob);
              }}
            >
              <div className="cloud-grid">
                <label className="cloud-field">
                  <span>云项目</span>
                  <select
                    aria-label="云项目"
                    required
                    disabled={!connected || !!busy}
                    value={projectId}
                    onChange={(event) => void chooseProject(event.target.value)}
                  >
                    <option value="">选择项目</option>
                    {projects.map((project) => (
                      <option key={project.id} value={text(project.id)}>
                        {text(project.name)} ·{" "}
                        {projectLabels[project.type] || text(project.type)}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="cloud-field">
                  <span>能力</span>
                  <select
                    aria-label="能力"
                    disabled={!connected || !!busy}
                    value={capability}
                    onChange={(event) => {
                      const item = catalog.find(
                        (entry) => entry.id === event.target.value,
                      );
                      if (item) chooseCapability(item);
                    }}
                  >
                    {catalog.map((item) => (
                      <option
                        key={item.id}
                        value={item.id}
                        disabled={!item.enabled}
                      >
                        {text(item.name)}
                        {!item.enabled ? " · 未启用" : ""}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="cloud-field">
                  <span>执行工具</span>
                  <select
                    aria-label="执行工具"
                    disabled={!connected || !!busy || capability === "video"}
                    value={provider}
                    onChange={(event) => setProvider(event.target.value)}
                  >
                    <option value="codex">Codex</option>
                    <option value="codebuddy">CodeBuddy</option>
                  </select>
                  <small>
                    {runtimes.length
                      ? runtimes.map(runtimeLabel).join("；")
                      : "设备是否就绪可在“执行设备”查看。"}
                  </small>
                </label>
                <label className="cloud-field">
                  <span>Agent 模型（可选）</span>
                  <input
                    aria-label="Agent 模型（可选）"
                    disabled={!connected || !!busy}
                    value={model}
                    onChange={(event) => setModel(event.target.value)}
                    placeholder="留空使用执行设备当前模型"
                  />
                </label>
              </div>
              <label className="cloud-field">
                <span>任务描述</span>
                <textarea
                  aria-label="任务描述"
                  required
                  disabled={!connected || !!busy}
                  rows={6}
                  value={prompt}
                  onChange={(event) => setPrompt(event.target.value)}
                  placeholder="说明需要什么成果、使用哪些参考和必须保留的要求。"
                />
              </label>
              {selectedCapability?.template && (
                <div className="cloud-template">
                  <button
                    type="button"
                    className="cloud-button"
                    disabled={!connected || !!busy}
                    onClick={applyTemplate}
                  >
                    使用管理员模板
                  </button>
                  <span>
                    {preset
                      ? "已应用模板；你可以继续修改描述与参数。"
                      : "应用后填入任务描述与默认参数。"}
                  </span>
                </div>
              )}
              <div className="cloud-reference-heading">
                <strong>引用素材</strong>
                <button
                  type="button"
                  className="cloud-button"
                  disabled={!connected || !!busy || !projectId}
                  onClick={() => fileInput.current?.click()}
                >
                  <Upload size={14} />
                  上传素材
                </button>
              </div>
              <input
                ref={fileInput}
                hidden
                type="file"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = "";
                  if (file) void run("上传素材", () => uploadAsset(file));
                }}
              />
              {projectAssets.filter((asset) => asset.role !== "result")
                .length ? (
                <div className="cloud-references">
                  {projectAssets
                    .filter((asset) => asset.role !== "result")
                    .map((asset) => (
                      <label key={asset.id}>
                        <input
                          type="checkbox"
                          disabled={!connected || !!busy}
                          checked={inputAssetIds.includes(text(asset.id))}
                          onChange={(event) =>
                            setInputAssetIds((previous) =>
                              event.target.checked
                                ? [...previous, text(asset.id)]
                                : previous.filter(
                                    (id) => id !== text(asset.id),
                                  ),
                            )
                          }
                        />
                        <span>
                          {text(asset.filename)}
                          <small>{text(asset.format || asset.mime)}</small>
                        </span>
                      </label>
                    ))}
                </div>
              ) : (
                <p className="cloud-note">
                  {projectId
                    ? "已加载的当前项目素材中还没有参考文件。文本任务可以直接提交。"
                    : "先选择项目，可以继续上传和引用素材。"}
                </p>
              )}
              {projectId && assets.length >= requestedCounts.current.assets && (
                <button
                  type="button"
                  className="cloud-button"
                  disabled={!!busy || !connected}
                  onClick={() => loadMore("assets")}
                >
                  加载更多参考素材
                </button>
              )}
              {[
                "quick",
                "canvas",
                "render",
                "dialogue",
                "image_to_model",
                "video",
              ].includes(capability) && (
                <details className="cloud-options">
                  <summary>创作参数</summary>
                  <div className="cloud-grid">
                    {capability !== "video" && (
                      <>
                        <label className="cloud-field">
                          <span>画面尺寸</span>
                          <select
                            aria-label="画面尺寸"
                            value={options.size || "auto"}
                            onChange={(event) =>
                              configure("size", event.target.value)
                            }
                          >
                            <option value="auto">由执行工具决定</option>
                            <option value="1024x1024">1024 × 1024</option>
                            <option value="1536x1024">1536 × 1024</option>
                            <option value="1024x1536">1024 × 1536</option>
                          </select>
                        </label>
                        <label className="cloud-field">
                          <span>质量</span>
                          <select
                            aria-label="质量"
                            value={options.quality || "auto"}
                            onChange={(event) =>
                              configure("quality", event.target.value)
                            }
                          >
                            <option value="auto">默认</option>
                            <option value="low">快速</option>
                            <option value="medium">标准</option>
                            <option value="high">高质量</option>
                          </select>
                        </label>
                      </>
                    )}
                    {capability === "video" && (
                      <>
                        <label className="cloud-field">
                          <span>分辨率</span>
                          <select
                            aria-label="分辨率"
                            value={options.resolution || "1080P"}
                            onChange={(event) =>
                              configure("resolution", event.target.value)
                            }
                          >
                            <option>720P</option>
                            <option>1080P</option>
                          </select>
                        </label>
                        <label className="cloud-field">
                          <span>画面比例</span>
                          <select
                            aria-label="画面比例"
                            value={options.aspect_ratio || "16:9"}
                            onChange={(event) =>
                              configure("aspect_ratio", event.target.value)
                            }
                          >
                            <option>16:9</option>
                            <option>9:16</option>
                            <option>1:1</option>
                          </select>
                        </label>
                        <label className="cloud-field">
                          <span>音频</span>
                          <select
                            aria-label="音频"
                            value={
                              options.enable_audio === false ? "false" : "true"
                            }
                            onChange={(event) =>
                              configure(
                                "enable_audio",
                                event.target.value === "true",
                              )
                            }
                          >
                            <option value="true">生成音频</option>
                            <option value="false">无音频</option>
                          </select>
                        </label>
                        <label className="cloud-field">
                          <span>排除内容</span>
                          <input
                            aria-label="排除内容"
                            value={text(options.negative_prompt)}
                            onChange={(event) =>
                              configure("negative_prompt", event.target.value)
                            }
                          />
                        </label>
                      </>
                    )}
                    <label className="cloud-field">
                      <span>风格与材料</span>
                      <input
                        aria-label="风格与材料"
                        value={text(options.style)}
                        onChange={(event) =>
                          configure("style", event.target.value)
                        }
                        placeholder="自然日光、木材与混凝土…"
                      />
                    </label>
                    {provider === "codebuddy" && (
                      <label className="cloud-field">
                        <span>媒体模型（可选）</span>
                        <input
                          aria-label="媒体模型（可选）"
                          value={text(options.media_model)}
                          onChange={(event) =>
                            configure("media_model", event.target.value)
                          }
                          placeholder="使用执行工具当前媒体模型"
                        />
                      </label>
                    )}
                  </div>
                  <label className="cloud-field">
                    <span>补充参数（JSON，可选）</span>
                    <textarea
                      aria-label="补充参数（JSON，可选）"
                      className="cloud-code"
                      value={advanced}
                      rows={4}
                      onChange={(event) => setAdvanced(event.target.value)}
                    />
                  </label>
                </details>
              )}
              <div className="cloud-submit">
                <p className="cloud-note">
                  {capability === "image_to_model"
                    ? "模型任务完成后提供真实模型文件；排队和参考图片不代表模型已生成。"
                    : capability === "video"
                      ? "由已启用的 CodeBuddy 设备执行，模型与媒体工具可用性以实际任务结果为准。"
                      : "本机工具的账号额度与模型可用性以实际执行结果为准。"}
                </p>
                <button
                  className="cloud-button primary"
                  disabled={
                    !connected ||
                    !!busy ||
                    !projectId ||
                    !prompt.trim() ||
                    !selectedCapability?.enabled
                  }
                >
                  {busy === "创建任务" ? (
                    <Loader2 size={15} className="spin" />
                  ) : (
                    <Cloud size={15} />
                  )}
                  提交任务
                </button>
              </div>
            </form>
          </section>
        </>
      )}
      {section === "jobs" && (
        <section className="cloud-card">
          <div className="cloud-card-heading">
            <h3>任务记录</h3>
            <span className="cloud-note">进度和成果来自执行设备</span>
          </div>
          <div className="cloud-filters">
            <input
              aria-label="搜索任务"
              value={jobQuery}
              onChange={(event) => setJobQuery(event.target.value)}
              placeholder="搜索任务描述或编号"
            />
            <select
              aria-label="任务状态筛选"
              value={jobState}
              onChange={(event) => setJobState(event.target.value)}
            >
              <option value="">全部状态</option>
              {Object.entries(stateLabels).map(([state, label]) => (
                <option key={state} value={state}>
                  {label}
                </option>
              ))}
            </select>
            <select
              aria-label="任务能力筛选"
              value={jobCapability}
              onChange={(event) => setJobCapability(event.target.value)}
            >
              <option value="">全部能力</option>
              {catalog.map((item) => (
                <option key={item.id} value={item.id}>
                  {text(item.name)}
                </option>
              ))}
            </select>
          </div>
          {!visibleJobs.length && (
            <div className="cloud-empty">
              {loading
                ? "正在加载任务…"
                : jobs.length
                  ? "没有符合筛选条件的任务。"
                  : "还没有云端任务。创建任务后可以在这里跟踪。"}
            </div>
          )}
          <div className="cloud-job-list">
            {visibleJobs.map((job) => (
              <article className="cloud-job" key={job.id}>
                <button
                  className="cloud-job-main"
                  onClick={() => setSelectedJob(job)}
                >
                  <span className={"cloud-badge " + text(job.state)}>
                    {stateLabels[job.state] || text(job.state)}
                  </span>
                  <strong>{text(job.prompt)}</strong>
                  <small>
                    {text(
                      catalog.find((item) => item.id === job.capability)?.name,
                    ) || text(job.capability)}{" "}
                    ·{" "}
                    {text(
                      projects.find((item) => item.id === job.project_id)?.name,
                    ) || text(job.project_id)}{" "}
                    · {stamp(job.created_at_ms)}
                  </small>
                  {progress(job) !== null && (
                    <div className="cloud-progress">
                      <progress value={progress(job)!} max={100} />
                      <span>{progress(job)}%</span>
                    </div>
                  )}
                </button>
                {activeJob(job) && (
                  <button
                    className="cloud-button danger"
                    disabled={!!busy || !connected}
                    onClick={() =>
                      void run("取消任务", async () => {
                        await api(
                          `/cloud/jobs/${encodeURIComponent(text(job.id))}/cancel`,
                          {},
                        );
                        callbacks.current.toast(
                          "取消请求已提交，请查看更新后的任务状态",
                        );
                      })
                    }
                  >
                    <Square size={13} />
                    取消
                  </button>
                )}
              </article>
            ))}
          </div>
          {jobs.length >= requestedCounts.current.jobs && (
            <button
              className="cloud-button"
              disabled={!!busy || !connected}
              onClick={() => loadMore("jobs")}
            >
              加载更多任务
            </button>
          )}
        </section>
      )}
      {section === "projects" && (
        <div className="cloud-project-layout">
          <section className="cloud-card cloud-project-list">
            <div className="cloud-card-heading">
              <h3>我的云项目</h3>
              <button
                className="cloud-button"
                disabled={!connected || !!busy}
                onClick={() => void chooseProject("")}
              >
                新建
              </button>
            </div>
            {!projects.length && (
              <div className="cloud-empty">
                保存首个项目，或从本机成果同步。
              </div>
            )}
            {projects.map((project) => (
              <button
                key={project.id}
                className={
                  "cloud-project-item " +
                  (projectId === text(project.id) ? "selected" : "")
                }
                disabled={!!busy || !connected}
                onClick={() => void chooseProject(text(project.id))}
              >
                <strong>{text(project.name)}</strong>
                <span>
                  {projectLabels[project.type] || text(project.type)} · 版本{" "}
                  {project.revision}
                </span>
                <small>{stamp(project.updated_at_ms)}</small>
              </button>
            ))}
            {projects.length >= requestedCounts.current.projects && (
              <button
                className="cloud-button"
                disabled={!!busy || !connected}
                onClick={() => loadMore("projects")}
              >
                加载更多项目
              </button>
            )}
            <hr />
            <h4>同步本机成果</h4>
            <p className="cloud-note">
              选择已有知识库、画布、文档或室内方案，保存到当前会员的云项目。
            </p>
            <label className="cloud-field">
              <span>本机项目</span>
              <select
                aria-label="本机项目"
                disabled={!connected || !!busy}
                value={localObject}
                onChange={(event) => setLocalObject(event.target.value)}
              >
                <option value="">选择已保存的本机成果</option>
                {localObjects.map((item) => (
                  <option
                    key={`${item.kind}:${item.id}`}
                    value={`${item.kind}:${item.id}`}
                  >
                    {text(item.name)} ·{" "}
                    {projectLabels[item.kind] || text(item.kind)}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="cloud-button"
              disabled={!connected || !!busy || !localObject}
              onClick={() =>
                void run("同步本机成果", async () => {
                  const item = localObjects.find(
                    (value) => `${value.kind}:${value.id}` === localObject,
                  );
                  if (!item)
                    throw new Error("本机成果已变化，请刷新后重新选择");
                  const saved = await api("/cloud/publish-local", {
                    kind: item.kind,
                    id: item.id,
                    ...(projectId
                      ? {
                          project_id: projectId,
                          base_revision: editingRevision,
                        }
                      : {}),
                  });
                  callbacks.current.toast("本机成果已保存到云项目");
                  const project = saved.project || saved;
                  if (project.id) {
                    setProjectId(text(project.id));
                    setProjectName(text(project.name));
                    setProjectType(text(project.type));
                    setEditingRevision(Number(project.revision));
                    setDocumentDraft(pretty(project.document));
                  }
                })
              }
            >
              <Upload size={14} />
              同步到{projectId ? "当前项目" : "新云项目"}
            </button>
          </section>
          <section className="cloud-card">
            <div className="cloud-card-heading">
              <h3>{projectId ? "项目内容与版本" : "创建云项目"}</h3>
              {selectedProject && (
                <div className="cloud-heading-actions">
                  <span className="cloud-note">
                    云端版本 {selectedProject.revision}
                  </span>
                  {["knowledge", "canvas", "documents", "interior"].includes(
                    selectedProject.type,
                  ) && (
                    <button
                      className="cloud-button"
                      disabled={!connected || !!busy}
                      onClick={() =>
                        void run("保存到本机", async () => {
                          const restored = await api(
                            `/cloud/projects/${encodeURIComponent(projectId)}/restore`,
                            {},
                          );
                          if (
                            !restored.id ||
                            ![
                              "knowledge",
                              "canvas",
                              "documents",
                              "interior",
                            ].includes(restored.kind)
                          )
                            throw new Error(
                              "服务没有返回有效的本机项目，请刷新本机项目核对",
                            );
                          setRestoredProject(restored);
                          callbacks.current.toast(
                            `已保存为本机新项目：${text(restored.name) || text(restored.id)}`,
                          );
                        })
                      }
                    >
                      <Download size={14} />
                      保存为本机新项目
                    </button>
                  )}
                </div>
              )}
            </div>
            {restoredProject && (
              <div className="cloud-restored" role="status">
                <CheckCircle2 size={16} />
                <div>
                  已保存到本机：
                  {text(restoredProject.name) || text(restoredProject.id)}
                  <small>
                    {projectLabels[restoredProject.kind]} ·{" "}
                    {text(restoredProject.id)}
                  </small>
                </div>
                {onRestore && (
                  <button
                    className="cloud-button"
                    onClick={() => onRestore(restoredProject)}
                  >
                    在本机打开
                  </button>
                )}
              </div>
            )}
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void run("保存项目", saveProject);
              }}
            >
              <div className="cloud-grid">
                <label className="cloud-field">
                  <span>项目名称</span>
                  <input
                    aria-label="项目名称"
                    required
                    maxLength={200}
                    disabled={!connected || !!busy}
                    value={projectName}
                    onChange={(event) => setProjectName(event.target.value)}
                  />
                </label>
                <label className="cloud-field">
                  <span>项目类型</span>
                  <select
                    aria-label="项目类型"
                    disabled={!connected || !!busy || !!projectId}
                    value={projectType}
                    onChange={(event) => setProjectType(event.target.value)}
                  >
                    {Object.entries(projectLabels).map(([id, name]) => (
                      <option key={id} value={id}>
                        {name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <label className="cloud-field">
                <span>项目说明与内容</span>
                <textarea
                  aria-label="项目说明与内容"
                  rows={8}
                  disabled={!connected || !!busy || !projectDocument}
                  value={text(projectDocument?.[documentContentKey])}
                  onChange={(event) =>
                    setDocumentDraft(
                      pretty({
                        ...projectDocument,
                        [documentContentKey]: event.target.value,
                      }),
                    )
                  }
                  placeholder="写下项目背景、设计要求、知识内容或文档草稿。"
                />
                <small>
                  可以直接编辑文字，也可以从左侧同步已保存的本机成果。
                </small>
              </label>
              <details className="cloud-options">
                <summary>结构化内容（可选）</summary>
                <label className="cloud-field">
                  <span>项目内容（JSON）</span>
                  <textarea
                    aria-label="项目内容（JSON）"
                    className="cloud-code"
                    rows={12}
                    disabled={!connected || !!busy}
                    value={documentDraft}
                    onChange={(event) => setDocumentDraft(event.target.value)}
                  />
                  <small>
                    可直接保存结构化内容，也可以从左侧同步已有本机成果。
                  </small>
                </label>
              </details>
              {conflict && (
                <div className="cloud-conflict" role="alert">
                  本机草稿基于旧版本 {editingRevision}。
                  <button
                    type="button"
                    className="cloud-button"
                    onClick={loadVersions}
                  >
                    查看最新版本
                  </button>
                  <button
                    type="button"
                    className="cloud-button"
                    disabled={!selectedProject}
                    onClick={() => {
                      setEditingRevision(Number(selectedProject?.revision));
                      setConflict(false);
                    }}
                  >
                    草稿已合并，使用版本 {selectedProject?.revision} 保存
                  </button>
                </div>
              )}
              <button
                className="cloud-button primary"
                disabled={
                  !connected || !!busy || !projectName.trim() || conflict
                }
              >
                <CheckCircle2 size={15} />
                {busy === "保存项目" ? "保存中…" : "保存项目"}
              </button>
            </form>
            {projectId && (
              <>
                <hr />
                <div className="cloud-reference-heading">
                  <h4>项目版本</h4>
                  <button
                    className="cloud-button"
                    disabled={!!busy || !connected}
                    onClick={loadVersions}
                  >
                    加载历史
                  </button>
                </div>
                {versions.map((version) => (
                  <details className="cloud-version" key={version.revision}>
                    <summary>
                      版本 {version.revision} · {stamp(version.created_at_ms)}
                    </summary>
                    <pre>{pretty(version.document)}</pre>
                    <button
                      className="cloud-button"
                      disabled={!!busy || !connected}
                      onClick={() => {
                        setDocumentDraft(pretty(version.document));
                        setEditingRevision(Number(selectedProject?.revision));
                        setConflict(false);
                        callbacks.current.toast(
                          "历史内容已填入草稿，保存后创建新版本",
                        );
                      }}
                    >
                      填入草稿
                    </button>
                  </details>
                ))}
                <hr />
                <div className="cloud-reference-heading">
                  <h4>分享项目快照</h4>
                  <button
                    className="cloud-button"
                    disabled={!!busy || !connected}
                    onClick={loadShares}
                  >
                    查看已分享
                  </button>
                </div>
                <div className="cloud-share-controls">
                  <select
                    aria-label="分享有效期"
                    value={shareExpiry}
                    onChange={(event) => setShareExpiry(event.target.value)}
                  >
                    <option value="3600">1 小时</option>
                    <option value="86400">1 天</option>
                    <option value="604800">7 天</option>
                  </select>
                  <button
                    className="cloud-button"
                    disabled={!!busy || !connected}
                    onClick={() =>
                      void run("分享项目", async () => {
                        const share = await api(
                          `/cloud/projects/${encodeURIComponent(projectId)}/shares`,
                          { expires_in_seconds: Number(shareExpiry) },
                        );
                        if (!share.id || !share.url)
                          throw new Error("未获得有效分享链接");
                        setShares((previous) => [
                          ...previous.filter((value) => value.id !== share.id),
                          share,
                        ]);
                        callbacks.current.toast("已创建当前版本的分享快照");
                      })
                    }
                  >
                    <Share2 size={14} />
                    创建分享
                  </button>
                </div>
                <p className="cloud-note">
                  持有链接的人可在有效期内查看该版本；后续编辑不会改变已分享快照。
                </p>
                {shares.map((share) => (
                  <div className="cloud-share" key={share.id}>
                    <div>
                      <strong>
                        {share.revoked
                          ? "已撤销"
                          : `有效期至 ${stamp(share.expires_at_ms)}`}
                      </strong>
                      {share.url && (
                        <input
                          aria-label="分享链接"
                          readOnly
                          value={text(share.url)}
                        />
                      )}
                    </div>
                    {share.url && !share.revoked && (
                      <button
                        className="cloud-button"
                        onClick={() =>
                          void run("复制分享", async () => {
                            await navigator.clipboard.writeText(
                              text(share.url),
                            );
                            callbacks.current.toast("分享链接已复制");
                          })
                        }
                      >
                        复制
                      </button>
                    )}
                    <button
                      className="cloud-button danger"
                      disabled={!!busy || !connected || share.revoked === true}
                      onClick={() =>
                        void run("撤销分享", async () => {
                          await api(
                            `/cloud/projects/${encodeURIComponent(projectId)}/shares/${encodeURIComponent(text(share.id))}`,
                            undefined,
                            "DELETE",
                          );
                          setShares((previous) =>
                            previous.map((value) =>
                              value.id === share.id
                                ? { ...value, revoked: true }
                                : value,
                            ),
                          );
                          callbacks.current.toast("分享已撤销");
                        })
                      }
                    >
                      撤销
                    </button>
                  </div>
                ))}
                <hr />
                <h4>项目素材与成果</h4>
                {projectAssets.length ? (
                  projectAssets.map(renderAsset)
                ) : (
                  <p className="cloud-note">还没有素材或完成的成果。</p>
                )}
                {assets.length >= requestedCounts.current.assets && (
                  <button
                    className="cloud-button"
                    disabled={!!busy || !connected}
                    onClick={() => loadMore("assets")}
                  >
                    加载更多素材与成果
                  </button>
                )}
              </>
            )}
          </section>
        </div>
      )}
      {section === "devices" && (
        <section className="cloud-card">
          <div className="cloud-card-heading">
            <h3>我的执行设备</h3>
            <span className="cloud-note">
              启用本机开关后，此设备才会领取云任务
            </span>
          </div>
          {!devices.length && (
            <div className="cloud-empty">
              还没有执行设备。打开上方“允许本机执行云任务”后注册此设备。
            </div>
          )}
          <div className="cloud-device-grid">
            {devices.map((device) => (
              <article className="cloud-device" key={device.id}>
                <Monitor size={24} />
                <div>
                  <strong>{text(device.name) || "未命名设备"}</strong>
                  <span
                    className={
                      "cloud-badge " +
                      (device.status === "active" ? "running" : "cancelled")
                    }
                  >
                    {device.status === "active"
                      ? "已启用"
                      : device.status === "revoked"
                        ? "已撤销"
                        : text(device.status)}
                  </span>
                  <p>
                    {device.status === "active" &&
                    Date.now() - Number(device.last_seen_at_ms) < 90000
                      ? "最近在线"
                      : "离线"}{" "}
                    · 最近在线：{stamp(device.last_seen_at_ms)}
                  </p>
                  <p>
                    {text(device.platform)} ·{" "}
                    {Array.isArray(device.capabilities) &&
                    device.capabilities.length
                      ? device.capabilities
                          .map(
                            (id: string) =>
                              text(
                                catalog.find((item) => item.id === id)?.name,
                              ) || id,
                          )
                          .join("、")
                      : "可用能力尚未报告"}
                  </p>
                </div>
                {device.status === "active" && (
                  <button
                    className="cloud-button danger"
                    disabled={!!busy || !connected}
                    onClick={() =>
                      void run("撤销设备", async () => {
                        await api(
                          `/cloud/devices/${encodeURIComponent(text(device.id))}/revoke`,
                          {},
                        );
                        callbacks.current.toast("设备已撤销，不再领取新任务");
                      })
                    }
                  >
                    撤销
                  </button>
                )}
              </article>
            ))}
          </div>
          {devices.length >= requestedCounts.current.devices && (
            <button
              className="cloud-button"
              disabled={!!busy || !connected}
              onClick={() => loadMore("devices")}
            >
              加载更多设备
            </button>
          )}
        </section>
      )}
      {selectedJob && (
        <div
          className="cloud-dialog-overlay"
          onClick={(event) => {
            if (event.target === event.currentTarget) setSelectedJob(null);
          }}
        >
          <section
            ref={dialog}
            role="dialog"
            aria-modal="true"
            aria-labelledby="cloud-job-title"
            className="cloud-dialog"
            onKeyDown={(event) => {
              if (event.key === "Escape") setSelectedJob(null);
            }}
          >
            <div className="cloud-card-heading">
              <h3 id="cloud-job-title">任务详情</h3>
              <button
                autoFocus
                className="cloud-button"
                aria-label="关闭任务详情"
                onClick={() => setSelectedJob(null)}
              >
                <X size={16} />
              </button>
            </div>
            <span className={"cloud-badge " + selectedJob.state}>
              {stateLabels[selectedJob.state] || text(selectedJob.state)}
            </span>
            <p className="cloud-note">
              {text(selectedJob.id)} · {stamp(selectedJob.created_at_ms)}
            </p>
            <h4>任务描述</h4>
            <p className="cloud-prewrap">{text(selectedJob.prompt)}</p>
            {text(selectedJob.summary) && (
              <>
                <h4>执行摘要</h4>
                <p className="cloud-prewrap">{text(selectedJob.summary)}</p>
              </>
            )}
            {(selectedJob.error_message ||
              selectedJob.error ||
              selectedJob.message) && (
              <p className="cloud-error">
                {text(
                  selectedJob.error_message ||
                    selectedJob.error?.message ||
                    selectedJob.error ||
                    selectedJob.message,
                )}
              </p>
            )}
            <details className="cloud-options">
              <summary>本次参数与执行记录</summary>
              <pre>
                {pretty({
                  options: selectedJob.options,
                  run_id: selectedJob.run_id,
                  usage: selectedJob.usage,
                })}
              </pre>
            </details>
            <h4>成果文件</h4>
            {selectedJob.state === "completed" &&
            Array.isArray(selectedJob.output_asset_ids) &&
            selectedJob.output_asset_ids.length ? (
              selectedJob.output_asset_ids.map((id: string) => {
                const asset = assets.find((item) => item.id === id);
                return asset ? (
                  renderAsset(asset)
                ) : (
                  <div key={id}>
                    <p className="cloud-note">成果 {id} 尚未加载。</p>
                    <button
                      className="cloud-button"
                      disabled={!!busy || !connected}
                      onClick={() => loadMore("assets")}
                    >
                      加载更多成果
                    </button>
                  </div>
                );
              })
            ) : (
              <p className="cloud-note">
                {activeJob(selectedJob)
                  ? "任务尚未完成，完成后展示已验证的成果文件。"
                  : selectedJob.state === "completed"
                    ? "任务没有返回可下载成果，请核对执行记录。"
                    : "当前任务没有完成的成果。"}
              </p>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
