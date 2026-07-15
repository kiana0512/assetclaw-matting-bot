<script setup>
import { computed, onBeforeUnmount, onMounted, reactive } from "vue";

const POLL_MS = 12000;
const FINISHED = new Set(["DONE", "FAILED", "CANCELED", "BLOCKED", "DONE_WITH_ERRORS"]);
const STAGE_LABELS = {
  feishu_download: "飞书下载",
  frame_extract: "抽帧",
  matting: "ComfyUI 抠图",
  cherry_smooth: "Cherry 平滑",
  unity_ready: "unity_ready",
  unity_import: "Unity 导入",
  p4_shelve: "P4 Shelve",
};

const tabs = [
  { id: "overview", label: "总览" },
  { id: "tasks", label: "任务" },
  { id: "launch", label: "启动" },
  { id: "agent", label: "Agent" },
];

const state = reactive({
  tab: "overview",
  theme: localStorage.getItem("assetclaw.webui.theme") || "dark",
  auto: true,
  refreshing: false,
  health: null,
  error: "",
  updatedAt: "",
  flows: { items: [], current: null },
  animation: null,
  workspaceSummary: null,
  comfyStatus: null,
  frameStatus: null,
  cherryStatus: null,
  comfyRuns: [],
  frameRuns: [],
  cherryRuns: [],
  jobs: [],
  detail: null,
  toasts: [],
  form: {
    date: todayLabel(),
    mode: "iteration",
    priority: "",
    workflowPath: "",
    unityProject: "D:/Spark/Client",
    package: "both",
    p4Stream: "//streams/rel_0.0.1",
    allowP4Writes: true,
    fakeMatting: false,
  },
  launchResult: "",
  launchBusy: false,
  chatText: "",
  chatBusy: false,
  attachments: [],
  conversationId: "webui-production",
  messages: [
    {
      role: "agent",
      text: "WebUI 已切到动画生产面板。这里不展示全技能仓库，只保留动画自动化、当前任务和 Agent 入口。",
    },
  ],
});

let timer = null;

const currentFlow = computed(() => state.flows.current || state.flows.items[0] || null);
const currentFlowUi = computed(() => flowDisplay(currentFlow.value));
const currentUnity = computed(() => unityImportSummary(currentFlow.value));
const currentStage = computed(() => currentFlow.value?.current_stage || "");
const currentStageIndex = computed(() => {
  const stages = currentFlow.value?.stages || [];
  const index = stages.findIndex((item) => item.key === currentStage.value);
  return index >= 0 ? index : 0;
});
const stageProgress = computed(() => flowStageProgress(currentFlow.value));
const allTasks = computed(() => {
  const flowTasks = (state.flows.items || []).map((item) => normalizeTask("AFLOW", item));
  const comfy = state.comfyRuns.map((item) => normalizeTask("COMFY", item));
  const frame = state.frameRuns.map((item) => normalizeTask("FRAME", item));
  const cherry = state.cherryRuns.map((item) => normalizeTask("CHERRY", item));
  return mergeCurrentTasks([...flowTasks, ...comfy, ...frame, ...cherry]).sort((a, b) => b.timeValue - a.timeValue);
});
const activeTasks = computed(() => allTasks.value.filter((task) => !FINISHED.has(task.status)));
const primaryTask = computed(() => {
  const meaningful = activeTasks.value.filter(isMeaningfulActiveTask);
  return meaningful.find((task) => task.module === "AFLOW")
    || meaningful.find((task) => task.module === "COMFY")
    || meaningful[0]
    || null;
});
const failedTasks = computed(() => allTasks.value.filter((task) => ["FAILED", "BLOCKED", "DONE_WITH_ERRORS"].includes(task.status)));
const workspaceCards = computed(() => buildWorkspaceCards());
const stageCards = computed(() => buildStageCards(currentFlow.value));
const childRuns = computed(() => buildChildRuns(currentFlow.value));
const connectionText = computed(() => state.health?.ok === false ? "后端异常" : state.health ? "后端在线" : "连接中");
const launchCommand = computed(() => {
  const compactDate = state.form.date.replaceAll("-", "");
  const modeText = state.form.mode === "iteration" ? "替换" : "导入";
  const priority = state.form.priority.trim() ? ` 优先 ${state.form.priority.trim()}` : "";
  return `动画自动化${compactDate} ${modeText}${priority}`;
});

onMounted(() => {
  refreshAll();
  timer = window.setInterval(() => {
    if (state.auto) refreshAll(true);
  }, POLL_MS);
});

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer);
});

