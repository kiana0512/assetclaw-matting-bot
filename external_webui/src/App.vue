<script setup>
import { computed, nextTick, onMounted, reactive } from "vue";

const DEFAULT_TOKEN = localStorage.getItem("assetclaw.skillToken") || "";
const CHAT_STORAGE_KEY = "assetclaw.mikuAgent.chat.test";
const STATUS_CACHE_KEY = "assetclaw.mikuAgent.statusCache.v1";
const DEFAULT_SYSTEM_MESSAGE = {
  role: "system",
  text: "Miku Agent 控制台已连接。本页发送的内容会原样进入本机 Agent，与飞书走同一套后端。",
};
const HEALTH_FAILURES_BEFORE_OFFLINE = 3;
const HEALTH_GRACE_MS = 30000;
const HEALTH_TIMEOUT_MS = 25000;
const STATUS_POLL_MS = 20000;
const FRONTEND_CACHE_MS = 2500;
const MAX_STATUS_PARALLEL = 4;
const VISIBLE_TASK_LIMIT = 40;
const VISIBLE_LOG_LIMIT = 60;
const DEFAULT_DAY = localStorage.getItem("assetclaw.workspaceDay") || localDateStamp();
const DEFAULT_WORKSPACE_ROOT = `E:/animation_automation/${DEFAULT_DAY}`;

const inflightRequests = new Map();
const responseCache = new Map();

const views = [
  { id: "chat", icon: "⌘", label: "对话", eyebrow: "本机 Agent 入口", title: "AI 对话工作台" },
  { id: "overview", icon: "◎", label: "总控", eyebrow: "生产状态总览", title: "总控台" },
  { id: "queues", icon: "▦", label: "队列", eyebrow: "任务队列与控制", title: "任务队列" },
  { id: "pipeline", icon: "⇄", label: "流程", eyebrow: "动画自动化流程", title: "流程总览" },
  { id: "builder", icon: "▣", label: "生产", eyebrow: "一键自动化", title: "动画自动化流程" },
  { id: "voice", icon: "♬", label: "语音", eyebrow: "ASR / TTS", title: "语音工作台" },
  { id: "p4", icon: "◇", label: "P4", eyebrow: "版本管理", title: "P4 工作区" },
  { id: "memory", icon: "◌", label: "记忆", eyebrow: "Memory / RAG", title: "记忆检索" },
  { id: "skills", icon: "✦", label: "技能", eyebrow: "后端能力", title: "技能清单" },
  { id: "logs", icon: "≡", label: "日志", eyebrow: "运行观测", title: "操作日志" },
];

const statusCalls = [
  { key: "gpu", skill: "system.gpu_status", args: {} },
  { key: "process", skill: "system.process_status", args: { names: ["ComfyUI", "python", "p4", "ffmpeg", "Cherry"] } },
  { key: "agentWork", skill: "agent.current_work", args: { include_gpu: true } },
  { key: "agentDiagnose", skill: "agent.diagnose", args: { include_gpu: false }, lazy: true },
  { key: "comfyQueue", skill: "comfyui.queue_status", args: {} },
  { key: "comfyRuns", skill: "comfyui.run_list", args: { limit: 12, include_finished: true } },
  { key: "comfyCurrent", skill: "comfyui.run_status", args: { include_gpu: false } },
  { key: "cherryRuns", skill: "cherry.run_list", args: { limit: 12, include_finished: true } },
  { key: "cherryCurrent", skill: "cherry.run_status", args: { include_gpu: false } },
  { key: "frameRuns", skill: "frame.run_list", args: { limit: 12, include_finished: true, include_archived: false } },
  { key: "frameCurrent", skill: "frame.run_status", args: {} },
  { key: "pipelineRuns", skill: "pipeline.run_list", args: { limit: 12, include_finished: true } },
  { key: "pipelineCurrent", skill: "pipeline.run_status", args: {} },
  { key: "animationFlowRuns", skill: "animation_flow.list", args: { limit: 12, include_finished: true } },
  { key: "animationFlowCurrent", skill: "animation_flow.status", args: {} },
  { key: "customPipelineRuns", skill: "custom_pipeline.run_list", args: { limit: 12, include_finished: true } },
  { key: "customPipelineCurrent", skill: "custom_pipeline.run_status", args: {} },
  { key: "unityReady", skill: "unity_ready.status", args: {} },
  { key: "unityImport", skill: "unity_import.status", args: {} },
  { key: "animation", skill: "animation.status", args: { include_runs: true } },
  { key: "p4", skill: "p4.status", args: {} },
];

const modules = {
  feishuVideo: {
    title: "飞书视频准备",
    subtitle: "从飞书表格读取动画附件并准备视频目录。当前后端入口仍复用 frame.run_start。",
    previewSkill: "frame.run_preview",
    startSkill: "frame.run_start",
    statusKey: "frameCurrent",
    runKey: "frameRuns",
    fields: [
      f("download_dir", "视频下载目录", "path", pathInWorkspace("videos"), "建议按日期建目录：E:/animation_automation/当天/videos。", "dir"),
      f("export_dir", "抽帧输出目录", "path", pathInWorkspace("frames"), "当前后端 frame.run_start 会同时抽帧，所以这里先指定抽帧输出。后续后端拆出纯下载 API 后可只保留下载目录。", "dir"),
      f("fps", "抽帧 FPS", "number", 24, "当前兼容参数，一般 24。"),
      f("max_frames", "最多帧数", "number", 0, "0 表示不限；只测试飞书下载时可填很小的数字。"),
      f("diff_threshold", "去重差异阈值", "number", 0.2, "当前兼容参数，一般 0.15-0.3。"),
      f("dedup_enabled", "启用关键帧去重", "checkbox", true, "当前兼容参数。"),
      f("dedup_renumber", "去重后重新编号", "checkbox", true, "当前兼容参数。"),
      f("notify_interval_seconds", "进度通知间隔", "number", 60, "建议 60-300 秒。WebUI 会在队列/流程页展示进度。"),
    ],
  },
  frame: {
    title: "抽帧 / 剔除关键帧",
    subtitle: "从视频目录抽帧，可启用去重和重新编号。",
    previewSkill: "frame.run_preview",
    startSkill: "frame.run_start",
    statusKey: "frameCurrent",
    runKey: "frameRuns",
    fields: [
      f("download_dir", "视频目录", "path", pathInWorkspace("videos"), "放飞书下载下来的视频，或你手动放进去的视频。", "dir"),
      f("export_dir", "抽帧输出目录", "path", pathInWorkspace("frames"), "抽出来的帧会写到这里。", "dir"),
      f("fps", "抽帧 FPS", "number", 24, "一般 12/24/30。动画流程默认 24。"),
      f("max_frames", "最多帧数", "number", 0, "0 表示不限；测试时可以填 200。"),
      f("diff_threshold", "去重差异阈值", "number", 0.2, "越小越容易判定为不同帧；常用 0.15-0.3。"),
      f("dedup_enabled", "启用关键帧去重", "checkbox", true, "测试阶段建议打开。"),
      f("dedup_renumber", "去重后重新编号", "checkbox", true, "输出帧会连续编号，方便后续 ComfyUI。"),
      f("notify_interval_seconds", "进度通知间隔", "number", 300, "后端给飞书/日志报告进度的间隔。"),
    ],
  },
  comfyui: {
    title: "ComfyUI 抠图",
    subtitle: "批量把帧送进工作流，输出透明底或遮罩结果。",
    previewSkill: "comfyui.run_preview",
    startSkill: "comfyui.run_start",
    statusKey: "comfyCurrent",
    runKey: "comfyRuns",
    fields: [
      f("workflow_path", "工作流 JSON", "path", "", "一般选择 ComfyUI/user/default/workflows 里的 json。", "file"),
      f("input_dir", "输入帧目录", "path", pathInWorkspace("frames"), "通常填抽帧输出目录。", "dir"),
      f("output_dir", "抠图输出目录", "path", pathInWorkspace("matte"), "ComfyUI 输出会同步到这里。", "dir"),
      f("input_node_id", "LoadImage 节点 ID", "text", "", "不确定就留空，后端会尝试自动识别。"),
      f("input_name", "输入字段名", "text", "image", "通常是 image。"),
      f("recursive", "递归子目录", "checkbox", true, "目录下有多层帧序列时打开。"),
      f("preserve_structure", "保留目录结构", "checkbox", true, "建议打开，便于按镜头/角色回溯。"),
      f("skip_existing", "跳过已有输出", "checkbox", true, "断点续跑时建议打开。"),
      f("max_images", "最多图片数", "number", 50000, "测试时可填 100 或 500。"),
      f("notify_interval_seconds", "进度通知间隔", "number", 300, "后端报告进度的间隔。"),
    ],
  },
  cherry: {
    title: "Cherry 平滑",
    subtitle: "对抠图结果做时序平滑、缩放、锐化。",
    previewSkill: "cherry.run_preview",
    startSkill: "cherry.run_start",
    statusKey: "cherryCurrent",
    runKey: "cherryRuns",
    fields: [
      f("input_dir", "抠图输入目录", "path", pathInWorkspace("matte"), "通常填 ComfyUI 输出目录。", "dir"),
      f("output_dir", "平滑输出目录", "path", pathInWorkspace("smooth"), "最终平滑结果写到这里。", "dir"),
      f("recursive", "递归子目录", "checkbox", true, "建议打开。"),
      f("skip_existing", "跳过已有输出", "checkbox", true, "断点续跑建议打开。"),
      f("max_images", "最多图片数", "number", 50000, "测试可填 100。"),
      f("use_denoise", "Alpha 去噪", "checkbox", true, "减少边缘脏点。"),
      f("denoise_threshold", "去噪阈值", "number", 0.06, "一般 0.04-0.08。"),
      f("denoise_radius", "去噪半径", "number", 0, "0 表示默认。"),
      f("use_smooth", "启用时序平滑", "checkbox", true, "核心功能，建议打开。"),
      f("smooth_window", "平滑窗口", "number", 5, "越大越稳，过大可能糊。"),
      f("smooth_sigma", "平滑强度", "number", 1.0, "一般 0.8-1.5。"),
      f("min_alpha", "最小 Alpha", "number", 0.05, "低于该值视为透明。"),
      f("sync_rgb", "同步 RGB 边缘", "checkbox", true, "让颜色边缘跟随 Alpha。"),
      f("use_resize", "启用缩放", "checkbox", true, "需要统一尺寸时打开。"),
      f("resize_width", "输出宽度", "number", 256, "按项目要求填。"),
      f("resize_height", "输出高度", "number", 256, "按项目要求填。"),
      f("use_sharpen", "启用锐化", "checkbox", true, "增强边缘。"),
      f("sharpen_amount", "锐化强度", "number", 2.0, "一般 1-3。"),
      f("sharpen_radius", "锐化半径", "number", 2, "一般 1-3。"),
      f("sharpen_threshold", "锐化阈值", "number", 0.02, "边缘触发阈值。"),
      f("sharpen_shrink", "锐化缩小倍率", "number", 4, "性能优化参数。"),
      f("notify_interval_seconds", "进度通知间隔", "number", 300, "后端报告进度的间隔。"),
    ],
  },
  animationFlow: {
    title: "完整 7 步动画自动化",
    subtitle: "飞书下载 -> 抽帧 -> 抠图 -> Cherry -> unity_ready -> Unity 导入 -> P4 Shelve-only。",
    previewSkill: "animation_flow.preview",
    startSkill: "animation_flow.start",
    statusKey: "animationFlowCurrent",
    runKey: "animationFlowRuns",
    fields: [
      f("date_root", "当天工作区", "path", pathInWorkspace(), "例如 E:/animation_automation/2026-06-09。", "dir"),
      f("workflow_path", "ComfyUI 工作流", "path", "", "抠图用工作流 json；留空走后端默认。", "file"),
      f("unity_project", "Unity 工程", "path", "D:/Spark/Client", "插件 API 在这个 Unity 工程里执行。", "dir"),
      f("p4_stream", "P4 Stream", "text", "//streams/rel_0.0.1", "当前固定使用 rel_0.0.1。"),
      f("package", "Unity 导入包", "text", "both", "both / scene / emoji。"),
      f("fps", "抽帧 FPS", "number", 24, "默认 24。"),
      f("notify_interval_seconds", "进度通知间隔", "number", 60, "飞书和 WebUI 进度刷新间隔。"),
      f("allow_p4_writes", "允许 P4 create/reconcile/shelve", "checkbox", true, "只允许 shelve-only，submit 永远禁用。"),
    ],
  },
  unityReady: {
    title: "unity_ready 整理",
    subtitle: "把处理后的 scene / emoji 资源整理成 Unity 插件可读取的 JSON + frames。",
    previewSkill: "unity_ready.preview",
    startSkill: "unity_ready.build",
    statusKey: "unityReady",
    runKey: "",
    fields: [
      f("date_root", "当天工作区", "path", pathInWorkspace(), "例如 E:/animation_automation/2026-06-09。", "dir"),
      f("overwrite", "覆盖已有 unity_ready", "checkbox", true, "整体流程会覆盖当天 unity_ready。"),
      f("copy_mode", "复制模式", "text", "copy", "默认 copy。"),
    ],
  },
  unityImport: {
    title: "Unity 插件导入引擎",
    subtitle: "通过插件 API/MCP 导入 unity_ready，不改插件源码，不点击 UI。",
    previewSkill: "unity_import.preview",
    startSkill: "unity_import.run",
    statusKey: "unityImport",
    runKey: "",
    fields: [
      f("unity_ready", "unity_ready 目录", "path", pathInWorkspace("unity_ready"), "包含 scene/emoji 两包。", "dir"),
      f("unity_project", "Unity 工程", "path", "D:/Spark/Client", "当前 Spark Client 工程。", "dir"),
      f("package", "导入包", "text", "both", "both / scene / emoji。"),
      f("timeout_seconds", "超时时间", "number", 900, "Unity 导入可能比较慢。"),
    ],
  },
  pipeline: {
    title: "旧三步流程",
    subtitle: "仅抽帧 -> ComfyUI -> Cherry；主流程请用完整 7 步动画自动化。",
    previewSkill: "pipeline.run_preview",
    startSkill: "pipeline.run_start",
    statusKey: "pipelineCurrent",
    runKey: "pipelineRuns",
    fields: [
      f("input_dir", "视频输入目录", "path", pathInWorkspace("videos"), "流程第一步读取这里的视频。", "dir"),
      f("frame_output_dir", "抽帧输出目录", "path", pathInWorkspace("frames"), "抽帧结果。", "dir"),
      f("matte_output_dir", "抠图输出目录", "path", pathInWorkspace("matte"), "ComfyUI 结果。", "dir"),
      f("smooth_output_dir", "平滑输出目录", "path", pathInWorkspace("smooth"), "Cherry 结果。", "dir"),
      f("workflow_path", "ComfyUI 工作流", "path", "", "选择抠图工作流 json。", "file"),
      f("fps", "抽帧 FPS", "number", 24, "一般 24。"),
      f("max_frames", "最多帧数", "number", 0, "0 表示不限。"),
      f("diff_threshold", "去重差异阈值", "number", 0.2, "关键帧去重阈值。"),
      f("notify_interval_seconds", "进度通知间隔", "number", 300, "后端报告进度的间隔。"),
    ],
  },
};

const flowSkills = [
  "feishu_table.export_json",
  "frame.run_preview",
  "frame.run_start",
  "frame.run_status",
  "comfyui.run_start",
  "comfyui.run_status",
  "cherry.run_start",
  "cherry.run_status",
  "unity_ready.preview",
  "unity_ready.build",
  "unity_ready.status",
  "unity_import.preview",
  "unity_import.run",
  "unity_import.status",
  "animation_flow.preview",
  "animation_flow.start",
  "animation_flow.status",
  "animation_flow.list",
  "pipeline.run_start",
  "p4.shelve_ui_import",
  "speech.transcribe",
  "speech.synthesize",
  "memory.context_pack",
];

