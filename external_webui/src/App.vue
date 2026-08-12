<script setup>
import { computed, onBeforeUnmount, onMounted, reactive } from "vue";
import PerformanceDashboard from "./components/PerformanceDashboard.vue";
import TaskDetailDrawer from "./components/TaskDetailDrawer.vue";
import {
  buildTaskPerformance,
  formatDurationMs,
  summarizePerformance,
} from "./domain/task-performance.js";

const POLL_MS = 12000;
const FINISHED = new Set(["DONE", "FAILED", "CANCELED", "BLOCKED", "DONE_WITH_ERRORS"]);
const STAGE_LABELS = {
  feishu_download: "飞书下载",
  frame_extract: "抽帧",
  matting: "ComfyUI 抠图",
  local_oom_gpu_fallback_queued: "GPU 集群接管中",
  waiting_character: "等待角色确认",
  cherry_smooth: "Cherry 平滑",
  done_matte_only: "抠图结果已交付",
  unity_ready: "unity_ready",
  unity_import: "Unity 导入",
  p4_shelve: "P4 Shelve",
};

const tabs = [
  { id: "overview", label: "总览" },
  { id: "tasks", label: "任务" },
  { id: "analysis", label: "分析" },
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
  currentFlowStatus: null,
  animation: null,
  workspaceSummary: null,
  comfyStatus: null,
  frameStatus: null,
  cherryStatus: null,
  comfyRuns: [],
  frameRuns: [],
  cherryRuns: [],
  directImageRuns: [],
  directVideoRuns: [],
  redeliveryBusy: {},
  redeliveryConfirm: null,
  rerunBusy: {},
  rerunConfirm: null,
  cancelConfirm: null,
  jobs: [],
  taskFilter: "all",
  taskStatusFilter: "all",
  taskQuery: "",
  runtimeConfig: { animation_root: "", unity_project: "" },
  detail: null,
  toasts: [],
  form: {
    date: todayLabel(),
    mode: "iteration",
    priority: "",
    workflowPath: "",
    unityProject: "",
    package: "both",
    fps: 24,
    p4Stream: "//streams/rel_0.0.1",
    allowP4Writes: false,
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

const recentFlow = computed(() => state.flows.current || state.flows.items[0] || null);
const currentFlowBase = computed(() => {
  const candidates = [state.flows.current, ...(state.flows.items || [])].filter(Boolean);
  return candidates.find((item) => isActiveStatus(flowDisplay(item).status)) || null;
});
const currentFlow = computed(() => {
  const base = currentFlowBase.value;
  const detail = state.currentFlowStatus;
  if (!base || !detail || detail.run_id !== (base.run_id || base.id)) return base;
  return {
    ...base,
    ...detail,
    children: { ...(base.children || {}), ...(detail.children || {}) },
  };
});
const currentPipeline = computed(() => currentFlow.value?.pipeline || null);
const currentFrameRun = computed(() => currentPipeline.value?.frame || null);
const currentComfyRun = computed(() => currentPipeline.value?.comfyui || null);
const currentCherryRun = computed(() => currentFlow.value?.cherry || currentPipeline.value?.cherry || null);
const currentFlowUi = computed(() => flowDisplay(currentFlow.value));
const currentUnity = computed(() => unityImportSummary(currentFlow.value));
const currentStage = computed(() => currentFlow.value?.current_stage || "");
const currentStageIndex = computed(() => {
  const stages = currentFlow.value?.stages || [];
  const index = stages.findIndex((item) => item.key === currentStage.value);
  return index >= 0 ? index : 0;
});
const stageProgress = computed(() => flowStageProgress(currentFlow.value));
const childTasks = computed(() => {
  const currentId = currentFlow.value?.run_id || currentFlow.value?.id || "";
  const flowTasks = (state.flows.items || []).map((item) => normalizeTask(
    "AFLOW",
    (item.run_id || item.id) === currentId ? currentFlow.value : item,
  ));
  const comfy = state.comfyRuns.map((item) => normalizeTask("COMFY", item));
  const frame = state.frameRuns.map((item) => normalizeTask("FRAME", item));
  const cherry = state.cherryRuns.map((item) => normalizeTask("CHERRY", item));
  return mergeCurrentTasks([...flowTasks, ...comfy, ...frame, ...cherry]).sort((a, b) => b.timeValue - a.timeValue);
});
const managedParentTasks = computed(() => collapseEquivalentDirectTasks([
  ...[state.flows.current, ...(state.flows.items || [])]
    .filter((item, index, list) => item && list.findIndex((candidate) => (candidate?.run_id || candidate?.id) === (item.run_id || item.id)) === index)
    .map((item) => normalizeTask("AFLOW", (item.run_id || item.id) === (currentFlow.value?.run_id || currentFlow.value?.id) ? currentFlow.value : item)),
  ...state.directImageRuns.map((item) => normalizeDirectTask("DIRECT_IMAGE", item)),
  ...state.directVideoRuns.map((item) => normalizeDirectTask("DIRECT_VIDEO", item)),
]));
const allTasks = computed(() => {
  const claimed = new Set(managedParentTasks.value.flatMap((task) => [
    ...parentChildIds(task.raw),
    ...(task.supersededAttempts || []).flatMap((attempt) => attempt.childIds || []),
  ]));
  const parentIds = new Set(managedParentTasks.value.map((task) => task.id));
  const standalone = childTasks.value.filter((task) => {
    if (task.module === "AFLOW" || claimed.has(task.id)) return false;
    const inferredParent = parentIdFromTask(task);
    return !inferredParent || !parentIds.has(inferredParent);
  });
  return [...managedParentTasks.value, ...standalone].sort((a, b) => b.timeValue - a.timeValue);
});
const performanceRecords = computed(() => {
  // updatedAt keeps active elapsed time moving with the existing refresh cycle.
  void state.updatedAt;
  return managedParentTasks.value
    .map((task) => buildTaskPerformance(task))
    .sort((a, b) => (b.startMs || 0) - (a.startMs || 0));
});
const performanceSummary = computed(() => summarizePerformance(performanceRecords.value));
const performanceByKey = computed(() => new Map(performanceRecords.value.map((item) => [item.key, item])));
const selectedTask = computed(() => {
  const selected = state.detail;
  if (!selected) return null;
  return allTasks.value.find((task) => taskKey(task) === selected.key) || selected.snapshot || null;
});
const selectedPerformance = computed(() => selectedTask.value ? taskPerformance(selectedTask.value) : null);
const activeTasks = computed(() => allTasks.value.filter((task) => !FINISHED.has(task.status)));
const taskCategories = computed(() => buildTaskCategories(activeTasks.value));
const taskSummary = computed(() => ({
  total: allTasks.value.length,
  active: allTasks.value.filter((task) => isActiveStatus(task.status)).length,
  done: allTasks.value.filter((task) => task.status === "DONE").length,
  attention: allTasks.value.filter((task) => ["FAILED", "BLOCKED", "DONE_WITH_ERRORS"].includes(task.status)).length,
}));
const filteredTasks = computed(() => {
  const query = state.taskQuery.trim().toLocaleLowerCase();
  return allTasks.value.filter((task) => {
    if (state.taskFilter !== "all" && task.category !== state.taskFilter) return false;
    if (state.taskStatusFilter === "active" && !isActiveStatus(task.status)) return false;
    if (state.taskStatusFilter === "done" && task.status !== "DONE") return false;
    if (state.taskStatusFilter === "attention" && !["FAILED", "BLOCKED", "DONE_WITH_ERRORS"].includes(task.status)) return false;
    if (!query) return true;
    return [task.name, task.id, task.label, task.stageLabel, task.user?.displayName]
      .some((value) => String(value || "").toLocaleLowerCase().includes(query));
  });
});
const filteredTaskRows = computed(() => filteredTasks.value.map((task) => ({ ...task, performance: taskPerformance(task) })));
const unifiedQueue = computed(() => [...activeTasks.value]
  .filter(isMeaningfulActiveTask)
  .sort((a, b) => {
    const stateDelta = queueStateRank(a) - queueStateRank(b);
    if (stateDelta) return stateDelta;
    const positionDelta = queuePosition(a) - queuePosition(b);
    if (positionDelta) return positionDelta;
    return a.timeValue - b.timeValue;
  })
  .map((task, index) => ({ ...task, queueIndex: index + 1, queueLabel: queueStateLabel(task) })));
const queueSummary = computed(() => {
  const tasks = unifiedQueue.value;
  const running = tasks.filter((task) => queueStateRank(task) === 0).length;
  const waiting = Math.max(0, tasks.length - running);
  const progress = tasks.length ? Math.round(tasks.reduce((sum, task) => sum + Number(task.progress || 0), 0) / tasks.length) : 0;
  return { total: tasks.length, running, waiting, progress };
});
const primaryTask = computed(() => {
  return unifiedQueue.value[0] || null;
});
const failedTasks = computed(() => allTasks.value.filter((task) => ["FAILED", "BLOCKED", "DONE_WITH_ERRORS"].includes(task.status)));
const workspaceCards = computed(() => buildWorkspaceCards());
const stageCards = computed(() => buildStageCards(currentFlow.value));
const childRuns = computed(() => buildChildRuns(currentFlow.value));
const processProfile = computed(() => buildProcessProfile(currentFlow.value));
const overviewMetrics = computed(() => buildOverviewMetrics());
const activeStageRun = computed(() => flowActiveChild(currentFlow.value)?.raw || null);
const activeStageProgress = computed(() => progressFrom(activeStageRun.value || {}));
const primaryIsFlow = computed(() => primaryTask.value?.module === "AFLOW");
const focusProgress = computed(() => primaryIsFlow.value ? currentFlowUi.value.progress : (primaryTask.value?.progress || 0));
const focusLabel = computed(() => primaryIsFlow.value ? "总体流程" : (primaryTask.value?.stageLabel || `${primaryTask.value?.module || "任务"} 进度`));
const fpsValid = computed(() => Number.isInteger(Number(state.form.fps)) && Number(state.form.fps) > 0);
const connectionText = computed(() => state.health?.ok === false ? "后端异常" : state.health ? "后端在线" : "连接中");
const launchCommand = computed(() => {
  const compactDate = state.form.date.replaceAll("-", "");
  const modeText = state.form.mode === "iteration" ? "替换" : "导入";
  const priority = state.form.priority.trim() ? ` 优先 ${state.form.priority.trim()}` : "";
  const p4 = state.form.allowP4Writes ? " P4 Shelve" : " P4 跳过";
  return `动画自动化${compactDate} · ${state.form.fps} FPS · ${modeText}${priority}${p4}`;
});

onMounted(async () => {
  await loadRuntimeConfig();
  refreshAll();
  timer = window.setInterval(() => {
    if (state.auto) refreshAll(true);
  }, POLL_MS);
});

async function loadRuntimeConfig() {
  try {
    const payload = await jsonFetch("/api/local/runtime-config", { timeoutMs: 7000 });
    state.runtimeConfig = payload || state.runtimeConfig;
    if (!state.form.unityProject) state.form.unityProject = payload?.unity_project || "";
  } catch {
    // The normal refresh error state handles a missing local API.
  }
}

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer);
});