async function refreshAll(silent = false) {
  if (state.refreshing) return;
  state.refreshing = true;
  if (!silent) state.error = "";
  try {
    const [health, flows, workspaceSummary, animation, comfyStatus, frameStatus, cherryStatus, comfyRuns, frameRuns, cherryRuns, jobs] = await Promise.allSettled([
      jsonFetch("/api/health", { timeoutMs: 7000 }),
      jsonFetch("/api/local/animation-flow-runs?limit=20&include_finished=true", { timeoutMs: 7000 }),
      jsonFetch(`/api/local/workspace-summary?root=${encodeURIComponent(currentWorkspaceRoot())}`, { timeoutMs: 10000 }),
      skillCall("animation.status", { root: workspaceRoot(), include_runs: true }, true),
      skillCall("comfyui.run_status", { include_gpu: false }, true),
      skillCall("frame.run_status", {}, true),
      skillCall("cherry.run_status", {}, true),
      skillCall("comfyui.run_list", { limit: 10, include_finished: true }, true),
      skillCall("frame.run_list", { limit: 8, include_finished: true, include_archived: false }, true),
      skillCall("cherry.run_list", { limit: 8, include_finished: true, include_archived: false }, true),
      jsonFetch("/api/brain/jobs?limit=12", { timeoutMs: 7000 }),
    ]);

    state.health = valueOf(health) || state.health;
    state.flows = valueOf(flows) || state.flows;
    state.workspaceSummary = valueOf(workspaceSummary) || state.workspaceSummary;
    state.animation = unwrapSkill(valueOf(animation));
    state.comfyStatus = unwrapSkill(valueOf(comfyStatus));
    state.frameStatus = unwrapSkill(valueOf(frameStatus));
    state.cherryStatus = unwrapSkill(valueOf(cherryStatus));
    state.comfyRuns = unwrapSkill(valueOf(comfyRuns))?.items || [];
    state.frameRuns = unwrapSkill(valueOf(frameRuns))?.items || [];
    state.cherryRuns = unwrapSkill(valueOf(cherryRuns))?.items || [];
    state.jobs = valueOf(jobs)?.items || [];
    state.updatedAt = timeOnly(new Date());
  } catch (error) {
    state.error = String(error?.message || error);
  } finally {
    state.refreshing = false;
  }
}

async function previewFlow() {
  state.launchBusy = true;
  state.launchResult = "正在生成预览...";
  try {
    const payload = await skillCall("animation_flow.preview", flowArgs(), false);
    state.launchResult = formatPayload(payload);
    toast("预览完成");
  } catch (error) {
    state.launchResult = String(error?.message || error);
    toast("预览失败", "bad");
  } finally {
    state.launchBusy = false;
  }
}

async function startFlow() {
  if (!window.confirm(`确认启动完整动画自动化？\n\n${launchCommand.value}\n\n这会从飞书下载、抽帧、抠图、Cherry 平滑、Unity 导入，并走 P4 shelve-only。`)) return;
  state.launchBusy = true;
  state.launchResult = "正在启动...";
  try {
    const payload = await skillCall("animation_flow.start", flowArgs(), false);
    state.launchResult = formatPayload(payload);
    toast("动画自动化已启动");
    await refreshAll(true);
    state.tab = "overview";
  } catch (error) {
    state.launchResult = String(error?.message || error);
    toast("启动失败", "bad");
  } finally {
    state.launchBusy = false;
  }
}

async function cancelFlow(runId) {
  if (!runId || !window.confirm(`确认终止 ${runId}？`)) return;
  try {
    await skillCall("animation_flow.cancel", { run_id: runId }, false);
    toast("已发送终止请求");
    await refreshAll(true);
  } catch (error) {
    toast(String(error?.message || error), "bad");
  }
}

async function controlComfy(action, runId) {
  if (!runId) return;
  const skill = {
    pause: "comfyui.run_pause",
    resume: "comfyui.run_resume",
    cancel: "comfyui.run_cancel",
  }[action];
  if (!skill) return;
  if (action === "cancel" && !window.confirm(`确认取消 ${runId}？`)) return;
  try {
    await skillCall(skill, { run_id: runId, interrupt_current: true }, false);
    toast("ComfyUI 控制请求已发送");
    await refreshAll(true);
  } catch (error) {
    toast(String(error?.message || error), "bad");
  }
}

async function sendAgent() {
  const text = state.chatText.trim();
  if ((!text && !state.attachments.length) || state.chatBusy) return;
  const attachments = [...state.attachments];
  state.messages.push({ role: "user", text });
  state.chatText = "";
  state.chatBusy = true;
  const pending = {
    role: "agent",
    text: attachments.length ? "正在上传附件并分析..." : "已送入 Agent 后台队列，等待回复...",
    pending: true,
  };
  state.messages.push(pending);
  try {
    const payload = await jsonFetch("/api/brain/test", {
      method: "POST",
      timeoutMs: attachments.length ? 180000 : 30000,
      body: JSON.stringify({
        text: attachments.length ? multimodalPrompt(text, attachments) : text,
        conversation_id: state.conversationId,
        source: "external_webui",
        attachments,
        async_mode: !attachments.length,
      }),
    });
    let finalPayload = payload;
    if (payload.queued && payload.job_id) {
      finalPayload = await waitForJob(payload.job_id, pending);
    }
    pending.text = extractText(finalPayload.response || finalPayload) || "后端已处理，但没有返回可展示文本。";
    pending.pending = false;
    state.attachments = [];
    await refreshAll(true);
  } catch (error) {
    pending.text = String(error?.message || error);
    pending.pending = false;
    toast("Agent 请求失败", "bad");
  } finally {
    state.chatBusy = false;
  }
}

async function waitForJob(jobId, pending) {
  const started = Date.now();
  while (Date.now() - started < 30 * 60 * 1000) {
    const job = await readJob(jobId);
    if (job.status === "DONE") return job;
    if (job.status === "FAILED") throw new Error(job.error || "Agent 后台任务失败");
    if (job.last_progress) pending.text = job.last_progress;
    else if (job.status === "QUEUED") pending.text = `排队中${job.position ? `，前面还有 ${Math.max(0, job.position - 1)} 条` : ""}...`;
    else pending.text = "Agent 正在处理...";
    await sleep(job.status === "QUEUED" ? 1500 : 2200);
  }
  throw new Error("Agent 任务超过 30 分钟仍未结束。");
}