const state = reactive({
  view: "chat",
  polling: true,
  health: null,
  healthError: "",
  refreshing: false,
  connection: {
    stableOnline: false,
    checking: true,
    consecutiveFailures: 0,
    lastOkAt: "",
    lastOkMs: 0,
    lastAttemptAt: "",
    lastError: "",
  },
  status: {},
  statusUpdatedAt: {},
  skills: [],
  messages: [DEFAULT_SYSTEM_MESSAGE],
  input: "",
  sending: false,
  attachments: [],
  token: DEFAULT_TOKEN,
  settingsOpen: false,
  lastRefreshAt: "",
  detail: null,
  configModule: "",
  configValues: {},
  configResult: "",
  commandOpen: false,
  commandQuery: "",
  action: {
    current: "",
    startedAt: "",
    startedMs: 0,
    nowMs: Date.now(),
    last: "",
    busy: {},
  },
  pathPicker: null,
  pathItems: [],
  pathLoading: false,
  pathDialogLoading: false,
  pathDialogError: "",
  pathManual: "",
  toasts: [],
  flowName: "animation_custom",
  flowDescription: "自定义动画自动化流程",
  flowVariables: JSON.stringify({
    workspace_root: pathInWorkspace(),
    videos: pathInWorkspace("videos"),
    frames: pathInWorkspace("frames"),
    matte: pathInWorkspace("matte"),
    smooth: pathInWorkspace("smooth"),
  }, null, 2),
  flowVars: [
    { key: "workspace_root", label: "当天工作区根目录", value: pathInWorkspace(), mode: "dir" },
    { key: "videos", label: "飞书视频下载目录", value: pathInWorkspace("videos"), mode: "dir" },
    { key: "frames", label: "抽帧输出目录", value: pathInWorkspace("frames"), mode: "dir" },
    { key: "matte", label: "ComfyUI 抠图输出目录", value: pathInWorkspace("matte"), mode: "dir" },
    { key: "smooth", label: "Cherry 平滑输出目录", value: pathInWorkspace("smooth"), mode: "dir" },
  ],
  flowSteps: [],
  quickFlow: {
    workflowPath: "",
    unityProject: "D:/Spark/Client",
    p4Stream: "//streams/rel_0.0.1",
    package: "both",
    fps: 24,
    notifyIntervalSeconds: 60,
    allowP4Writes: true,
    p4Description: "[UI Emoji Import] 动画资源导入 - Shelve-only",
    advancedOpen: false,
  },
  flowResult: "",
  flowResultSummary: null,
  flowBusy: false,
  flowStepConfigIndex: null,
  flowDefinitions: [],
  moduleCatalog: null,
  asr: { audio_path: "", language: "zh", prompt: "飞书语音指令，可能包含动画流程、ComfyUI、P4、文件路径。" },
  tts: { text: "你好，我是 Miku Agent。动画自动化流程已经准备好了。", output_path: "", voice: "", engine: "auto", rate: "" },
  asrResult: "",
  ttsResult: "",
  memoryScope: "global",
  memoryConversation: "test",
  memoryAdvanced: false,
  memoryItems: [],
  memoryPack: null,
  logs: [],
  skillCalls: [],
  logsLoading: false,
  logError: "",
  conversationId: "test",
  logTab: "brain",
  queueVisibleLimit: VISIBLE_TASK_LIMIT,
  logVisibleLimit: VISIBLE_LOG_LIMIT,
  queueFilters: { keyword: "", status: "all", day: "" },
  workspaceRoot: localStorage.getItem("assetclaw.workspaceRoot") || DEFAULT_WORKSPACE_ROOT,
  p4Result: null,
  p4LastAction: "status",
  p4Advanced: false,
  p4Form: {
    workflow: "",
    workspace: "",
    cl: "",
    description: "[UI Emoji Import] 动画资源导入 - Shelve-only",
    allow_delete: false,
    force: false,
  },
});

const currentView = computed(() => views.find((item) => item.id === state.view) || views[0]);
const online = computed(() => state.connection.stableOnline);
const connectionTitle = computed(() => {
  if (online.value) return "后端在线";
  if (state.connection.checking) return "正在连接";
  return "后端离线";
});
const connectionSubtitle = computed(() => {
  if (online.value && state.connection.consecutiveFailures > 0) {
    return `连接有抖动，保留上次成功：${state.connection.lastOkAt || "-"}`;
  }
  if (online.value) return state.health?.service || "本机 Agent 已连接";
  if (state.connection.checking) return "正在检测本机代理";
  return state.connection.lastError || state.healthError || "本机代理暂不可达";
});
const tasks = computed(() => normalizeTasks());
const filteredTasks = computed(() => {
  const keyword = state.queueFilters.keyword.trim().toLowerCase();
  return tasks.value.filter((task) => {
    if (state.queueFilters.status !== "all" && String(task.status).toLowerCase() !== state.queueFilters.status) return false;
    if (state.queueFilters.day && task.day !== state.queueFilters.day) return false;
    if (!keyword) return true;
    return `${task.module} ${task.id} ${task.input} ${task.output} ${task.status}`.toLowerCase().includes(keyword);
  });
});
const visibleTasks = computed(() => filteredTasks.value.slice(0, state.queueVisibleLimit));
const visibleLogs = computed(() => state.logs.slice(0, state.logVisibleLimit));
const visibleSkillCalls = computed(() => state.skillCalls.slice(0, state.logVisibleLimit));
const queueDays = computed(() => Array.from(new Set(tasks.value.map((task) => task.day).filter(Boolean))).slice(0, 12));
const activeTasks = computed(() => tasks.value.filter((item) => !isTaskDone(item)));
const retrievalSkills = computed(() => state.skills.filter((item) => ["memory", "web", "life", "speech"].includes(item.domain) || /rag|memory|search|recall|vector/i.test(`${item.name} ${item.description}`)));
const filteredCommands = computed(() => {
  const q = state.commandQuery.trim().toLowerCase();
  return commands.filter((item) => `${item.title} ${item.hint} ${item.keys}`.toLowerCase().includes(q));
});
const snapshotRows = computed(() => {
  const gpu = unwrap(state.status.gpu);
  const g0 = gpu.gpus?.[0] || {};
  return [
    ["Agent", connectionTitle.value],
    ["后端", state.health?.service || "本机代理"],
    ["GPU", g0.memory_total_mb ? `${g0.memory_used_mb}/${g0.memory_total_mb} MB` : compact(gpu.error, "未知")],
    ["ComfyUI", unwrap(state.status.comfyCurrent).status || "空闲"],
    ["Cherry", unwrap(state.status.cherryCurrent).status || "空闲"],
    ["7步流程", unwrap(state.status.animationFlowCurrent).status || unwrap(state.status.animationFlowRuns).items?.[0]?.status || "空闲"],
  ];
});
const diagnosticCards = computed(() => buildDiagnosticCards(state.status.agentDiagnose || state.status.agentWork));
const p4Display = computed(() => buildP4Display(state.p4Result || unwrap(state.status.p4)));
const activeAction = computed(() => {
  const keys = Object.keys(state.action.busy);
  if (!keys.length) return null;
  const elapsed = state.action.startedMs ? Math.max(0, Math.round((state.action.nowMs - state.action.startedMs) / 1000)) : 0;
  const progress = Math.min(92, 18 + elapsed * 3);
  return {
    label: state.action.current || Object.values(state.action.busy)[0] || "后端处理中",
    elapsed,
    progress,
    count: keys.length,
  };
});
const selectedFlowStep = computed(() => {
  if (state.flowStepConfigIndex === null || state.flowStepConfigIndex === undefined || state.flowStepConfigIndex === "") return null;
  const index = Number(state.flowStepConfigIndex);
  return Number.isInteger(index) && state.flowSteps[index] ? state.flowSteps[index] : null;
});
const flowModules = computed(() => [
  { key: "animation_flow.start", title: "完整 7 步流程", subtitle: "飞书到 Unity 导入再到 P4 Shelve-only", module: "animationFlow" },
  { key: "animation_flow.status", title: "7 步流程进度", subtitle: "查看当前完整流程、子任务和 CL 信息", module: "animationFlow" },
  { key: "feishu_table.export_json", title: "飞书表格读取", subtitle: "先把飞书表单/多维表格导出成 JSON", module: "feishu_table" },
  { key: "frame.run_preview", title: "飞书下载预检", subtitle: "确认下载目录、抽帧目录和参数", module: "frame" },
  { key: "frame.run_start", title: "视频下载 + 抽帧", subtitle: "读取飞书视频附件、下载、抽帧、去重", module: "frame" },
  { key: "frame.run_status", title: "抽帧进度检查", subtitle: "查看下载/抽帧当前记录和最近日志", module: "frame" },
  { key: "comfyui.run_start", title: "ComfyUI 抠图", subtitle: "批量送入工作流，输出 matte", module: "comfyui" },
  { key: "comfyui.run_status", title: "ComfyUI 进度检查", subtitle: "查看当前 ComfyUI 批处理状态", module: "comfyui" },
  { key: "cherry.run_start", title: "Cherry 平滑", subtitle: "时序平滑、缩放、锐化", module: "cherry" },
  { key: "cherry.run_status", title: "Cherry 进度检查", subtitle: "查看平滑任务进度和最近错误", module: "cherry" },
  { key: "unity_ready.build", title: "unity_ready 整理", subtitle: "生成 scene/emoji JSON 和 frames", module: "unityReady" },
  { key: "unity_import.run", title: "Unity 导入引擎", subtitle: "调用插件 API 导入 unity_ready", module: "unityImport" },
  { key: "pipeline.run_start", title: "旧三步流程", subtitle: "只跑抽帧到平滑，不含 Unity/P4", module: "pipeline" },
  { key: "p4.shelve_ui_import", title: "P4 Shelve", subtitle: "安全检查、预览、创建 CL、reconcile、shelve 和报告", module: "p4" },
  { key: "speech.transcribe", title: "ASR 语音识别", subtitle: "把音频转成文字指令", module: "speech" },
  { key: "speech.synthesize", title: "TTS 语音合成", subtitle: "把流程结果合成为语音", module: "speech" },
  { key: "memory.context_pack", title: "记忆 / RAG", subtitle: "读取对话上下文和记忆包", module: "memory" },
]);
const flowStepStats = computed(() => {
  const enabled = state.flowSteps.filter((step) => step.enabled !== false).length;
  return [
    ["步骤", state.flowSteps.length],
    ["启用", enabled],
    ["变量", state.flowVars.length],
    ["状态", state.flowBusy ? "处理中" : "就绪"],
  ];
});
const pipelineSteps = computed(() => buildPipelineSteps());
const customPipelineRun = computed(() => {
  const current = unwrap(state.status.customPipelineCurrent);
  if (current?.run_id) return current;
  return unwrap(state.status.customPipelineRuns).items?.[0] || {};
});
const customFlowStepCards = computed(() => buildCustomFlowStepCards(customPipelineRun.value));
const pipelineRuns = computed(() => tasks.value.filter((task) => task.module.includes("流程") || task.module.includes("抽帧") || task.module.includes("ComfyUI") || task.module.includes("Cherry") || task.module.includes("Unity")));
const memorySummary = computed(() => buildMemorySummary());

const commands = [
  { id: "status", title: "问 Agent：总状态", hint: "按模块汇总所有任务", keys: "状态 任务 agent", run: () => sendPrompt("列出当前所有任务状态，按 ComfyUI、Cherry、抽帧、Pipeline、自定义流程分组说明，并指出可以暂停或终止的任务。") },
  { id: "diagnose", title: "运行后端诊断", hint: "检查 Agent / ComfyUI / P4 / 流程", keys: "诊断 后端 p4 comfy", run: runDiagnose },
  { id: "builder", title: "打开流程编排器", hint: "自定义步骤、参数、路径", keys: "流程 编排", run: () => switchView("builder") },
  { id: "path", title: "打开路径浏览器", hint: "从本机允许目录选择路径", keys: "路径 文件 浏览", run: () => openPathPicker({ target: "scratch", mode: "dir", current: "E:/" }) },
  { id: "logs", title: "查看操作日志", hint: "Agent 对话和技能调用", keys: "日志 log", run: () => switchView("logs") },
  { id: "refresh", title: "刷新状态", hint: "拉取最新后端快照", keys: "刷新", run: () => refreshAll(true) },
];

const p4Actions = [
  { kind: "status", skill: "p4.status", title: "读取状态", subtitle: "查看 P4PORT / P4USER / P4CLIENT / root / stream / 登录状态。" },
  { kind: "check", skill: "p4.check", title: "安全检查", subtitle: "检查登录、workspace、白名单、opened 文件和 shelve-only 边界。" },
  { kind: "preview", skill: "p4.preview", title: "预览改动", subtitle: "只对 UI 白名单目录执行 reconcile -n，不改变 P4 状态。" },
  { kind: "create_cl", skill: "p4.create_cl", title: "创建 CL", subtitle: "创建 pending changelist，描述会自动补 Shelve-only 信息。", needsDescription: true },
  { kind: "reconcile", skill: "p4.reconcile", title: "放入 CL", subtitle: "把白名单 UI 目录的 add/edit/delete reconcile 到指定 CL。", needsCl: true, confirm: "确认执行 P4 reconcile？这会打开白名单目录内的文件到指定 CL。" },
  { kind: "shelve", skill: "p4.shelve", title: "Shelve", subtitle: "安全检查后 shelve 指定 CL；不会 submit。", needsCl: true, confirm: "确认 Shelve 这个 CL？不会 submit，但会更新 shelf。" },
  { kind: "report", skill: "p4.report", title: "生成报告", subtitle: "生成可以复制到飞书的 shelf / 文件统计 / 安全检查报告。", needsCl: true },
  { kind: "shelve_ui_import", skill: "p4.shelve_ui_import", title: "一键导入 Shelve", subtitle: "check -> preview -> create CL -> reconcile -> shelve -> report。", needsDescription: true, confirm: "确认一键执行 UI 导入 Shelve？这会创建 CL、reconcile 并 shelve。" },
];
const p4PrimaryKinds = new Set(["status", "check", "preview", "shelve_ui_import"]);
const p4PrimaryActions = computed(() => p4Actions.filter((item) => p4PrimaryKinds.has(item.kind)));
const p4AdvancedActions = computed(() => p4Actions.filter((item) => !p4PrimaryKinds.has(item.kind)));

function f(key, label, type, value, help, pathMode = "") {
  return { key, label, type, value, help, pathMode };
}

function localDateStamp(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function todayStamp() {
  return localDateStamp();
}

function pathInWorkspace(name = "") {
  return name ? `${DEFAULT_WORKSPACE_ROOT}/${name}` : DEFAULT_WORKSPACE_ROOT;
}

function workspacePath(name = "") {
  const root = state?.workspaceRoot || DEFAULT_WORKSPACE_ROOT;
  return name ? `${root.replace(/[\\/]$/, "")}/${name}` : root;
}

function applyWorkspaceRoot(root) {
  const normalized = String(root || DEFAULT_WORKSPACE_ROOT).replaceAll("\\", "/").replace(/[\\/]$/, "");
  state.workspaceRoot = normalized;
  localStorage.setItem("assetclaw.workspaceRoot", normalized);
  syncWorkspaceVariables(normalized);
  toast(`工作区已切换：${normalized}`, "ok");
}

function syncWorkspaceVariables(root = state.workspaceRoot) {
  const normalized = String(root || DEFAULT_WORKSPACE_ROOT).replaceAll("\\", "/").replace(/[\\/]$/, "");
  const mapping = {
    workspace_root: normalized,
    videos: `${normalized}/videos`,
    frames: `${normalized}/frames`,
    matte: `${normalized}/matte`,
    smooth: `${normalized}/smooth`,
  };
  for (const item of state.flowVars) {
    if (mapping[item.key]) item.value = mapping[item.key];
  }
  state.flowVariables = JSON.stringify(mapping, null, 2);
}

function headers() {
  return state.token ? { "X-Skill-Token": state.token } : {};
}

function stableStringify(value) {
  if (!value || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map((item) => stableStringify(item)).join(",")}]`;
  return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(",")}}`;
}

function clonePayload(value) {
  if (value === undefined) return value;
  try {
    return structuredClone(value);
  } catch {
    return JSON.parse(JSON.stringify(value));
  }
}

function requestCacheKey(path, fetchOptions) {
  const method = String(fetchOptions.method || "GET").toUpperCase();
  const body = typeof fetchOptions.body === "string" ? fetchOptions.body : stableStringify(fetchOptions.body || {});
  return `${method} ${path} ${body}`;
}

function clearFrontendCache() {
  responseCache.clear();
}

async function request(path, options = {}) {
  const { timeoutMs = 45000, cacheMs = 0, dedupe = true, ...fetchOptions } = options;
  const cacheKey = requestCacheKey(path, fetchOptions);
  const now = Date.now();
  if (cacheMs > 0) {
    const cached = responseCache.get(cacheKey);
    if (cached && now - cached.at < cacheMs) {
      return { ...clonePayload(cached.data), frontendCached: true };
    }
  }
  if (dedupe && inflightRequests.has(cacheKey)) {
    try {
      return clonePayload(await inflightRequests.get(cacheKey));
    } catch (error) {
      const message = error?.name === "AbortError" ? "请求超时：后端这次没有在限定时间内返回。" : String(error?.message || error);
      return { ok: false, offline: true, error: message };
    }
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const promise = (async () => {
    const response = await fetch(path, {
      ...fetchOptions,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...(fetchOptions.headers || {}) },
    });
    const text = await response.text();
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      data = { ok: false, error: text || response.statusText };
    }
    if (!response.ok) return { ok: false, status: response.status, ...data };
    return data;
  })();
  if (dedupe) inflightRequests.set(cacheKey, promise);
  try {
    const payload = await promise;
    if (cacheMs > 0 && payload?.ok !== false) {
      responseCache.set(cacheKey, { at: Date.now(), data: clonePayload(payload) });
      if (responseCache.size > 160) responseCache.delete(responseCache.keys().next().value);
    }
    return payload;
  } catch (error) {
    const message = error?.name === "AbortError" ? "请求超时：后端这次没有在限定时间内返回。" : String(error?.message || error);
    return { ok: false, offline: true, error: message };
  } finally {
    clearTimeout(timer);
    if (dedupe) inflightRequests.delete(cacheKey);
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isReadonlySkill(skill) {
  return /(\.status|\.run_list|\.queue_status|\.diagnose|\.current_work|\.list_|\.get_|manifest$|module_catalog|context_pack)/.test(skill)
    || skill.startsWith("system.")
    || skill === "animation.status"
    || skill === "p4.status";
}

async function skillCall(skill, args = {}, options = {}) {
  const readonly = isReadonlySkill(skill);
  return request("/api/skills/call", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ skill, arguments: args, requested_by: "external_webui_vue3" }),
    timeoutMs: options.timeoutMs || 45000,
    cacheMs: options.cacheMs ?? (readonly ? FRONTEND_CACHE_MS : 0),
    dedupe: options.dedupe ?? readonly,
  });
}

async function adminCleanup(target, options = {}) {
  clearFrontendCache();
  const payload = await request("/api/admin/cleanup", {
    method: "POST",
    body: JSON.stringify({
      target,
      conversation_id: options.conversation_id || state.conversationId || "test",
      scope: options.scope || state.memoryScope || "global",
    }),
  });
  if (payload.ok === false) {
    toast(payload.error || "清理失败", "bad");
    return payload;
  }
  toast(`已清理 ${payload.deleted ?? 0} 条记录`, "ok");
  refreshInBackground(true);
  return payload;
}

