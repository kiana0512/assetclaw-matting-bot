const state = {
  currentView: "chat",
  polling: true,
  lastStatus: {},
  skills: [],
  attachments: [],
  lastRefreshAt: null,
  apiBase: inferApiBase(),
  flowSteps: [],
  moduleCatalog: null,
};

const $ = (selector) => document.querySelector(selector);

const els = {
  agentSignal: $("#agentSignal"),
  agentState: $("#agentState"),
  agentUrl: $("#agentUrl"),
  viewTitle: $("#viewTitle"),
  viewEyebrow: $("#viewEyebrow"),
  messages: $("#messages"),
  chatForm: $("#chatForm"),
  chatInput: $("#chatInput"),
  attachBtn: $("#attachBtn"),
  fileInput: $("#fileInput"),
  attachmentTray: $("#attachmentTray"),
  commandPaletteBtn: $("#commandPaletteBtn"),
  commandDialog: $("#commandDialog"),
  closeCommandDialog: $("#closeCommandDialog"),
  commandSearch: $("#commandSearch"),
  commandList: $("#commandList"),
  toastHost: $("#toastHost"),
  refreshBtn: $("#refreshBtn"),
  pollBtn: $("#pollBtn"),
  token: $("#skillToken"),
  miniStatus: $("#miniStatus"),
  snapshotStamp: $("#snapshotStamp"),
  activeCount: $("#activeCount"),
  activeWorkList: $("#activeWorkList"),
  statusStrip: $("#statusStrip"),
  moduleBoard: $("#moduleBoard"),
  taskTable: $("#taskTable"),
  taskTableStamp: $("#taskTableStamp"),
  diagnoseBtn: $("#diagnoseBtn"),
  diagnostics: $("#diagnostics"),
  queueStamp: $("#queueStamp"),
  comfyQueuePanel: $("#comfyQueuePanel"),
  comfyRunsPanel: $("#comfyRunsPanel"),
  cherryRunsPanel: $("#cherryRunsPanel"),
  frameRunsPanel: $("#frameRunsPanel"),
  controlRunId: $("#controlRunId"),
  controlModule: $("#controlModule"),
  controlResult: $("#controlResult"),
  timeline: $("#timeline"),
  timelineStamp: $("#timelineStamp"),
  workspaceRoot: $("#workspaceRoot"),
  workspaceCounters: $("#workspaceCounters"),
  pipelineRunsPanel: $("#pipelineRunsPanel"),
  agentBuildFlowBtn: $("#agentBuildFlowBtn"),
  addFlowStepBtn: $("#addFlowStepBtn"),
  flowName: $("#flowName"),
  flowDescription: $("#flowDescription"),
  flowVariables: $("#flowVariables"),
  flowSteps: $("#flowSteps"),
  saveFlowBtn: $("#saveFlowBtn"),
  previewFlowBtn: $("#previewFlowBtn"),
  runFlowBtn: $("#runFlowBtn"),
  flowResult: $("#flowResult"),
  loadModuleCatalogBtn: $("#loadModuleCatalogBtn"),
  moduleCatalog: $("#moduleCatalog"),
  loadFlowDefsBtn: $("#loadFlowDefsBtn"),
  flowDefinitions: $("#flowDefinitions"),
  asrAudioPath: $("#asrAudioPath"),
  asrLanguage: $("#asrLanguage"),
  asrPrompt: $("#asrPrompt"),
  runAsrBtn: $("#runAsrBtn"),
  asrResult: $("#asrResult"),
  ttsText: $("#ttsText"),
  ttsOutputPath: $("#ttsOutputPath"),
  ttsVoice: $("#ttsVoice"),
  ttsEngine: $("#ttsEngine"),
  ttsRate: $("#ttsRate"),
  runTtsBtn: $("#runTtsBtn"),
  ttsResult: $("#ttsResult"),
  p4Panel: $("#p4Panel"),
  p4Result: $("#p4Result"),
  memoryScope: $("#memoryScope"),
  memoryConversation: $("#memoryConversation"),
  loadMemoryBtn: $("#loadMemoryBtn"),
  compactMemoryBtn: $("#compactMemoryBtn"),
  memoryList: $("#memoryList"),
  memorySkills: $("#memorySkills"),
  memorySkillCount: $("#memorySkillCount"),
  skillsList: $("#skillsList"),
  skillSearch: $("#skillSearch"),
  skillCount: $("#skillCount"),
  logsList: $("#logsList"),
  conversationId: $("#conversationId"),
  loadLogsBtn: $("#loadLogsBtn"),
  detailDialog: $("#detailDialog"),
  detailTitle: $("#detailTitle"),
  detailBody: $("#detailBody"),
  closeDetail: $("#closeDetail"),
  configDialog: $("#configDialog"),
  configTitle: $("#configTitle"),
  configHint: $("#configHint"),
  configForm: $("#configForm"),
  closeConfig: $("#closeConfig"),
  saveConfig: $("#saveConfig"),
  previewConfig: $("#previewConfig"),
  startConfig: $("#startConfig"),
  configResult: $("#configResult"),
};

const views = {
  chat: ["本机 Agent 入口", "AI 对话工作台"],
  command: ["生产机器总览", "机器总控台"],
  queues: ["任务队列与控制", "任务队列"],
  pipeline: ["动画自动化流程", "流程总览"],
  builder: ["自定义流程", "流程编排器"],
  voice: ["语音与多模态", "ASR / TTS"],
  p4: ["版本工作区", "P4 工作区"],
  memory: ["检索与记忆", "记忆 / RAG"],
  skills: ["后端能力注册表", "技能清单"],
  logs: ["共享脑子观测", "对话日志"],
};

const commands = [
  { id: "chat-status", title: "问 Agent：总状态", hint: "用自然语言汇总所有模块", keys: "状态 agent 任务", run: () => sendChat("列出当前所有任务状态，按 ComfyUI、Cherry、抽帧、Pipeline、自定义流程分组说明，并指出可以暂停或终止的任务。") },
  { id: "diagnose", title: "后端自动诊断", hint: "检查后端、ComfyUI、P4、动画流程", keys: "诊断 后端 comfy p4", run: async () => { switchView("command"); state.lastStatus.agentDiagnose = await skillCall("agent.diagnose", { include_gpu: false }); renderDiagnostics(); toast("诊断结果已刷新", "ok"); } },
  { id: "queues", title: "打开任务队列", hint: "查看暂停、继续、终止入口", keys: "队列 暂停 终止 cancel", run: () => switchView("queues") },
  { id: "builder", title: "打开流程编排器", hint: "自定义步骤、参数、输入输出路径", keys: "流程 编排 自定义 pipeline", run: () => switchView("builder") },
  { id: "build-agent", title: "让 Agent 生成流程", hint: "把需求变成 custom_pipeline JSON", keys: "agent 生成 流程 json", run: () => seedAgentFlowPrompt() },
  { id: "voice", title: "打开 ASR / TTS", hint: "语音转文字、文字转语音", keys: "语音 asr tts 多模态", run: () => switchView("voice") },
  { id: "memory", title: "打开 Memory / RAG", hint: "读取 context pack 与后端压缩", keys: "rag memory 记忆 检索", run: () => switchView("memory") },
  { id: "logs", title: "打开日志", hint: "分区查看对话日志与技能调用", keys: "log 日志 skill call", run: () => switchView("logs") },
  { id: "refresh", title: "刷新所有状态", hint: "拉取后端最新快照", keys: "刷新 refresh", run: () => refreshAll({ includeLazy: true }) },
];