async function readJob(jobId) {
  try {
    return await jsonFetch(`/api/brain/jobs/${encodeURIComponent(jobId)}`, { timeoutMs: 30000 });
  } catch (error) {
    const list = await jsonFetch(`/api/brain/jobs?conversation_id=${encodeURIComponent(state.conversationId)}&limit=30`, { timeoutMs: 12000 });
    const job = (list.items || []).find((item) => item.job_id === jobId || item.id === jobId);
    if (job) return job;
    throw error;
  }
}

async function onFilesSelected(event) {
  const files = Array.from(event.target.files || []);
  try {
    for (const file of files.slice(0, 8 - state.attachments.length)) {
      state.attachments.push(await fileToAttachment(file));
    }
  } catch (error) {
    toast(String(error?.message || error), "bad");
  }
  event.target.value = "";
}

function removeAttachment(index) {
  state.attachments.splice(index, 1);
}

function toggleTheme() {
  state.theme = state.theme === "light" ? "dark" : "light";
  localStorage.setItem("assetclaw.webui.theme", state.theme);
}

function fileToAttachment(file) {
  return new Promise((resolve, reject) => {
    if (file.size > 25 * 1024 * 1024) {
      reject(new Error(`${file.name} 超过 25MB，WebUI 不发送。`));
      return;
    }
    const reader = new FileReader();
    reader.onload = () => resolve({
      name: file.name,
      file_name: file.name,
      type: file.type || "file",
      mime: file.type || "file",
      size: file.size,
      data_url: String(reader.result || ""),
    });
    reader.onerror = () => reject(reader.error || new Error("读取附件失败"));
    reader.readAsDataURL(file);
  });
}

function multimodalPrompt(text, attachments) {
  const hasImage = attachments.some((item) => String(item.type || item.mime || "").startsWith("image/"));
  const prefix = hasImage ? "请分析这个附件图片" : "请查看这个附件";
  return text ? `${prefix}：${text}` : `${prefix}。`;
}

function flowArgs() {
  const args = {
    date_root: workspaceRoot(),
    unity_import_mode: state.form.mode,
    unity_project: state.form.unityProject,
    package: state.form.package || "both",
    p4_stream: state.form.p4Stream || "//streams/rel_0.0.1",
    allow_p4_writes: Boolean(state.form.allowP4Writes),
    fake_matting_from_frames: Boolean(state.form.fakeMatting),
  };
  if (state.form.workflowPath.trim()) args.workflow_path = state.form.workflowPath.trim();
  if (state.form.priority.trim()) args.priority_characters = state.form.priority.split(/[,\s，、]+/).map((x) => x.trim()).filter(Boolean);
  return args;
}

function workspaceRoot() {
  const day = (state.form.date || todayLabel()).trim();
  return `E:/animation_automation/${day}`;
}

function currentWorkspaceRoot() {
  return currentFlow.value?.date_root || workspaceRoot();
}

async function skillCall(skill, args = {}, quiet = false) {
  const payload = await jsonFetch("/api/skills/call", {
    method: "POST",
    timeoutMs: quiet ? 12000 : 60000,
    body: JSON.stringify({ skill, arguments: args, requested_by: "webui" }),
  });
  if (payload.ok === false && !quiet) throw new Error(payload.error || payload.detail || `${skill} failed`);
  return payload;
}

async function jsonFetch(url, options = {}) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), options.timeoutMs || 20000);
  try {
    const response = await fetch(url, {
      method: options.method || "GET",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: options.body,
      signal: controller.signal,
    });
    const text = await response.text();
    const payload = text ? JSON.parse(text) : {};
    if (!response.ok) throw new Error(payload.error || payload.detail || response.statusText);
    return payload;
  } finally {
    window.clearTimeout(timeout);
  }
}

function unwrapSkill(payload) {
  if (!payload) return null;
  return payload.result || payload;
}

function valueOf(result) {
  return result.status === "fulfilled" ? result.value : null;
}

function mergeCurrentTasks(tasks) {
  const merged = [...tasks];
  for (const [module, raw] of [["COMFY", state.comfyStatus], ["FRAME", state.frameStatus], ["CHERRY", state.cherryStatus]]) {
    if (!raw?.run_id) continue;
    const current = normalizeTask(module, raw);
    const index = merged.findIndex((task) => task.module === module && task.id === current.id);
    if (index >= 0) {
      merged[index] = normalizeTask(module, { ...merged[index].raw, ...raw });
    } else {
      merged.push(current);
    }
  }
  return merged;
}