async function cleanupWithConfirm(target, label, options = {}) {
  if (!confirm(`确认清理${label}？只删除后端记录，不删除实际产物文件。`)) return null;
  return adminCleanup(target, options);
}

function unwrap(result) {
  return result?.result?.result || result?.result || result || {};
}

function compact(value, fallback = "-") {
  if (value === undefined || value === null || value === "") return fallback;
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) return String(value.length);
  if (typeof value === "object") return value.error || value.status || value.run_id || JSON.stringify(value).slice(0, 100);
  return String(value);
}

function parseDate(value) {
  const date = new Date(value || "");
  return Number.isNaN(date.getTime()) ? null : date;
}

function taskTime(item) {
  return item.started_at || item.created_at || item.updated_at || item.finished_at || item.completed_at || "";
}

function formatTaskDay(value) {
  const date = parseDate(value);
  if (!date) return "时间未知";
  return date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

function formatTaskClock(value) {
  const date = parseDate(value);
  if (!date) return "--:--";
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatTaskFullTime(value) {
  const date = parseDate(value);
  if (!date) return "后端未返回时间";
  return date.toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function statusClass(status) {
  const lower = String(status || "").toLowerCase();
  if (lower.includes("fail") || lower.includes("error") || lower.includes("cancel") || lower.includes("offline")) return "bad";
  if (lower.includes("run") || lower.includes("queue") || lower.includes("pending") || lower.includes("waiting") || lower.includes("pause")) return "warn";
  return "ok";
}

function toast(message, type = "info") {
  const id = Math.random().toString(16).slice(2);
  state.toasts.push({ id, message, type });
  setTimeout(() => {
    const index = state.toasts.findIndex((item) => item.id === id);
    if (index >= 0) state.toasts.splice(index, 1);
  }, 2800);
}

function isBusy(key) {
  return Boolean(state.action.busy[key]);
}

function beginAction(key, label) {
  state.action.busy[key] = label;
  state.action.current = label;
  state.action.startedAt = new Date().toLocaleTimeString();
  state.action.startedMs = Date.now();
  state.action.nowMs = state.action.startedMs;
}

function endAction(key, last = "") {
  delete state.action.busy[key];
  const next = Object.values(state.action.busy)[0] || "";
  state.action.current = next;
  if (!next) {
    state.action.startedAt = "";
    state.action.startedMs = 0;
  } else {
    state.action.startedMs = Date.now();
    state.action.nowMs = state.action.startedMs;
  }
  if (last) state.action.last = last;
}

async function runWithFeedback(key, label, worker) {
  if (isBusy(key)) {
    toast(`${label} 已经在处理中`, "info");
    return null;
  }
  beginAction(key, label);
  toast(`${label}：已提交给后端`, "info");
  try {
    return await worker();
  } catch (error) {
    const message = String(error?.message || error);
    toast(`${label} 失败：${message}`, "bad");
    return { ok: false, error: message };
  } finally {
    endAction(key, `${label} 已结束`);
  }
}

function refreshInBackground(includeLazy = false) {
  setTimeout(() => {
    refreshAll(includeLazy).catch((error) => toast(`后台刷新失败：${String(error?.message || error)}`, "bad"));
  }, 200);
}

function switchView(view) {
  state.view = view;
  state.queueVisibleLimit = VISIBLE_TASK_LIMIT;
  state.logVisibleLimit = VISIBLE_LOG_LIMIT;
  refreshInBackground(false);
  if (view === "skills") loadSkills();
  if (view === "logs") loadLogs();
  if (view === "memory") loadMemory();
  if (view === "builder") {
    loadFlowDefinitions();
  }
}

async function runLimited(items, limit, worker) {
  const results = new Array(items.length);
  let index = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (index < items.length) {
      const current = index++;
      results[current] = await worker(items[current], current);
    }
  });
  await Promise.all(workers);
  return results;
}

function statusCallsForCurrentView(includeLazy = false) {
  if (includeLazy) return statusCalls;
  const common = ["gpu", "agentWork"];
  const byView = {
    chat: ["comfyCurrent", "cherryCurrent", "animationFlowCurrent", "customPipelineCurrent"],
    overview: ["comfyCurrent", "cherryCurrent", "frameCurrent", "animationFlowCurrent", "unityReady", "unityImport", "customPipelineCurrent", "animation"],
    queues: ["comfyRuns", "cherryRuns", "frameRuns", "animationFlowRuns", "pipelineRuns", "customPipelineRuns"],
    pipeline: ["frameCurrent", "animationFlowCurrent", "animationFlowRuns", "unityReady", "unityImport", "pipelineRuns", "customPipelineCurrent", "customPipelineRuns", "animation"],
    builder: ["animationFlowCurrent", "animationFlowRuns", "unityReady", "unityImport", "customPipelineCurrent", "customPipelineRuns", "frameCurrent", "comfyCurrent", "cherryCurrent", "p4"],
    voice: [],
    p4: ["p4"],
    memory: [],
    skills: [],
    logs: [],
  };
  const keys = new Set([...common, ...(byView[state.view] || [])]);
  return statusCalls.filter((item) => keys.has(item.key) && !item.lazy);
}

async function refreshAll(includeLazy = false) {
  if (state.refreshing) return;
  state.refreshing = true;
  state.connection.checking = !state.connection.stableOnline;
  state.connection.lastAttemptAt = new Date().toLocaleTimeString();
  try {
    state.lastRefreshAt = new Date().toLocaleTimeString();
    const health = await request("/api/health", { timeoutMs: HEALTH_TIMEOUT_MS });
    if (health.ok) {
      markHealthOk(health);
      const calls = statusCallsForCurrentView(includeLazy);
      const results = await runLimited(calls, MAX_STATUS_PARALLEL, (call) => (
        skillCall(call.skill, call.args, {
          cacheMs: includeLazy ? 1200 : FRONTEND_CACHE_MS,
          timeoutMs: call.lazy ? 45000 : 25000,
        }).catch((error) => ({ ok: false, error: String(error) }))
      ));
      calls.forEach((call, index) => assignStatus(call.key, results[index], includeLazy));
      persistStatusCache();
      const failedSkills = results.filter((item) => item?.ok === false).length;
      if (includeLazy && failedSkills) toast(`后端在线，但有 ${failedSkills} 个状态接口暂时不可用。`, "info");
    } else {
      markHealthFailure(health, includeLazy);
    }
  } finally {
    state.connection.checking = false;
    state.refreshing = false;
  }
}

function assignStatus(key, payload, includeLazy = false) {
  const previous = state.status[key];
  if (shouldKeepPreviousStatus(key, payload, previous, includeLazy)) {
    state.statusUpdatedAt[key] ||= "缓存";
    return;
  }
  state.status[key] = payload;
  state.statusUpdatedAt[key] = new Date().toLocaleTimeString();
}

function shouldKeepPreviousStatus(key, payload, previous, includeLazy = false) {
  if (!previous) return false;
  const next = unwrap(payload);
  const prev = unwrap(previous);
  if (payload?.ok === false || next?.error) return true;
  if (key.endsWith("Runs") && Array.isArray(prev.items) && prev.items.length && Array.isArray(next.items) && !next.items.length && !includeLazy) return true;
  if (key === "animation" && prev.counts && next.counts) {
    const prevTotal = Object.values(prev.counts).reduce((sum, value) => sum + Number(value || 0), 0);
    const nextTotal = Object.values(next.counts).reduce((sum, value) => sum + Number(value || 0), 0);
    if (prevTotal > 0 && nextTotal === 0 && !includeLazy) return true;
  }
  return false;
}

function persistStatusCache() {
  const payload = { status: state.status, statusUpdatedAt: state.statusUpdatedAt, savedAt: new Date().toISOString() };
  localStorage.setItem(STATUS_CACHE_KEY, JSON.stringify(payload));
}

function loadStatusCache() {
  try {
    const saved = JSON.parse(localStorage.getItem(STATUS_CACHE_KEY) || "{}");
    if (saved.status && typeof saved.status === "object") {
      state.status = saved.status;
      state.statusUpdatedAt = saved.statusUpdatedAt || {};
      state.lastRefreshAt = saved.savedAt ? `缓存 ${new Date(saved.savedAt).toLocaleTimeString()}` : "缓存";
    }
  } catch {
    localStorage.removeItem(STATUS_CACHE_KEY);
  }
}

async function loadSkills() {
  const payload = await request("/api/skills/manifest", { cacheMs: 10_000, dedupe: true, timeoutMs: 20000 });
  if (payload.ok === false) {
    state.skills = [];
    return;
  }
  state.skills = payload.skills || [];
}

function markHealthOk(health) {
  const now = new Date();
  state.health = health;
  state.healthError = "";
  state.connection.stableOnline = true;
  state.connection.checking = false;
  state.connection.consecutiveFailures = 0;
  state.connection.lastError = "";
  state.connection.lastOkMs = now.getTime();
  state.connection.lastOkAt = now.toLocaleTimeString();
}

function markHealthFailure(health, includeLazy = false) {
  const error = health?.error || health?.detail || "健康检查失败";
  const now = Date.now();
  state.healthError = error;
  state.connection.checking = false;
  state.connection.consecutiveFailures += 1;
  state.connection.lastError = error;

  const neverConnected = !state.connection.lastOkMs;
  const graceExpired = now - state.connection.lastOkMs > HEALTH_GRACE_MS;
  const failedEnough = state.connection.consecutiveFailures >= HEALTH_FAILURES_BEFORE_OFFLINE;

  if (neverConnected || (failedEnough && graceExpired)) {
    state.connection.stableOnline = false;
    state.health = { ok: false, error };
    if (includeLazy) toast(`后端连续不可达：${error}`, "bad");
    return;
  }

  if (includeLazy) {
    toast(`本次同步失败，但后端刚刚在线：${error}`, "info");
  }
}

async function sendPrompt(text) {
  switchView("chat");
  await nextTick();
  state.input = text;
  return sendChat();
}

function persistChat() {
  localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(state.messages.slice(-120)));
}

function loadChatHistory() {
  try {
    const saved = JSON.parse(localStorage.getItem(CHAT_STORAGE_KEY) || "[]");
    if (Array.isArray(saved) && saved.length) {
      state.messages = saved;
      return;
    }
  } catch {
    localStorage.removeItem(CHAT_STORAGE_KEY);
  }
  state.messages = [DEFAULT_SYSTEM_MESSAGE];
  persistChat();
}

function pushMessage(role, text, extra = {}) {
  const message = { role, text, at: new Date().toLocaleTimeString(), ...extra };
  state.messages.push(message);
  persistChat();
  nextTick(() => document.querySelector(".messages")?.scrollTo({ top: document.querySelector(".messages")?.scrollHeight || 0, behavior: "smooth" }));
  return message;
}

function updateMessage(message, patch) {
  Object.assign(message, patch);
  persistChat();
  nextTick(() => document.querySelector(".messages")?.scrollTo({ top: document.querySelector(".messages")?.scrollHeight || 0, behavior: "smooth" }));
}

function clearChat() {
  if (!confirm("确认清空 WebUI 本地对话记录？这不会删除后端数据库或飞书记录。")) return;
  state.messages = [DEFAULT_SYSTEM_MESSAGE];
  persistChat();
  toast("本地对话已清空", "ok");
}

function extractAgentText(payload) {
  if (!payload) return "";
  if (typeof payload === "string") return payload;
  const rawToolJson = payload.raw?.llm_tool_json;
  if (typeof rawToolJson === "string") {
    try {
      const parsed = JSON.parse(rawToolJson);
      if (parsed?.type === "final" && parsed?.content) return String(parsed.content);
      if (parsed?.content) return String(parsed.content);
    } catch {
      // Keep the normal fallback path if the raw field is redacted or not JSON.
    }
  }
  return payload.text || payload.reply || payload.message || payload.response || payload.result?.text || payload.result?.reply || payload.result?.message || "";
}

async function waitForBrainJob(jobId, pendingMessage) {
  const startedAt = Date.now();
  let lastStatus = "";
  while (Date.now() - startedAt < 30 * 60 * 1000) {
    const job = await request(`/api/brain/jobs/${encodeURIComponent(jobId)}`, { timeoutMs: 30000 });
    const status = String(job.status || "");
    if (job.ok === false && status !== "FAILED") {
      updateMessage(pendingMessage, { text: `后台队列查询失败：${job.error || job.detail || "未知错误"}` });
      await sleep(2000);
      continue;
    }
    if (status !== lastStatus || job.last_progress) {
      const position = job.position ? `，前面还有 ${job.position - 1} 条` : "";
      const progress = job.last_progress ? `\n${job.last_progress}` : "";
      updateMessage(pendingMessage, { text: status === "QUEUED" ? `已进入后台队列${position}，不用等页面卡住。${progress}` : `后台正在处理这条消息，长任务会继续更新状态。${progress}` });
      lastStatus = status;
    }
    if (status === "DONE") return job;
    if (status === "FAILED") throw new Error(job.error || "后台任务失败");
    await sleep(status === "QUEUED" ? 1500 : 2200);
  }
  throw new Error("后台任务超过 30 分钟仍未结束，请到日志页查看。");
}

function onComposerKeydown(event) {
  if (event.key !== "Enter" || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return;
  event.preventDefault();
  sendChat();
}

async function sendChat() {
  const text = state.input.trim();
  if (!text || state.sending) return;
  let attachments = [];
  try {
    attachments = await prepareAttachmentsForBrain();
  } catch (error) {
    toast(String(error?.message || error), "bad");
    return;
  }
  const attachmentNote = attachments.length
    ? `\n\n[WebUI 附件已上传到本机后端：${attachments.map((file) => file.name).join(", ")}。]`
    : "";
  pushMessage("user", text);
  const pendingMessage = pushMessage("agent", "正在理解你的指令，准备调用本机 Agent...", { pending: true });
  state.input = "";
  state.sending = true;
  toast("已发送给 Agent，正在等后端回复", "info");
  const stages = [
    "已送达本机 Agent，正在排队进入大模型...",
    "大模型正在理解你的指令。如果需要调用技能，后端会继续执行。",
    "仍在处理中：这通常表示模型在思考或技能调用还没返回，不是前端卡住。",
  ];
  let stageIndex = 0;
  const stageTimer = setInterval(() => {
    if (!pendingMessage.pending) return;
    updateMessage(pendingMessage, { text: stages[Math.min(stageIndex, stages.length - 1)] });
    stageIndex += 1;
  }, 5000);
  try {
    const payload = await request("/api/brain/test", {
      method: "POST",
      timeoutMs: 30000,
      body: JSON.stringify({ text: `${text}${attachmentNote}`, conversation_id: state.conversationId || "test", source: "external_webui", attachments }),
    });
    if (payload.ok === false) {
      updateMessage(pendingMessage, {
        pending: false,
        text: `这条消息没有成功进入后端：${payload.error || payload.detail || "未知错误"}\n\n如果飞书正常而这里异常，优先检查 WebUI 代理端口和 /api/brain/test 路由。`,
      });
      toast("Agent 返回异常", "bad");
      return;
    }
    if (payload.queued && payload.job_id) clearInterval(stageTimer);
    const finalPayload = payload.queued && payload.job_id ? (await waitForBrainJob(payload.job_id, pendingMessage)).response : payload;
    updateMessage(pendingMessage, {
      pending: false,
      text: extractAgentText(finalPayload) || "后端已收到，但这次没有返回可展示的自然语言。可以打开“日志”页确认 brain_messages 是否写入。",
    });
    state.attachments = [];
    setTimeout(() => refreshAll(true), 600);
  } catch (error) {
    updateMessage(pendingMessage, { pending: false, text: `处理被中断：${String(error?.message || error)}` });
  } finally {
    clearInterval(stageTimer);
    state.sending = false;
  }
}

function onAttach(event) {
  state.attachments = Array.from(event.target.files || []);
  toast(`已选择 ${state.attachments.length} 个附件`, "ok");
}

async function prepareAttachmentsForBrain() {
  const maxBytes = 25 * 1024 * 1024;
  const files = state.attachments.slice(0, 8);
  const result = [];
  for (const file of files) {
    if (file.size > maxBytes) {
      throw new Error(`${file.name} 超过 25MB。大视频请先放到本机目录，或继续用飞书附件触发后端下载。`);
    }
    const dataUrl = await readFileAsDataUrl(file);
    result.push({ name: file.name, type: file.type || "file", size: file.size, data_url: dataUrl });
  }
  return result;
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("附件读取失败"));
    reader.readAsDataURL(file);
  });
}

function taskProgress(item) {
  const total = Number(item.total || 0);
  const completed = Number(item.completed || 0);
  const failed = Number(item.failed || 0);
  if (!total) return 0;
  return Math.max(0, Math.min(100, ((completed + failed) / total) * 100));
}

function normalizeTasks() {
  const buckets = [
    ["ComfyUI", unwrap(state.status.comfyRuns).items || []],
    ["Cherry", unwrap(state.status.cherryRuns).items || []],
    ["抽帧", unwrap(state.status.frameRuns).items || []],
    ["完整7步流程", unwrap(state.status.animationFlowRuns).items || []],
    ["旧三步流程", unwrap(state.status.pipelineRuns).items || []],
    ["自定义流程", unwrap(state.status.customPipelineRuns).items || []],
  ];
  return buckets.flatMap(([module, items]) =>
    items.map((item) => ({
      module,
      id: item.run_id || item.id || "",
      status: item.status || "-",
      progress: taskProgress(item),
      input: item.date_root || item.input_dir || item.frame_output_dir || item.workspace_root || item.workflow_name || "-",
      output: item.unity_ready || item.output_dir || item.smooth_output_dir || item.matte_output_dir || "-",
      timeRaw: taskTime(item),
      day: formatTaskDay(taskTime(item)),
      clock: formatTaskClock(taskTime(item)),
      fullTime: formatTaskFullTime(taskTime(item)),
      updated_at: item.updated_at || item.created_at || "",
      raw: item,
    })),
  ).sort((a, b) => (Date.parse(b.timeRaw || "") || 0) - (Date.parse(a.timeRaw || "") || 0));
}