const statusCalls = [
  { key: "gpu", skill: "system.gpu_status", args: {} },
  { key: "process", skill: "system.process_status", args: { names: ["ComfyUI", "python", "p4", "ffmpeg", "Cherry"] } },
  { key: "agentWork", skill: "agent.current_work", args: { include_gpu: true } },
  { key: "agentDiagnose", skill: "agent.diagnose", args: { include_gpu: false }, lazy: true },
  { key: "comfyQueue", skill: "comfyui.queue_status", args: {} },
  { key: "comfyRuns", skill: "comfyui.run_list", args: { limit: 8, include_finished: true } },
  { key: "comfyCurrent", skill: "comfyui.run_status", args: { include_gpu: false } },
  { key: "cherryRuns", skill: "cherry.run_list", args: { limit: 8, include_finished: true } },
  { key: "cherryCurrent", skill: "cherry.run_status", args: { include_gpu: false } },
  { key: "frameRuns", skill: "frame.run_list", args: { limit: 8, include_finished: true, include_archived: false } },
  { key: "frameCurrent", skill: "frame.run_status", args: {} },
  { key: "pipelineRuns", skill: "pipeline.run_list", args: { limit: 8, include_finished: true } },
  { key: "pipelineCurrent", skill: "pipeline.run_status", args: {} },
  { key: "customPipelineRuns", skill: "custom_pipeline.run_list", args: { limit: 8, include_finished: true } },
  { key: "animation", skill: "animation.status", args: { include_runs: true } },
  { key: "p4", skill: "p4.status", args: {} },
];

const moduleConfigs = {
  comfyui: {
    title: "ComfyUI 批量抠图参数",
    previewSkill: "comfyui.run_preview",
    startSkill: "comfyui.run_start",
    fields: [
      ["workflow_path", "工作流 JSON 路径", "text", ""],
      ["input_dir", "输入目录", "text", ""],
      ["output_dir", "输出目录", "text", ""],
      ["recursive", "递归子目录", "checkbox", true],
      ["preserve_structure", "保留目录结构", "checkbox", true],
      ["skip_existing", "跳过已有输出", "checkbox", false],
      ["max_images", "最多图片数", "number", 10000],
      ["notify_interval_seconds", "通知间隔秒", "number", 300],
    ],
  },
  cherry: {
    title: "Cherry 帧序列平滑参数",
    previewSkill: "cherry.run_preview",
    startSkill: "cherry.run_start",
    fields: [
      ["input_dir", "输入目录", "text", ""],
      ["output_dir", "输出目录", "text", ""],
      ["recursive", "递归子目录", "checkbox", true],
      ["skip_existing", "跳过已有输出", "checkbox", false],
      ["max_images", "最多图片数", "number", 50000],
      ["use_denoise", "启用 Alpha 去噪", "checkbox", true],
      ["denoise_threshold", "去噪阈值", "number", 0.06],
      ["denoise_radius", "去噪半径", "number", 0],
      ["use_smooth", "启用时序平滑", "checkbox", true],
      ["smooth_window", "平滑窗口", "number", 5],
      ["smooth_sigma", "平滑强度", "number", 1.0],
      ["min_alpha", "最小 Alpha", "number", 0.05],
      ["sync_rgb", "同步 RGB 边缘", "checkbox", true],
      ["use_resize", "启用缩放", "checkbox", true],
      ["resize_width", "输出宽度", "number", 256],
      ["resize_height", "输出高度", "number", 256],
      ["use_sharpen", "启用锐化", "checkbox", true],
      ["sharpen_amount", "锐化强度", "number", 2.0],
      ["sharpen_radius", "锐化半径", "number", 2],
      ["sharpen_threshold", "锐化阈值", "number", 0.02],
      ["sharpen_shrink", "锐化缩小倍率", "number", 4],
      ["notify_interval_seconds", "通知间隔秒", "number", 300],
    ],
  },
  frame: {
    title: "飞书视频抽帧参数",
    previewSkill: "frame.run_preview",
    startSkill: "frame.run_start",
    fields: [
      ["download_dir", "视频下载目录", "text", ""],
      ["export_dir", "抽帧输出目录", "text", ""],
      ["fps", "抽帧 FPS", "number", 24],
      ["max_frames", "最多帧数 0=不限", "number", 0],
      ["diff_threshold", "差异阈值", "number", 0.2],
      ["dedup_enabled", "启用剔除关键帧/去重", "checkbox", false],
      ["dedup_renumber", "去重后重新编号", "checkbox", false],
      ["notify_interval_seconds", "通知间隔秒", "number", 300],
    ],
  },
  pipeline: {
    title: "动画自动化 Pipeline 参数",
    previewSkill: "pipeline.run_preview",
    startSkill: "pipeline.run_start",
    fields: [
      ["input_dir", "视频输入/下载目录", "text", ""],
      ["frame_output_dir", "抽帧输出目录", "text", ""],
      ["matte_output_dir", "抠图输出目录", "text", ""],
      ["smooth_output_dir", "平滑输出目录", "text", ""],
      ["workflow_path", "ComfyUI 工作流", "text", ""],
      ["fps", "抽帧 FPS", "number", 24],
      ["max_frames", "最多帧数 0=不限", "number", 0],
      ["diff_threshold", "差异阈值", "number", 0.2],
      ["notify_interval_seconds", "通知间隔秒", "number", 300],
    ],
  },
};

let activeConfigModule = "";

function inferApiBase() {
  const saved = localStorage.getItem("assetclaw.apiBase") || "";
  if (saved) return saved.replace(/\/$/, "");
  if (window.location.protocol === "file:") return "http://127.0.0.1:5178";
  return "";
}

function tokenHeaders() {
  const token = els.token.value.trim();
  return token ? { "X-Skill-Token": token } : {};
}

async function jsonFetch(url, options = {}) {
  const target = url.startsWith("http") ? url : `${state.apiBase}${url}`;
  try {
    const response = await fetch(target, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
    });
    const text = await response.text();
    let data;
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      data = { ok: false, error: text || response.statusText };
    }
    if (!response.ok) return { ok: false, status: response.status, ...data };
    return data;
  } catch (error) {
    return {
      ok: false,
      offline: true,
      error: String(error),
      hint: window.location.protocol === "file:" ? "当前是 file:// 打开，请同时启动 python .\\external_webui\\server.py --port 5178，或直接访问 http://127.0.0.1:5178" : "本机 WebUI/Agent 后端暂不可达",
      target,
    };
  }
}

async function skillCall(skill, args = {}) {
  return jsonFetch("/api/skills/call", {
    method: "POST",
    headers: tokenHeaders(),
    body: JSON.stringify({ skill, arguments: args, requested_by: "external_webui" }),
  });
}

function unwrap(result) {
  return result?.result?.result || result?.result || result || {};
}

function escapeHtml(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function compact(value, fallback = "-") {
  if (value === undefined || value === null || value === "") return fallback;
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) return String(value.length);
  if (typeof value === "object") return value.error || value.status || value.run_id || JSON.stringify(value).slice(0, 120);
  return String(value);
}

function fmtTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
}

function setAgentStatus(payload) {
  const ok = Boolean(payload && payload.ok);
  els.agentSignal.classList.toggle("online", ok);
  els.agentSignal.classList.toggle("offline", !ok);
  els.agentState.textContent = ok ? "online" : "offline";
  els.agentUrl.textContent = payload?.agent_url || payload?.service || state.apiBase || "127.0.0.1 agent";
}

function statusClass(status) {
  const lower = String(status || "").toLowerCase();
  if (lower.includes("fail") || lower.includes("error") || lower.includes("cancel") || lower.includes("offline")) return "bad";
  if (lower.includes("run") || lower.includes("queue") || lower.includes("pending") || lower.includes("pause")) return "warn";
  return "ok";
}

function pill(label, status = label) {
  return `<span class="pill ${statusClass(status)}">${escapeHtml(label || "ok")}</span>`;
}

function card(title, raw, metrics, extra = "") {
  const data = unwrap(raw);
  const status = raw?.ok === false ? "error" : data.status || (data.reachable === false ? "offline" : "ok");
  const body = metrics
    .map(([label, value]) => `<div class="metric"><span>${escapeHtml(label)}</span><b>${escapeHtml(compact(value))}</b></div>`)
    .join("");
  return `<article class="status-card">
    <div class="card-head"><h3>${escapeHtml(title)}</h3>${pill(raw?.ok === false ? "error" : compact(data.status || "ok"), status)}</div>
    ${body || `<p class="muted">暂无数据</p>`}
    ${extra}
  </article>`;
}