function normalizeTask(module, raw) {
  const id = raw.run_id || raw.id || raw.job_id || "";
  const flowUi = module === "AFLOW" ? flowDisplay(raw) : null;
  const status = flowUi?.status || String(raw.status || "UNKNOWN").toUpperCase();
  const created = raw.created_at || raw.createdAt || raw.updated_at || "";
  const input = raw.date_root || raw.input_dir || raw.download_dir || raw.workspace_root || "";
  const output = raw.output_dir || raw.export_dir || raw.unity_ready || raw.matte_output_dir || raw.smooth_output_dir || "";
  const activeChild = module === "AFLOW" ? flowActiveChild(raw) : null;
  const position = taskPosition(module, raw) || (activeChild ? taskPosition(activeChild.module, activeChild.raw) : "");
  const eta = etaText(raw) || (activeChild ? etaText(activeChild.raw) : "");
  return {
    module,
    id,
    status,
    stage: flowUi?.stage || raw.current_stage || raw.current_step || "",
    input,
    output,
    progress: flowUi?.progress ?? progressFrom(raw),
    count: countText(raw),
    last: flowUi?.short || lastDoneText(raw),
    position,
    eta,
    detail: flowUi?.detail || taskDetail(module, raw, input, output),
    time: formatDateTime(created),
    timeValue: Date.parse(created) || 0,
    raw,
  };
}

function flowActiveChild(run) {
  const stage = run?.current_stage || "";
  if (stage === "frame_extract" && state.frameStatus) return { module: "FRAME", raw: state.frameStatus };
  if (stage === "matting" && state.comfyStatus) return { module: "COMFY", raw: state.comfyStatus };
  if (stage === "cherry_smooth" && state.cherryStatus) return { module: "CHERRY", raw: state.cherryStatus };
  return null;
}

function progressFrom(raw) {
  if (typeof raw.progress_percent === "number") return Math.round(raw.progress_percent);
  const total = Number(raw.total || raw.total_records || 0);
  const done = Number(raw.completed || raw.processed_records || 0);
  if (total > 0) return Math.round((done / total) * 100);
  if (raw.status === "DONE") return 100;
  return 0;
}

function countText(raw) {
  const total = raw.total ?? raw.total_records;
  const done = raw.completed ?? raw.processed_records;
  if (total !== undefined || done !== undefined) return `${Number(done || 0)}/${Number(total || 0)}`;
  return "";
}

function lastDoneText(raw) {
  const detail = raw.last_completed_detail || {};
  const parts = [detail.role, detail.emotion, detail.frame].filter(Boolean);
  if (parts.length) return parts.join(" / ");
  return raw.last_completed || "";
}

function taskPosition(module, raw) {
  const direct = itemDetailText(raw.current_item, "正在处理")
    || itemDetailText(raw.current_detail, "正在处理")
    || itemDetailText(raw.processing_detail, "正在处理");
  if (direct) return direct;

  if (module === "AFLOW") {
    const stage = raw.current_stage || "";
    const childKey = stage === "frame_extract" ? "frame"
      : stage === "matting" ? "comfyui"
        : stage === "cherry_smooth" ? "cherry"
          : "";
    const child = childKey ? raw.children?.[childKey] || raw[childKey] : null;
    const childText = child
      ? itemDetailText(child.current_item, "正在处理")
        || itemDetailText(child.current_detail, "正在处理")
        || itemDetailText(child.processing_detail, "正在处理")
        || itemDetailText(child.last_completed_detail, "刚完成")
        || pathDetailText(child.last_completed, "刚完成")
      : "";
    if (childText) return childText;
  }

  return itemDetailText(raw.last_completed_detail, "刚完成")
    || pathDetailText(raw.last_completed, "刚完成");
}

function itemDetailText(detail, prefix) {
  if (!detail || typeof detail !== "object") return "";
  const role = detail.role || detail.character || detail.character_name || detail.name || "";
  const emotion = detail.emotion || detail.animation || detail.expression || detail.action || "";
  const frame = detail.frame || detail.file || detail.filename || detail.file_name || detail.video || "";
  const parts = [role, emotion, frame].filter(Boolean);
  return parts.length ? `${prefix} ${parts.join(" / ")}` : "";
}

function pathDetailText(value, prefix) {
  if (!value) return "";
  const parts = String(value).split(/[\\/]+/).filter(Boolean);
  if (!parts.length) return "";
  const file = parts.at(-1) || "";
  const emotion = parts.at(-2) || "";
  const role = parts.at(-3) || "";
  const readable = [role, emotion, file].filter(Boolean).join(" / ");
  return readable ? `${prefix} ${readable}` : "";
}

function taskDetail(module, raw, input, output) {
  const bits = [];
  const count = countText(raw);
  if (count) bits.push(`${module === "COMFY" ? "抠图" : "进度"} ${count}`);
  const last = lastDoneText(raw);
  if (last) bits.push(`刚完成 ${last}`);
  if (output) bits.push(`输出 ${output}`);
  else if (input) bits.push(`输入 ${input}`);
  return bits.join(" · ");
}

function etaText(raw) {
  const seconds = etaSeconds(raw);
  return seconds === null ? "" : `预计剩余 ${formatDuration(seconds)}`;
}

function etaSeconds(raw) {
  const direct = firstNumber(
    raw.eta_seconds,
    raw.estimated_remaining_seconds,
    raw.remaining_seconds,
    raw.eta,
    raw.remaining
  );
  if (direct !== null && direct >= 0) return Math.round(direct);
  const total = firstNumber(raw.total, raw.total_records);
  const done = firstNumber(raw.completed, raw.processed_records);
  if (total === null || done === null || total <= 0 || done <= 0 || done >= total) return null;
  const started = Date.parse(raw.started_at || raw.created_at || raw.createdAt || "");
  if (!Number.isFinite(started)) return null;
  const elapsed = Math.max(1, (Date.now() - started) / 1000);
  return Math.max(0, Math.round((elapsed / done) * (total - done)));
}