function taskActionSkills(module) {
  const key = String(module || "").toLowerCase();
  if (key.includes("comfy")) return { pause: "comfyui.run_pause", resume: "comfyui.run_resume", cancel: "comfyui.run_cancel" };
  if (key.includes("cherry")) return { cancel: "cherry.run_cancel" };
  if (key.includes("抽帧") || key.includes("frame")) return { cancel: "frame.run_cancel" };
  if (key.includes("完整7步") || key.includes("animation_flow")) return { cancel: "animation_flow.cancel" };
  if (key.includes("自定义")) return { cancel: "custom_pipeline.run_cancel" };
  if (key.includes("流程") || key.includes("pipeline")) return { cancel: "pipeline.run_cancel" };
  return {};
}

function isTaskDone(task) {
  return ["DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED", "ARCHIVED"].includes(String(task.status).toUpperCase());
}

async function runTaskAction(task, action) {
  const skill = taskActionSkills(task.module)[action];
  if (!skill) return toast(`${task.module} 暂未外露 ${action} 控制 API。`, "bad");
  if (action === "cancel" && !confirm(`确认终止 ${task.module} ${task.id || "当前/latest"}？`)) return;
  const args = task.id ? { run_id: task.id } : {};
  if (skill === "comfyui.run_cancel") args.interrupt_current = true;
  const payload = await runWithFeedback(`task:${skill}:${task.id || "latest"}`, `${task.module} ${action}`, () => skillCall(skill, args));
  if (!payload) return;
  state.detail = { title: skill, payload };
  toast(payload.ok === false ? "控制失败" : "控制指令已发送", payload.ok === false ? "bad" : "ok");
  refreshInBackground(true);
}

function openConfig(module) {
  const schema = modules[module];
  if (!schema) return;
  closeAllModals();
  const defaults = Object.fromEntries(schema.fields.map((field) => [field.key, field.value]));
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem(`assetclaw.moduleConfig.${module}`) || "{}");
  } catch {
    saved = {};
  }
  state.configModule = module;
  state.configValues = { ...defaults, ...saved };
  state.configResult = "";
}

function collectConfig() {
  const schema = modules[state.configModule];
  const result = {};
  for (const field of schema.fields) {
    const value = state.configValues[field.key];
    if (field.type === "number") result[field.key] = value === "" || value === undefined ? undefined : Number(value);
    else result[field.key] = value;
  }
  for (const key of Object.keys(result)) {
    if (result[key] === "" || result[key] === undefined || Number.isNaN(result[key])) delete result[key];
  }
  return result;
}

function saveConfig() {
  const payload = collectConfig();
  localStorage.setItem(`assetclaw.moduleConfig.${state.configModule}`, JSON.stringify(payload));
  state.configResult = "参数已保存到本机浏览器。点“预览”只检查参数，点“调用后端”才会真正启动模块。";
  toast("参数已保存", "ok");
  return payload;
}

async function callConfig(preview = true) {
  const schema = modules[state.configModule];
  const payload = saveConfig();
  const skill = preview ? schema.previewSkill : schema.startSkill;
  if (!preview && !confirm(`确认调用 ${skill}？`)) return;
  state.configResult = preview ? "正在把参数发给后端做预览..." : "正在把任务提交给后端，成功后可到“队列/流程”页看进度。";
  const result = await runWithFeedback(`config:${skill}`, `${schema.title}${preview ? "预览" : "启动"}`, () => skillCall(skill, payload));
  if (!result) return;
  state.configResult = formatSkillResult(skill, result);
  toast(result.ok === false ? "后端调用失败" : "后端已返回", result.ok === false ? "bad" : "ok");
  refreshInBackground(true);
}

async function openPathPicker({ target, mode = "dir", current = "" }) {
  state.detail = null;
  state.commandOpen = false;
  state.settingsOpen = false;
  const seed = normalizePathSeed(current);
  state.pathDialogLoading = true;
  state.pathDialogError = "";
  beginAction(`path:${target}`, mode === "file" ? "正在打开 Windows 文件选择器" : "正在打开 Windows 文件夹选择器");
  try {
    const payload = await request(`/api/local/path-dialog?mode=${encodeURIComponent(mode)}&current=${encodeURIComponent(current || seed)}`, { timeoutMs: 5 * 60 * 1000 });
    if (payload.ok && payload.path) {
      applySelectedPath(target, payload.path);
      toast(`已选择：${payload.path}`, "ok");
      return;
    }
    if (!payload.canceled) state.pathDialogError = payload.error || "系统选择器没有返回路径。";
  } finally {
    state.pathDialogLoading = false;
    endAction(`path:${target}`, "路径选择已结束");
  }
  openManualPathPicker({ target, mode, current: current || seed });
}

function openManualPathPicker({ target, mode = "dir", current = "" }) {
  const seed = normalizePathSeed(current);
  state.pathPicker = { target, mode, current: seed };
  state.pathManual = current || seed;
  state.pathItems = [];
  state.pathLoading = false;
}

function normalizePathSeed(value) {
  const raw = String(value || "").replaceAll("\\", "/");
  if (/^[A-Za-z]:\/?$/.test(raw)) return raw.endsWith("/") ? raw : `${raw}/`;
  if (raw && raw.includes("/")) return raw.split("/").slice(0, -1).join("/") || "E:/";
  return "E:/";
}

async function browsePath(path) {
  state.pathLoading = true;
  const payload = await skillCall("file.list_allowed", { path, max_items: 100 });
  const data = unwrap(payload);
  state.pathLoading = false;
  if (payload.ok === false || data.ok === false) {
    state.pathItems = [];
    toast(data.error || payload.error || "目录读取失败", "bad");
    return;
  }
  state.pathPicker.current = data.path || path;
  state.pathManual = data.path || path;
  state.pathItems = data.items || [];
}

function choosePath(item = null) {
  if (!state.pathPicker) return;
  applySelectedPath(state.pathPicker.target, item?.path || state.pathManual || state.pathPicker.current);
  state.pathPicker = null;
  state.pathItems = [];
  state.pathManual = "";
}

function applySelectedPath(target, selected) {
  if (target === "scratch") {
    state.detail = { title: "已选择路径", payload: { path: selected } };
  } else if (target.startsWith("config:")) {
    const key = target.split(":")[1];
    state.configValues[key] = selected;
  } else if (target.startsWith("voice:")) {
    const key = target.split(":")[1];
    state[key.split(".")[0]][key.split(".")[1]] = selected;
  } else if (target.startsWith("flowvar:")) {
    const index = Number(target.split(":")[1]);
    if (state.flowVars[index]) state.flowVars[index].value = selected;
  } else if (target.startsWith("flowstep:")) {
    const [, stepIndex, fieldKey] = target.split(":");
    const step = state.flowSteps[Number(stepIndex)];
    if (step) step.arguments[fieldKey] = selected;
  } else if (target.startsWith("quickFlow:")) {
    const key = target.split(":")[1];
    if (key in state.quickFlow) state.quickFlow[key] = selected;
  } else if (target === "workspaceRoot") {
    applyWorkspaceRoot(selected);
  }
}

function parentPath(path) {
  const normalized = String(path || "E:/").replaceAll("\\", "/").replace(/\/$/, "");
  if (/^[A-Za-z]:$/.test(normalized)) return `${normalized}/`;
  const parts = normalized.split("/");
  parts.pop();
  return parts.join("/") || "E:/";
}

function defaultFlowStep(skill = "frame.run_start") {
  const presets = {
    "feishu_table.export_json": { output_path: "${workspace_root}/feishu_table.json" },
    "frame.run_preview": { download_dir: "${videos}", export_dir: "${frames}", fps: 24, max_frames: 0, diff_threshold: 0.2, dedup_enabled: true, dedup_renumber: true },
    "frame.run_start": { download_dir: "${videos}", export_dir: "${frames}", fps: 24, max_frames: 0, diff_threshold: 0.2, dedup_enabled: true, dedup_renumber: true },
    "frame.run_status": {},
    "comfyui.run_start": { input_dir: "${frames}", output_dir: "${matte}", recursive: true, preserve_structure: true, skip_existing: true, max_images: 50000 },
    "comfyui.run_status": { include_gpu: false },
    "cherry.run_start": { input_dir: "${matte}", output_dir: "${smooth}", recursive: true, skip_existing: true, max_images: 50000, use_smooth: true, smooth_window: 5, smooth_sigma: 1.0 },
    "cherry.run_status": { include_gpu: false },
    "unity_ready.preview": { date_root: "${workspace_root}", copy_mode: "copy" },
    "unity_ready.build": { date_root: "${workspace_root}", overwrite: true, copy_mode: "copy" },
    "unity_ready.status": { date_root: "${workspace_root}" },
    "unity_import.preview": { unity_ready: "${workspace_root}/unity_ready", unity_project: "D:/Spark/Client", package: "both" },
    "unity_import.run": { unity_ready: "${workspace_root}/unity_ready", unity_project: "D:/Spark/Client", package: "both" },
    "unity_import.status": { unity_ready: "${workspace_root}/unity_ready", unity_project: "D:/Spark/Client", package: "both" },
    "animation_flow.preview": { date_root: "${workspace_root}", unity_project: "D:/Spark/Client", p4_stream: "//streams/rel_0.0.1", package: "both" },
    "animation_flow.start": { date_root: "${workspace_root}", unity_project: "D:/Spark/Client", p4_stream: "//streams/rel_0.0.1", package: "both", fps: 24, notify_interval_seconds: 60, allow_p4_writes: true },
    "animation_flow.status": {},
    "animation_flow.list": { limit: 10, include_finished: true },
    "pipeline.run_start": { input_dir: "${videos}", frame_output_dir: "${frames}", matte_output_dir: "${matte}", smooth_output_dir: "${smooth}", fps: 24 },
    "p4.shelve_ui_import": { desc: "[UI Emoji Import] 动画资源导入 - Shelve-only", yes: true },
    "speech.synthesize": { text: "流程已经完成。", engine: "auto" },
    "memory.context_pack": { conversation_id: "test", recent_limit: 12, max_chars: 6000 },
  };
  return {
    id: `step_${state.flowSteps.length + 1}`,
    name: flowModuleMeta(skill).title,
    skill,
    enabled: true,
    arguments: { ...(presets[skill] || {}) },
    expanded: false,
  };
}

function ensureFlowSteps() {
  if (!state.flowSteps.length) {
    state.flowSteps.push(
      defaultFlowStep("animation_flow.start"),
      defaultFlowStep("unity_ready.build"),
      defaultFlowStep("unity_import.run"),
      defaultFlowStep("frame.run_start"),
      defaultFlowStep("comfyui.run_start"),
      defaultFlowStep("cherry.run_start"),
    );
  }
}

function addFlowStep(skill = "frame.run_start") {
  state.flowSteps.push(defaultFlowStep(skill));
  toast(`已添加：${flowModuleMeta(skill).title}`, "ok");
}

function removeFlowStep(index) {
  state.flowSteps.splice(index, 1);
  if (state.flowStepConfigIndex === index) state.flowStepConfigIndex = null;
}

function moveFlowStep(index, direction) {
  const next = direction === "up" ? index - 1 : index + 1;
  if (next < 0 || next >= state.flowSteps.length) return;
  [state.flowSteps[index], state.flowSteps[next]] = [state.flowSteps[next], state.flowSteps[index]];
}

function onFlowSkillChange(step) {
  const next = defaultFlowStep(step.skill);
  step.name = next.name;
  step.arguments = next.arguments;
}

function findFlowStep(skill) {
  return state.flowSteps.find((step) => step.skill === skill);
}

function applyQuickFlowToSteps() {
  ensureFlowSteps();
  syncWorkspaceVariables();
  state.flowName = state.flowName || "animation_custom";
  state.flowDescription = "完整 7 步动画自动化：飞书下载 -> 抽帧 -> 抠图 -> Cherry -> unity_ready -> Unity 导入 -> P4 Shelve-only";
  const flow = findFlowStep("animation_flow.start");
  if (flow) {
    flow.arguments.date_root = "${workspace_root}";
    flow.arguments.workflow_path = state.quickFlow.workflowPath.trim();
    flow.arguments.unity_project = state.quickFlow.unityProject.trim() || "D:/Spark/Client";
    flow.arguments.p4_stream = state.quickFlow.p4Stream.trim() || "//streams/rel_0.0.1";
    flow.arguments.package = state.quickFlow.package.trim() || "both";
    flow.arguments.fps = Number(state.quickFlow.fps || 24);
    flow.arguments.notify_interval_seconds = Number(state.quickFlow.notifyIntervalSeconds || 60);
    flow.arguments.allow_p4_writes = Boolean(state.quickFlow.allowP4Writes);
  }
  const comfy = findFlowStep("comfyui.run_start");
  if (comfy) {
    comfy.arguments.workflow_path = state.quickFlow.workflowPath.trim();
    comfy.arguments.input_dir ||= "${frames}";
    comfy.arguments.output_dir ||= "${matte}";
  }
}

function openFlowStepConfig(index) {
  const step = state.flowSteps[index];
  if (!step) return;
  closeAllModals();
  state.flowStepConfigIndex = index;
}

function closeFlowStepConfig() {
  state.flowStepConfigIndex = null;
}

function closeAllModals() {
  state.configModule = "";
  state.commandOpen = false;
  state.pathPicker = null;
  state.settingsOpen = false;
  state.detail = null;
  state.flowStepConfigIndex = null;
  state.pathDialogError = "";
}

function stepArgumentSummary(step) {
  const args = step.arguments || {};
  const keys = Object.keys(args).filter((key) => args[key] !== "" && args[key] !== undefined);
  if (!keys.length) return ["使用模块默认参数"];
  return keys.slice(0, 4).map((key) => `${key}: ${compact(args[key])}`).concat(keys.length > 4 ? [`另有 ${keys.length - 4} 项`] : []);
}

function flowModuleMeta(skill) {
  return flowModules.value.find((item) => item.key === skill) || { key: skill, title: skill, subtitle: "自定义后端步骤", module: "" };
}

function flowStepFields(step) {
  if (step.skill === "feishu_table.export_json") {
    return [
      f("table_url", "飞书表格链接", "text", "", "不填则使用后端 feishu_frame_tool/config.json 里的默认表格。"),
      f("output_path", "导出 JSON 路径", "path", "${workspace_root}/feishu_table.json", "建议放在当天工作区根目录。", "file"),
      f("config_path", "飞书工具配置", "path", "", "通常留空，让后端使用默认配置。", "file"),
    ];
  }
  if (step.skill.endsWith(".run_status")) {
    return [
      f("run_id", "Run ID", "text", "", "留空表示查看当前/最新任务。"),
      f("include_gpu", "附带 GPU 信息", "checkbox", false, "只有 ComfyUI/Cherry 等模块需要时再打开。"),
    ];
  }
  const moduleKey = flowModuleMeta(step.skill).module;
  if (modules[moduleKey]) return modules[moduleKey].fields;
  if (step.skill === "speech.transcribe") {
    return [
      f("audio_path", "音频路径", "path", "", "选择需要识别的本机音频。", "file"),
      f("language", "语言", "text", "zh", "中文语音默认 zh。"),
      f("prompt", "提示词", "text", "动画流程语音指令", "给 ASR 的上下文提示。"),
    ];
  }
  if (step.skill === "speech.synthesize") {
    return [
      f("text", "合成文本", "text", "流程已经完成。", "TTS 要读出的文字。"),
      f("output_path", "输出目录", "path", "", "语音文件写到这里。", "dir"),
      f("engine", "引擎", "text", "auto", "auto / indextts / edge_tts。"),
      f("rate", "语速", "text", "", "例如 +0%，不填使用默认。"),
    ];
  }
  if (step.skill === "memory.context_pack") {
    return [
      f("conversation_id", "会话 ID", "text", "test", "默认读取 test 会话。"),
      f("recent_limit", "最近消息数", "number", 12, "一般 8-20。"),
      f("max_chars", "最大字符数", "number", 6000, "控制塞入上下文的长度。"),
    ];
  }
  if (step.skill === "p4.shelve_ui_import") {
    return [
      f("desc", "CL 描述", "text", state.quickFlow.p4Description, "会用于创建 Shelve-only CL。"),
      f("workflow", "Workflow", "text", "", "通常留空，使用后端默认。"),
      f("workspace", "Workspace", "text", "", "通常留空，使用后端默认 P4CLIENT。"),
      f("cl", "已有 CL", "text", "", "留空则一键流程自动创建。"),
      f("force", "覆盖已有 Shelf", "checkbox", false, "仅需要覆盖 shelf 时开启。"),
      f("yes", "确认一键 Shelve", "checkbox", true, "必须开启，后端才会执行 shelve_ui_import。"),
    ];
  }
  return [];
}

function cleanStepArguments(step) {
  const result = {};
  const fields = flowStepFields(step);
  for (const field of fields) {
    const value = step.arguments?.[field.key];
    if (value === "" || value === undefined || Number.isNaN(value)) continue;
    result[field.key] = field.type === "number" ? Number(value) : value;
  }
  for (const [key, value] of Object.entries(step.arguments || {})) {
    if (!(key in result) && value !== "" && value !== undefined) result[key] = value;
  }
  return result;
}