function addMessage(role, text, meta = "") {
  const node = document.createElement("div");
  node.className = `message ${role}`;
  node.textContent = meta ? `${text}\n\n${meta}` : text;
  els.messages.append(node);
  els.messages.scrollTop = els.messages.scrollHeight;
}

function openDetail(title, payload) {
  els.detailTitle.textContent = title;
  els.detailBody.textContent = JSON.stringify(payload, null, 2);
  els.detailDialog.showModal();
}

function toast(message, type = "info") {
  if (!els.toastHost) return;
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  node.innerHTML = `<b>${escapeHtml(type === "ok" ? "完成" : type === "bad" ? "注意" : "提示")}</b><span>${escapeHtml(message)}</span>`;
  els.toastHost.append(node);
  setTimeout(() => node.classList.add("leaving"), 2600);
  setTimeout(() => node.remove(), 3100);
}

function switchView(viewName) {
  const button = document.querySelector(`[data-view="${viewName}"]`);
  if (!button) return;
  button.click();
}

function renderCommandPalette(filter = "") {
  const q = filter.trim().toLowerCase();
  const matched = commands.filter((item) => `${item.title} ${item.hint} ${item.keys}`.toLowerCase().includes(q));
  els.commandList.innerHTML = matched
    .map((item) => `<button class="command-item" data-command="${escapeHtml(item.id)}">
      <span>${escapeHtml(item.title)}</span>
      <b>${escapeHtml(item.hint)}</b>
    </button>`)
    .join("") || `<div class="log-item"><h3>没有匹配项</h3><pre>换个关键词试试，例如：流程、终止、RAG、ASR。</pre></div>`;
}

function openCommandPalette() {
  renderCommandPalette();
  els.commandDialog.showModal();
  els.commandSearch.value = "";
  setTimeout(() => els.commandSearch.focus(), 30);
}

function closeCommandPalette() {
  if (els.commandDialog.open) els.commandDialog.close();
}

function seedAgentFlowPrompt() {
  switchView("chat");
  els.chatInput.value = "请帮我生成一个自定义动画自动化流程 JSON：步骤包括抽帧并剔除关键帧、ComfyUI 抠图、Cherry 平滑；每一步都要给 skill 和 arguments，路径使用 ${videos}/${frames}/${matte}/${smooth} 变量，并说明哪些参数建议我在 WebUI 里手动改。";
  els.chatInput.focus();
  toast("已把流程生成提示放到对话框", "ok");
}

function taskProgress(item) {
  const total = Number(item.total || 0);
  const completed = Number(item.completed || 0);
  const failed = Number(item.failed || 0);
  if (!total) return 0;
  return Math.max(0, Math.min(100, ((completed + failed) / total) * 100));
}

function normalizeTasks() {
  const s = state.lastStatus;
  const buckets = [
    ["ComfyUI", unwrap(s.comfyRuns).items || []],
    ["Cherry", unwrap(s.cherryRuns).items || []],
    ["Frame", unwrap(s.frameRuns).items || []],
    ["Pipeline", unwrap(s.pipelineRuns).items || []],
    ["自定义流程", unwrap(s.customPipelineRuns).items || []],
  ];
  return buckets.flatMap(([module, items]) =>
    items.map((item) => ({
      module,
      id: item.run_id || item.id || "",
      status: item.status || "-",
      progress: taskProgress(item),
      input: item.input_dir || item.frame_output_dir || item.workspace_root || item.workflow_name || "-",
      output: item.output_dir || item.smooth_output_dir || item.matte_output_dir || "-",
      updated_at: item.updated_at || item.created_at || "",
      raw: item,
    })),
  );
}

function activeTasks() {
  return normalizeTasks().filter((item) => !["DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED", "ARCHIVED"].includes(String(item.status).toUpperCase()));
}

function taskActionSkills(module) {
  const key = String(module || "").toLowerCase();
  if (key.includes("comfy")) return { pause: "comfyui.run_pause", resume: "comfyui.run_resume", cancel: "comfyui.run_cancel" };
  if (key.includes("cherry")) return { cancel: "cherry.run_cancel" };
  if (key.includes("frame") || key.includes("抽帧")) return { cancel: "frame.run_cancel" };
  if (key.includes("自定义")) return { cancel: "custom_pipeline.run_cancel" };
  if (key.includes("pipeline")) return { cancel: "pipeline.run_cancel" };
  return {};
}

function taskActionButtons(item) {
  const map = taskActionSkills(item.module);
  const key = `${item.module}:${item.id}`;
  const done = ["DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED", "ARCHIVED"].includes(String(item.status).toUpperCase());
  const buttons = [`<button class="small-button ghost" data-open-detail="${escapeHtml(key)}">详情</button>`];
  if (!done && map.pause) buttons.push(`<button class="small-button" data-task-action="pause" data-task-key="${escapeHtml(key)}">暂停</button>`);
  if (!done && map.resume) buttons.push(`<button class="small-button" data-task-action="resume" data-task-key="${escapeHtml(key)}">继续</button>`);
  if (!done && map.cancel) buttons.push(`<button class="small-button danger" data-task-action="cancel" data-task-key="${escapeHtml(key)}">终止</button>`);
  return buttons.join("");
}

async function runTaskAction(task, action) {
  if (!task) return;
  if (!els.token.value.trim()) {
    toast("需要先填写 Skill Token，才能控制后端任务。", "bad");
    return;
  }
  const skill = taskActionSkills(task.module)[action];
  if (!skill) {
    toast(`${task.module} 暂未外露 ${action} 控制 API。`, "bad");
    return;
  }
  if (action === "cancel" && !confirm(`确认终止 ${task.module} ${task.id || "当前/latest"}？`)) return;
  const args = task.id ? { run_id: task.id } : {};
  if (skill === "comfyui.run_cancel") args.interrupt_current = true;
  toast(`正在调用 ${skill}`, "info");
  const payload = await skillCall(skill, args);
  if (payload.ok === false) {
    toast(payload.error || `${skill} 调用失败`, "bad");
  } else {
    toast(`${task.module} ${action === "cancel" ? "终止" : action === "pause" ? "暂停" : "继续"}指令已发送`, "ok");
  }
  openDetail(skill, payload);
  await refreshAll({ includeLazy: true });
}

function renderSnapshot() {
  const s = state.lastStatus;
  const health = s.health || {};
  const gpu = unwrap(s.gpu);
  const g0 = gpu.gpus?.[0] || {};
  const comfyCurrent = unwrap(s.comfyCurrent);
  const cherryCurrent = unwrap(s.cherryCurrent);
  const pipelineCurrent = unwrap(s.pipelineCurrent);
  const tasks = activeTasks();
  els.snapshotStamp.textContent = state.lastRefreshAt ? state.lastRefreshAt.toLocaleTimeString() : "-";
  els.activeCount.textContent = String(tasks.length);
  els.miniStatus.innerHTML = [
    mini("Agent", health.ok ? "online" : "offline"),
    mini("API", state.apiBase || "same-origin"),
    mini("GPU", g0.memory_total_mb ? `${g0.memory_used_mb}/${g0.memory_total_mb} MB` : compact(gpu.error, "unknown")),
    mini("ComfyUI", comfyCurrent.status || "idle"),
    mini("Cherry", cherryCurrent.status || "idle"),
    mini("Pipeline", pipelineCurrent.status || unwrap(s.pipelineRuns).items?.[0]?.status || "idle"),
  ].join("");
  els.activeWorkList.innerHTML = tasks.length
    ? tasks.slice(0, 7).map((item) => compactTask(item)).join("")
    : `<div class="log-item"><h3>${health.ok ? "Idle" : "Backend Offline"}</h3><pre>${escapeHtml(health.hint || health.error || "暂无活动任务")}</pre></div>`;
}

function mini(label, value) {
  return `<div class="metric"><span>${escapeHtml(label)}</span><b>${escapeHtml(compact(value))}</b></div>`;
}