function firstNumber(...values) {
  for (const value of values) {
    if (value === undefined || value === null || value === "") continue;
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function formatDuration(seconds) {
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))} 秒`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} 小时 ${rest} 分钟` : `${hours} 小时`;
}

function buildStageCards(run) {
  const stages = run?.stages?.length ? run.stages : Object.entries(STAGE_LABELS).map(([key, label]) => ({ key, label, status: "pending" }));
  const unity = unityImportSummary(run);
  const flowUi = flowDisplay(run);
  return stages.map((stage) => ({
    ...stage,
    label: STAGE_LABELS[stage.key] || stage.label || stage.key,
    status: flowUi.status === "DONE" ? "done" : (unity?.recovered && stage.key === "unity_import" ? "done" : stage.status),
  }));
}

function buildChildRuns(run) {
  const children = run?.children || {};
  const result = [];
  if (children.pipeline_run_id) result.push(["Pipeline", children.pipeline_run_id]);
  const comfy = state.comfyStatus?.run_id;
  if (comfy) result.push(["ComfyUI", comfy]);
  const cherryIds = children.cherry_run_ids || [];
  if (cherryIds.length) result.push(["Cherry", cherryIds.join(", ")]);
  else if (children.cherry_run_id) result.push(["Cherry", children.cherry_run_id]);
  const unity = unityImportSummary(run);
  if (unity) result.push(["Unity", unity.compact]);
  const p4 = children.p4;
  if (p4?.changelist_id) result.push(["P4 CL", p4.changelist_id]);
  return result;
}

function buildWorkspaceCards() {
  if (state.workspaceSummary?.items?.length) {
    return state.workspaceSummary.items
      .filter((item) => ["videos", "frames", "matte", "smooth"].includes(item.key))
      .map((item) => ({
        label: item.label,
        value: item.exists ? `${item.count} / ${item.folders}组` : "目录不存在",
        path: item.path,
      }));
  }
  const a = state.animation || {};
  const keys = [
    ["videos", "视频"],
    ["frames", "帧"],
    ["matte", "抠图"],
    ["smooth", "后处理"],
  ];
  return keys.map(([key, label]) => {
    const item = a[key] || a.counts?.[key] || {};
    return {
      label,
      value: item.count ?? item.files ?? item.png_count ?? item.total ?? "-",
      path: item.path || item.dir || "",
    };
  });
}

function statusClass(status) {
  const normalized = String(status || "").toUpperCase();
  if (["DONE", "DONE_WITH_ERRORS", "CONFIRMED"].includes(normalized)) return "ok";
  if (["FAILED", "BLOCKED"].includes(normalized)) return "bad";
  if (["CANCELED"].includes(normalized)) return "muted";
  if (["RUNNING", "QUEUED", "PAUSED", "READY_P4", "LATE_RESULT"].includes(normalized)) return "live";
  return "idle";
}

function isActiveStatus(status) {
  return !FINISHED.has(String(status || "").toUpperCase());
}

function isMeaningfulActiveTask(task) {
  if (!task || FINISHED.has(task.status)) return false;
  if (task.module === "FRAME" && task.count === "0/0" && !task.last && task.progress === 0) return false;
  if (task.module === "AFLOW") return true;
  return Boolean(task.id || task.last || task.input || task.output || task.progress);
}

function formatStage(stage) {
  return STAGE_LABELS[stage] || stage || "-";
}

function flowDisplay(run) {
  if (!run) return { status: "IDLE", stage: "", label: "暂无流程", progress: 0, short: "", detail: "" };
  const rawStatus = String(run.status || "UNKNOWN").toUpperCase();
  const unity = unityImportSummary(run);
  const p4 = flowP4Summary(run);
  if (rawStatus === "DONE") {
    return {
      status: "DONE",
      stage: "p4_shelve",
      label: "完整流程完成",
      progress: 100,
      short: p4?.changelistId ? `CL ${p4.changelistId}` : "",
      detail: [unity?.detail, p4?.detail].filter(Boolean).join(" · "),
    };
  }
  if (unity?.recovered) {
    return {
      status: "READY_P4",
      stage: "p4_shelve",
      label: "Unity 已确认，待继续 P4",
      progress: 83,
      short: "Unity 结果晚到",
      detail: unity.detail,
    };
  }
  if (unity?.diskConfirmed) {
    return {
      status: "CONFIRMED",
      stage: "unity_import",
      label: "Unity 磁盘主动确认",
      progress: 83,
      short: "磁盘确认完成",
      detail: unity.detail,
    };
  }
  return {
    status: rawStatus,
    stage: run.current_stage || "",
    label: formatStage(run.current_stage),
    progress: flowStageProgress(run),
    short: "",
    detail: run.error || "",
  };
}

function flowP4Summary(run) {
  const p4 = run?.children?.p4;
  if (!p4) return null;
  const changelistId = p4.changelist_id || "";
  const detail = [
    changelistId ? `CL ${changelistId}` : "",
    p4.shelved ? "已 shelve" : "",
    Array.isArray(p4.target_paths) && p4.target_paths.length ? `${p4.target_paths.length} 条路径` : "",
  ].filter(Boolean).join(" · ");
  return { changelistId, detail };
}