function flowVariablesObject() {
  return Object.fromEntries(state.flowVars.filter((item) => item.key).map((item) => [item.key, item.value]));
}

function collectFlowDefinition() {
  applyQuickFlowToSteps();
  const variables = flowVariablesObject();
  const steps = [];
  for (const step of state.flowSteps) {
    steps.push({ id: step.id, name: step.name, skill: step.skill, enabled: step.enabled !== false, arguments: cleanStepArguments(step) });
  }
  return { name: state.flowName, description: state.flowDescription, variables, steps };
}

function collectAnimationFlowArgs() {
  const args = {
    date_root: workspacePath(),
    workflow_path: state.quickFlow.workflowPath.trim(),
    unity_project: state.quickFlow.unityProject.trim() || "D:/Spark/Client",
    p4_stream: state.quickFlow.p4Stream.trim() || "//streams/rel_0.0.1",
    package: state.quickFlow.package.trim() || "both",
    fps: Number(state.quickFlow.fps || 24),
    notify_interval_seconds: Number(state.quickFlow.notifyIntervalSeconds || 60),
    allow_p4_writes: Boolean(state.quickFlow.allowP4Writes),
  };
  if (state.p4Form.workflow.trim()) args.p4_workflow = state.p4Form.workflow.trim();
  if (state.p4Form.workspace.trim()) args.p4_workspace = state.p4Form.workspace.trim();
  Object.keys(args).forEach((key) => {
    if (args[key] === "" || args[key] === undefined || Number.isNaN(args[key])) delete args[key];
  });
  return args;
}

async function previewAnimationFlow() {
  await runFlowAction("animation_flow.preview", collectAnimationFlowArgs(), "7 步流程预览已生成");
}

async function runAnimationFlow() {
  const args = collectAnimationFlowArgs();
  const p4Text = args.allow_p4_writes ? "第 7 步会 create CL / reconcile / shelve，submit 仍然禁用。" : "第 7 步会停在 P4 等待确认。";
  if (!confirm(`确认启动完整 7 步动画自动化？\n${p4Text}`)) return;
  await runFlowAction("animation_flow.start", args, "完整 7 步流程已启动");
  refreshInBackground(true);
}

async function saveFlow() {
  const definition = collectFlowDefinition();
  if (!definition) return;
  await runFlowAction("custom_pipeline.save_definition", { ...definition, overwrite: true }, "流程已保存");
}

async function previewFlow() {
  const definition = collectFlowDefinition();
  if (!definition) return;
  await runFlowAction("custom_pipeline.preview_definition", { definition, variables: definition.variables }, "预览已生成");
}

async function runFlow() {
  const definition = collectFlowDefinition();
  if (!definition || !confirm(`确认执行 ${definition.name}？`)) return;
  await runFlowAction("custom_pipeline.run_start", { definition, variables: definition.variables }, "流程已提交后端");
  refreshInBackground(true);
}

async function runFlowAction(skill, args, okText) {
  state.flowBusy = true;
  state.flowResultSummary = { title: "正在处理", tone: "warn", lines: [`正在调用 ${skill}，请稍等。`] };
  try {
    const payload = await runWithFeedback(`flow:${skill}`, `流程动作 ${skill}`, () => skillCall(skill, args));
    if (!payload) return;
    state.flowResult = JSON.stringify(payload, null, 2);
    state.flowResultSummary = summarizeFlowPayload(skill, payload);
    toast(payload.ok === false ? "后端调用失败" : okText, payload.ok === false ? "bad" : "ok");
    if (skill === "custom_pipeline.save_definition") await loadFlowDefinitions();
    else refreshInBackground(false);
  } finally {
    state.flowBusy = false;
  }
}

function summarizeFlowPayload(skill, payload) {
  const data = unwrap(payload);
  if (payload?.ok === false || data?.error) {
    return { title: "后端返回异常", tone: "bad", lines: [payload.error || data.error || "未知错误"] };
  }
  if (skill.endsWith("save_definition")) {
    return { title: "流程已保存", tone: "ok", lines: [`名称：${state.flowName}`, `步骤：${state.flowSteps.filter((step) => step.enabled !== false).length} 个启用`] };
  }
  if (skill.endsWith("preview_definition")) {
    const steps = data.steps || data.preview?.steps || collectFlowDefinition()?.steps || [];
    return { title: "预览完成", tone: "ok", lines: [`将执行 ${steps.length} 个步骤。`, ...steps.slice(0, 5).map((step, index) => `${index + 1}. ${step.name || step.skill}`)] };
  }
  if (skill === "animation_flow.preview") {
    const stages = data.stages || [];
    const p4 = data.p4 || {};
    return { title: "7 步预览完成", tone: "ok", lines: [`工作区：${data.date_root}`, ...stages.map((stage) => stage.label || stage.key).slice(0, 7), `P4 Stream：${p4.stream || "//streams/rel_0.0.1"}`, "Submit：disabled"] };
  }
  if (skill === "animation_flow.start") {
    return { title: "7 步流程已启动", tone: "ok", lines: [`Run：${data.run_id || data.id || "后端已接收"}`, `工作区：${data.date_root || workspacePath()}`, "完成后飞书会返回 CL/Shelf ID；submit disabled。"] };
  }
  return { title: "流程已启动", tone: "ok", lines: [`Run：${data.run_id || data.id || "后端已接收"}`, `名称：${state.flowName}`, "可以去“队列”或“流程”页查看实时状态。"] };
}

function formatSkillResult(skill, payload) {
  const data = unwrap(payload);
  if (payload?.ok === false || data?.ok === false || data?.error) {
    return `调用失败：${payload.error || data.error || "后端返回异常"}\n\n需要看完整对象时点右上角“查看原始详情”。`;
  }
  const lines = [`后端已执行：${skill}`];
  const text = firstText(data);
  if (data.run_id || data.id) lines.push(`Run ID：${data.run_id || data.id}`);
  if (data.status) lines.push(`状态：${data.status}`);
  if (data.input_dir || data.download_dir) lines.push(`输入：${data.input_dir || data.download_dir}`);
  if (data.output_dir || data.export_dir || data.smooth_output_dir) lines.push(`输出：${data.output_dir || data.export_dir || data.smooth_output_dir}`);
  if (text && !lines.includes(text)) lines.push(text);
  if (lines.length === 1) lines.push("后端已返回成功。可以到“队列/流程”页继续看进度。");
  return lines.join("\n");
}

async function loadModuleCatalog() {
  state.moduleCatalog = unwrap(await skillCall("custom_pipeline.module_catalog", {}));
}

async function loadFlowDefinitions() {
  const payload = unwrap(await skillCall("custom_pipeline.list_definitions", {}));
  state.flowDefinitions = payload.items || [];
}

async function loadFlowDefinition(name) {
  const payload = unwrap(await skillCall("custom_pipeline.get_definition", { name }));
  const def = payload.definition || {};
  state.flowName = def.name || name;
  state.flowDescription = def.description || "";
  state.flowVariables = JSON.stringify(def.variables || {}, null, 2);
  state.flowVars = Object.entries(def.variables || flowVariablesObject()).map(([key, value]) => ({ key, label: key, value, mode: String(key).includes("workflow") ? "file" : "dir" }));
  state.flowSteps = (def.steps || []).map((step, index) => ({
    id: step.id || `step_${index + 1}`,
    name: step.name || flowModuleMeta(step.skill).title,
    skill: step.skill,
    enabled: step.enabled !== false,
    arguments: { ...(step.arguments || {}) },
    expanded: false,
  }));
  const comfy = findFlowStep("comfyui.run_start");
  const p4 = findFlowStep("p4.shelve_ui_import");
  state.quickFlow.workflowPath = comfy?.arguments?.workflow_path || "";
  state.quickFlow.allowP4Writes = p4 ? p4.enabled !== false : state.quickFlow.allowP4Writes;
  state.quickFlow.p4Description = p4?.arguments?.desc || state.quickFlow.p4Description;
}

async function runDiagnose() {
  switchView("overview");
  state.status.agentDiagnose = await runWithFeedback("diagnose", "后端自动诊断", () => skillCall("agent.diagnose", { include_gpu: false }));
  toast(unwrap(state.status.agentDiagnose).error ? "诊断返回异常" : "诊断已完成", unwrap(state.status.agentDiagnose).error ? "bad" : "ok");
}

async function runAsr() {
  state.asrResult = "正在识别音频，后端处理中...";
  const payload = await runWithFeedback("voice:asr", "ASR 语音识别", () => skillCall("speech.transcribe", { ...state.asr }));
  state.asrResult = formatSkillResult("speech.transcribe", payload);
}

async function runTts() {
  const args = { ...state.tts };
  Object.keys(args).forEach((key) => { if (!args[key]) delete args[key]; });
  state.ttsResult = "正在生成语音，后端处理中...";
  const payload = await runWithFeedback("voice:tts", "TTS 语音生成", () => skillCall("speech.synthesize", args));
  state.ttsResult = formatSkillResult("speech.synthesize", payload);
}

async function loadMemory() {
  beginAction("memory:load", "正在读取记忆与上下文");
  const scope = encodeURIComponent(state.memoryScope || "global");
  const conversation = encodeURIComponent(state.memoryConversation || "test");
  try {
    const payload = await request(`/api/admin/memory-pack?scope=${scope}&conversation_id=${conversation}&limit=10`, { timeoutMs: 30000, cacheMs: 3000, dedupe: true });
    state.memoryItems = payload.items || [];
    state.memoryPack = payload.pack || {};
  } finally {
    endAction("memory:load", "记忆读取已结束");
  }
}

async function compactMemory() {
  const payload = await runWithFeedback("memory:compact", "手动压缩记忆", () => skillCall("memory.compact", { conversation_id: state.memoryConversation || "test", keep_messages: 12, max_chars: 6000 }));
  state.detail = { title: "memory.compact", payload };
  await loadMemory();
}

async function runP4(kind) {
  const action = p4Actions.find((item) => item.kind === kind) || { kind, skill: `p4.${kind}`, title: p4OperationLabel(kind) };
  await runP4Action(action);
}

function p4ActionArgs(action) {
  const args = {};
  if (state.p4Form.workflow.trim()) args.workflow = state.p4Form.workflow.trim();
  if (state.p4Form.workspace.trim()) args.workspace = state.p4Form.workspace.trim();
  if (action.needsCl && state.p4Form.cl.trim()) args.cl = state.p4Form.cl.trim();
  if (action.needsDescription && state.p4Form.description.trim()) args.desc = state.p4Form.description.trim();
  if (state.p4Form.allow_delete) args.allow_delete = true;
  if (state.p4Form.force && ["shelve", "shelve_ui_import"].includes(action.kind)) args.force = true;
  if (action.kind === "shelve_ui_import") args.yes = true;
  return args;
}

async function runP4Action(action) {
  if (action.needsCl && !state.p4Form.cl.trim()) {
    toast("这个动作需要先填写 CL 编号", "bad");
    return;
  }
  if (action.confirm && !confirm(action.confirm)) return;
  const payload = await runWithFeedback(`p4:${action.kind}`, `P4 ${action.title}`, () => skillCall(action.skill, p4ActionArgs(action)));
  if (!payload) return;
  state.p4LastAction = action.kind;
  state.p4Result = unwrap(payload);
  if (action.kind === "status") state.status.p4 = payload;
  toast(state.p4Result?.error ? "P4 返回异常" : `P4 ${action.title}已完成`, state.p4Result?.error ? "bad" : "ok");
  refreshInBackground(false);
}

function asList(value) {
  if (!value) return [];
  if (Array.isArray(value)) return value;
  return [value];
}