function compactTask(item) {
  return `<div class="run-row" data-detail="${escapeHtml(item.module)}:${escapeHtml(item.id)}">
    <div><h3>${escapeHtml(item.module)}</h3><span class="muted">${escapeHtml(item.id || "-")}</span></div>
    ${pill(item.status, item.status)}
    <div class="progress"><div class="bar" style="width:${item.progress}%"></div></div>
    <span class="muted">${escapeHtml(fmtTime(item.updated_at))}</span>
    <div class="row-actions">${taskActionButtons(item)}</div>
  </div>`;
}

function renderCommand() {
  const s = state.lastStatus;
  const health = s.health || {};
  const gpu = unwrap(s.gpu);
  const g0 = gpu.gpus?.[0] || {};
  const process = unwrap(s.process);
  const comfyQueue = unwrap(s.comfyQueue);
  const animation = unwrap(s.animation);
  const p4 = unwrap(s.p4);

  els.statusStrip.innerHTML = [
    card("Gateway", health, [
      ["Brain", health.brain_provider],
      ["GPU Agent", health.agent_runs_on_gpu],
      ["Comfy Fake", health.comfyui_fake_mode],
    ]),
    card("GPU", s.gpu, [
      ["型号", g0.name],
      ["显存", g0.memory_total_mb ? `${g0.memory_used_mb}/${g0.memory_total_mb} MB` : "-"],
      ["利用率", g0.utilization_gpu_percent !== undefined ? `${g0.utilization_gpu_percent}%` : "-"],
    ]),
    card("Process", s.process, [
      ["匹配", process.count],
      ["首个", process.items?.[0]?.name],
      ["PID", process.items?.[0]?.pid],
    ]),
    card("Queue", s.comfyQueue, [
      ["Reachable", comfyQueue.reachable],
      ["Running", comfyQueue.running?.length ?? comfyQueue.queue_running ?? 0],
      ["Pending", comfyQueue.pending?.length ?? comfyQueue.queue_pending ?? 0],
    ]),
  ].join("");

  const modules = [
    moduleCard("comfyui", "ComfyUI", s.comfyCurrent, ["抠图", "队列", "工作流"], unwrap(s.comfyRuns)),
    moduleCard("cherry", "Cherry", s.cherryCurrent, ["时序平滑", "缩放", "锐化"], unwrap(s.cherryRuns)),
    moduleCard("frame", "抽帧", s.frameCurrent, ["飞书下载", "抽帧", "manifest"], unwrap(s.frameRuns)),
    moduleCard("pipeline", "Pipeline", s.pipelineCurrent, ["抽帧", "ComfyUI", "Cherry"], unwrap(s.pipelineRuns)),
    card("Animation Workspace", s.animation, [
      ["Root", animation.root],
      ["Videos", animation.counts?.videos],
      ["Frames", animation.counts?.frames],
      ["Matte", animation.counts?.matte],
      ["Smooth", animation.counts?.smooth],
    ]),
    card("P4", s.p4, [
      ["Workspace", p4.workspace || p4.client || p4.p4_client],
      ["Opened", p4.opened_count ?? p4.opened?.length],
      ["Error", p4.error],
    ]),
  ];
  els.moduleBoard.innerHTML = modules.join("");
  renderTaskTable();
  renderDiagnostics();
}

function moduleCard(key, title, currentRaw, stages, list) {
  const current = unwrap(currentRaw);
  const first = list.items?.[0] || {};
  const progress = Number(current.progress_percent || taskProgress(first));
  return `<article class="module-card">
    <div class="card-head"><h3>${escapeHtml(title)}</h3>${pill(currentRaw?.ok === false ? "异常" : current.status || first.status || "空闲", current.status || first.status)}</div>
    <div class="metric"><span>Active</span><b>${escapeHtml(first.run_id || current.run_id || "-")}</b></div>
    <div class="metric"><span>Runs</span><b>${escapeHtml(compact(list.count ?? list.items?.length ?? 0))}</b></div>
    <div class="metric"><span>Stages</span><b>${escapeHtml(stages.join(" / "))}</b></div>
    <div class="progress"><div class="bar" style="width:${Math.max(0, Math.min(100, progress))}%"></div></div>
    <div class="row-actions"><button class="small-button" data-config-module="${escapeHtml(key)}">调整参数</button></div>
  </article>`;
}

function renderTaskTable() {
  const tasks = normalizeTasks();
  els.taskTableStamp.textContent = state.lastRefreshAt ? state.lastRefreshAt.toLocaleTimeString() : "-";
  const header = `<div class="task-row header"><span>模块 / Run</span><span>状态</span><span>输入</span><span>输出</span><span>操作</span></div>`;
  els.taskTable.innerHTML =
    header +
    (tasks.length
      ? tasks.map((item) => taskRow(item)).join("")
      : `<div class="log-item"><h3>暂无任务</h3><pre>后端没有返回任务记录，或 Skill Token 未填写。</pre></div>`);
}

function taskRow(item) {
  return `<div class="task-row">
    <div><h3>${escapeHtml(item.module)}</h3><span class="muted">${escapeHtml(item.id || "-")}</span></div>
    <div>${pill(item.status, item.status)}<div class="progress"><div class="bar" style="width:${item.progress}%"></div></div></div>
    <span class="muted">${escapeHtml(item.input)}</span>
    <span class="muted">${escapeHtml(item.output)}</span>
    <div class="row-actions">${taskActionButtons(item)}</div>
  </div>`;
}

function renderDiagnostics() {
  const diag = unwrap(state.lastStatus.agentDiagnose);
  const work = unwrap(state.lastStatus.agentWork);
  const payload = diag.ok || diag.text || diag.error ? diag : work;
  els.diagnostics.innerHTML = `<div class="log-item"><h3>${escapeHtml(payload.ok === false ? "Need Attention" : "Snapshot")}</h3><pre>${escapeHtml(JSON.stringify(payload, null, 2))}</pre></div>`;
}

function renderQueues() {
  const queue = unwrap(state.lastStatus.comfyQueue);
  els.queueStamp.textContent = state.lastRefreshAt ? state.lastRefreshAt.toLocaleTimeString() : "-";
  els.comfyQueuePanel.innerHTML = card("ComfyUI Queue", state.lastStatus.comfyQueue, [
    ["Reachable", queue.reachable],
    ["Fake Mode", queue.fake_mode],
    ["Running", queue.running?.length ?? queue.queue_running ?? 0],
    ["Pending", queue.pending?.length ?? queue.queue_pending ?? 0],
  ]);
  els.comfyRunsPanel.innerHTML = runList("ComfyUI", unwrap(state.lastStatus.comfyRuns).items || []);
  els.cherryRunsPanel.innerHTML = runList("Cherry", unwrap(state.lastStatus.cherryRuns).items || []);
  els.frameRunsPanel.innerHTML = runList("Frame", unwrap(state.lastStatus.frameRuns).items || []);
}

function runList(module, items) {
  if (!items.length) return `<div class="log-item"><h3>${escapeHtml(module)} idle</h3><pre>暂无记录</pre></div>`;
  return `<div class="run-list">${items.map((item) => compactTask({ module, id: item.run_id, status: item.status, progress: taskProgress(item), updated_at: item.updated_at || item.created_at, raw: item })).join("")}</div>`;
}

function renderPipeline() {
  const current = unwrap(state.lastStatus.pipelineCurrent);
  const list = unwrap(state.lastStatus.pipelineRuns);
  const animation = unwrap(state.lastStatus.animation);
  renderTimeline(current.run_id ? current : list.items?.[0] || {});
  els.workspaceRoot.textContent = animation.root || "-";
  const counts = animation.counts || {};
  els.workspaceCounters.innerHTML = [
    counter("Videos", counts.videos),
    counter("Frames", counts.frames),
    counter("Matte", counts.matte),
    counter("Smooth", counts.smooth),
  ].join("");
  els.pipelineRunsPanel.innerHTML = runList("Pipeline", list.items || []);
}