async function refreshAll(silent = false) {
  if (state.refreshing) return;
  state.refreshing = true;
  if (!silent) state.error = "";
  try {
    const [health, flows, workspaceSummary, animation, comfyStatus, frameStatus, cherryStatus, comfyRuns, frameRuns, cherryRuns, directImageRuns, directVideoRuns, jobs] = await Promise.allSettled([
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
      skillCall("direct_image.list", { limit: 20, include_finished: true }, true),
      skillCall("direct_video.list", { limit: 20, include_finished: true }, true),
      jsonFetch("/api/brain/jobs?limit=12", { timeoutMs: 7000 }),
    ]);

    state.health = valueOf(health) || state.health;
    state.flows = valueOf(flows) || state.flows;
    state.workspaceSummary = valueOf(workspaceSummary) || state.workspaceSummary;
    state.animation = unwrapSkill(valueOf(animation));
    state.comfyStatus = unwrapSkill(valueOf(comfyStatus));
    state.frameStatus = unwrapSkill(valueOf(frameStatus));
    state.cherryStatus = unwrapSkill(valueOf(cherryStatus));
    state.comfyRuns = lastKnownItems(comfyRuns, state.comfyRuns);
    state.frameRuns = lastKnownItems(frameRuns, state.frameRuns);
    state.cherryRuns = lastKnownItems(cherryRuns, state.cherryRuns);
    state.directImageRuns = lastKnownItems(directImageRuns, state.directImageRuns);
    state.directVideoRuns = lastKnownItems(directVideoRuns, state.directVideoRuns);
    state.jobs = lastKnownItems(jobs, state.jobs, false);
    const activeFlow = [state.flows?.current, ...(state.flows?.items || [])].filter(Boolean).find((item) => isActiveStatus(flowDisplay(item).status));
    const flowId = activeFlow?.run_id || activeFlow?.id || "";
    if (flowId) {
      try {
        const detail = unwrapSkill(await skillCall("animation_flow.status", { run_id: flowId }, true));
        if (detail?.run_id === flowId) state.currentFlowStatus = detail;
      } catch {
        // Keep the last known flow detail when this optional enrichment call fails.
      }
    } else {
      state.currentFlowStatus = null;
    }
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
  const p4Text = state.form.allowP4Writes ? "最后执行 P4 shelve-only。" : "P4 阶段将跳过。";
  if (!window.confirm(`确认启动完整动画自动化？\n\n${launchCommand.value}\n\n将从飞书下载、按 ${state.form.fps} FPS 抽帧、抠图、Cherry 后处理并导入 Unity。${p4Text}`)) return;
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
  if (!fpsValid.value) throw new Error("抽帧速率必须是大于 0 的整数 FPS。");
  const args = {
    date_root: workspaceRoot(),
    unity_import_mode: state.form.mode,
    unity_project: state.form.unityProject,
    package: state.form.package || "both",
    fps: Number(state.form.fps),
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
  const root = String(state.runtimeConfig.animation_root || "animation_auto").replace(/[\\/]+$/, "");
  return `${root}/${day}`;
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

function lastKnownItems(result, previous, skillPayload = true) {
  const payload = valueOf(result);
  const resolved = skillPayload ? unwrapSkill(payload) : payload;
  return Array.isArray(resolved?.items) ? resolved.items : previous;
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
  const activeInput = activeChild?.raw?.input_dir || activeChild?.raw?.download_dir || activeChild?.raw?.workspace_root || "";
  const activeOutput = activeChild?.raw?.output_dir || activeChild?.raw?.export_dir || activeChild?.raw?.matte_output_dir || activeChild?.raw?.smooth_output_dir || "";
  const position = taskPosition(module, raw) || (activeChild ? taskPosition(activeChild.module, activeChild.raw) : "");
  const eta = etaText(raw) || (activeChild ? etaText(activeChild.raw) : "");
  const activeCount = activeChild ? countText(activeChild.raw) : "";
  return {
    module,
    category: module === "AFLOW" ? "feishu" : "standalone",
    label: module === "AFLOW" ? "飞书动画" : module,
    id,
    status,
    stage: flowUi?.stage || raw.current_stage || raw.current_step || "",
    input: activeInput || input,
    output: activeOutput || output,
    progress: flowUi?.progress ?? progressFrom(raw),
    count: flowUi?.count || activeCount || countText(raw),
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
  if (stage === "frame_extract") {
    const raw = run?.pipeline?.frame || null;
    if (raw) return { module: "FRAME", raw };
  }
  if (stage === "matting") {
    const raw = run?.pipeline?.comfyui || null;
    if (raw) return { module: "COMFY", raw };
  }
  if (stage === "cherry_smooth") {
    const raw = run?.cherry || run?.pipeline?.cherry || null;
    if (raw) return { module: "CHERRY", raw };
  }
  return null;
}

function taskKey(task) {
  return `${task?.module || "TASK"}:${task?.id || task?.raw?.id || task?.raw?.run_id || ""}`;
}

function taskPerformance(task) {
  return performanceByKey.value.get(taskKey(task)) || buildTaskPerformance(task);
}

function openTaskDetail(task) {
  if (!task) return;
  state.detail = { key: taskKey(task), snapshot: task };
}

function requestCancelTask(task) {
  if (!task?.id || !isActiveStatus(task.status)) return;
  state.cancelConfirm = task;
}

function closeCancelConfirm() {
  state.cancelConfirm = null;
}

async function confirmCancelTask() {
  const task = state.cancelConfirm;
  if (!task?.id) return;
  closeCancelConfirm();
  const skills = {
    AFLOW: "animation_flow.cancel",
    DIRECT_IMAGE: "direct_image.cancel",
    DIRECT_VIDEO: "direct_video.cancel",
    COMFY: "comfyui.run_cancel",
    FRAME: "frame.run_cancel",
    CHERRY: "cherry.run_cancel",
  };
  const skill = skills[task.module];
  if (!skill) return;
  try {
    const args = { run_id: task.id };
    if (task.module === "COMFY") args.interrupt_current = true;
    await skillCall(skill, args, false);
    toast(`${task.label || task.module} 已发送取消请求`);
    await refreshAll(true);
  } catch (error) {
    toast(String(error?.message || error), "bad");
  }
}

function isDirectTask(task) {
  return task?.module === "DIRECT_IMAGE" || task?.module === "DIRECT_VIDEO";
}

function redeliveryActive(task) {
  return ["QUEUED", "PACKAGING", "UPLOADING"].includes(String(task?.redelivery?.status || "").toUpperCase());
}

function redeliveryDisabled(task) {
  return isActiveStatus(task?.status) || redeliveryActive(task) || Boolean(state.redeliveryBusy[taskKey(task)]);
}

function redeliveryLabel(task) {
  const status = String(task?.redelivery?.status || "").toUpperCase();
  if (status === "QUEUED") return "等待重发";
  if (status === "PACKAGING") return "正在打包";
  if (status === "UPLOADING") return "正在上传";
  if (status === "FAILED") return "重试重发";
  if (status === "DONE") return "再次重发";
  return "完整重发";
}

function userInitial(task) {
  return Array.from(String(task?.user?.displayName || "飞书").trim())[0] || "飞";
}

async function requestFullRedelivery(task) {
  const key = taskKey(task);
  if (!isDirectTask(task) || !task.id || redeliveryDisabled(task)) return;
  state.redeliveryConfirm = task;
}

function closeRedeliveryConfirm() {
  state.redeliveryConfirm = null;
}

async function confirmFullRedelivery() {
  const task = state.redeliveryConfirm;
  if (!task) return;
  const key = taskKey(task);
  const userName = task.user?.displayName || "飞书用户";
  closeRedeliveryConfirm();
  state.redeliveryBusy[key] = true;
  try {
    const skill = task.module === "DIRECT_VIDEO" ? "direct_video.full_resend" : "direct_image.full_resend";
    const payload = unwrapSkill(await skillCall(skill, { run_id: task.id }, false));
    toast(payload?.already_running ? "该任务已在重发队列中" : `已开始为 ${userName} 重新打包并发送`);
    await refreshAll(true);
  } catch (error) {
    toast(String(error?.message || error), "bad");
  } finally {
    delete state.redeliveryBusy[key];
  }
}

function redeliveryContents(task) {
  return task?.module === "DIRECT_VIDEO"
    ? ["原视频", "原始抽帧", "透明抠图", "后处理结果"]
    : ["原始图片 / 序列帧", "透明抠图", "后处理结果"];
}

function rerunActive(task) {
  return ["PREPARING", "QUEUED", "RUNNING", "PENDING", "WAITING_CHARACTER", "PAUSED"]
    .includes(String(task?.rerun?.status || "").toUpperCase());
}

function rerunDisabled(task) {
  return isActiveStatus(task?.status) || rerunActive(task) || Boolean(state.rerunBusy[taskKey(task)]);
}

function rerunLabel(task) {
  const status = String(task?.rerun?.status || "").toUpperCase();
  if (["PREPARING", "QUEUED"].includes(status)) return "等待重跑";
  if (["RUNNING", "PENDING", "WAITING_CHARACTER", "PAUSED"].includes(status)) return "重跑处理中";
  if (["FAILED", "DONE_WITH_ERRORS"].includes(status)) return "重试重跑";
  if (status === "DONE") return "再次重跑";
  return "完整重跑";
}

function requestFullRerun(task) {
  if (!isDirectTask(task) || !task.id || rerunDisabled(task)) return;
  state.rerunConfirm = task;
}

function closeRerunConfirm() {
  state.rerunConfirm = null;
}

async function confirmFullRerun() {
  const task = state.rerunConfirm;
  if (!task) return;
  const key = taskKey(task);
  closeRerunConfirm();
  state.rerunBusy[key] = true;
  try {
    const skill = task.module === "DIRECT_VIDEO" ? "direct_video.full_rerun" : "direct_image.full_rerun";
    const payload = unwrapSkill(await skillCall(skill, { run_id: task.id, confirmed: true }, false));
    if (payload?.already_running) {
      toast("该任务已经在原地重跑处理中");
    } else {
      toast(`任务 ${payload?.run_id || task.id} 已从头重新运行`);
    }
    await refreshAll(true);
  } catch (error) {
    toast(String(error?.message || error), "bad");
  } finally {
    delete state.rerunBusy[key];
  }
}

function rerunSteps(task) {
  return task?.module === "DIRECT_VIDEO"
    ? ["读取原视频", "重新抽帧", "重新抠图", "重新后处理", "打包并发送"]
    : ["读取原图 / 序列帧", "重新抠图", "重新后处理", "打包并发送"];
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
  // Creation time includes overnight pauses, restarts and queue wait.  When
  // the backend has no measured ETA, show "计算中" instead of inventing one.
  return null;
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
  return stages.map((stage) => {
    const stageRun = stageRunFor(stage.key, run);
    const terminalStageStatus = ["CANCELED", "FAILED", "BLOCKED"].includes(flowUi.status) && stage.key === run?.current_stage
      ? flowUi.status.toLowerCase()
      : "";
    return {
      ...stage,
      label: STAGE_LABELS[stage.key] || stage.label || stage.key,
      status: terminalStageStatus || (run?.allow_p4_writes === false && stage.key === "p4_shelve"
        ? "skipped"
        : (flowUi.status === "DONE" ? "done" : (unity?.recovered && stage.key === "unity_import" ? "done" : stage.status))),
      detail: stageDetailText(stage.key, run, stageRun),
      progress: stageRun ? progressFrom(stageRun) : (stage.status === "done" ? 100 : 0),
    };
  });
}

function buildChildRuns(run) {
  const children = run?.children || {};
  const result = [];
  if (children.pipeline_run_id) result.push(["Pipeline", children.pipeline_run_id]);
  const frame = run?.pipeline?.frame?.run_id || run?.pipeline?.frame_run_id;
  if (frame) result.push(["抽帧", frame]);
  const comfy = run?.pipeline?.comfyui?.run_id || run?.pipeline?.comfyui_run_id || currentComfyRun.value?.run_id;
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

function stageRunFor(key, run) {
  if (key === "frame_extract") return run?.pipeline?.frame || null;
  if (key === "matting") return run?.pipeline?.comfyui || null;
  if (key === "cherry_smooth") return run?.cherry || run?.pipeline?.cherry || null;
  return null;
}

function stageDetailText(key, run, stageRun) {
  if (key === "feishu_download") return "Timo · 仅处理待抽帧";
  if (key === "frame_extract") {
    const frames = workspaceItem("frames");
    return `${run?.fps || 24} FPS${frames?.count ? ` · ${frames.count} 帧 / ${frames.folders} 组` : ""}`;
  }
  if (key === "matting") {
    const count = stageRun ? countText(stageRun) : "";
    return [run?.workflow_name || fileName(run?.workflow_path), count && `抠图 ${count}`].filter(Boolean).join(" · ") || "等待抠图";
  }
  if (key === "cherry_smooth") {
    const count = stageRun ? countText(stageRun) : "";
    return ["Cherry 默认后处理", count].filter(Boolean).join(" · ");
  }
  if (key === "unity_ready") {
    const item = workspaceItem("unity_ready");
    return item?.count ? `${item.count} 个产物` : "等待整理";
  }
  if (key === "unity_import") return `${run?.unity_import_mode === "iteration" ? "替换 / 迭代" : "新导入"} · ${run?.package || "both"}`;
  if (key === "p4_shelve") return run?.allow_p4_writes === false ? "本次跳过" : (run?.p4?.stream || "Shelve-only");
  return "";
}

function workspaceItem(key) {
  return state.workspaceSummary?.items?.find((item) => item.key === key) || null;
}

function fileName(value) {
  const parts = String(value || "").split(/[\\/]+/).filter(Boolean);
  return parts.at(-1) || "";
}

function buildProcessProfile(run) {
  const active = run || {};
  const p4Enabled = run ? active.allow_p4_writes !== false : state.form.allowP4Writes;
  return [
    { label: "飞书来源", value: "Timo", detail: "指定视图 · 仅待抽帧" },
    { label: "抽帧策略", value: `${active.fps || state.form.fps || 24} FPS`, detail: "保留原始顺序 · 不去重" },
    { label: "抠图工作流", value: active.workflow_name || fileName(active.workflow_path) || "后端默认", detail: active.workflow_path || "启动时由后端解析" },
    { label: "后处理", value: "Cherry 默认流程", detail: "透明边缘修整 · 时序平滑关闭" },
    { label: "Unity", value: active.unity_import_mode === "iteration" ? "替换 / 迭代" : "新导入", detail: active.unity_project || state.form.unityProject || "未指定" },
    { label: "P4", value: p4Enabled ? "Shelve-only" : "已跳过", detail: p4Enabled ? (active.p4?.stream || state.form.p4Stream || "未指定") : "本次不创建 changelist" },
  ];
}

function buildOverviewMetrics() {
  const specs = [
    ["videos", "视频"],
    ["frames", "抽帧"],
    ["matte", "抠图"],
    ["smooth", "后处理"],
  ];
  return specs.map(([key, label]) => {
    const item = workspaceItem(key);
    return {
      key,
      label,
      value: item?.exists ? String(item.count) : "0",
      detail: item?.folders ? `${item.folders} 组` : "等待产出",
    };
  });
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
  if (["RUNNING", "QUEUED", "PAUSED", "WAITING_CHARACTER", "READY_P4", "LATE_RESULT"].includes(normalized)) return "live";
  return "idle";
}

function statusLabel(status) {
  const normalized = String(status || "").toUpperCase();
  return {
    WAITING_CHARACTER: "等待角色",
    DONE_WITH_ERRORS: "完成但有错误",
    CANCELED: "已取消",
    RUNNING: "处理中",
    QUEUED: "排队中",
    PAUSED: "已暂停",
    FAILED: "失败",
    DONE: "完成",
  }[normalized] || normalized || "未知";
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
  const child = flowActiveChild(run);
  const childCount = child ? countText(child.raw) : "";
  const childProgress = child ? progressFrom(child.raw) : 0;
  return {
    status: rawStatus,
    stage: run.current_stage || "",
    label: formatStage(run.current_stage),
    progress: flowStageProgress(run),
    stageProgress: childProgress,
    count: childCount,
    short: childCount ? `${formatStage(run.current_stage)} ${childCount}` : "",
    detail: run.error || "",
  };
}

function normalizeDirectTask(module, raw) {
  const isImage = module === "DIRECT_IMAGE";
  const items = isImage ? (raw.images || []) : (raw.videos || []);
  const sourcePaths = items.map((item) => item.source_path || item.original_path || item.name).filter(Boolean);
  const sourceNames = items.map((item) => item.source_name || item.name).filter(Boolean);
  const displayName = raw.run_label || sourceNames.join("、") || raw.run_id || raw.id || "未命名任务";
  const status = String(raw.status || "UNKNOWN").toUpperCase();
  const stage = String(raw.stage || raw.current_stage || "");
  const stageInfo = directStageProgress(raw, isImage);
  const created = raw.created_at || raw.updated_at || "";
  return {
    module,
    category: isImage ? "image" : "video",
    label: isImage ? "图片直发" : "视频直发",
    name: displayName,
    id: raw.run_id || raw.id || "",
    status,
    stage,
    stageLabel: stageInfo.label,
    input: sourcePaths.length > 1 ? `${sourcePaths[0]} · 另 ${sourcePaths.length - 1} 项` : (sourcePaths[0] || ""),
    output: raw.sequence_zip_path || raw.zip_path || raw.run_dir || "",
    progress: stageInfo.overall,
    stageProgress: stageInfo.progress,
    count: stageInfo.count,
    last: raw.last_log || "",
    position: stageInfo.position || raw.last_log || "",
    eta: etaText(stageInfo.raw || {}),
    detail: `${items.length} ${isImage ? "张图片" : "个视频"} · ${stageInfo.label} ${stageInfo.count}`,
    time: formatDateTime(created),
    timeValue: Date.parse(created) || 0,
    user: {
      displayName: String(raw.feishu_user?.display_name || "飞书用户"),
      avatarUrl: String(raw.feishu_user?.avatar_url || ""),
    },
    redelivery: raw.redelivery || {},
    rerun: raw.rerun || {},
    raw,
  };
}

function directContentSignature(task) {
  if (!isDirectTask(task)) return "";
  const raw = task.raw || {};
  const items = task.module === "DIRECT_IMAGE" ? (raw.images || []) : (raw.videos || []);
  if (!items.length) return "";
  const owner = String(raw.user_id || raw.chat_id || task.user?.displayName || "").trim().toLocaleLowerCase();
  const sources = [...items]
    .sort((a, b) => Number(a.index || 0) - Number(b.index || 0))
    .map((item) => `${String(item.source_name || item.name || "").trim().toLocaleLowerCase()}:${Number(item.source_size_bytes || 0)}`)
    .join("|");
  return `${task.module}:${owner}:${sources}`;
}

function collapseEquivalentDirectTasks(tasks) {
  const result = [];
  const bySignature = new Map();
  for (const task of tasks) {
    const signature = directContentSignature(task);
    const existingIndex = signature ? bySignature.get(signature) : undefined;
    if (existingIndex === undefined) {
      if (signature) bySignature.set(signature, result.length);
      result.push(task);
      continue;
    }
    const existing = result[existingIndex];
    const canCollapse = [existing, task].some((item) => ["FAILED", "CANCELED", "DONE_WITH_ERRORS"].includes(item.status));
    if (!canCollapse) {
      result.push(task);
      continue;
    }
    const winner = task.timeValue > existing.timeValue ? task : existing;
    const superseded = winner === task ? existing : task;
    const original = task.timeValue < existing.timeValue ? task : existing;
    result[existingIndex] = {
      ...winner,
      name: original.name || winner.name,
      supersededAttempts: [
        ...(winner.supersededAttempts || []),
        ...(superseded.supersededAttempts || []),
        {
          id: superseded.id,
          status: superseded.status,
          stage: superseded.stage,
          error: superseded.raw?.error || "",
          updatedAt: superseded.raw?.updated_at || "",
          childIds: parentChildIds(superseded.raw),
        },
      ],
    };
  }
  return result;
}

function directStageProgress(run, isImage) {
  const stage = String(run.stage || "").toLowerCase();
  const children = run.children || {};
  const comfy = children.comfyui && typeof children.comfyui === "object" ? children.comfyui : {};
  const cherryRunMap = children.cherry_runs && typeof children.cherry_runs === "object" ? children.cherry_runs : {};
  const declaredCherryIds = [...new Set([
    ...(Array.isArray(children.cherry_run_ids) ? children.cherry_run_ids : []),
    children.cherry_run_id,
  ].filter(Boolean))];
  // cherry_runs is an audit history and can contain an older failed attempt.
  // Progress must only aggregate the current generation declared by the parent.
  const cherryRuns = declaredCherryIds.length
    ? declaredCherryIds.map((id) => cherryRunMap[id]).filter(Boolean)
    : Object.values(cherryRunMap);
  const expected = isImage
    ? (run.images || []).length
    : (run.videos || []).reduce((sum, item) => sum + Number(item.frame_count || 0), 0);
  const comfyTotal = Number(comfy.total || expected || 0);
  const comfyDone = Math.min(comfyTotal, Number(comfy.completed || 0));
  const repairAudits = Array.isArray(run.partial_matting_repairs) ? run.partial_matting_repairs : [];
  const repairingFailedFrames = repairAudits.length > 0 && isActiveStatus(run.status);
  const preservedBeforeCurrentRepair = repairAudits.reduce(
    (sum, item) => sum + Number(item?.completed_preserved || 0),
    0,
  );
  const cherryRunTotal = cherryRuns.reduce((sum, item) => sum + Number(item?.total || 0), 0);
  const cherryRunDone = cherryRuns.reduce((sum, item) => sum + Number(item?.completed || (String(item?.status || "").toUpperCase() === "DONE" ? item?.total || 1 : 0)), 0);
  const cherryTotal = cherryRunTotal || expected || cherryRuns.length || Number(children.cherry?.total || 0);
  const cherryDone = cherryRuns.length
    ? Math.min(cherryTotal, cherryRunDone)
    : Math.min(cherryTotal, Number(children.cherry?.completed || 0));
  const cherryCurrent = cherryRuns.find((item) => isActiveStatus(item?.status)) || children.cherry || {};
  const runStatus = String(run.status || "").toUpperCase();
  const doneStatus = String(run.status || "").toUpperCase() === "DONE";
  const matteOnly = String(run.result_mode || "").toLowerCase() === "matte_only"
    || stage === "done_matte_only"
    || Boolean(run.postprocess_skipped);
  if (doneStatus && matteOnly) {
    return {
      label: "抠图结果已交付",
      progress: 100,
      overall: 100,
      count: `${expected}/${expected}`,
      position: "未执行角色校色与位置矫正",
      raw: run,
    };
  }
  if (doneStatus) {
    return {
      label: "完整流程已交付",
      progress: 100,
      overall: 100,
      count: `${expected}/${expected}`,
      position: "已完成抠图、角色校色与位置矫正",
      raw: run,
    };
  }
  if (runStatus === "WAITING_CHARACTER" || stage === "waiting_character") {
    const pending = Number(run.character_resolution?.pending || 0);
    const mattingProgress = comfyTotal > 0 ? Math.round((comfyDone / comfyTotal) * 100) : 100;
    const overall = isImage ? Math.round(mattingProgress * 0.5) : 20 + Math.round(mattingProgress * 0.4);
    return {
      label: "等待角色确认",
      progress: 0,
      overall: Math.min(60, overall),
      count: pending > 0 ? `${pending} 项待确认` : "等待回复",
      position: "抠图已完成，确认角色后继续后处理",
      raw: run,
    };
  }
  if (stage.includes("send") || stage.includes("pack") || stage.includes("zip") || stage.includes("delivery")) {
    return { label: "打包与发送", progress: 50, overall: 98, count: "处理中", raw: run };
  }
  if (stage.includes("cherry") || stage.includes("smooth") || cherryRuns.length) {
    const progress = cherryTotal > 0 ? Math.round((cherryDone / cherryTotal) * 100) : progressFrom(cherryCurrent);
    const overall = isImage ? 50 + Math.round(progress * 0.46) : 60 + Math.round(progress * 0.36);
    return { label: "Cherry 后处理", progress, overall: Math.min(96, overall), count: `${cherryDone}/${cherryTotal || "-"}`, position: taskPosition("CHERRY", cherryCurrent), raw: cherryCurrent };
  }
  if (stage === "local_oom_gpu_fallback_queued" && !children.comfyui_run_id) {
    return {
      label: "GPU 集群接管中",
      progress: 0,
      overall: 0,
      count: "等待集群接单",
      position: "本机显存不足，已自动切换 GPU 集群",
      raw: run,
    };
  }
  if (stage.includes("mat") || stage.includes("comfy") || comfyTotal) {
    const effectiveDone = repairingFailedFrames
      ? Math.min(expected, preservedBeforeCurrentRepair + comfyDone)
      : comfyDone;
    const effectiveTotal = repairingFailedFrames ? expected : comfyTotal;
    const progress = effectiveTotal > 0 ? Math.round((effectiveDone / effectiveTotal) * 100) : progressFrom(comfy);
    const overall = isImage ? Math.round(progress * 0.5) : 20 + Math.round(progress * 0.4);
    const remote = String(comfy.backend || run.matting_backend || "").toLowerCase() === "gpu_control";
    const nodes = Object.entries(comfy.node_distribution || {})
      .filter(([, count]) => Number(count) > 0)
      .map(([name, count]) => `${name}:${count}`)
      .join(" · ");
    const remotePosition = [comfy.remote_status, comfy.remote_batch_id, nodes].filter(Boolean).join(" · ");
    const latestRepair = repairAudits[repairAudits.length - 1] || {};
    const failedDetails = (latestRepair.error_items || []).slice(0, 3).map((item) => {
      const ordinal = item?.ordinal === null || item?.ordinal === undefined ? item?.frame : `ordinal ${item.ordinal}`;
      const reason = item?.error_kind || item?.error || "失败";
      const attempts = Number(item?.attempts || 0) > 0 ? `，已跨节点尝试 ${item.attempts} 次` : "";
      return `${ordinal}: ${reason}${attempts}`;
    }).join("；");
    if (repairingFailedFrames) {
      return {
        label: "GPU 失败帧补算",
        progress,
        overall: Math.min(60, overall),
        count: `${effectiveDone}/${expected || "-"}`,
        position: failedDetails || `已保留 ${preservedBeforeCurrentRepair} 帧，仅补算缺失帧`,
        raw: comfy,
      };
    }
    return {
      label: remote ? "GPU Control 抠图" : "ComfyUI 抠图",
      progress,
      overall: Math.min(60, overall),
      count: `${comfyDone}/${comfyTotal || "-"}`,
      position: remote ? remotePosition : taskPosition("COMFY", comfy),
      raw: comfy,
    };
  }
  if (stage.includes("frame") || stage.includes("extract")) return { label: "视频抽帧", progress: 0, overall: 5, count: `${expected || 0} 帧`, raw: run };
  return { label: formatStage(stage) || "准备中", progress: 0, overall: 0, count: "等待", raw: run };
}

function parentChildIds(raw) {
  const children = raw?.children || {};
  return [
    children.pipeline_run_id,
    children.frame_run_id,
    children.comfyui_run_id,
    ...(children.comfyui_run_ids || []),
    children.cherry_run_id,
    ...(children.cherry_run_ids || []),
  ].filter(Boolean);
}

function parentIdFromTask(task) {
  const match = `${task.input || ""} ${task.output || ""}`.match(/\b(?:IMG|VID)_[A-Z0-9]+\b/i);
  return match?.[0] || "";
}

function buildTaskCategories(tasks) {
  const specs = [
    ["image", "图片直发", "单图、批量图片与 ZIP 序列"],
    ["video", "视频直发", "聊天视频的抽帧、抠图与后处理"],
    ["feishu", "飞书动画", "Timo 表格下载与完整 AFLOW"],
    ["standalone", "独立任务", "单独启动的抠图、抽帧或后处理"],
  ];
  return specs.map(([key, label, hint]) => ({ key, label, hint, tasks: tasks.filter((task) => task.category === key) }));
}

function queuePosition(task) {
  const raw = task?.raw || {};
  const children = raw.children || {};
  const child = children.comfyui || children.frame || children.cherry || {};
  const value = firstNumber(raw.queue_position, raw.position, child.queue_position, child.position);
  return value === null ? Number.MAX_SAFE_INTEGER : value;
}

function queueStateRank(task) {
  const status = String(task?.status || "").toUpperCase();
  const stage = String(task?.stage || "").toLowerCase();
  const raw = task?.raw || {};
  const children = raw.children || {};
  const child = stage.includes("post") || stage.includes("cherry") || stage.includes("smooth")
    ? (children.cherry || {})
    : stage.includes("frame") || stage.includes("extract")
      ? (children.frame || raw.pipeline?.frame || {})
      : (children.comfyui || raw.pipeline?.comfyui || {});
  const childStatus = String(child.status || "").toUpperCase();
  if (status === "PAUSED" || childStatus === "PAUSED") return 2;
  if (["QUEUED", "PENDING"].includes(status) || ["QUEUED", "PENDING"].includes(childStatus) || stage.includes("waiting") || stage.includes("queue")) return 1;
  return 0;
}

function queueStateLabel(task) {
  const rank = queueStateRank(task);
  if (rank === 2) return "已暂停";
  if (rank === 1) {
    const position = queuePosition(task);
    return Number.isFinite(position) && position < Number.MAX_SAFE_INTEGER ? `排队第 ${position}` : "等待中";
  }
  return "正在处理";
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
  const stages = (run?.stages || []).filter((stage) => !(run?.allow_p4_writes === false && stage.key === "p4_shelve"));
  if (!stages.length) return 0;
  const current = run?.current_stage || "";
  const currentIndex = Math.max(0, stages.findIndex((item) => item.key === current));
  const done = stages.filter((stage, index) => stage.status === "done" || index < currentIndex).length;
  const child = flowActiveChild(run);
  const running = child ? progressFrom(child.raw) / 100 : (stages.some((stage) => stage.status === "running") ? 0.05 : 0);
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
        <div class="mark">Li</div>
        <div class="brand-copy">
          <b class="brand-title">LilClick Animation</b>
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
          <p class="eyebrow">LILCLICK · PIPELINE</p>
          <h1>动画生产面板</h1>
        </div>
        <div class="top-actions">
          <span class="timestamp">更新 {{ state.updatedAt || "-" }}</span>
          <button class="ghost" @click="toggleTheme">{{ state.theme === "light" ? "深色模式" : "浅色模式" }}</button>
          <button class="ghost" :disabled="state.refreshing" @click="refreshAll()">{{ state.refreshing ? "刷新中" : "刷新" }}</button>
        </div>
      </header>

      <section v-if="state.error" class="alert bad">{{ state.error }}</section>

      <section v-if="state.tab === 'overview'" class="view overview">
        <section v-if="currentFlow" class="hero-panel">
          <div>
            <p class="eyebrow">CURRENT FLOW</p>
            <h2>{{ currentFlow?.run_id || "暂无完整流程" }}</h2>
            <p>{{ currentFlow?.date_root || workspaceRoot() }}</p>
            <div class="hero-meta">
              <span>{{ currentFlow?.fps || state.form.fps }} FPS</span>
              <span>{{ currentFlow?.workflow_name || fileName(currentFlow?.workflow_path) || "默认抠图工作流" }}</span>
              <span>{{ currentFlow ? (currentFlow.allow_p4_writes === false ? "P4 已跳过" : "P4 Shelve-only") : (state.form.allowP4Writes ? "P4 Shelve-only" : "P4 已跳过") }}</span>
            </div>
          </div>
          <div class="hero-state">
            <span :class="['status-pill', statusClass(currentFlowUi.status)]">{{ currentFlowUi.status }}</span>
            <b>{{ currentFlowUi.label || formatStage(currentStage) }}</b>
            <small>总体 {{ currentFlowUi.progress ?? stageProgress }}%</small>
          </div>
        </section>
        <section v-else class="hero-panel flow-empty-state">
          <div>
            <p class="eyebrow">ANIMATION FLOW</p>
            <h2>当前没有运行中的动画全流程</h2>
            <p>已结束或已取消的 AFLOW 不再占用当前生产链路；其他类型任务仍在下方独立显示。</p>
          </div>
          <div v-if="recentFlow" class="recent-flow">
            <span>最近流程</span>
            <b>{{ recentFlow.run_id || recentFlow.id }}</b>
            <small :class="['status-text', statusClass(flowDisplay(recentFlow).status)]">{{ flowDisplay(recentFlow).status }}</small>
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
              <h2>{{ primaryTask.label || primaryTask.module }} <span>{{ primaryTask.id }}</span></h2>
            </div>
            <span :class="['status-pill', statusClass(primaryTask.status)]">{{ primaryTask.status }}</span>
          </div>
          <div class="focus-progress">
            <div class="progress-copy"><span>{{ focusLabel }}</span><b>{{ focusProgress }}%</b></div>
            <div class="progress"><i :style="{ width: `${focusProgress}%` }"></i></div>
            <div v-if="!primaryIsFlow && primaryTask.stageProgress !== undefined" class="stage-progress-row">
              <div class="progress-copy"><span>{{ primaryTask.stageLabel }} · {{ primaryTask.count }}</span><b>{{ primaryTask.stageProgress }}%</b></div>
              <div class="progress secondary"><i :style="{ width: `${primaryTask.stageProgress}%` }"></i></div>
            </div>
            <div v-if="primaryIsFlow && activeStageRun" class="stage-progress-row">
              <div class="progress-copy"><span>{{ currentFlowUi.label }} · {{ primaryTask.count || "计算中" }}</span><b>{{ activeStageProgress }}%</b></div>
              <div class="progress secondary"><i :style="{ width: `${activeStageProgress}%` }"></i></div>
            </div>
          </div>
          <div class="focus-detail">
            <span>{{ primaryTask.position || (primaryTask.last ? `刚完成 ${primaryTask.last}` : formatStage(primaryTask.stage)) }}</span>
            <small>{{ primaryTask.eta || "预计剩余计算中" }}</small>
            <small class="focus-path">{{ primaryTask.output || primaryTask.input || "暂无路径" }}</small>
          </div>
        </section>

        <section v-if="unifiedQueue.length" class="panel queue-panel">
          <div class="panel-title">
            <div><h3>统一任务队列</h3><span>跨来源按先后顺序汇总；各来源仍保持独立分类</span></div>
            <div class="queue-overall"><span>总体进度</span><b>{{ queueSummary.progress }}%</b></div>
          </div>
          <div class="queue-summary">
            <span><b>{{ queueSummary.total }}</b> 队列任务</span>
            <span><b>{{ queueSummary.running }}</b> 正在处理</span>
            <span><b>{{ queueSummary.waiting }}</b> 等待 / 暂停</span>
            <div class="progress"><i :style="{ width: `${queueSummary.progress}%` }"></i></div>
          </div>
          <div class="queue-track">
            <button v-for="task in unifiedQueue" :key="task.module + task.id" class="queue-item" @click="openTaskDetail(task)">
              <strong>{{ task.queueIndex }}</strong>
              <span><b>{{ task.name || task.label || task.module }}</b><small>{{ task.id }} · 已用 {{ formatDurationMs(taskPerformance(task).totalMs) }}</small></span>
              <em>{{ task.queueLabel }}</em>
              <span class="queue-stage"><b>{{ task.stageLabel || formatStage(task.stage) }}</b><small>{{ task.count || `${task.progress}%` }}</small></span>
              <i>{{ task.progress }}%</i>
            </button>
          </div>
        </section>

        <section v-if="currentFlow" class="stats-grid production-metrics">
          <article v-for="metric in overviewMetrics" :key="metric.key" class="stat">
            <span>{{ metric.label }}</span>
            <b>{{ metric.value }}</b>
            <small>{{ metric.detail }}</small>
          </article>
        </section>

        <section class="panel task-center-panel">
          <div class="panel-title">
            <div><h3>正在处理</h3><span>不同来源分开管理，父任务汇总真实子任务进度</span></div>
            <button class="ghost" @click="state.tab = 'tasks'">任务中心</button>
          </div>
          <div class="task-category-grid">
            <article v-for="category in taskCategories" :key="category.key" :class="['task-category', { active: category.tasks.length }]">
              <div class="category-head">
                <div><b>{{ category.label }}</b><small>{{ category.hint }}</small></div>
                <strong>{{ category.tasks.length }}</strong>
              </div>
              <div v-if="!category.tasks.length" class="category-empty">当前无任务</div>
              <button v-for="task in category.tasks.slice(0, 3)" :key="task.id" class="category-task" @click="openTaskDetail(task)">
                <span><b>{{ task.id }}</b><small>{{ task.stageLabel || formatStage(task.stage) }}</small></span>
                <em>{{ task.count || `${task.progress}%` }}</em>
              </button>
            </article>
          </div>
        </section>

        <section v-if="currentFlow" class="panel">
          <div class="panel-title">
            <div><h3>生产链路</h3><span>所有进度均绑定当前 AFLOW 的真实子任务</span></div>
            <button v-if="currentFlow?.run_id && isActiveStatus(currentFlow.status)" class="danger" @click="cancelFlow(currentFlow.run_id)">终止流程</button>
          </div>
          <div class="stages">
            <article v-for="(stage, index) in stageCards" :key="stage.key" :class="['stage', stage.status, { active: index === currentStageIndex }]">
              <span>{{ index + 1 }}</span>
              <div class="stage-copy"><b>{{ stage.label }}</b><small>{{ stage.detail || stage.status }}</small></div>
              <em>{{ stage.status === "running" && stage.progress ? `${stage.progress}%` : stage.status }}</em>
            </article>
          </div>
        </section>
        <section v-else class="panel flow-chain-empty">
          <div><h3>动画生产链路</h3><p>只有运行中的飞书动画 AFLOW 才会在这里显示七步链路。</p></div>
          <button class="ghost" @click="state.tab = 'launch'">启动动画流程</button>
        </section>

        <section v-if="currentFlow" class="panel process-panel">
          <div class="panel-title">
            <div><h3>本次处理配置</h3><span>来源、工作流与交付策略</span></div>
          </div>
          <div class="profile-grid">
            <article v-for="item in processProfile" :key="item.label" class="profile-item">
              <span>{{ item.label }}</span>
              <b>{{ item.value }}</b>
              <small :title="item.detail">{{ item.detail }}</small>
            </article>
          </div>
        </section>

        <section v-if="currentFlow" class="content-grid">
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

      </section>

      <section v-if="state.tab === 'tasks'" class="view">
        <section class="panel">
          <div class="task-center-heading">
            <div><p class="eyebrow">TASK OPERATIONS</p><h2>任务中心</h2><span>统一查看来源、处理状态、性能和交付操作</span></div>
            <div class="task-summary-cards">
              <button :class="{ active: state.taskStatusFilter === 'all' }" @click="state.taskStatusFilter = 'all'"><small>全部任务</small><b>{{ taskSummary.total }}</b></button>
              <button :class="{ active: state.taskStatusFilter === 'active' }" @click="state.taskStatusFilter = 'active'"><small>处理中</small><b>{{ taskSummary.active }}</b></button>
              <button :class="{ active: state.taskStatusFilter === 'done' }" @click="state.taskStatusFilter = 'done'"><small>已完成</small><b>{{ taskSummary.done }}</b></button>
              <button :class="['attention', { active: state.taskStatusFilter === 'attention' }]" @click="state.taskStatusFilter = 'attention'"><small>需关注</small><b>{{ taskSummary.attention }}</b></button>
            </div>
          </div>
          <div class="task-toolbar">
            <label class="task-search"><span>⌕</span><input v-model="state.taskQuery" placeholder="搜索任务名、用户、任务 ID 或阶段" /><button v-if="state.taskQuery" aria-label="清空搜索" @click="state.taskQuery = ''">×</button></label>
            <span class="task-result-count">{{ filteredTaskRows.length }} 条结果</span>
          </div>
          <div class="task-filters task-source-filters">
            <button v-for="item in [{key:'all',label:'全部'}, {key:'image',label:'图片直发'}, {key:'video',label:'视频直发'}, {key:'feishu',label:'飞书动画'}, {key:'standalone',label:'独立任务'}]" :key="item.key" :class="{ active: state.taskFilter === item.key }" @click="state.taskFilter = item.key">{{ item.label }} <span>{{ item.key === 'all' ? allTasks.length : allTasks.filter(task => task.category === item.key).length }}</span></button>
          </div>
          <div v-if="filteredTaskRows.length" class="task-table-head"><span>任务</span><span>来源用户</span><span>状态</span><span>当前阶段</span><span>性能</span><span>操作</span></div>
          <div v-if="!filteredTaskRows.length" class="empty task-empty"><b>没有匹配的任务</b><span>调整来源、状态筛选或搜索关键词后再试。</span></div>
          <article v-for="task in filteredTaskRows" :key="task.module + task.id" class="task-row large performance-task-row">
            <div class="task-main">
              <b :title="task.name || task.label || task.module">{{ task.name || task.label || task.module }}</b>
              <span>{{ task.label }} · {{ task.id }}</span>
              <small>启动 {{ task.time || "-" }} · {{ task.count || "-" }}</small>
            </div>
            <div v-if="isDirectTask(task)" class="task-user" :title="`飞书来源：${task.user.displayName}`">
              <span class="task-avatar">
                <i>{{ userInitial(task) }}</i>
                <img v-if="task.user.avatarUrl" :src="task.user.avatarUrl" alt="" @error="$event.currentTarget.remove()" />
              </span>
              <span><b>{{ task.user.displayName }}</b><small>来源：飞书</small></span>
            </div>
            <div v-else class="task-user task-user-local">
              <span class="task-avatar"><i>本</i></span>
              <span><b>本地任务</b><small>来源：系统</small></span>
            </div>
            <span :class="['status-pill', statusClass(task.status)]" :title="task.status">{{ statusLabel(task.status) }}</span>
            <span class="stage-name">{{ task.stageLabel || task.last || formatStage(task.stage) }}</span>
            <div class="performance-cell">
              <span><em>总耗时</em><b>{{ formatDurationMs(task.performance.totalMs) }}</b></span>
              <small><em>瓶颈</em><span>{{ task.performance.bottleneck?.label || "暂无阶段拆分" }}<template v-if="task.performance.bottleneck"> · {{ Math.round(task.performance.bottleneck.share * 100) }}%</template></span></small>
              <small><em>速度</em><span>{{ task.performance.throughput ? `${task.performance.throughput.toFixed(1)} 帧/分` : task.performance.backend }}</span></small>
            </div>
            <div class="task-actions">
              <button v-if="task.module === 'COMFY' && task.status === 'RUNNING'" class="ghost" @click="controlComfy('pause', task.id)">暂停</button>
              <button v-if="task.module === 'COMFY' && task.status === 'PAUSED'" class="ghost" @click="controlComfy('resume', task.id)">继续</button>
              <button v-if="isActiveStatus(task.status)" class="danger" @click="requestCancelTask(task)">取消</button>
              <button
                v-if="isDirectTask(task)"
                class="rerun"
                :disabled="rerunDisabled(task)"
                :title="task.rerun?.error || '保留当前任务 ID 和审计历史，从原始素材开始重新执行完整处理流程'"
                @click="requestFullRerun(task)"
              >{{ rerunLabel(task) }}</button>
              <button
                v-if="isDirectTask(task)"
                class="redeliver"
                :disabled="redeliveryDisabled(task)"
                :title="task.redelivery?.error || '重新生成一个完整素材包并发送到原飞书会话'"
                @click="requestFullRedelivery(task)"
              >{{ redeliveryLabel(task) }}</button>
              <button class="ghost" @click="openTaskDetail(task)">耗时详情</button>
            </div>
          </article>
        </section>
      </section>

      <section v-if="state.tab === 'analysis'" class="view">
        <PerformanceDashboard :records="performanceRecords" :summary="performanceSummary" @open="openTaskDetail" />
      </section>

      <section v-if="state.tab === 'launch'" class="view launch-grid">
        <section class="panel launch-panel">
          <div class="panel-title"><div><h3>启动完整动画自动化</h3><span>真实环境 · Timo 表格下载 · P4 默认关闭</span></div></div>

          <section class="launch-section">
            <div class="section-index">01</div>
            <div class="section-body">
              <div class="section-heading"><div><h4>飞书来源与抽帧</h4><p>只下载指定视图中进度为“待抽帧”且包含动画附件的记录。</p></div><span class="source-tag">Timo</span></div>
              <div class="form-grid three">
                <label><span>输出日期</span><input v-model="state.form.date" /></label>
                <label><span>抽帧速率</span><input v-model.number="state.form.fps" type="number" min="1" step="1" inputmode="numeric" placeholder="24" /><small>可填写任意正整数 FPS；默认 24，不改变视频原文件。</small></label>
                <label><span>优先角色</span><input v-model="state.form.priority" placeholder="留空则按表格顺序" /></label>
              </div>
            </div>
          </section>

          <section class="launch-section">
            <div class="section-index">02</div>
            <div class="section-body">
              <div class="section-heading"><div><h4>抠图与后处理</h4><p>与直接图片、视频任务共用 ComfyUI 队列和 ImageClip 执行组件。</p></div></div>
              <div class="form-grid">
                <label class="wide"><span>ComfyUI 工作流</span><input v-model="state.form.workflowPath" placeholder="留空使用后端默认 ImageClip.json" /><small>总览会显示本次实际解析到的工作流名称与路径。</small></label>
                <label class="read-only-field"><span>Cherry 后处理</span><input value="默认后处理 · 时序平滑关闭" readonly /></label>
                <label class="check"><input v-model="state.form.fakeMatting" type="checkbox" /><span>仅测试：直接把抽帧复制为抠图结果</span></label>
              </div>
            </div>
          </section>

          <section class="launch-section">
            <div class="section-index">03</div>
            <div class="section-body">
              <div class="section-heading"><div><h4>Unity 交付</h4><p>整理 unity_ready 后执行替换/迭代或新导入。</p></div></div>
              <div class="form-grid">
                <label><span>Unity 模式</span><select v-model="state.form.mode"><option value="iteration">替换 / 迭代</option><option value="import">新导入</option></select></label>
                <label><span>资源包</span><select v-model="state.form.package"><option value="both">全部（both）</option><option value="scene">场景（scene）</option><option value="emoji">表情（emoji）</option><option value="story">剧情（story）</option></select></label>
                <label class="wide"><span>Unity Project</span><input v-model="state.form.unityProject" /></label>
                <label class="check"><input v-model="state.form.allowP4Writes" type="checkbox" /><span>完成 Unity 后执行 P4 Shelve（默认关闭）</span></label>
                <label v-if="state.form.allowP4Writes"><span>P4 Stream</span><input v-model="state.form.p4Stream" /></label>
              </div>
            </div>
          </section>

          <div class="command-preview">{{ launchCommand }}</div>
          <div class="button-row">
            <button class="ghost" :disabled="state.launchBusy || !fpsValid" @click="previewFlow">预览</button>
            <button class="primary" :disabled="state.launchBusy || !fpsValid" @click="startFlow">{{ state.launchBusy ? "处理中" : "启动完整流程" }}</button>
          </div>
        </section>
        <section class="panel">
          <div class="panel-title"><div><h3>预览与返回</h3><span>启动前核对最终参数</span></div></div>
          <div class="launch-summary">
            <div><span>飞书筛选</span><b>待抽帧</b></div>
            <div><span>抽帧</span><b>{{ state.form.fps }} FPS</b></div>
            <div><span>抠图</span><b>{{ fileName(state.form.workflowPath) || "后端默认" }}</b></div>
            <div><span>后处理</span><b>Cherry 默认流程</b></div>
            <div><span>P4</span><b>{{ state.form.allowP4Writes ? "Shelve-only" : "跳过" }}</b></div>
          </div>
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

    <div
      v-if="state.cancelConfirm"
      class="redelivery-modal-backdrop"
      role="presentation"
      @click.self="closeCancelConfirm"
    >
      <section class="redelivery-modal cancel-modal" role="dialog" aria-modal="true" aria-labelledby="cancel-title">
        <header>
          <div class="redelivery-recipient">
            <span class="task-avatar large">
              <i>{{ isDirectTask(state.cancelConfirm) ? userInitial(state.cancelConfirm) : '任' }}</i>
              <img v-if="state.cancelConfirm.user?.avatarUrl" :src="state.cancelConfirm.user.avatarUrl" alt="" />
            </span>
            <span><small>当前任务</small><b>{{ state.cancelConfirm.user?.displayName || state.cancelConfirm.label }}</b></span>
          </div>
          <button class="modal-close" aria-label="关闭" @click="closeCancelConfirm">×</button>
        </header>
        <div class="redelivery-modal-body">
          <span class="modal-kicker">停止当前执行</span>
          <h3 id="cancel-title">{{ state.cancelConfirm.name || state.cancelConfirm.id }}</h3>
          <p>确认取消当前任务？系统会停止正在运行的阶段，但不会删除原始素材、处理记录或已生成的文件。</p>
          <div class="cancel-task-summary">
            <span><small>任务 ID</small><b>{{ state.cancelConfirm.id }}</b></span>
            <span><small>当前阶段</small><b>{{ state.cancelConfirm.stageLabel || formatStage(state.cancelConfirm.stage) }}</b></span>
          </div>
          <div class="redelivery-fallback-note cancel-warning">
            <b>取消后仍可完整重跑</b>
            <span>该任务会在原位置显示“已取消”，历史记录继续保留，不会新增或删除列表项。</span>
          </div>
        </div>
        <footer>
          <button class="ghost" @click="closeCancelConfirm">继续运行</button>
          <button class="danger cancel-confirm-button" @click="confirmCancelTask">确认取消任务</button>
        </footer>
      </section>
    </div>

    <div
      v-if="state.redeliveryConfirm"
      class="redelivery-modal-backdrop"
      role="presentation"
      @click.self="closeRedeliveryConfirm"
    >
      <section class="redelivery-modal" role="dialog" aria-modal="true" aria-labelledby="redelivery-title">
        <header>
          <div class="redelivery-recipient">
            <span class="task-avatar large">
              <i>{{ userInitial(state.redeliveryConfirm) }}</i>
              <img v-if="state.redeliveryConfirm.user.avatarUrl" :src="state.redeliveryConfirm.user.avatarUrl" alt="" />
            </span>
            <span><small>发送回原飞书会话</small><b>{{ state.redeliveryConfirm.user.displayName }}</b></span>
          </div>
          <button class="modal-close" aria-label="关闭" @click="closeRedeliveryConfirm">×</button>
        </header>
        <div class="redelivery-modal-body">
          <span class="modal-kicker">完整素材重发</span>
          <h3 id="redelivery-title">{{ state.redeliveryConfirm.name }}</h3>
          <p>系统会重新生成一个压缩包，历史交付记录不会导致本次发送被跳过。</p>
          <div class="redelivery-content-list">
            <span v-for="item in redeliveryContents(state.redeliveryConfirm)" :key="item"><i>✓</i>{{ item }}</span>
          </div>
          <div class="redelivery-fallback-note">
            <b>缺少后处理也会交付</b>
            <span>已有的原始素材和透明抠图会照常打包，并在对应目录附说明。</span>
          </div>
        </div>
        <footer>
          <button class="ghost" @click="closeRedeliveryConfirm">取消</button>
          <button class="primary redelivery-confirm-button" @click="confirmFullRedelivery">确认完整重发</button>
        </footer>
      </section>
    </div>

    <div
      v-if="state.rerunConfirm"
      class="redelivery-modal-backdrop"
      role="presentation"
      @click.self="closeRerunConfirm"
    >
      <section class="redelivery-modal rerun-modal" role="dialog" aria-modal="true" aria-labelledby="rerun-title">
        <header>
          <div class="redelivery-recipient">
            <span class="task-avatar large">
              <i>{{ userInitial(state.rerunConfirm) }}</i>
              <img v-if="state.rerunConfirm.user.avatarUrl" :src="state.rerunConfirm.user.avatarUrl" alt="" />
            </span>
            <span><small>完成后发送回原飞书会话</small><b>{{ state.rerunConfirm.user.displayName }}</b></span>
          </div>
          <button class="modal-close" aria-label="关闭" @click="closeRerunConfirm">×</button>
        </header>
        <div class="redelivery-modal-body">
          <span class="modal-kicker">端到端完整重跑</span>
          <h3 id="rerun-title">{{ state.rerunConfirm.name }}</h3>
          <p>系统会保留当前任务 ID 和列表位置，将本次失败写入内部历史，然后从原始素材开始执行全部步骤。列表始终只显示这一条任务。</p>
          <div class="redelivery-content-list rerun-step-list">
            <span v-for="(item, index) in rerunSteps(state.rerunConfirm)" :key="item"><i>{{ index + 1 }}</i>{{ item }}</span>
          </div>
          <div class="redelivery-fallback-note rerun-warning">
            <b>这是一次原地完整重算</b>
            <span>会重新占用 ComfyUI / GPU 和后处理资源；旧状态只保留在审计历史中，不会新增第二行任务。</span>
          </div>
        </div>
        <footer>
          <button class="ghost" @click="closeRerunConfirm">取消</button>
          <button class="primary rerun-confirm-button" @click="confirmFullRerun">确认完整重跑</button>
        </footer>
      </section>
    </div>

    <TaskDetailDrawer v-if="selectedTask && selectedPerformance" :task="selectedTask" :performance="selectedPerformance" @close="state.detail = null" />

    <div class="toasts">
      <article v-for="item in state.toasts" :key="item.id" :class="['toast', item.type]">{{ item.message }}</article>
    </div>
  </div>
</template>