function firstText(payload, keys = ["safe_summary", "summary", "message", "text", "detail", "stderr", "stdout", "error"]) {
  if (!payload || typeof payload !== "object") return "";
  for (const key of keys) {
    const value = payload[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  for (const value of Object.values(payload)) {
    if (value && typeof value === "object") {
      const nested = firstText(value, keys);
      if (nested) return nested;
    }
  }
  return "";
}

function buildDiagnosticCards(payload) {
  const data = unwrap(payload);
  if (!data || !Object.keys(data).length) {
    return [{ title: "尚未运行诊断", tone: "warn", lines: ["点击右侧“诊断”，WebUI 会调用 agent.diagnose，然后按模块展示结论。"] }];
  }
  if (data.error) {
    return [{ title: "诊断接口异常", tone: "bad", lines: [data.error, "健康检查和技能调用是两套状态：飞书正常时，这里通常是 WebUI 代理端口或路由没对上。"] }];
  }

  const gpu = data.gpu || data.gpu_status || data.system_gpu || {};
  const g0 = asList(gpu.gpus)[0] || data.gpu0 || {};
  const work = data.current_work || data.work || data;
  const lines = [];
  const running = asList(work.running || work.active || work.active_tasks || work.tasks).filter(Boolean);
  lines.push(running.length ? `当前检测到 ${running.length} 个运行中任务。` : "当前没有检测到运行中的 ComfyUI / Cherry / 抽帧 / 全流程任务。");
  if (g0.name || g0.memory_total_mb) {
    lines.push(`GPU：${g0.name || "GPU 0"}，显存 ${compact(g0.memory_used_mb, "?")}/${compact(g0.memory_total_mb, "?")} MB，利用率 ${compact(g0.utilization_gpu_percent, compact(g0.utilization_gpu, compact(g0.utilization, "?")))}%。`);
  } else if (firstText(gpu)) {
    lines.push(`GPU：${firstText(gpu)}`);
  }

  const judgement = firstText(data, ["judgement", "diagnosis", "safe_summary", "summary", "message", "text"]);
  if (judgement && !lines.includes(judgement)) lines.push(judgement);
  const recentError = data.recent_error || data.info_recent_error || data.last_error || work.recent_error;
  if (recentError) lines.push(`最近错误：${compact(recentError)}`);

  return [
    { title: "执行现场", tone: running.length ? "warn" : "ok", lines },
    { title: "后端入口", tone: state.health?.ok ? "ok" : "bad", lines: [`健康检查：${state.health?.ok ? "在线" : "异常"}`, `服务：${state.health?.service || state.health?.error || "未知"}`] },
    { title: "下一步", tone: "info", lines: ["需要更细的原始对象时点“查看原始详情”；默认页只展示自然语言结论。"] },
  ];
}

function buildPipelineSteps() {
  const aflow = unwrap(state.status.animationFlowCurrent);
  const aflowStages = Array.isArray(aflow?.stages) ? aflow.stages : [];
  if (aflowStages.length) {
    return aflowStages.map((stage) => ({
      title: stage.label || animationFlowStageTitle(stage.key),
      skill: stage.key || "animation_flow",
      status: stage.status || (aflow.current_stage === stage.key ? "running" : "pending"),
      progress: stage.status === "done" ? 100 : stage.status === "running" ? 45 : 0,
      log: animationFlowStageLog(stage.key, aflow),
    }));
  }
  const custom = unwrap(state.status.customPipelineRuns).items?.[0] || unwrap(state.status.customPipelineRuns).items?.[0];
  const customSteps = Array.isArray(custom?.steps) ? custom.steps : [];
  const customResults = Array.isArray(custom?.results) ? custom.results : [];
  if (customSteps.length) {
    return customSteps.map((step, index) => {
      const result = customResults[index] || {};
      return {
        title: step.name || flowModuleMeta(step.skill).title || step.skill,
        skill: step.skill,
        status: result.status || result.result?.status || (custom.current_step === step.id ? "RUNNING" : custom.status || "等待"),
        progress: progressFromPayload(result.result || result),
        log: firstText(result.result || result) || compact(result.error, "等待后端写入该步骤日志"),
      };
    });
  }
  if (state.flowSteps.length) {
    return state.flowSteps.map((step) => ({
      title: step.name || flowModuleMeta(step.skill).title,
      skill: step.skill,
      status: step.enabled === false ? "已禁用" : "可执行",
      progress: step.enabled === false ? 0 : 8,
      log: flowModuleMeta(step.skill).subtitle,
    }));
  }
  return [
    pipelineStepFromStatus("飞书视频下载 / 抽帧", "frame.run_status", unwrap(state.status.frameCurrent)),
    pipelineStepFromStatus("ComfyUI 抠图", "comfyui.run_status", unwrap(state.status.comfyCurrent)),
    pipelineStepFromStatus("Cherry 平滑", "cherry.run_status", unwrap(state.status.cherryCurrent)),
    pipelineStepFromStatus("unity_ready 整理", "unity_ready.status", unwrap(state.status.unityReady)),
    pipelineStepFromStatus("Unity 导入引擎", "unity_import.status", unwrap(state.status.unityImport)),
    pipelineStepFromStatus("P4 Shelve-only", "p4.status", unwrap(state.status.p4)),
  ];
}

function animationFlowStageTitle(key) {
  return {
    feishu_download: "1 飞书文档/表格下载视频",
    frame_extract: "2 抽帧",
    matting: "3 抠图",
    cherry: "4 Cherry 后处理",
    unity_ready: "5 unity_ready 整理",
    unity_import: "6 Unity 插件导入引擎",
    p4_shelve: "7 P4 reconcile/changelist/shelve/report",
  }[key] || key || "动画流程";
}

function animationFlowStageLog(key, payload) {
  if (payload?.error && payload.current_stage === key) return payload.error;
  if (key === "p4_shelve") {
    const p4 = payload?.children?.p4 || payload?.p4 || {};
    const cl = p4.changelist_id || p4.cl || p4.shelf;
    return cl ? `CL/Shelf ${cl}，Submit disabled` : "只做 create CL / reconcile / shelve / report，submit disabled。";
  }
  if (key === "unity_import") return firstText(payload?.children?.unity_import || {}) || "调用 Unity 插件 API，不改插件源码。";
  if (key === "unity_ready") return "整理 scene / emoji 两包 JSON 和 frames。";
  return firstText(payload?.pipeline || payload) || "等待后端更新进度。";
}

function pipelineStepFromStatus(title, skill, payload) {
  return {
    title,
    skill,
    status: payload.status || "空闲",
    progress: progressFromPayload(payload),
    log: payload.last_log || payload.error || payload.current_item?.label || firstText(payload) || "暂无进度日志",
  };
}

function buildCustomFlowStepCards(run) {
  const results = Array.isArray(run?.results) ? run.results : [];
  const childStatus = run?.child_status || {};
  return state.flowSteps.map((step, index) => {
    const result = results.find((item) => item.step === step.id || item.skill === step.skill);
    const child = Object.values(childStatus).find((item) => {
      const data = unwrap(item);
      return String(data?.skill || data?.module || "").includes(step.skill.split(".")[0]) || data?.run_id;
    });
    const resultPayload = unwrap(result?.result || {});
    const childPayload = unwrap(child || {});
    const isCurrent = run?.current_step === step.id;
    const status = step.enabled === false ? "SKIPPED" : result ? (resultPayload.status || "DONE") : isCurrent ? "RUNNING" : run?.status === "FAILED" ? "WAITING" : "PENDING";
    const text = resultPayload.error || childPayload.error || firstText(resultPayload) || childPayload.last_log || flowModuleMeta(step.skill).subtitle;
    return {
      id: step.id,
      index: index + 1,
      title: step.name,
      skill: step.skill,
      status,
      result: text,
      payload: result || child || step,
    };
  });
}

function progressFromPayload(payload) {
  const percent = Number(payload?.progress_percent ?? payload?.progress ?? payload?.percent ?? 0);
  if (Number.isFinite(percent) && percent > 0) return Math.max(0, Math.min(100, percent));
  const total = Number(payload?.total_records ?? payload?.total ?? payload?.total_images ?? 0);
  const done = Number(payload?.processed_records ?? payload?.completed ?? payload?.done ?? payload?.processed ?? 0);
  if (total > 0) return Math.max(0, Math.min(100, (done / total) * 100));
  return statusClass(payload?.status) === "ok" && String(payload?.status || "").toUpperCase() === "DONE" ? 100 : 0;
}

function p4OperationLabel(kind) {
  return {
    status: "状态读取",
    check: "安全检查",
    preview: "改动预览",
    create_cl: "创建 CL",
    reconcile: "放入 CL",
    shelve: "Shelve",
    report: "报告生成",
    shelve_ui_import: "一键导入 Shelve",
  }[kind] || kind || "P4 检查";
}

function commandSummary(command) {
  if (!command) return "";
  const name = Array.isArray(command.command) ? command.command.join(" ") : compact(command.command, "p4");
  const result = command.safe_summary || command.stdout || command.stderr || "";
  const code = command.returncode === 0 ? "成功" : `返回码 ${compact(command.returncode, "?")}`;
  return `${name}：${code}${result ? `，${String(result).trim()}` : ""}`;
}

function buildP4Display(payload) {
  const data = unwrap(payload);
  if (!data || !Object.keys(data).length) {
    return {
      title: "P4 尚未刷新",
      tone: "warn",
      cards: [
        ["工作区", "-"],
        ["打开文件", "0"],
        ["预览变更", "0"],
        ["安全模式", "只 Shelve"],
      ],
      sections: [{ title: "下一步", tone: "info", lines: ["先点“读取状态”。如果状态正常，再点“预览改动”。确认后使用“一键导入 Shelve”。"] }],
    };
  }

  const commands = asList(data.commands).filter(Boolean);
  const summary = data.summary || data.result?.summary || {};
  const safety = data.safety || data.safety_result || summary.safety || {};
  const opened = asList(data.opened || data.opened_files || data.files_opened || summary.opened_files || summary.opened).filter(Boolean);
  const reconcile = asList(data.reconcile || data.reconcile_files || data.changed_files || data.preview_files || summary.reconcile_files || summary.files).filter(Boolean);
  const reportText = data.report_text || data.report || data.text || "";
  const error = data.error || commands.find((item) => item.returncode && item.returncode !== 0)?.stderr;
  const workspace = data.workspace || data.client || data.p4client || summary.workspace || data.workflow || "-";
  const openedCount = data.opened_count ?? summary.opened_count ?? opened.length ?? 0;
  const reconcileCount = data.reconcile_count ?? summary.reconcile_count ?? summary.change_count ?? reconcile.length ?? 0;
  const cl = data.cl || data.changelist || data.change || data.shelf_cl || summary.cl || summary.changelist;

  const sections = [];
  const summaryText = firstText(data, ["readable_summary", "safe_summary", "report_text", "text", "message", "summary"]);
  if (summaryText) {
    sections.push({ title: error ? "需要处理" : "本次结果", tone: error ? "bad" : "ok", lines: summaryText.split("\n").filter(Boolean).slice(0, 8) });
  }
  if (state.p4Advanced && (data.root || data.workspace_root || data.cwd || summary.root)) {
    sections.push({ title: "工作区路径", lines: [`根目录：${data.root || data.workspace_root || data.cwd || summary.root}`] });
  }
  if (cl) {
    sections.push({ title: "CL / Shelf", tone: "info", lines: [`当前 CL：${cl}`, "后续 reconcile、shelve、report 可以沿用这个编号。"] });
  }
  if (opened.length && state.p4Advanced) {
    sections.push({ title: "已打开文件", lines: opened.slice(0, 8).map((item) => compact(item.depotFile || item.path || item.file || item)) });
  }
  if (reconcile.length) {
    sections.push({ title: "预览到的变更", tone: "info", lines: reconcile.slice(0, state.p4Advanced ? 12 : 5).map((item) => compact(item.depotFile || item.local_path || item.path || item.file || item)) });
  }
  if (reportText && state.p4LastAction === "report") {
    sections.push({ title: "飞书报告", tone: "ok", lines: String(reportText).split("\n").filter(Boolean).slice(0, 12) });
  }
  if (safety.warnings?.length) {
    sections.push({ title: "安全提醒", tone: "warn", lines: safety.warnings.map((item) => compact(item)) });
  }
  if (safety.errors?.length || safety.blockers?.length) {
    sections.unshift({ title: "安全阻断", tone: "bad", lines: [...asList(safety.errors), ...asList(safety.blockers)].map((item) => compact(item)) });
  }
  if (state.p4Advanced && commands.length) {
    sections.push({ title: "命令结论", lines: commands.map(commandSummary).filter(Boolean) });
  }
  if (error) {
    sections.unshift({ title: "异常原因", tone: "bad", lines: [String(error).trim()] });
  }
  if (!sections.length) {
    const next = reconcileCount > 0 ? "有变更可以继续 Shelve。确认文件符合预期后，填写或创建 CL，再执行 Shelve。" : "目前没有看到需要提交到 Shelf 的变更。";
    const text = firstText(data);
    sections.push({ title: "下一步", tone: "info", lines: [text || next] });
  }

  return {
    title: error ? "P4 有异常需要处理" : `${p4OperationLabel(state.p4LastAction)}完成`,
    tone: error ? "bad" : "ok",
    cards: [
      ["工作区", workspace],
      ["打开文件", openedCount],
      ["预览变更", reconcileCount],
      ["安全模式", "只 Shelve"],
    ],
    sections,
  };
}

function buildMemorySummary() {
  const pack = state.memoryPack || {};
  const summary = pack.summary || {};
  const recent = Array.isArray(pack.recent) ? pack.recent : [];
  const notes = Array.isArray(state.memoryItems) ? state.memoryItems : [];
  const lines = [];
  lines.push(`会话：${state.memoryConversation || "test"}`);
  lines.push(`长期记忆：${notes.length} 条；最近消息：${recent.length || pack.message_count || 0} 条。`);
  if (summary.summary_text) lines.push(`滚动摘要：${String(summary.summary_text).slice(0, 220)}`);
  else if (summary.compacted_until_id) lines.push(`已有压缩摘要，压缩到消息 ID ${summary.compacted_until_id}。`);
  else lines.push("还没有滚动摘要；对话积累后后端会按规则自动压缩。");
  return lines;
}

function memoryNoteTitle(item) {
  return item.key || item.scope || item.source || `记忆 #${item.id || ""}`;
}

function memoryNoteText(item) {
  return item.value || item.message_text || item.response_text || firstText(item) || JSON.stringify(item);
}

async function loadLogs() {
  if (state.logsLoading) return;
  state.logVisibleLimit = VISIBLE_LOG_LIMIT;
  state.logsLoading = true;
  beginAction("logs:load", "正在读取日志");
  const id = encodeURIComponent(state.conversationId || "test");
  try {
    const [messages, calls] = await Promise.all([
      request(`/api/admin/brain-messages?conversation_id=${id}&limit=60`, { timeoutMs: 30000, cacheMs: 2500, dedupe: true }),
      request("/api/admin/skill-calls?limit=80", { timeoutMs: 30000, cacheMs: 2500, dedupe: true }),
    ]);
    state.logError = "";
    if (messages.ok === false || calls.ok === false) {
      state.logError = messages.error || calls.error || "当前后端进程还没有加载日志接口。请重启 Agent 网关后再刷新日志页。";
    }
    state.logs = messages.items || [];
    state.skillCalls = calls.items || [];
  } finally {
    state.logsLoading = false;
    endAction("logs:load", "日志读取已结束");
  }
}

function runCommand(command) {
  state.commandOpen = false;
  state.settingsOpen = false;
  command.run();
}

function seedAgentFlowPrompt() {
  state.commandOpen = false;
  state.settingsOpen = false;
  sendPrompt("请帮我生成一个自定义动画自动化流程 JSON：步骤包括抽帧并剔除关键帧、ComfyUI 抠图、Cherry 平滑；每一步都要给 skill 和 arguments，路径使用 ${videos}/${frames}/${matte}/${smooth} 变量，并说明哪些参数建议我在 WebUI 里手动改。");
}

function onTokenChange() {
  localStorage.setItem("assetclaw.skillToken", state.token);
  toast("连接设置已保存", "ok");
  refreshAll(true);
}

onMounted(() => {
  loadChatHistory();
  loadStatusCache();
  ensureFlowSteps();
  refreshAll(false).catch((error) => toast(`初始刷新失败：${String(error?.message || error)}`, "bad"));
  setTimeout(() => loadSkills(), 1200);
  setInterval(() => {
    state.action.nowMs = Date.now();
  }, 1000);
  setInterval(() => {
    if (state.polling && !document.hidden) refreshAll(false);
  }, STATUS_POLL_MS);
  window.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      state.commandOpen = false;
      state.settingsOpen = true;
      nextTick(() => document.querySelector(".command-search")?.focus());
    }
    if (event.key === "Escape") {
      closeAllModals();
    }
  });
  window.addEventListener("pointermove", (event) => {
    document.documentElement.style.setProperty("--mx", `${event.clientX}px`);
    document.documentElement.style.setProperty("--my", `${event.clientY}px`);
  });
});
</script>