function counter(label, value) {
  return `<div class="counter"><b>${escapeHtml(compact(value, "0"))}</b><span class="muted">${escapeHtml(label)}</span></div>`;
}

function renderTimeline(run = {}) {
  els.timelineStamp.textContent = state.lastRefreshAt ? state.lastRefreshAt.toLocaleTimeString() : "-";
  const current = run.current_step || "";
  const status = run.status || "";
  const steps = [
    ["frame", "1. 抽帧 / 下载", run.frame_run_id || run.frame?.run_id],
    ["comfyui", "2. ComfyUI 抠图", run.comfyui_run_id || run.comfyui?.run_id],
    ["cherry", "3. Cherry 平滑", run.cherry_run_id || run.cherry?.run_id],
    ["done", "4. 输出 / 复核", status],
  ];
  els.timeline.innerHTML = steps
    .map(([key, label, detail]) => {
      const done = key === "done" ? ["DONE", "DONE_WITH_ERRORS"].includes(String(status).toUpperCase()) : Boolean(detail) && key !== current;
      return `<div class="step ${current === key ? "active" : ""} ${done ? "done" : ""}">
        <h3>${escapeHtml(label)}</h3>
        <p class="muted">${escapeHtml(compact(detail))}</p>
      </div>`;
    })
    .join("");
}

function renderP4() {
  const p4 = unwrap(state.lastStatus.p4);
  els.p4Panel.innerHTML = card("P4 Status", state.lastStatus.p4, [
    ["Workspace", p4.workspace || p4.client || p4.p4_client],
    ["Root", p4.root || p4.workspace_root],
    ["Opened", p4.opened_count ?? p4.opened?.length],
    ["Reconcile", p4.reconcile_count ?? p4.reconcile?.length],
    ["Error", p4.error],
  ]);
}

function defaultFlowStep(skill = "frame.run_start") {
  const index = state.flowSteps.length + 1;
  const presets = {
    "frame.run_start": { download_dir: "${videos}", export_dir: "${frames}", fps: 24, max_frames: 0, diff_threshold: 0.2, dedup_enabled: false, dedup_renumber: false },
    "comfyui.run_start": { input_dir: "${frames}", output_dir: "${matte}", recursive: true, preserve_structure: true, skip_existing: true, max_images: 50000 },
    "cherry.run_start": { input_dir: "${matte}", output_dir: "${smooth}", recursive: true, skip_existing: true, max_images: 50000, use_smooth: true, smooth_window: 5, smooth_sigma: 1.0 },
    "speech.synthesize": { text: "流程已经完成。", engine: "auto" },
  };
  return {
    id: `step_${index}`,
    name: skill,
    skill,
    enabled: true,
    arguments: presets[skill] || {},
  };
}

function renderFlowSteps() {
  if (!state.flowSteps.length) {
    state.flowSteps = [
      defaultFlowStep("frame.run_start"),
      defaultFlowStep("comfyui.run_start"),
      defaultFlowStep("cherry.run_start"),
    ];
  }
  els.flowSteps.innerHTML = state.flowSteps
    .map((step, index) => `<article class="flow-step" data-step-index="${index}">
      <div class="card-head">
        <div class="flow-node-title">
          <span class="flow-index">${escapeHtml(String(index + 1).padStart(2, "0"))}</span>
          <div>
            <h3>${escapeHtml(step.name || step.skill)}</h3>
            <b>${escapeHtml(step.skill)}</b>
          </div>
        </div>
        <div class="button-row">
          <button class="small-button" data-flow-move="up" data-step-index="${index}">上移</button>
          <button class="small-button" data-flow-move="down" data-step-index="${index}">下移</button>
          <button class="small-button danger" data-flow-remove="${index}">删除</button>
        </div>
      </div>
      <div class="flow-glance">
        <span>${step.enabled !== false ? "已启用" : "已停用"}</span>
        <span>${escapeHtml(Object.keys(step.arguments || {}).length)} 个参数</span>
        <span>${escapeHtml(flowStepSummary(step.arguments || {}))}</span>
      </div>
      <div class="flow-step-grid">
        <label><span>步骤 ID</span><input data-flow-field="id" data-step-index="${index}" value="${escapeHtml(step.id)}" /></label>
        <label><span>显示名称</span><input data-flow-field="name" data-step-index="${index}" value="${escapeHtml(step.name)}" /></label>
        <label><span>后端 Skill</span><select data-flow-field="skill" data-step-index="${index}">${flowSkillOptions(step.skill)}</select></label>
        <label class="toggle-field"><input type="checkbox" data-flow-field="enabled" data-step-index="${index}" ${step.enabled !== false ? "checked" : ""} /><span>启用此步骤</span></label>
        <label class="wide-field"><span>参数 JSON</span><textarea rows="8" data-flow-field="arguments" data-step-index="${index}">${escapeHtml(JSON.stringify(step.arguments || {}, null, 2))}</textarea></label>
      </div>
    </article>`)
    .join("");
}

function flowStepSummary(args) {
  const picks = ["download_dir", "export_dir", "input_dir", "output_dir", "workflow_path", "audio_path", "text"];
  const hit = picks.find((key) => args[key]);
  return hit ? `${hit}: ${args[hit]}` : "等待配置输入输出";
}

function flowSkillOptions(selected) {
  const skills = [
    "frame.run_start",
    "frame.run_preview",
    "comfyui.run_start",
    "comfyui.run_preview",
    "comfyui.run_update",
    "cherry.run_start",
    "cherry.run_preview",
    "pipeline.run_start",
    "pipeline.run_preview",
    "speech.transcribe",
    "speech.synthesize",
    "memory.context_pack",
  ];
  return skills.map((skill) => `<option value="${escapeHtml(skill)}" ${skill === selected ? "selected" : ""}>${escapeHtml(skill)}</option>`).join("");
}

function syncFlowStepFromField(target) {
  const index = Number(target.dataset.stepIndex);
  const field = target.dataset.flowField;
  if (!Number.isInteger(index) || !field || !state.flowSteps[index]) return;
  if (field === "enabled") {
    state.flowSteps[index][field] = target.checked;
  } else if (field === "arguments") {
    try {
      state.flowSteps[index][field] = JSON.parse(target.value || "{}");
      target.classList.remove("invalid");
    } catch {
      target.classList.add("invalid");
    }
  } else {
    state.flowSteps[index][field] = target.value;
    if (field === "skill") {
      state.flowSteps[index].name = target.value;
      if (!state.flowSteps[index].arguments || !Object.keys(state.flowSteps[index].arguments).length) {
        state.flowSteps[index].arguments = defaultFlowStep(target.value).arguments;
      }
      renderFlowSteps();
    }
  }
}

function collectFlowDefinition() {
  let variables = {};
  try {
    variables = JSON.parse(els.flowVariables.value || "{}");
    els.flowVariables.classList.remove("invalid");
  } catch {
    els.flowVariables.classList.add("invalid");
    throw new Error("变量 JSON 格式不正确");
  }
  return {
    name: els.flowName.value.trim() || "custom_flow",
    description: els.flowDescription.value.trim(),
    variables,
    steps: state.flowSteps,
  };
}

async function loadModuleCatalog() {
  const payload = await skillCall("custom_pipeline.module_catalog", {});
  state.moduleCatalog = unwrap(payload);
  const modules = state.moduleCatalog.modules || [];
  els.moduleCatalog.innerHTML = modules.length
    ? modules.map((mod) => `<article class="module-catalog-card">
      <h3>${escapeHtml(mod.name)}</h3>
      <p class="muted">${escapeHtml((mod.skills || []).join(" / "))}</p>
      <pre>${escapeHtml(JSON.stringify(mod.parameters || {}, null, 2))}</pre>
    </article>`).join("")
    : `<article class="log-item"><h3>暂无模块目录</h3><pre>${escapeHtml(JSON.stringify(payload, null, 2))}</pre></article>`;
}