function flowStageProgress(run) {
  if (String(run?.status || "").toUpperCase() === "DONE") return 100;
  const stages = run?.stages || [];
  if (!stages.length) return 0;
  const current = run?.current_stage || "";
  const currentIndex = Math.max(0, stages.findIndex((item) => item.key === current));
  const done = stages.filter((stage, index) => stage.status === "done" || index < currentIndex).length;
  const running = stages.some((stage) => stage.status === "running") ? 0.5 : 0;
  return Math.min(100, Math.round(((done + running) / stages.length) * 100));
}

function unityImportSummary(run) {
  const unity = run?.children?.unity_import;
  if (!unity) return null;
  const result = unity.result || {};
  const totals = result.totals || {};
  const disk = unity.disk_progress || {};
  const status = unity.latest_status || {};
  const mode = unity.mode === "iteration" || result.mode === "iteration" ? "替换" : "导入";
  const recovered = Boolean(unity.recovered);
  const diskConfirmed = Boolean(unity.disk_confirmed || result.inferredFromDisk);
  const done = Number(totals.replaced || totals.textures || disk.replacedTextures || 0);
  const total = Number(disk.replaceableTextures || totals.textures || 0);
  const skipped = Number(totals.skipped || disk.skippedTextures || 0);
  let label = unity.display_status || unity.error || (unity.ok ? "OK" : "PENDING");
  if (diskConfirmed) label = "磁盘确认";
  else if (recovered) label = "结果晚到";
  const compact = recovered
    ? `结果晚到 · ${mode} ${done}${total ? `/${total}` : ""} · 待继续 P4`
    : diskConfirmed
      ? `磁盘确认 · ${mode} ${done}${total ? `/${total}` : ""}`
      : `${label} · ${mode}${done || total ? ` ${done}/${total || done}` : ""}`;
  const statusTail = [status.phase, status.package, status.character].filter(Boolean).join(" / ");
  const detailParts = [];
  if (done || total || skipped) detailParts.push(`${mode} ${done}${total ? `/${total}` : ""}，跳过 ${skipped}`);
  if (statusTail) detailParts.push(`最后状态 ${statusTail}`);
  if (unity.result_path) detailParts.push(`result ${unity.result_path}`);
  return {
    compact,
    label,
    recovered,
    diskConfirmed,
    done,
    total,
    skipped,
    progress: total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0,
    detail: detailParts.join(" · "),
  };
}

function formatDateTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return `${String(date.getMonth() + 1).padStart(2, "0")}/${String(date.getDate()).padStart(2, "0")} ${timeOnly(date)}`;
}