<template>
  <div class="ambient-stage" aria-hidden="true">
    <div class="scanline"></div>
    <div class="light-sweep"></div>
    <div class="orbital orbital-a"></div>
    <div class="orbital orbital-b"></div>
    <div class="equalizer"><i v-for="n in 8" :key="n"></i></div>
  </div>

  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">MI</div>
        <div class="brand-copy"><strong>Miku Agent</strong><span>本机生产控制台</span></div>
      </div>

      <nav class="nav">
        <button v-for="item in views" :key="item.id" class="nav-item" :class="{ active: state.view === item.id }" @click="switchView(item.id)">
          <span>{{ item.icon }}</span><b>{{ item.label }}</b>
        </button>
      </nav>

      <div class="sidebar-stack">
        <div class="connection">
          <div class="signal" :class="{ online, checking: state.connection.checking && !online, offline: !online && !state.connection.checking }"></div>
          <div><strong>{{ connectionTitle }}</strong><span>{{ connectionSubtitle }}</span></div>
        </div>
        <button class="settings-button" @click="state.settingsOpen = true">高级设置</button>
        <div class="local-guard"><b>仅本机运行</b><span>Vite / WebUI / Agent 都不暴露公网</span></div>
      </div>
    </aside>

    <main>
      <section class="topbar">
        <div><p class="eyebrow">{{ currentView.eyebrow }}</p><h1>{{ currentView.title }}</h1></div>
        <div class="toolbar">
          <span class="guard-pill">No Tunnel</span>
          <button class="small-button ghost" title="选择当前动画工作区" @click="openPathPicker({ target: 'workspaceRoot', mode: 'dir', current: state.workspaceRoot })">工作区</button>
          <button class="icon-button wide-icon" title="高级设置" @click="state.settingsOpen = true">高级</button>
          <button class="icon-button" title="刷新状态" :disabled="state.refreshing" @click="refreshAll(true)">↻</button>
          <button class="segmented" :class="{ active: state.polling }" @click="state.polling = !state.polling">{{ state.polling ? "Auto" : "Manual" }}</button>
        </div>
      </section>
      <section v-if="activeAction" class="action-banner">
        <span class="spinner"></span>
        <div class="action-banner-copy">
          <b>{{ activeAction.label }}</b>
          <small>{{ state.action.startedAt }} 开始，已等待 {{ activeAction.elapsed }} 秒。后端还在处理时页面可以继续浏览其它区域。</small>
          <div class="indeterminate-progress"><span :style="{ width: `${activeAction.progress}%` }"></span></div>
        </div>
        <span class="action-count" v-if="activeAction.count > 1">{{ activeAction.count }} 项</span>
      </section>

      <section v-if="state.view === 'chat'" class="view active">
        <div class="chat-workbench">
          <div class="chat-panel">
            <div class="chat-header">
              <div><h2>统一自然语言入口</h2><span>你在这里输入的内容会原样进入本机 Agent，和飞书共用同一个后端。</span></div>
              <button class="small-button ghost" @click="clearChat">清空本地对话</button>
              <div class="chat-mode">
                <button class="chip active" @click="sendPrompt('看一下现在所有任务状态')">总状态</button>
                <button class="chip" @click="sendPrompt('ComfyUI 现在跑到哪里了，队列还有多少')">ComfyUI</button>
                <button class="chip" @click="sendPrompt('动画自动化流程现在到哪一步了')">Pipeline</button>
              </div>
            </div>
            <div class="messages">
              <div v-for="(msg, index) in state.messages" :key="index" class="message" :class="[msg.role, { pending: msg.pending }]">
                <div class="message-meta">{{ msg.role === "user" ? "你" : msg.role === "agent" ? "Miku Agent" : "系统" }} <span>{{ msg.at || "" }}</span></div>
                <div>{{ msg.text }}</div>
                <div v-if="msg.pending" class="typing-dots"><i></i><i></i><i></i><span>后端正在处理</span></div>
              </div>
            </div>
            <div class="attachment-tray" :class="{ active: state.attachments.length }">
              <span v-for="file in state.attachments" :key="file.name" class="attachment-chip">{{ file.name }} <b>{{ Math.ceil(file.size / 1024) }} KB</b></span>
            </div>
            <form class="composer" @submit.prevent="sendChat">
              <label class="icon-button" title="添加附件">＋<input type="file" multiple hidden @change="onAttach" /></label>
              <textarea v-model="state.input" rows="1" placeholder="Enter 发送，Shift+Enter 换行。输入任务、状态查询、路径调整、确认指令或普通聊天..." @keydown="onComposerKeydown" />
              <button class="send-button" :disabled="state.sending">{{ state.sending ? "等待中" : "发送" }}</button>
            </form>
          </div>
          <aside class="operator-rail">
            <section class="rail-panel persona-panel">
              <div class="persona-avatar"><div class="antenna"></div><div class="face"><span></span><span></span></div><div class="voice-bars"><i></i><i></i><i></i><i></i></div></div>
              <div><h2>Miku Agent</h2><span>初音未来风格智能体</span></div>
            </section>
            <section class="rail-panel">
              <div class="panel-title"><h2>实时快照</h2><span>{{ state.lastRefreshAt || "-" }}</span></div>
              <div class="mini-status">
                <div v-for="row in snapshotRows" :key="row[0]" class="metric"><span>{{ row[0] }}</span><b>{{ row[1] }}</b></div>
                <div v-if="state.connection.consecutiveFailures && online" class="metric soft-warning"><span>同步</span><b>最近一次轮询失败，仍保留在线状态</b></div>
              </div>
            </section>
            <section class="rail-panel">
              <div class="panel-title"><h2>进行中任务</h2><span>{{ activeTasks.length }}</span></div>
              <div class="compact-list">
                <div v-if="!activeTasks.length" class="log-item"><h3>{{ online ? "空闲" : "后端离线" }}</h3><pre>{{ state.health?.error || "暂无活动任务" }}</pre></div>
                <div v-for="task in activeTasks.slice(0, 6)" :key="`${task.module}:${task.id}`" class="run-row compact-run">
                  <div><h3>{{ task.module }}</h3><span class="muted">{{ task.id || "-" }}</span></div>
                  <span class="pill" :class="statusClass(task.status)">{{ task.status }}</span>
                  <button class="small-button ghost" @click="state.detail = { title: `${task.module}:${task.id}`, payload: task.raw }">详情</button>
                </div>
              </div>
            </section>
          </aside>
        </div>
      </section>

      <section v-if="state.view === 'overview'" class="view active command-layout">
        <section class="wide-panel ask-agent-panel">
          <div class="panel-title"><div><h2>问 Agent</h2><span>这些按钮会发中文指令给后端 Agent，不是前端假数据。</span></div></div>
          <div class="ask-grid">
            <button class="ask-card" @click="sendPrompt('列出当前所有任务，按 ComfyUI、Cherry、抽帧、Pipeline 分组说明状态')">所有任务</button>
            <button class="ask-card" @click="sendPrompt('诊断现在后端、ComfyUI、P4、动画流程哪里可能有问题')">自动诊断</button>
            <button class="ask-card" @click="sendPrompt('说明当前动画自动化流程每一步状态，并指出是否需要人工确认')">流程复核</button>
            <button class="ask-card" @click="sendPrompt('列出当前可以调整的模块参数，并说明哪些参数会影响输出')">参数说明</button>
          </div>
        </section>
        <section class="module-board">
          <article v-for="(schema, key) in modules" :key="key" class="module-card">
            <div class="card-head"><h3>{{ schema.title }}</h3><span class="pill" :class="statusClass(unwrap(state.status[schema.statusKey]).status)">{{ unwrap(state.status[schema.statusKey]).status || "可配置" }}</span></div>
            <p class="muted">{{ schema.subtitle }}</p>
            <div class="metric"><span>启动 API</span><b>{{ schema.startSkill }}</b></div>
            <div class="metric"><span>运行记录</span><b>{{ unwrap(state.status[schema.runKey]).items?.length || 0 }}</b></div>
            <button class="small-button" @click="openConfig(key)">调整参数</button>
          </article>
        </section>
        <section class="wide-panel">
          <div class="panel-title"><h2>诊断结果</h2><div class="button-row"><button class="small-button ghost" @click="state.detail = { title: '诊断原始详情', payload: unwrap(state.status.agentDiagnose || state.status.agentWork) }">查看原始详情</button><button class="small-button" @click="runDiagnose">诊断</button></div></div>
          <div class="summary-grid">
            <article v-for="card in diagnosticCards" :key="card.title" class="summary-card" :class="card.tone">
              <h3>{{ card.title }}</h3>
              <p v-for="line in card.lines" :key="line">{{ line }}</p>
            </article>
          </div>
        </section>
      </section>

      <section v-if="state.view === 'queues'" class="view active">
        <section class="wide-panel">
          <div class="panel-title"><div><h2>生产任务</h2><span>上次同步：{{ state.lastRefreshAt || "-" }}。短暂刷新失败时会保留上次成功快照。</span></div><div class="button-row"><button class="small-button danger" @click="cleanupWithConfirm('production_runs', '生产任务历史')">清空历史</button><button class="small-button ghost" @click="refreshAll(true)">刷新队列</button></div></div>
          <div class="queue-filterbar">
            <input v-model="state.queueFilters.keyword" class="search" placeholder="搜索 Run ID、模块、输入/输出路径..." />
            <select v-model="state.queueFilters.status"><option value="all">全部状态</option><option value="running">RUNNING</option><option value="done">DONE</option><option value="failed">FAILED</option><option value="canceled">CANCELED</option></select>
            <select v-model="state.queueFilters.day"><option value="">全部日期</option><option v-for="day in queueDays" :key="day" :value="day">{{ day }}</option></select>
          </div>
          <div class="task-table">
            <div class="task-row header"><span>运行时间</span><span>模块 / Run</span><span>状态</span><span>输入</span><span>输出</span><span>操作</span></div>
            <div v-if="!filteredTasks.length" class="log-item"><h3>{{ tasks.length ? "没有匹配记录" : "暂无任务" }}</h3><pre>{{ tasks.length ? "换一个筛选条件试试。" : "后端没有返回任务记录；如果刚刷新失败，页面会继续保留上次成功快照。" }}</pre></div>
            <div v-for="task in visibleTasks" :key="`${task.module}:${task.id}`" class="task-row queue-row">
              <div class="task-time"><b>{{ task.clock }}</b><span>{{ task.day }}</span><small>{{ task.fullTime }}</small></div>
              <div><h3>{{ task.module }}</h3><span class="muted">{{ task.id || "-" }}</span></div>
              <div><span class="pill" :class="statusClass(task.status)">{{ task.status }}</span><div class="progress"><div class="bar" :style="{ width: `${task.progress}%` }"></div></div></div>
              <span class="muted">{{ task.input }}</span>
              <span class="muted">{{ task.output }}</span>
              <div class="row-actions">
                <button class="small-button ghost" @click="state.detail = { title: `${task.module}:${task.id}`, payload: task.raw }">详情</button>
                <button v-if="!isTaskDone(task) && taskActionSkills(task.module).pause" class="small-button" @click="runTaskAction(task, 'pause')">暂停</button>
                <button v-if="!isTaskDone(task) && taskActionSkills(task.module).resume" class="small-button" @click="runTaskAction(task, 'resume')">继续</button>
                <button v-if="!isTaskDone(task) && taskActionSkills(task.module).cancel" class="small-button danger" @click="runTaskAction(task, 'cancel')">终止</button>
              </div>
            </div>
            <button v-if="filteredTasks.length > visibleTasks.length" class="small-button ghost load-more" @click="state.queueVisibleLimit += VISIBLE_TASK_LIMIT">
              显示更多 {{ filteredTasks.length - visibleTasks.length }} 条
            </button>
          </div>
        </section>
      </section>

      <section v-if="state.view === 'pipeline'" class="view active">
        <div class="pipeline-hero">
          <section class="wide-panel">
            <div class="panel-title"><h2>7 步动画流程时间线</h2><span>{{ unwrap(state.status.animationFlowCurrent).run_id || "当前无活动流程" }}</span></div>
            <div class="timeline dynamic">
              <div v-for="(step, index) in pipelineSteps" :key="`${step.skill}:${index}`" class="step" :class="statusClass(step.status)">
                <div class="step-head"><span>{{ String(index + 1).padStart(2, "0") }}</span><b class="pill" :class="statusClass(step.status)">{{ step.status }}</b></div>
                <h3>{{ step.title }}</h3>
                <p class="muted">{{ step.skill }}</p>
                <div class="progress"><div class="bar" :style="{ width: `${step.progress}%` }"></div></div>
                <small>{{ step.log }}</small>
              </div>
            </div>
          </section>
          <section class="wide-panel">
            <div class="panel-title"><h2>工作区计数</h2><span>{{ unwrap(state.status.animation).root || "-" }}</span></div>
            <div class="counter-grid">
              <div v-for="key in ['videos', 'frames', 'matte', 'smooth']" :key="key" class="counter"><b>{{ compact(unwrap(state.status.animation).counts?.[key], '0') }}</b><span class="muted">{{ key }}</span></div>
            </div>
          </section>
        </div>
        <section class="wide-panel"><div class="panel-title"><h2>流程运行记录</h2><div class="button-row"><button class="small-button" @click="switchView('builder')">7 步生产</button><button class="small-button" @click="openConfig('animationFlow')">完整流程参数</button><button class="small-button ghost" @click="openConfig('pipeline')">旧三步</button></div></div><div class="task-table"><div v-if="!pipelineRuns.length" class="log-item"><h3>暂无流程记录</h3><pre>可以从“生产”页启动完整 7 步流程；每一步会在这里显示状态和日志。</pre></div><div v-for="task in pipelineRuns" :key="`${task.module}:${task.id}`" class="task-row"><div class="task-time"><b>{{ task.clock }}</b><span>{{ task.day }}</span><small>{{ task.fullTime }}</small></div><div><h3>{{ task.module }} {{ task.id }}</h3><span class="muted">{{ task.updated_at }}</span></div><span class="pill" :class="statusClass(task.status)">{{ task.status }}</span><span>{{ task.input }}</span><span>{{ task.output }}</span><button class="small-button ghost" @click="state.detail = { title: task.id, payload: task.raw }">详情</button></div></div></section>
      </section>

      <section v-if="state.view === 'builder'" class="view active workflow-studio production-flow">
        <section class="workflow-main">
          <div class="builder-hero wide-panel">
            <div>
              <p class="eyebrow">动画自动化</p>
              <h2>7 步生产流程</h2>
              <span>飞书下载、抽帧、抠图、Cherry、unity_ready、Unity 导入、P4 Shelve-only 会按顺序执行。</span>
            </div>
            <div class="builder-actions">
              <button class="small-button" @click="previewAnimationFlow" :disabled="state.flowBusy">预览7步</button>
              <button class="small-button" @click="saveFlow" :disabled="state.flowBusy">保存自定义方案</button>
              <button class="send-button" @click="runAnimationFlow" :disabled="state.flowBusy">{{ state.flowBusy ? "执行中" : "启动7步流程" }}</button>
            </div>
          </div>

          <section class="wide-panel quick-flow-panel">
            <div class="panel-title"><div><h2>生产输入</h2><span>这几个字段就是日常要改的内容。</span></div><button class="small-button ghost" @click="state.quickFlow.advancedOpen = !state.quickFlow.advancedOpen">{{ state.quickFlow.advancedOpen ? "收起模块参数" : "模块参数" }}</button></div>
            <div class="quick-flow-grid">
              <label class="wide-field"><span>ComfyUI 工作流</span><div class="path-input"><input v-model="state.quickFlow.workflowPath" placeholder="E:/.../workflow.json" /><button class="small-button" @click="openPathPicker({ target: 'quickFlow:workflowPath', mode: 'file', current: state.quickFlow.workflowPath })">浏览</button></div></label>
              <label><span>当天工作区</span><div class="path-input"><input v-model="state.workspaceRoot" /><button class="small-button" @click="openPathPicker({ target: 'workspaceRoot', mode: 'dir', current: state.workspaceRoot })">浏览</button></div></label>
              <label><span>Unity 工程</span><div class="path-input"><input v-model="state.quickFlow.unityProject" /><button class="small-button" @click="openPathPicker({ target: 'quickFlow:unityProject', mode: 'dir', current: state.quickFlow.unityProject })">浏览</button></div></label>
              <label><span>P4 Stream</span><input v-model="state.quickFlow.p4Stream" /></label>
              <label><span>导入包</span><input v-model="state.quickFlow.package" placeholder="both / scene / emoji" /></label>
              <label><span>抽帧 FPS</span><input v-model="state.quickFlow.fps" type="number" /></label>
              <label><span>通知间隔秒</span><input v-model="state.quickFlow.notifyIntervalSeconds" type="number" /></label>
              <label class="toggle-field"><input v-model="state.quickFlow.allowP4Writes" type="checkbox" /><span>允许第 7 步 create CL / reconcile / shelve；submit 永远禁用</span></label>
            </div>
          </section>

          <section v-if="state.quickFlow.advancedOpen" class="wide-panel flow-canvas">
            <div class="panel-title"><div><h2>模块参数</h2><span>只改你关心的参数；其余使用默认值。</span></div><button class="small-button" @click="addFlowStep('frame.run_start')">添加步骤</button></div>
            <div class="flow-steps compact">
              <article v-for="(step, index) in state.flowSteps" :key="step.id" class="flow-step modern" :class="{ disabled: step.enabled === false }">
                <div class="card-head">
                  <div class="flow-node-title">
                    <span class="flow-index">{{ String(index + 1).padStart(2, "0") }}</span>
                    <div><h3>{{ step.name }}</h3><b>{{ step.skill }}</b></div>
                  </div>
                  <div class="button-row">
                    <button class="small-button ghost" @click="openFlowStepConfig(index)">参数</button>
                    <button class="small-button" @click="moveFlowStep(index, 'up')">上移</button>
                    <button class="small-button" @click="moveFlowStep(index, 'down')">下移</button>
                    <button class="small-button danger" @click="removeFlowStep(index)">删除</button>
                  </div>
                </div>
                <div class="step-summary">
                  <span>{{ flowModuleMeta(step.skill).subtitle }}</span>
                  <label class="toggle-field compact-toggle"><input v-model="step.enabled" type="checkbox" /><span>启用</span></label>
                </div>
                <div class="flow-glance"><span v-for="item in stepArgumentSummary(step)" :key="item">{{ item }}</span></div>
              </article>
            </div>
          </section>

          <section class="wide-panel flow-status-panel">
            <div class="panel-title"><div><h2>执行状态</h2><span>{{ customPipelineRun.run_id || "尚未启动" }} {{ customPipelineRun.status ? `· ${customPipelineRun.status}` : "" }}</span></div><button class="small-button ghost" @click="state.detail = { title: '自定义流程原始详情', payload: customPipelineRun }">原始详情</button></div>
            <div class="flow-status-grid">
              <article v-for="step in customFlowStepCards" :key="step.id" class="flow-status-card" :class="statusClass(step.status)">
                <div class="step-head"><span>{{ String(step.index).padStart(2, "0") }}</span><b class="pill" :class="statusClass(step.status)">{{ step.status }}</b></div>
                <h3>{{ step.title }}</h3>
                <small>{{ step.skill }}</small>
                <p>{{ step.result }}</p>
                <button class="small-button ghost" @click="state.detail = { title: step.title, payload: step.payload }">结果</button>
              </article>
            </div>
          </section>

          <section v-if="state.flowResultSummary" class="wide-panel flow-result-panel">
            <div class="panel-title"><h2>{{ state.flowResultSummary.title }}</h2><button class="small-button ghost" @click="state.detail = { title: '流程原始详情', payload: state.flowResult }">查看原始详情</button></div>
            <div class="summary-grid"><article class="summary-card" :class="state.flowResultSummary.tone"><p v-for="line in state.flowResultSummary.lines" :key="line">{{ line }}</p></article></div>
          </section>
        </section>
      </section>

      <section v-if="state.view === 'voice'" class="view active split-layout">
        <section class="wide-panel">
          <div class="panel-title"><div><h2>ASR 语音转文字</h2><span>选择本机音频路径，调用 speech.transcribe。</span></div></div>
          <div class="config-form"><label><span>音频路径</span><div class="path-input"><input v-model="state.asr.audio_path" placeholder="E:/path/to/audio.wav" /><button class="small-button" @click="openPathPicker({ target: 'voice:asr.audio_path', mode: 'file', current: state.asr.audio_path })">浏览</button></div></label><label><span>语言</span><input v-model="state.asr.language" /></label><label class="wide-field"><span>提示词</span><input v-model="state.asr.prompt" /></label></div>
          <button class="send-button" @click="runAsr">开始识别</button><pre class="json-box">{{ state.asrResult }}</pre>
        </section>
        <section class="wide-panel">
          <div class="panel-title"><div><h2>TTS 文字转语音</h2><span>调用 speech.synthesize。</span></div></div>
          <div class="config-form"><label class="wide-field"><span>合成文本</span><textarea v-model="state.tts.text" rows="4"></textarea></label><label><span>输出路径</span><div class="path-input"><input v-model="state.tts.output_path" /><button class="small-button" @click="openPathPicker({ target: 'voice:tts.output_path', mode: 'dir', current: state.tts.output_path })">浏览</button></div></label><label><span>音色/提示音频</span><div class="path-input"><input v-model="state.tts.voice" /><button class="small-button" @click="openPathPicker({ target: 'voice:tts.voice', mode: 'file', current: state.tts.voice })">浏览</button></div></label><label><span>引擎</span><input v-model="state.tts.engine" placeholder="auto / indextts / edge_tts" /></label><label><span>语速</span><input v-model="state.tts.rate" placeholder="+0%" /></label></div>
          <button class="send-button" @click="runTts">生成语音</button><pre class="json-box">{{ state.ttsResult }}</pre>
        </section>
      </section>

      <section v-if="state.view === 'p4'" class="view active">
        <section class="wide-panel p4-hero">
          <div class="panel-title"><div><h2>P4 发布助手</h2><span>只做 UI 动画资源的安全检查、预览和 Shelve，不做 submit。</span></div><div class="button-row"><button class="small-button ghost" @click="state.p4Advanced = !state.p4Advanced">{{ state.p4Advanced ? "收起高级" : "高级设置" }}</button><button class="small-button" :disabled="isBusy('p4:status')" @click="runP4('status')">{{ isBusy('p4:status') ? "读取中" : "读取状态" }}</button></div></div>
          <div class="status-strip">
            <article v-for="card in p4Display.cards" :key="card[0]" class="status-card"><h3>{{ card[0] }}</h3><b>{{ card[1] }}</b></article>
          </div>
        </section>
        <section v-if="state.p4Advanced" class="wide-panel p4-control-panel">
          <div class="panel-title"><div><h2>高级参数</h2><span>平时不用填；只有指定 workspace、复用 CL 或覆盖 shelf 时才改。</span></div></div>
          <div class="p4-form-grid">
            <label><span>Workflow</span><input v-model="state.p4Form.workflow" placeholder="可留空，例如 spark_client_ui" /></label>
            <label><span>Workspace</span><input v-model="state.p4Form.workspace" placeholder="可留空，使用后端默认 P4CLIENT" /></label>
            <label><span>CL 编号</span><input v-model="state.p4Form.cl" placeholder="例如 123456；shelve/report/reconcile 必填" /></label>
            <label><span>CL 描述</span><input v-model="state.p4Form.description" placeholder="[UI Emoji Import] ..." /></label>
            <label class="toggle-field"><input v-model="state.p4Form.allow_delete" type="checkbox" /><span>允许删除文件。默认不勾选，delete 会被安全阻断。</span></label>
            <label class="toggle-field"><input v-model="state.p4Form.force" type="checkbox" /><span>Shelve 时允许覆盖已有 shelf。</span></label>
          </div>
        </section>
        <section class="wide-panel">
          <div class="panel-title"><div><h2>推荐操作</h2><span>{{ p4Display.title }}</span></div><button class="small-button ghost" @click="state.detail = { title: 'P4 原始详情', payload: state.p4Result || unwrap(state.status.p4) }">原始详情</button></div>
          <div class="p4-action-grid">
            <button v-for="action in p4PrimaryActions" :key="action.kind" class="p4-action-card" :disabled="isBusy(`p4:${action.kind}`)" @click="runP4Action(action)">
              <b>{{ isBusy(`p4:${action.kind}`) ? "处理中..." : action.title }}</b>
              <span>{{ action.subtitle }}</span>
              <small>{{ action.kind === "shelve_ui_import" ? "自动完成安全流程" : action.skill }}</small>
            </button>
          </div>
          <div v-if="state.p4Advanced" class="p4-action-grid compact">
            <button v-for="action in p4AdvancedActions" :key="action.kind" class="p4-action-card" :disabled="isBusy(`p4:${action.kind}`)" @click="runP4Action(action)">
              <b>{{ isBusy(`p4:${action.kind}`) ? "处理中..." : action.title }}</b>
              <span>{{ action.subtitle }}</span>
              <small>{{ action.skill }}</small>
            </button>
          </div>
          <div class="p4-summary" :class="p4Display.tone">
            <section v-for="section in p4Display.sections" :key="section.title" class="summary-section" :class="section.tone">
              <h3>{{ section.title }}</h3>
              <p v-for="line in section.lines" :key="line">{{ line }}</p>
            </section>
          </div>
        </section>
      </section>

      <section v-if="state.view === 'memory'" class="view active split-layout">
        <section class="wide-panel memory-console">
          <div class="panel-title"><div><h2>记忆检索</h2><span>直接看当前对话的摘要、最近消息和长期记忆；工程字段收在高级设置里。</span></div><div class="button-row"><button class="small-button" @click="state.memoryScope = 'global'; state.memoryConversation = 'test'; loadMemory()">读取当前 WebUI 对话</button><button class="small-button danger" @click="cleanupWithConfirm('memory', '当前 scope 记忆', { scope: state.memoryScope })">清空记忆</button><button class="small-button ghost" @click="state.memoryAdvanced = !state.memoryAdvanced">{{ state.memoryAdvanced ? "收起高级" : "高级设置" }}</button></div></div>
          <div v-if="state.memoryAdvanced" class="inline-fields memory-advanced">
            <label><span>记忆范围</span><input v-model="state.memoryScope" class="search" placeholder="默认 global" /></label>
            <label><span>对话编号</span><input v-model="state.memoryConversation" class="search" placeholder="默认 test；飞书会话通常是 feishu:..." /></label>
            <button class="small-button" @click="loadMemory">读取</button>
            <button class="small-button" @click="compactMemory">手动压缩</button>
          </div>
          <div class="memory-guide">
            <article><h3>怎么用</h3><p>平时直接点“读取当前 WebUI 对话”。只有要查某个飞书会话时，才打开高级设置填写对话编号。</p></article>
            <article><h3>后端逻辑</h3><p>当前是“全量消息 + 滚动摘要 + RAG/记忆条目”的混合结构。页面默认只展示中文摘要，原始 JSON 放进详情。</p></article>
          </div>
          <section class="summary-grid">
            <article class="summary-card info"><h3>上下文摘要</h3><p v-for="line in memorySummary" :key="line">{{ line }}</p><button class="small-button ghost" @click="state.detail = { title: 'RAG Context Pack 原始详情', payload: state.memoryPack }">查看原始详情</button></article>
            <article class="summary-card"><h3>长期记忆</h3><p v-if="!state.memoryItems.length">当前 scope 暂无长期记忆。</p><p v-for="item in state.memoryItems.slice(0, 5)" :key="item.id || item.key"><b>{{ memoryNoteTitle(item) }}：</b>{{ memoryNoteText(item).slice(0, 140) }}</p></article>
          </section>
          <div class="memory-notes">
            <article v-for="item in state.memoryItems" :key="item.id || item.key" class="log-item"><h3>{{ memoryNoteTitle(item) }}</h3><pre>{{ memoryNoteText(item) }}</pre></article>
          </div>
        </section>
        <section class="wide-panel"><div class="panel-title"><h2>检索相关技能</h2><span>{{ retrievalSkills.length }}</span></div><div class="skills-list compact"><article v-for="skill in retrievalSkills" :key="skill.name" class="skill-card"><div class="card-head"><h3>{{ skill.name }}</h3><span class="pill">{{ skill.risk_level || "readonly" }}</span></div><p>{{ skill.description }}</p></article></div></section>
      </section>

      <section v-if="state.view === 'skills'" class="view active">
        <div class="panel-title"><div><h2>技能清单</h2><span>{{ state.skills.length }}</span></div><button class="small-button" @click="loadSkills">刷新</button></div>
        <div class="skills-list"><article v-for="skill in state.skills" :key="skill.name" class="skill-card"><div class="card-head"><h3>{{ skill.name }}</h3><span class="pill">{{ skill.risk_level || "readonly" }}</span></div><p>{{ skill.description }}</p><div class="metric"><span>Domain</span><b>{{ skill.domain || "-" }}</b></div></article></div>
      </section>

      <section v-if="state.view === 'logs'" class="view active">
        <div class="panel-title"><div><h2>操作日志</h2><span>默认只读取最近记录，避免大日志把页面拖慢。</span></div><div class="inline-fields"><input v-model="state.conversationId" class="search" /><button class="small-button danger" @click="cleanupWithConfirm('brain_messages', '当前会话对话日志', { conversation_id: state.conversationId })">清空对话</button><button class="small-button danger" @click="cleanupWithConfirm('skill_calls', '技能调用日志')">清空技能日志</button><button class="small-button" :disabled="state.logsLoading" @click="loadLogs">{{ state.logsLoading ? "读取中" : "读取" }}</button></div></div>
        <article v-if="state.logsLoading" class="loading-panel">
          <b>正在读取日志</b>
          <span>日志量大时后端可能需要几十秒；页面没有卡死，你可以切到其它页面继续看状态。</span>
          <div class="indeterminate-progress"><span style="width: 72%"></span></div>
        </article>
        <article v-if="state.logError" class="log-item"><h3>日志接口不可用</h3><pre>{{ state.logError }}</pre></article>
        <div class="tabs"><button class="chip" :class="{ active: state.logTab === 'brain' }" @click="state.logTab = 'brain'">Agent 对话</button><button class="chip" :class="{ active: state.logTab === 'skills' }" @click="state.logTab = 'skills'">技能调用</button></div>
        <div v-if="state.logTab === 'brain'" class="log-table">
          <div class="log-row header"><span>时间</span><span>用户输入</span><span>Agent 回复</span><span>工具</span></div>
          <article v-if="!state.logsLoading && !state.logs.length" class="log-empty">暂无对话日志。</article>
          <div v-for="item in visibleLogs" :key="item.id || item.created_at" class="log-row"><span>{{ item.created_at || "-" }}</span><span>{{ item.message_text || "-" }}</span><span>{{ item.response_text || "-" }}</span><button class="small-button ghost" @click="state.detail = { title: '工具调用', payload: item.tool_calls_json }">详情</button></div>
          <button v-if="state.logs.length > visibleLogs.length" class="small-button ghost load-more" @click="state.logVisibleLimit += VISIBLE_LOG_LIMIT">显示更多 {{ state.logs.length - visibleLogs.length }} 条</button>
        </div>
        <div v-else class="log-table">
          <div class="log-row header"><span>时间</span><span>技能</span><span>请求方</span><span>结果</span></div>
          <article v-if="!state.logsLoading && !state.skillCalls.length" class="log-empty">暂无技能调用日志。</article>
          <div v-for="item in visibleSkillCalls" :key="item.id || item.created_at" class="log-row"><span>{{ item.created_at || "-" }}</span><span>{{ item.skill }}</span><span>{{ item.requested_by }}</span><button class="small-button" :class="{ danger: !item.ok }" @click="state.detail = { title: item.skill, payload: item }">{{ item.ok ? "成功" : "失败" }}</button></div>
          <button v-if="state.skillCalls.length > visibleSkillCalls.length" class="small-button ghost load-more" @click="state.logVisibleLimit += VISIBLE_LOG_LIMIT">显示更多 {{ state.skillCalls.length - visibleSkillCalls.length }} 条</button>
        </div>
      </section>
    </main>
  </div>

  <div v-if="state.configModule" class="modal-backdrop" @click.self="closeAllModals">
    <aside class="drawer">
      <div class="drawer-head"><div><h2>{{ modules[state.configModule].title }}</h2><span>{{ modules[state.configModule].subtitle }}</span></div><button class="icon-button" @click="closeAllModals">×</button></div>
      <div class="config-form drawer-form">
        <label v-for="field in modules[state.configModule].fields" :key="field.key" :class="{ 'wide-field': field.type === 'path' }">
          <span>{{ field.label }}</span>
          <div v-if="field.type === 'path'" class="path-input"><input v-model="state.configValues[field.key]" :placeholder="String(field.value || '')" /><button class="small-button" @click="openPathPicker({ target: `config:${field.key}`, mode: field.pathMode, current: state.configValues[field.key] })">浏览</button></div>
          <label v-else-if="field.type === 'checkbox'" class="toggle-field"><input v-model="state.configValues[field.key]" type="checkbox" /><span>{{ field.help }}</span></label>
          <input v-else v-model="state.configValues[field.key]" :type="field.type" :placeholder="String(field.value ?? '')" />
          <small v-if="field.type !== 'checkbox'">{{ field.help }}</small>
        </label>
      </div>
      <pre v-if="state.configResult" class="json-box">{{ state.configResult }}</pre>
      <div class="drawer-actions"><button class="small-button" @click="saveConfig">保存</button><button class="small-button" @click="callConfig(true)">预览</button><button class="send-button" @click="callConfig(false)">调用后端</button></div>
    </aside>
  </div>

  <div v-if="selectedFlowStep" class="modal-backdrop" @click.self="closeAllModals">
    <aside class="drawer">
      <div class="drawer-head">
        <div><h2>{{ selectedFlowStep.name }}</h2><span>这是流程里这一小步的参数，只影响当前自定义流程。</span></div>
        <button class="icon-button" @click="closeAllModals">×</button>
      </div>
      <div class="config-form drawer-form">
        <label><span>步骤 ID</span><input v-model="selectedFlowStep.id" /></label>
        <label><span>显示名称</span><input v-model="selectedFlowStep.name" /></label>
        <label class="wide-field"><span>后端模块</span><select v-model="selectedFlowStep.skill" @change="onFlowSkillChange(selectedFlowStep)"><option v-for="skill in flowSkills" :key="skill">{{ skill }}</option></select><small>这里只选择用哪个模块。模块默认参数可在“总控”里调整。</small></label>
        <label v-for="field in flowStepFields(selectedFlowStep)" :key="field.key" :class="{ 'wide-field': field.type === 'path' }">
          <span>{{ field.label }}</span>
          <div v-if="field.type === 'path'" class="path-input">
            <input v-model="selectedFlowStep.arguments[field.key]" :placeholder="String(field.value || '')" />
            <button class="small-button" @click="openPathPicker({ target: `flowstep:${state.flowStepConfigIndex}:${field.key}`, mode: field.pathMode || 'dir', current: selectedFlowStep.arguments[field.key] })">浏览</button>
          </div>
          <label v-else-if="field.type === 'checkbox'" class="toggle-field"><input v-model="selectedFlowStep.arguments[field.key]" type="checkbox" /><span>{{ field.help }}</span></label>
          <input v-else v-model="selectedFlowStep.arguments[field.key]" :type="field.type" :placeholder="String(field.value ?? '')" />
          <small v-if="field.type !== 'checkbox'">{{ field.help }}</small>
        </label>
      </div>
      <div class="drawer-actions"><button class="small-button" @click="closeAllModals">关闭</button><button class="send-button" @click="closeAllModals">保存到流程</button></div>
    </aside>
  </div>

  <div v-if="state.pathPicker" class="modal-backdrop" @click.self="closeAllModals">
    <aside class="drawer path-drawer">
      <div class="drawer-head"><div><h2>手动填写路径</h2><span>系统选择器取消或不可用时，用这里直接填后端可访问的 Windows 路径。</span></div><button class="icon-button" @click="closeAllModals">×</button></div>
      <section class="path-hero">
        <article v-if="state.pathDialogError" class="inline-alert warn">{{ state.pathDialogError }}</article>
        <label><span>路径</span><div class="path-input"><input v-model="state.pathManual" @keydown.enter.prevent="choosePath({ path: state.pathManual || state.pathPicker.current })" /><button class="send-button" @click="choosePath({ path: state.pathManual || state.pathPicker.current })">使用</button></div></label>
        <div class="path-toolbar"><button class="small-button" @click="openPathPicker({ target: state.pathPicker.target, mode: state.pathPicker.mode, current: state.pathManual || state.pathPicker.current })">重新打开系统选择器</button><button class="small-button" @click="state.pathManual = workspacePath()">当天工作区</button><button class="small-button" @click="state.pathManual = 'E:/'">E:</button><button class="small-button" @click="state.pathManual = 'D:/'">D:</button></div>
        <p class="muted">不会再自动扫描目录；这里仅把路径填回表单，实际权限由后端执行时判断。</p>
      </section>
      <div class="path-empty"><b>路径选择已改为系统窗口优先</b><span>如果你刚刚取消了 Windows 选择器，可以直接在上面粘贴路径。</span></div>
    </aside>
  </div>

  <div v-if="state.settingsOpen" class="modal-backdrop" @click.self="closeAllModals">
    <aside class="drawer settings-drawer">
      <div class="drawer-head"><div><h2>高级设置</h2><span>连接、指令面板、模块库和已保存流程都收在这里。</span></div><button class="icon-button" @click="closeAllModals">×</button></div>
      <section class="advanced-section">
        <h3>指令面板</h3>
        <input v-model="state.commandQuery" class="command-search" placeholder="输入：状态 / 流程 / 日志 / 路径 / ASR / P4 ..." />
        <div class="command-list compact"><button v-for="command in filteredCommands" :key="command.id" class="command-item" @click="runCommand(command)"><span>{{ command.title }}</span><b>{{ command.hint }}</b></button></div>
      </section>
      <section class="advanced-section">
        <h3>连接</h3>
        <label><span>X-Skill-Token 覆盖值</span><input v-model="state.token" type="password" placeholder="通常留空，代理会读取项目 .env" @change="onTokenChange" /><small>只有后端开启 skill API 校验并且你要手动覆盖时才填。</small></label>
      </section>
      <section class="advanced-section">
        <h3>维护清理</h3>
        <div class="button-row wrap">
          <button class="small-button danger" @click="cleanupWithConfirm('agent_jobs', 'Agent 后台 job 状态')">清 Agent Jobs</button>
          <button class="small-button danger" @click="cleanupWithConfirm('production_runs', '生产任务历史')">清生产队列</button>
          <button class="small-button danger" @click="cleanupWithConfirm('custom_pipeline_runs', '自定义流程运行记录')">清流程运行</button>
          <button class="small-button danger" @click="cleanupWithConfirm('all_brain_messages', '全部对话日志')">清全部对话</button>
          <button class="small-button danger" @click="cleanupWithConfirm('skill_calls', '全部技能日志')">清技能日志</button>
          <button class="small-button danger" @click="cleanupWithConfirm('all_memory', '全部记忆和摘要')">清全部记忆</button>
        </div>
      </section>
      <section class="advanced-section">
        <div class="panel-title"><h3>已保存流程</h3><button class="small-button" @click="loadFlowDefinitions">刷新</button></div>
        <article v-if="!state.flowDefinitions.length" class="log-item"><h3>暂无保存记录</h3><pre>保存后会出现在这里。</pre></article>
        <article v-for="flow in state.flowDefinitions" :key="flow.name" class="saved-flow-card">
          <div><h3>{{ flow.name }}</h3><span>{{ flow.description || flow.path }}</span></div>
          <button class="small-button" @click="loadFlowDefinition(flow.name); closeAllModals(); switchView('builder')">载入</button>
        </article>
      </section>
      <section class="advanced-section">
        <h3>模块库</h3>
        <button v-for="module in flowModules" :key="module.key" class="module-palette-card" @click="addFlowStep(module.key); switchView('builder'); closeAllModals()">
          <strong>{{ module.title }}</strong><span>{{ module.subtitle }}</span><b>{{ module.key }}</b>
        </button>
      </section>
    </aside>
  </div>

  <div v-if="state.commandOpen" class="modal-backdrop" @click.self="closeAllModals">
    <aside class="command-dialog">
      <div class="drawer-head"><div><h2>Miku 指令面板</h2><span>搜索页面、后端动作、流程操作。</span></div><button class="icon-button" @click="closeAllModals">×</button></div>
      <input v-model="state.commandQuery" class="command-search" placeholder="输入：状态 / 流程 / 日志 / 路径 / ASR / RAG ..." />
      <div class="command-list"><button v-for="command in filteredCommands" :key="command.id" class="command-item" @click="runCommand(command)"><span>{{ command.title }}</span><b>{{ command.hint }}</b></button></div>
    </aside>
  </div>

  <div v-if="state.detail" class="modal-backdrop" @click.self="closeAllModals">
    <aside class="drawer detail-drawer"><div class="drawer-head"><h2>{{ state.detail.title }}</h2><button class="icon-button" @click="closeAllModals">×</button></div><pre class="json-box">{{ JSON.stringify(state.detail.payload, null, 2) }}</pre></aside>
  </div>

  <div class="toast-host"><div v-for="item in state.toasts" :key="item.id" class="toast" :class="item.type"><b>{{ item.type === "bad" ? "注意" : item.type === "ok" ? "完成" : "提示" }}</b><span>{{ item.message }}</span></div></div>
</template>