async function loadFlowDefinitions() {
  const payload = await skillCall("custom_pipeline.list_definitions", {});
  const items = unwrap(payload).items || [];
  els.flowDefinitions.innerHTML = items.length
    ? items.map((item) => `<article class="log-item">
      <div class="card-head"><h3>${escapeHtml(item.name)}</h3><button class="small-button" data-load-flow="${escapeHtml(item.name)}">载入</button></div>
      <pre>${escapeHtml(item.description || item.path || "")}</pre>
    </article>`).join("")
    : `<article class="log-item"><h3>暂无保存流程</h3><pre>你可以先保存一个自定义流程。</pre></article>`;
}

async function saveFlowDefinition() {
  if (!els.token.value.trim()) return alert("需要 Skill Token。");
  const definition = collectFlowDefinition();
  toast("正在保存自定义流程", "info");
  const payload = await skillCall("custom_pipeline.save_definition", { ...definition, overwrite: true });
  els.flowResult.textContent = JSON.stringify(payload, null, 2);
  toast(payload.ok === false ? "流程保存失败" : "流程已保存到后端", payload.ok === false ? "bad" : "ok");
  await loadFlowDefinitions();
}

async function previewFlowDefinition() {
  if (!els.token.value.trim()) return alert("需要 Skill Token。");
  const definition = collectFlowDefinition();
  toast("正在预览流程 API", "info");
  const payload = await skillCall("custom_pipeline.preview_definition", { definition, variables: definition.variables });
  els.flowResult.textContent = JSON.stringify(payload, null, 2);
  toast(payload.ok === false ? "流程预览失败" : "流程预览已生成", payload.ok === false ? "bad" : "ok");
}

async function runFlowDefinition() {
  if (!els.token.value.trim()) return alert("需要 Skill Token。");
  const definition = collectFlowDefinition();
  if (!confirm(`确认执行自定义流程 ${definition.name}？`)) return;
  toast("正在启动自定义流程", "info");
  const payload = await skillCall("custom_pipeline.run_start", { definition, variables: definition.variables });
  els.flowResult.textContent = JSON.stringify(payload, null, 2);
  toast(payload.ok === false ? "自定义流程启动失败" : "自定义流程已启动", payload.ok === false ? "bad" : "ok");
  await refreshAll({ includeLazy: true });
}

async function loadFlowDefinition(name) {
  const payload = await skillCall("custom_pipeline.get_definition", { name });
  const definition = unwrap(payload).definition || {};
  els.flowName.value = definition.name || name;
  els.flowDescription.value = definition.description || "";
  els.flowVariables.value = JSON.stringify(definition.variables || {}, null, 2);
  state.flowSteps = definition.steps || [];
  renderFlowSteps();
}

async function runAsr() {
  if (!els.token.value.trim()) return alert("需要 Skill Token。");
  toast("正在调用 ASR 语音识别", "info");
  const payload = await skillCall("speech.transcribe", {
    audio_path: els.asrAudioPath.value.trim(),
    language: els.asrLanguage.value.trim() || "zh",
    prompt: els.asrPrompt.value.trim(),
  });
  els.asrResult.textContent = JSON.stringify(payload, null, 2);
  toast(payload.ok === false ? "ASR 识别失败" : "ASR 识别完成", payload.ok === false ? "bad" : "ok");
}

async function runTts() {
  if (!els.token.value.trim()) return alert("需要 Skill Token。");
  const args = {
    text: els.ttsText.value.trim(),
    output_path: els.ttsOutputPath.value.trim(),
    voice: els.ttsVoice.value.trim(),
    engine: els.ttsEngine.value.trim(),
    rate: els.ttsRate.value.trim(),
  };
  for (const key of Object.keys(args)) {
    if (!args[key]) delete args[key];
  }
  toast("正在调用 TTS 语音合成", "info");
  const payload = await skillCall("speech.synthesize", args);
  els.ttsResult.textContent = JSON.stringify(payload, null, 2);
  toast(payload.ok === false ? "TTS 合成失败" : "TTS 合成完成", payload.ok === false ? "bad" : "ok");
}

async function renderMemory() {
  const scope = encodeURIComponent(els.memoryScope.value.trim() || "global");
  const payload = await jsonFetch(`/api/admin/memory?scope=${scope}&limit=30`);
  const conversationId = els.memoryConversation.value.trim() || "test";
  const pack = els.token.value.trim()
    ? await skillCall("memory.context_pack", { conversation_id: conversationId, recent_limit: 12, max_chars: 6000 })
    : { ok: false, error: "需要填写 Skill Token 才能读取后端 RAG context pack。" };
  const items = payload.items || [];
  els.memoryList.innerHTML = [
    `<article class="log-item"><h3>RAG Context Pack / ${escapeHtml(conversationId)}</h3><pre>${escapeHtml(JSON.stringify(unwrap(pack), null, 2))}</pre></article>`,
    ...(items.length
      ? items.map((item) => `<article class="log-item"><h3>${escapeHtml(item.key || item.scope || "memory")}</h3><pre>${escapeHtml(item.value || JSON.stringify(item, null, 2))}</pre></article>`)
      : [`<article class="log-item"><h3>暂无 Memory Notes</h3><pre>${escapeHtml(payload.error || "当前 scope 没有返回记忆条目。")}</pre></article>`]),
  ].join("");

  const retrieval = state.skills.filter((item) => ["memory", "web", "life", "speech"].includes(item.domain) || /rag|memory|search|recall|vector/i.test(`${item.name} ${item.description}`));
  els.memorySkillCount.textContent = String(retrieval.length);
  els.memorySkills.innerHTML = retrieval.map(skillCard).join("") || `<article class="skill-card"><h3>等待 manifest</h3><p>后端在线后会显示检索/记忆相关技能。</p></article>`;
}

function renderSkills() {
  const q = els.skillSearch.value.trim().toLowerCase();
  const filtered = state.skills.filter((item) => `${item.name} ${item.domain} ${item.risk_level} ${item.description}`.toLowerCase().includes(q));
  els.skillCount.textContent = `${filtered.length}/${state.skills.length}`;
  els.skillsList.innerHTML = filtered.map(skillCard).join("") || `<article class="skill-card"><h3>暂无技能</h3><p>后端未返回 manifest。</p></article>`;
}

function skillCard(item) {
  return `<article class="skill-card">
    <div class="card-head"><h3>${escapeHtml(item.name)}</h3>${pill(item.risk_level || "readonly", item.risk_level)}</div>
    <p>${escapeHtml(item.description || "")}</p>
    <div class="metric"><span>Domain</span><b>${escapeHtml(item.domain || "-")}</b></div>
    <div class="metric"><span>Confirm</span><b>${escapeHtml(item.requires_confirmation ? "yes" : "no")}</b></div>
  </article>`;
}

async function loadSkills() {
  const payload = await jsonFetch("/api/skills/manifest");
  if (payload.ok === false) {
    state.skills = [];
  } else {
    state.skills = payload.skills || [];
  }
  renderSkills();
  if (state.currentView === "memory") renderMemory();
}

async function loadLogs() {
  const id = encodeURIComponent(els.conversationId.value.trim() || "test");
  const [messages, calls] = await Promise.all([
    jsonFetch(`/api/admin/brain-messages?conversation_id=${id}&limit=40`),
    jsonFetch("/api/admin/skill-calls?limit=80"),
  ]);
  const msgItems = messages.items || [];
  const callItems = calls.items || [];
  const messageHtml = msgItems.length
    ? msgItems
        .map((item) => `<article class="log-item">
          <h3>对话 / ${escapeHtml(item.channel || item.created_at || "message")}</h3>
          <pre>${escapeHtml(`用户：${item.message_text || ""}\n助手：${item.response_text || ""}\n工具：${item.tool_calls_json || ""}`)}</pre>
        </article>`)
        .join("")
    : `<article class="log-item"><h3>对话日志为空</h3><pre>${escapeHtml(messages.error || "这个 conversation_id 还没有消息。")}</pre></article>`;
  const callHtml = callItems.length
    ? callItems
        .map((item) => `<article class="log-item">
          <h3>技能调用 / ${escapeHtml(item.skill)} ${item.ok ? "成功" : "失败"}</h3>
          <pre>${escapeHtml(`请求方：${item.requested_by}\n时间：${item.created_at}\n参数：${item.arguments_json}\n错误：${item.error || "-"}\n结果：${item.result_json}`)}</pre>
        </article>`)
        .join("")
    : `<article class="log-item"><h3>技能调用日志为空</h3><pre>${escapeHtml(calls.error || "还没有 skill call。")}</pre></article>`;
  els.logsList.innerHTML = `<div class="log-section-title">对话日志</div>${messageHtml}<div class="log-section-title">技能调用日志</div>${callHtml}`;
}