function timeOnly(date) {
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}:${String(date.getSeconds()).padStart(2, "0")}`;
}

function todayLabel() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function extractText(payload) {
  return payload?.text || payload?.reply || payload?.message || payload?.result?.text || "";
}

function formatPayload(payload) {
  return JSON.stringify(payload, null, 2);
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function toast(message, type = "ok") {
  const id = Math.random().toString(16).slice(2);
  state.toasts.push({ id, message, type });
  window.setTimeout(() => {
    const index = state.toasts.findIndex((item) => item.id === id);
    if (index >= 0) state.toasts.splice(index, 1);
  }, 3200);
}
</script>

<template>
  <div :class="['shell', `theme-${state.theme}`]">
    <aside class="sidebar">
      <div class="brand">
        <div class="mark">AC</div>
        <div class="brand-copy">
          <b class="brand-title">Animation Console</b>
          <span class="brand-sub">{{ connectionText }}</span>
        </div>
      </div>
      <nav class="nav">
        <button v-for="tab in tabs" :key="tab.id" :class="{ active: state.tab === tab.id }" @click="state.tab = tab.id">
          {{ tab.label }}
        </button>
      </nav>
      <section class="side-status">
        <b>{{ activeTasks.length }}</b>
        <span>当前活动任务</span>
      </section>
      <button class="ghost full" @click="state.auto = !state.auto">{{ state.auto ? "Auto 刷新中" : "Auto 已暂停" }}</button>
    </aside>

    <main class="main">
      <header class="topbar">
        <div>
          <p class="eyebrow">SPARK ANIMATION</p>
          <h1>动画生产面板</h1>
        </div>
        <div class="top-actions">
          <span class="timestamp">更新 {{ state.updatedAt || "-" }}</span>
          <button class="ghost" @click="toggleTheme">{{ state.theme === "light" ? "夜间模式" : "纯白模式" }}</button>
          <button class="ghost" :disabled="state.refreshing" @click="refreshAll()">{{ state.refreshing ? "刷新中" : "刷新" }}</button>
        </div>
      </header>

      <section v-if="state.error" class="alert bad">{{ state.error }}</section>

      <section v-if="state.tab === 'overview'" class="view overview">
        <section class="hero-panel">
          <div>
            <p class="eyebrow">CURRENT FLOW</p>
            <h2>{{ currentFlow?.run_id || "暂无完整流程" }}</h2>
            <p>{{ currentFlow?.date_root || workspaceRoot() }}</p>
          </div>
          <div class="hero-state">
            <span :class="['status-pill', statusClass(currentFlowUi.status)]">{{ currentFlowUi.status }}</span>
            <b>{{ currentFlowUi.label || formatStage(currentStage) }}</b>
            <small>{{ currentFlowUi.progress || stageProgress }}%</small>
          </div>
        </section>

        <section v-if="currentUnity" :class="['unity-strip', { recovered: currentUnity.recovered, confirmed: currentUnity.diskConfirmed }]">
          <div>
            <p class="eyebrow">UNITY IMPORT</p>
            <h3>{{ currentUnity.recovered ? "结果晚到，导入已确认" : currentUnity.diskConfirmed ? "磁盘主动确认完成" : currentUnity.label }}</h3>
            <span>{{ currentUnity.detail || "等待 Unity 导入状态。" }}</span>
          </div>
          <div class="unity-metrics">
            <b>{{ currentUnity.done }}{{ currentUnity.total ? `/${currentUnity.total}` : "" }}</b>
            <small>替换/导入</small>
            <b>{{ currentUnity.skipped }}</b>
            <small>跳过</small>
          </div>
        </section>

        <section v-if="primaryTask" class="running-focus">
          <div class="focus-head">
            <div>
              <p class="eyebrow">RUNNING NOW</p>
              <h2>{{ primaryTask.module }} <span>{{ primaryTask.id }}</span></h2>
            </div>
            <span :class="['status-pill', statusClass(primaryTask.status)]">{{ primaryTask.status }}</span>
          </div>
          <div class="focus-progress">
            <div class="progress"><i :style="{ width: `${primaryTask.progress}%` }"></i></div>
            <b>{{ primaryTask.count || `${primaryTask.progress}%` }}</b>
          </div>
          <div class="focus-detail">
            <span>{{ primaryTask.position || (primaryTask.last ? `刚完成 ${primaryTask.last}` : formatStage(primaryTask.stage)) }}</span>
            <small>{{ primaryTask.eta || "预计剩余计算中" }}</small>
            <small class="focus-path">{{ primaryTask.output || primaryTask.input || "暂无路径" }}</small>
          </div>
        </section>

        <section class="stats-grid">
          <article class="stat"><span>活动任务</span><b>{{ activeTasks.length }}</b></article>
          <article class="stat"><span>失败 / 阻塞</span><b>{{ failedTasks.length }}</b></article>
          <article class="stat"><span>ComfyUI</span><b>{{ state.comfyStatus?.completed || 0 }}/{{ state.comfyStatus?.total || 0 }}</b></article>
          <article class="stat"><span>Agent 队列</span><b>{{ state.jobs.filter((j) => ['QUEUED','RUNNING'].includes(j.status)).length }}</b></article>
        </section>

        <section class="panel">
          <div class="panel-title">
            <h3>七步流程</h3>
            <button v-if="currentFlow?.run_id && isActiveStatus(currentFlow.status)" class="danger" @click="cancelFlow(currentFlow.run_id)">终止流程</button>
          </div>
          <div class="stages">
            <article v-for="(stage, index) in stageCards" :key="stage.key" :class="['stage', stage.status, { active: index === currentStageIndex }]">
              <span>{{ index + 1 }}</span>
              <b>{{ stage.label }}</b>
              <small>{{ stage.status }}</small>
            </article>
          </div>
        </section>

        <section class="content-grid">
          <article class="panel">
            <div class="panel-title"><h3>关键子任务</h3></div>
            <div v-if="!childRuns.length" class="empty">暂无子任务信息。</div>
            <div v-for="[name, value] in childRuns" :key="name" class="kv-row"><span>{{ name }}</span><b>{{ value }}</b></div>
          </article>
          <article class="panel">
            <div class="panel-title"><h3>工作区产物</h3></div>
            <div v-for="card in workspaceCards" :key="card.label" class="kv-row workspace-row">
              <span>{{ card.label }}</span>
              <b><strong>{{ card.value }}</strong><small>{{ card.path }}</small></b>
            </div>
          </article>
        </section>

        <section class="panel">
          <div class="panel-title"><h3>活动任务</h3><button class="ghost" @click="state.tab = 'tasks'">查看全部</button></div>
          <div v-if="!activeTasks.length" class="empty">当前没有活动任务。</div>
          <article v-for="task in activeTasks.slice(0, 5)" :key="task.module + task.id" class="task-row">
            <div class="task-main compact">
              <b>{{ task.module }}</b>
              <span>{{ task.id }}</span>
              <small>{{ task.count || task.time }}</small>
            </div>
            <span :class="['status-pill', statusClass(task.status)]">{{ task.status }}</span>
            <div class="task-progress">
              <div class="progress"><i :style="{ width: `${task.progress}%` }"></i></div>
              <small>{{ task.position || task.detail || formatStage(task.stage) }}</small>
              <small v-if="task.eta" class="task-eta">{{ task.eta }}</small>
            </div>
            <button class="ghost" @click="state.detail = task.raw">详情</button>
          </article>
        </section>
      </section>

      <section v-if="state.tab === 'tasks'" class="view">
        <section class="panel">
          <div class="panel-title"><h3>任务列表</h3><span>{{ allTasks.length }} 条</span></div>
          <article v-for="task in allTasks" :key="task.module + task.id" class="task-row large">
            <div class="task-main">
              <b>{{ task.module }}</b>
              <span>{{ task.id }}</span>
              <small>{{ task.count || task.time }}</small>
            </div>
            <span :class="['status-pill', statusClass(task.status)]">{{ task.status }}</span>
            <span class="stage-name">{{ task.last || formatStage(task.stage) }}</span>
            <div class="path-cell">
              <small><em>输入</em><span>{{ task.input || "-" }}</span></small>
              <small><em>输出</em><span>{{ task.output || "-" }}</span></small>
              <small v-if="task.position"><em>位置</em><span>{{ task.position }}</span></small>
              <small v-if="task.eta"><em>ETA</em><span>{{ task.eta }}</span></small>
              <small v-if="task.detail"><em>明细</em><span>{{ task.detail }}</span></small>
            </div>
            <div class="task-actions">
              <button v-if="task.module === 'COMFY' && task.status === 'RUNNING'" class="ghost" @click="controlComfy('pause', task.id)">暂停</button>
              <button v-if="task.module === 'COMFY' && task.status === 'PAUSED'" class="ghost" @click="controlComfy('resume', task.id)">继续</button>
              <button v-if="task.module === 'COMFY' && isActiveStatus(task.status)" class="danger" @click="controlComfy('cancel', task.id)">取消</button>
              <button v-if="task.module === 'AFLOW' && isActiveStatus(task.status)" class="danger" @click="cancelFlow(task.id)">终止</button>
              <button class="ghost" @click="state.detail = task.raw">详情</button>
            </div>
          </article>
        </section>
      </section>

      <section v-if="state.tab === 'launch'" class="view launch-grid">
        <section class="panel">
          <div class="panel-title"><h3>启动完整动画自动化</h3></div>
          <div class="form-grid">
            <label><span>日期</span><input v-model="state.form.date" /></label>
            <label><span>Unity 模式</span><select v-model="state.form.mode"><option value="iteration">替换 / 迭代</option><option value="import">新导入</option></select></label>
            <label><span>优先角色</span><input v-model="state.form.priority" placeholder="留空则不指定" /></label>
            <label><span>Unity Project</span><input v-model="state.form.unityProject" /></label>
            <label class="wide"><span>ComfyUI 工作流</span><input v-model="state.form.workflowPath" placeholder="留空使用后端默认工作流" /></label>
            <label><span>Package</span><select v-model="state.form.package"><option value="both">both</option><option value="scene">scene</option><option value="emoji">emoji</option><option value="story">story</option></select></label>
            <label><span>P4 Stream</span><input v-model="state.form.p4Stream" /></label>
            <label class="check"><input v-model="state.form.allowP4Writes" type="checkbox" /><span>允许 P4 create/reconcile/shelve</span></label>
            <label class="check"><input v-model="state.form.fakeMatting" type="checkbox" /><span>测试模式：抽帧当抠图</span></label>
          </div>
          <div class="command-preview">{{ launchCommand }}</div>
          <div class="button-row">
            <button class="ghost" :disabled="state.launchBusy" @click="previewFlow">预览</button>
            <button class="primary" :disabled="state.launchBusy" @click="startFlow">{{ state.launchBusy ? "处理中" : "启动完整流程" }}</button>
          </div>
        </section>
        <section class="panel">
          <div class="panel-title"><h3>返回结果</h3></div>
          <pre>{{ state.launchResult || "还没有操作。" }}</pre>
        </section>
      </section>

      <section v-if="state.tab === 'agent'" class="view agent-layout">
        <section class="panel chat-panel">
          <div class="panel-title"><h3>自然语言入口</h3><span>与飞书共用后端 Agent</span></div>
          <div class="messages">
            <article v-for="(msg, index) in state.messages" :key="index" :class="['message', msg.role, { pending: msg.pending }]">{{ msg.text }}</article>
          </div>
          <div class="composer">
            <div v-if="state.attachments.length" class="attachment-list">
              <span v-for="(file, index) in state.attachments" :key="file.name + index">
                {{ file.name }}
                <button type="button" @click="removeAttachment(index)">移除</button>
              </span>
            </div>
            <textarea v-model="state.chatText" placeholder="例如：查看动画自动化状态 / 取消 COMFY_xxxx / 动画自动化20260612 替换 优先 casualheather" @keydown.enter.exact.prevent="sendAgent"></textarea>
            <div class="composer-actions">
              <label class="file-button">
                <input type="file" multiple accept="image/*,video/*,audio/*,.txt,.json,.md" @change="onFilesSelected" />
                添加附件
              </label>
              <button class="primary" :disabled="state.chatBusy" @click="sendAgent">{{ state.chatBusy ? "发送中" : "发送" }}</button>
            </div>
          </div>
        </section>
        <section class="panel">
          <div class="panel-title"><h3>Agent 后台队列</h3></div>
          <div v-if="!state.jobs.length" class="empty">暂无后台 job。</div>
          <div v-for="job in state.jobs" :key="job.job_id" class="kv-row"><span>{{ job.status }}</span><b>{{ job.text_preview || job.job_id }}</b></div>
        </section>
      </section>
    </main>

    <div v-if="state.detail" class="detail-backdrop" @click.self="state.detail = null">
      <section class="detail-modal">
        <div class="detail-head"><h3>详情</h3><button class="ghost" @click="state.detail = null">关闭</button></div>
        <pre>{{ JSON.stringify(state.detail, null, 2) }}</pre>
      </section>
    </div>

    <div class="toasts">
      <article v-for="item in state.toasts" :key="item.id" :class="['toast', item.type]">{{ item.message }}</article>
    </div>
  </div>
</template>