function renderAll() {
  renderSnapshot();
  renderCommand();
  renderQueues();
  renderPipeline();
  renderP4();
  if (state.currentView === "skills") renderSkills();
}

async function refreshAll(options = {}) {
  const health = await jsonFetch("/api/health");
  state.lastStatus.health = health;
  setAgentStatus(health);
  state.lastRefreshAt = new Date();

  if (health.ok && els.token.value.trim()) {
    const calls = statusCalls.filter((call) => options.includeLazy || !call.lazy);
    const results = await Promise.all(calls.map((call) => skillCall(call.skill, call.args).catch((error) => ({ ok: false, error: String(error) }))));
    calls.forEach((call, index) => {
      state.lastStatus[call.key] = results[index];
    });
  }

  renderAll();
  if (state.currentView === "logs") await loadLogs();
  if (state.currentView === "memory") await renderMemory();
}

async function sendChat(text) {
  addMessage("user", text);
  toast("已发送给本机 Agent", "info");
  const attachmentNote = state.attachments.length
    ? `\n\n[WebUI attachments selected: ${state.attachments.map((file) => `${file.name} (${file.type || "file"})`).join(", ")}. Current backend upload bridge is not yet attached; use local paths or Feishu attachments for execution.]`
    : "";
  const payload = await jsonFetch("/api/brain/test", {
    method: "POST",
    body: JSON.stringify({ text: `${text}${attachmentNote}` }),
  });
  if (payload.ok === false) {
    addMessage("agent", `后端暂时不可用：${payload.error || payload.detail || "unknown error"}`);
    toast("Agent 后端返回异常", "bad");
    return;
  }
  addMessage("agent", payload.text || payload.reply || payload.message || JSON.stringify(payload, null, 2));
  toast("Agent 已回复", "ok");
  state.attachments = [];
  renderAttachments();
  setTimeout(() => refreshAll({ includeLazy: true }), 600);
}

function renderAttachments() {
  els.attachmentTray.classList.toggle("active", state.attachments.length > 0);
  els.attachmentTray.innerHTML = state.attachments.map((file) => `<span class="attachment-chip">${escapeHtml(file.name)} <b>${escapeHtml(Math.ceil(file.size / 1024))} KB</b></span>`).join("");
}

function configStorageKey(module) {
  return `assetclaw.moduleConfig.${module}`;
}

function loadModuleConfig(module) {
  const schema = moduleConfigs[module];
  const defaults = Object.fromEntries(schema.fields.map(([key, _label, _type, value]) => [key, value]));
  try {
    return { ...defaults, ...JSON.parse(localStorage.getItem(configStorageKey(module)) || "{}") };
  } catch {
    return defaults;
  }
}

function collectModuleConfig(module) {
  const schema = moduleConfigs[module];
  const result = {};
  for (const [key, _label, type] of schema.fields) {
    const input = els.configForm.querySelector(`[name="${key}"]`);
    if (!input) continue;
    if (type === "checkbox") {
      result[key] = input.checked;
    } else if (type === "number") {
      const value = input.value.trim();
      result[key] = value === "" ? undefined : Number(value);
    } else {
      result[key] = input.value.trim();
    }
  }
  for (const key of Object.keys(result)) {
    if (result[key] === "" || result[key] === undefined || Number.isNaN(result[key])) delete result[key];
  }
  return result;
}

function openConfig(module) {
  const schema = moduleConfigs[module];
  if (!schema) return;
  activeConfigModule = module;
  const values = loadModuleConfig(module);
  els.configTitle.textContent = schema.title;
  els.configHint.textContent = "这些是 WebUI 外露参数；保存后会作为该模块默认参数，调用后端仍走 skill API。";
  els.configForm.innerHTML = schema.fields
    .map(([key, label, type]) => {
      const value = values[key];
      if (type === "checkbox") {
        return `<label class="toggle-field"><input type="checkbox" name="${escapeHtml(key)}" ${value ? "checked" : ""} /><span>${escapeHtml(label)}</span></label>`;
      }
      return `<label><span>${escapeHtml(label)}</span><input name="${escapeHtml(key)}" type="${escapeHtml(type)}" value="${escapeHtml(value ?? "")}" /></label>`;
    })
    .join("");
  els.configResult.textContent = "";
  els.configDialog.showModal();
}

function saveActiveConfig() {
  if (!activeConfigModule) return {};
  const payload = collectModuleConfig(activeConfigModule);
  localStorage.setItem(configStorageKey(activeConfigModule), JSON.stringify(payload));
  els.configResult.textContent = `已保存 ${moduleConfigs[activeConfigModule].title}：\n${JSON.stringify(payload, null, 2)}`;
  toast(`${moduleConfigs[activeConfigModule].title} 已保存`, "ok");
  return payload;
}

async function callActiveConfig(preview = true) {
  if (!activeConfigModule) return;
  if (!els.token.value.trim()) {
    alert("需要先填写 Skill Token，才能调用后端 skill API。");
    return;
  }
  const schema = moduleConfigs[activeConfigModule];
  const payload = saveActiveConfig();
  const skill = preview ? schema.previewSkill : schema.startSkill;
  if (!preview && !confirm(`确认调用后端 ${skill}？`)) return;
  toast(`正在调用 ${skill}`, "info");
  const result = await skillCall(skill, payload);
  els.configResult.textContent = JSON.stringify(result, null, 2);
  toast(result.ok === false ? `${skill} 调用失败` : `${skill} 已返回`, result.ok === false ? "bad" : "ok");
  await refreshAll({ includeLazy: true });
}

async function runControl(action) {
  const module = els.controlModule.value;
  const runId = els.controlRunId.value.trim();
  const map = {
    comfyui: { pause: "comfyui.run_pause", resume: "comfyui.run_resume", cancel: "comfyui.run_cancel" },
    cherry: { pause: "", resume: "", cancel: "cherry.run_cancel" },
    pipeline: { pause: "", resume: "", cancel: "pipeline.run_cancel" },
    frame: { pause: "", resume: "", cancel: "frame.run_cancel" },
  };
  const skill = map[module]?.[action];
  if (!skill) {
    els.controlResult.textContent = `${module} 暂未注册 ${action} 控制技能。`;
    toast(`${module} 暂未注册 ${action} 控制技能`, "bad");
    return;
  }
  if (action === "cancel" && !confirm(`确认终止 ${module} ${runId || "当前/latest"}？`)) return;
  const args = runId ? { run_id: runId } : {};
  if (skill === "comfyui.run_cancel") args.interrupt_current = true;
  toast(`正在调用 ${skill}`, "info");
  const payload = await skillCall(skill, args);
  els.controlResult.textContent = JSON.stringify(payload, null, 2);
  toast(payload.ok === false ? `${skill} 调用失败` : `${skill} 指令已发送`, payload.ok === false ? "bad" : "ok");
  await refreshAll();
}

async function runP4(kind) {
  const skill = `p4.${kind}`;
  const payload = await skillCall(skill, {});
  els.p4Result.textContent = JSON.stringify(payload, null, 2);
  await refreshAll();
}

function findTaskByKey(key) {
  const [module, id] = key.split(":");
  return normalizeTasks().find((item) => item.module === module && item.id === id);
}

function bind() {
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", async () => {
      state.currentView = button.dataset.view;
      document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item === button));
      document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${state.currentView}`));
      const [eyebrow, title] = views[state.currentView] || views.chat;
      els.viewEyebrow.textContent = eyebrow;
      els.viewTitle.textContent = title;
      if (state.currentView === "skills") await loadSkills();
      if (state.currentView === "logs") await loadLogs();
      if (state.currentView === "memory") await renderMemory();
      if (state.currentView === "builder") {
        renderFlowSteps();
        if (!state.moduleCatalog) await loadModuleCatalog();
        await loadFlowDefinitions();
      }
    });
  });

  els.chatForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = els.chatInput.value.trim();
    if (!text) return;
    els.chatInput.value = "";
    sendChat(text);
  });
  els.chatInput.addEventListener("input", () => {
    els.chatInput.style.height = "auto";
    els.chatInput.style.height = `${Math.min(132, els.chatInput.scrollHeight)}px`;
  });
  document.querySelectorAll("[data-prompt]").forEach((button) => {
    button.addEventListener("click", () => {
      const prompt = button.dataset.prompt;
      if (state.currentView === "chat") {
        els.chatInput.value = prompt;
        els.chatInput.focus();
      } else {
        sendChat(prompt);
      }
    });
  });

  els.attachBtn.addEventListener("click", () => els.fileInput.click());
  els.fileInput.addEventListener("change", () => {
    state.attachments = Array.from(els.fileInput.files || []);
    renderAttachments();
  });

  els.refreshBtn.addEventListener("click", () => refreshAll({ includeLazy: true }));
  els.pollBtn.addEventListener("click", () => {
    state.polling = !state.polling;
    els.pollBtn.classList.toggle("active", state.polling);
    els.pollBtn.textContent = state.polling ? "Auto" : "Manual";
    toast(state.polling ? "已开启自动轮询" : "已切到手动刷新", "ok");
  });
  els.commandPaletteBtn.addEventListener("click", openCommandPalette);
  els.closeCommandDialog.addEventListener("click", closeCommandPalette);
  els.commandSearch.addEventListener("input", () => renderCommandPalette(els.commandSearch.value));
  els.commandList.addEventListener("click", async (event) => {
    const target = event.target.closest("[data-command]");
    if (!target) return;
    const command = commands.find((item) => item.id === target.dataset.command);
    closeCommandPalette();
    if (command) await command.run();
  });
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      openCommandPalette();
    }
    if (event.key === "Escape") closeCommandPalette();
  });
  document.addEventListener("pointermove", (event) => {
    document.documentElement.style.setProperty("--mx", `${event.clientX}px`);
    document.documentElement.style.setProperty("--my", `${event.clientY}px`);
  });
  els.token.addEventListener("change", () => {
    localStorage.setItem("assetclaw.skillToken", els.token.value.trim());
    refreshAll({ includeLazy: true });
    toast("Skill Token 已保存到本机浏览器", "ok");
  });
  els.diagnoseBtn.addEventListener("click", async () => {
    state.lastStatus.agentDiagnose = await skillCall("agent.diagnose", { include_gpu: false });
    renderDiagnostics();
  });
  document.querySelectorAll("[data-action]").forEach((button) => button.addEventListener("click", () => runControl(button.dataset.action)));
  document.querySelectorAll("[data-p4]").forEach((button) => button.addEventListener("click", () => runP4(button.dataset.p4)));
  els.skillSearch.addEventListener("input", renderSkills);
  els.loadLogsBtn.addEventListener("click", loadLogs);
  els.conversationId.addEventListener("change", loadLogs);
  els.loadMemoryBtn.addEventListener("click", renderMemory);
  els.compactMemoryBtn.addEventListener("click", async () => {
    if (!els.token.value.trim()) {
      alert("需要先填写 Skill Token。");
      return;
    }
    const conversationId = els.memoryConversation.value.trim() || "test";
    if (!confirm(`确认在后端压缩 ${conversationId}，只保留最近 12 条原文？`)) return;
    const payload = await skillCall("memory.compact", { conversation_id: conversationId, keep_messages: 12, max_chars: 6000 });
    openDetail("memory.compact", payload);
    await renderMemory();
  });
  els.closeDetail.addEventListener("click", () => els.detailDialog.close());
  document.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (button && !button.disabled) {
      const rect = button.getBoundingClientRect();
      const ripple = document.createElement("i");
      ripple.className = "ripple";
      ripple.style.left = `${event.clientX - rect.left}px`;
      ripple.style.top = `${event.clientY - rect.top}px`;
      button.append(ripple);
      setTimeout(() => ripple.remove(), 520);
    }
    const actionTarget = event.target.closest("[data-task-action]");
    if (actionTarget) {
      const task = findTaskByKey(actionTarget.dataset.taskKey);
      runTaskAction(task, actionTarget.dataset.taskAction);
      return;
    }
    const target = event.target.closest("[data-open-detail]");
    if (target) {
      const task = findTaskByKey(target.dataset.openDetail);
      openDetail(target.dataset.openDetail, task?.raw || task || {});
      return;
    }
    const configTarget = event.target.closest("[data-config-module]");
    if (configTarget) {
      openConfig(configTarget.dataset.configModule);
    }
  });
  els.closeConfig.addEventListener("click", () => els.configDialog.close());
  els.saveConfig.addEventListener("click", saveActiveConfig);
  els.previewConfig.addEventListener("click", () => callActiveConfig(true));
  els.startConfig.addEventListener("click", () => callActiveConfig(false));
  els.addFlowStepBtn.addEventListener("click", () => {
    state.flowSteps.push(defaultFlowStep("frame.run_start"));
    renderFlowSteps();
    toast("已添加流程步骤", "ok");
  });
  els.agentBuildFlowBtn.addEventListener("click", seedAgentFlowPrompt);
  els.flowSteps.addEventListener("input", (event) => {
    const target = event.target.closest("[data-flow-field]");
    if (target) syncFlowStepFromField(target);
  });
  els.flowSteps.addEventListener("change", (event) => {
    const target = event.target.closest("[data-flow-field]");
    if (target) syncFlowStepFromField(target);
  });
  els.flowSteps.addEventListener("click", (event) => {
    const remove = event.target.closest("[data-flow-remove]");
    if (remove) {
      state.flowSteps.splice(Number(remove.dataset.flowRemove), 1);
      renderFlowSteps();
      toast("已删除流程步骤", "ok");
      return;
    }
    const mover = event.target.closest("[data-flow-move]");
    if (mover) {
      const index = Number(mover.dataset.stepIndex);
      const next = mover.dataset.flowMove === "up" ? index - 1 : index + 1;
      if (next >= 0 && next < state.flowSteps.length) {
        [state.flowSteps[index], state.flowSteps[next]] = [state.flowSteps[next], state.flowSteps[index]];
        renderFlowSteps();
        toast("步骤顺序已调整", "ok");
      }
    }
  });
  els.saveFlowBtn.addEventListener("click", saveFlowDefinition);
  els.previewFlowBtn.addEventListener("click", previewFlowDefinition);
  els.runFlowBtn.addEventListener("click", runFlowDefinition);
  els.loadModuleCatalogBtn.addEventListener("click", loadModuleCatalog);
  els.loadFlowDefsBtn.addEventListener("click", loadFlowDefinitions);
  els.flowDefinitions.addEventListener("click", (event) => {
    const target = event.target.closest("[data-load-flow]");
    if (target) loadFlowDefinition(target.dataset.loadFlow);
  });
  els.runAsrBtn.addEventListener("click", runAsr);
  els.runTtsBtn.addEventListener("click", runTts);
}

async function boot() {
  els.token.value = localStorage.getItem("assetclaw.skillToken") || "";
  bind();
  addMessage("system", window.location.protocol === "file:"
    ? "你正在用 file:// 打开页面。按钮现在会工作，但 API 会自动连 http://127.0.0.1:5178；请保持本机 WebUI server 运行。"
    : "AssetClaw Operator Console 已就绪。本界面只监听本机地址，状态和聊天均通过本机 Agent 后端进入。");
  await loadSkills();
  renderFlowSteps();
  await refreshAll({ includeLazy: true });
  setInterval(() => {
    if (state.polling) refreshAll();
  }, 5000);
}

boot();
