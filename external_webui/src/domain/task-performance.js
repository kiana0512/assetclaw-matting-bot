const TERMINAL = new Set(["DONE", "FAILED", "CANCELED", "BLOCKED", "DONE_WITH_ERRORS"]);
const DONE = new Set(["DONE", "DONE_WITH_ERRORS"]);

export function buildTaskPerformance(task, nowMs = Date.now()) {
  const raw = task?.raw || {};
  const status = String(task?.status || raw.status || "UNKNOWN").toUpperCase();
  const startMs = parseTimestamp(raw.created_at || raw.createdAt || task?.timeValue);
  const delivery = deliveryTimestamp(raw);
  const terminalUpdated = parseTimestamp(raw.finished_at || raw.completed_at || raw.updated_at);
  const isTerminal = TERMINAL.has(status);
  const endMs = delivery || (isTerminal ? terminalUpdated : nowMs) || nowMs;
  const totalMs = validRange(startMs, endMs) ? endMs - startMs : null;
  const direct = ["DIRECT_IMAGE", "DIRECT_VIDEO"].includes(String(task?.module || ""));
  const segments = direct
    ? buildDirectSegments(task, startMs, endMs, nowMs)
    : buildGenericSegments(task, startMs, endMs);
  const normalized = segments
    .filter((item) => item.durationMs >= 0)
    .map((item) => ({
      ...item,
      share: totalMs > 0 ? Math.min(1, item.durationMs / totalMs) : 0,
    }));
  const bottleneck = normalized
    .filter((item) => item.durationMs > 0 && item.baselineEligible !== false && item.key !== "other")
    .sort((a, b) => b.durationMs - a.durationMs)[0] || null;
  const frames = taskVolume(task);
  const matting = normalized.find((item) => item.key === "matting");
  const throughputEligible = DONE.has(status) || !isTerminal;
  const throughput = throughputEligible && matting?.durationMs > 0 && frames > 0
    ? frames / (matting.durationMs / 60000)
    : null;
  const throughputUnit = task?.module === "DIRECT_VIDEO" ? "帧/分" : task?.module === "DIRECT_IMAGE" ? "张/分" : "个/分";
  const measuredMs = normalized.filter((item) => item.confidence === "measured").reduce((sum, item) => sum + item.durationMs, 0);
  const coveredMs = normalized.reduce((sum, item) => sum + item.durationMs, 0);
  const coverage = totalMs > 0 ? Math.min(1, coveredMs / totalMs) : 0;
  const measuredCoverage = totalMs > 0 ? Math.min(1, measuredMs / totalMs) : 0;
  const backend = mattingBackend(raw);

  return {
    key: `${task?.module || "TASK"}:${task?.id || raw.id || raw.run_id || ""}`,
    id: task?.id || raw.id || raw.run_id || "",
    name: task?.name || task?.label || raw.run_label || raw.id || raw.run_id || "未命名任务",
    label: task?.label || task?.module || "任务",
    module: task?.module || "TASK",
    category: task?.category || "standalone",
    status,
    startMs,
    endMs,
    totalMs,
    active: !isTerminal,
    successful: DONE.has(status),
    delivered: Boolean(delivery),
    frames,
    throughput,
    throughputUnit,
    backend,
    segments: normalized,
    bottleneck,
    bottleneckReason: bottleneckReason(bottleneck, backend, raw),
    coverage,
    measuredCoverage,
    source: task,
  };
}

export function summarizePerformance(records) {
  const successful = records.filter((item) => item.successful && item.totalMs > 0);
  const completed = records.filter((item) => TERMINAL.has(item.status) && item.totalMs > 0);
  const totals = successful.map((item) => item.totalMs);
  const throughputs = successful
    .filter((item) => item.module === "DIRECT_VIDEO")
    .map((item) => item.throughput)
    .filter((value) => Number.isFinite(value));
  const stages = stageBenchmarks(successful);
  const dominant = [...stages].sort((a, b) => b.totalMs - a.totalMs)[0] || null;
  return {
    sampleCount: records.length,
    completedCount: completed.length,
    successfulCount: successful.length,
    deliveredCount: successful.filter((item) => item.delivered).length,
    averageMs: average(totals),
    p50Ms: percentile(totals, 0.5),
    p90Ms: percentile(totals, 0.9),
    averageThroughput: average(throughputs),
    averageMeasuredCoverage: average(successful.map((item) => item.measuredCoverage)),
    stages,
    dominant,
  };
}

export function stageBenchmarks(records) {
  const buckets = new Map();
  for (const record of records) {
    for (const segment of record.segments || []) {
      if (!(segment.durationMs >= 0) || segment.baselineEligible === false || segment.key === "other") continue;
      const current = buckets.get(segment.key) || {
        key: segment.key,
        label: segment.label,
        durations: [],
        totalMs: 0,
        measured: 0,
      };
      current.durations.push(segment.durationMs);
      current.totalMs += segment.durationMs;
      if (segment.confidence === "measured") current.measured += 1;
      buckets.set(segment.key, current);
    }
  }
  const grandTotal = [...buckets.values()].reduce((sum, item) => sum + item.totalMs, 0);
  return [...buckets.values()]
    .map((item) => ({
      key: item.key,
      label: item.label,
      sampleCount: item.durations.length,
      measuredCount: item.measured,
      averageMs: average(item.durations),
      p50Ms: percentile(item.durations, 0.5),
      maxMs: item.durations.length ? Math.max(...item.durations) : null,
      totalMs: item.totalMs,
      share: grandTotal > 0 ? item.totalMs / grandTotal : 0,
    }))
    .sort((a, b) => b.totalMs - a.totalMs);
}

export function formatDurationMs(value, { compact = false } = {}) {
  if (!Number.isFinite(value) || value < 0) return "暂无数据";
  const seconds = Math.max(0, Math.round(value / 1000));
  if (seconds < 60) return `${seconds}秒`;
  const minutes = Math.floor(seconds / 60);
  const restSeconds = seconds % 60;
  if (minutes < 60) return compact || restSeconds === 0 ? `${minutes}分` : `${minutes}分${restSeconds}秒`;
  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;
  return restMinutes ? `${hours}小时${restMinutes}分` : `${hours}小时`;
}

export function formatTimestamp(value) {
  const ms = typeof value === "number" ? value : parseTimestamp(value);
  if (!Number.isFinite(ms)) return "-";
  const date = new Date(ms);
  return `${String(date.getMonth() + 1).padStart(2, "0")}/${String(date.getDate()).padStart(2, "0")} ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}:${String(date.getSeconds()).padStart(2, "0")}`;
}

export function parseTimestamp(value) {
  if (value === undefined || value === null || value === "") return null;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return null;
    return value > 1e12 ? value : value > 1e9 ? value * 1000 : value;
  }
  const text = String(value).trim();
  if (/^\d{10,16}$/.test(text)) {
    const numeric = Number(text);
    return text.length >= 13 ? numeric : numeric * 1000;
  }
  const localIso = text.replace(" ", "T");
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(localIso)) {
    return Date.parse(`${localIso}+08:00`);
  }
  const parsed = Date.parse(text);
  return Number.isFinite(parsed) ? parsed : null;
}

function buildDirectSegments(task, startMs, endMs, nowMs) {
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) return [];
  const raw = task.raw || {};
  const status = String(task.status || raw.status || "").toUpperCase();
  const stage = String(raw.stage || task.stage || "").toLowerCase();
  const comfy = raw.children?.comfyui && typeof raw.children.comfyui === "object" ? raw.children.comfyui : {};
  const comfyStatus = String(comfy.status || "").toUpperCase();
  const attempts = cherryAttempts(raw);
  const postStart = minTime(attempts.map((item) => parseTimestamp(item.started_at)));
  const postFinished = maxTime(attempts.map((item) => parseTimestamp(item.finished_at)));
  const postActive = (stage.includes("post") || stage.includes("cherry") || stage.includes("smooth")) && !TERMINAL.has(status);
  const postEnd = postActive ? nowMs : postFinished;
  const handshake = parseTimestamp(
    comfy.backend_handshake?.checked_at
      || comfy.started_at
      || comfy.created_at
  );
  const mattingStart = clampTime(handshake || startMs, startMs, endMs);
  const comfyUpdated = parseTimestamp(comfy.finished_at || comfy.completed_at || comfy.updated_at);
  if (!handshake && !comfyUpdated && !postStart) {
    return [{
      key: "other",
      label: "任务总生命周期",
      startMs,
      endMs,
      durationMs: endMs - startMs,
      confidence: "inferred",
      baselineEligible: false,
      note: "该旧任务只有起止时间，不能把中间时段误判为打包、回传或 GPU 计算",
    }];
  }
  let mattingEnd = null;
  if (["DONE", "FAILED", "CANCELED"].includes(comfyStatus) && comfyUpdated) mattingEnd = comfyUpdated;
  else if (postStart) mattingEnd = postStart;
  else if (stage.includes("mat") || stage.includes("comfy")) mattingEnd = nowMs;
  else if (comfyUpdated) mattingEnd = comfyUpdated;
  mattingEnd = clampTime(mattingEnd || mattingStart, mattingStart, postStart ? Math.min(postStart, endMs) : endMs);

  const segments = [];
  addSegment(segments, {
    key: "prepare",
    label: task.module === "DIRECT_VIDEO" ? "任务创建与抽帧" : "任务创建与准备",
    startMs,
    endMs: mattingStart,
    confidence: handshake ? "measured" : "inferred",
    note: task.module === "DIRECT_VIDEO" ? "父任务创建后到抠图握手，包含抽帧准备" : "父任务创建后到抠图握手的准备时间",
  });
  addSegment(segments, {
    key: "matting",
    label: `${mattingBackend(raw) === "GPU集群" ? "GPU 集群" : "本机"}抠图（含排队）`,
    startMs: mattingStart,
    endMs: mattingEnd,
    confidence: handshake && (comfyUpdated || postStart || !TERMINAL.has(status)) ? "measured" : "inferred",
    note: "现有数据未将服务端排队与纯 GPU 计算拆开",
  });

  if (postStart && postStart > mattingEnd) {
    addSegment(segments, {
      key: "handoff",
      label: raw.character_resolution?.pending || stage.includes("character") ? "等待角色确认" : "后处理准备",
      startMs: mattingEnd,
      endMs: postStart,
      confidence: "inferred",
      note: "由抠图结束与 Cherry 开始之间的时间差推导",
    });
  } else if (!postStart && stage.includes("character")) {
    addSegment(segments, {
      key: "handoff",
      label: "等待角色确认",
      startMs: mattingEnd,
      endMs,
      confidence: "measured",
      note: "当前任务停在角色确认阶段",
    });
  }

  if (postStart) {
    addSegment(segments, {
      key: "postprocess",
      label: "Cherry 后处理",
      startMs: clampTime(postStart, mattingEnd, endMs),
      endMs: clampTime(postEnd || postStart, postStart, endMs),
      confidence: postFinished || postActive ? "measured" : "inferred",
      note: "取无头浏览器 HTML 执行尝试的最早开始到最晚结束",
    });
  }

  const lastStageEnd = segments.length ? segments.at(-1).endMs : mattingEnd;
  const hasDeliveryReceipt = Boolean(deliveryTimestamp(raw));
  const isDeliveryStage = stage.includes("send") || stage.includes("zip") || stage.includes("delivery");
  const reachedDelivery = hasDeliveryReceipt || isDeliveryStage || (DONE.has(status) && Boolean(postStart));
  if (endMs > lastStageEnd && reachedDelivery) {
    addSegment(segments, {
      key: "delivery",
      label: "打包与飞书回传",
      startMs: lastStageEnd,
      endMs,
      confidence: hasDeliveryReceipt ? "measured" : "inferred",
      note: hasDeliveryReceipt ? "结束时间来自飞书文件回执" : "任务已进入打包或发送阶段，结束时间来自最后更新时间",
    });
  } else if (endMs > lastStageEnd) {
    addSegment(segments, {
      key: "other",
      label: "任务等待或状态收尾",
      startMs: lastStageEnd,
      endMs,
      confidence: "inferred",
      baselineEligible: false,
      note: "没有打包或飞书回传证据，该时段不计入阶段速度基线",
    });
  }

  if (!segments.length) {
    addSegment(segments, {
      key: "other",
      label: task.stageLabel || "任务处理",
      startMs,
      endMs,
      confidence: "inferred",
      note: "旧任务没有足够的独立阶段时间点",
    });
  }
  return segments;
}

function buildGenericSegments(task, startMs, endMs) {
  if (!validRange(startMs, endMs)) return [];
  const raw = task?.raw || {};
  const stages = Array.isArray(raw.stages) ? raw.stages : [];
  const timed = stages
    .map((stage) => ({
      key: stage.key || "other",
      label: stage.label || stage.key || "任务阶段",
      startMs: parseTimestamp(stage.started_at || stage.created_at),
      endMs: parseTimestamp(stage.finished_at || stage.completed_at || stage.updated_at),
      confidence: "measured",
      note: "来自流程阶段时间字段",
    }))
    .filter((stage) => validRange(stage.startMs, stage.endMs));
  if (timed.length) return timed.map((item) => ({ ...item, durationMs: item.endMs - item.startMs }));
  return [{
    key: "other",
    label: task.stageLabel || task.label || "任务总耗时",
    startMs,
    endMs,
    durationMs: endMs - startMs,
    confidence: "inferred",
    baselineEligible: false,
    note: "该类旧任务只有起止时间，暂无逐阶段埋点",
  }];
}

function addSegment(target, segment) {
  if (!validRange(segment.startMs, segment.endMs)) return;
  target.push({ ...segment, durationMs: segment.endMs - segment.startMs });
}

function cherryAttempts(raw) {
  const children = raw.children || {};
  const runs = [];
  const declaredIds = [...new Set([
    ...(Array.isArray(children.cherry_run_ids) ? children.cherry_run_ids : []),
    children.cherry_run_id,
  ].filter(Boolean))];
  if (declaredIds.length && children.cherry_runs && typeof children.cherry_runs === "object") {
    for (const id of declaredIds) {
      if (children.cherry_runs[id] && typeof children.cherry_runs[id] === "object") runs.push(children.cherry_runs[id]);
    }
  }
  if (!runs.length && children.cherry && typeof children.cherry === "object") runs.push(children.cherry);
  const seen = new Set();
  const result = [];
  for (const run of runs) {
    const candidates = [
      ...(Array.isArray(run?.options?.html_attempts) ? run.options.html_attempts : []),
      ...(Array.isArray(run?.html_attempts) ? run.html_attempts : []),
    ];
    for (const attempt of candidates) {
      const key = `${attempt?.started_at || ""}|${attempt?.finished_at || ""}|${attempt?.input_dir || ""}`;
      if (!attempt || seen.has(key)) continue;
      seen.add(key);
      result.push(attempt);
    }
  }
  return result;
}

function deliveryTimestamp(raw) {
  return parseTimestamp(
    raw.drive_file?.create_time
      || raw.delivery?.delivered_at
      || raw.delivery?.sent_at
      || raw.delivery?.completed_at
  );
}

function taskVolume(task) {
  const raw = task?.raw || {};
  if (Array.isArray(raw.videos)) return raw.videos.reduce((sum, item) => sum + Number(item?.frame_count || 0), 0);
  if (Array.isArray(raw.images)) return raw.images.length;
  const comfy = raw.children?.comfyui || {};
  return Number(comfy.total || raw.total || 0);
}

function mattingBackend(raw) {
  const backend = String(raw.children?.comfyui?.backend || raw.matting_backend || "").toLowerCase();
  if (backend === "gpu_control" || backend.includes("remote") || backend.includes("cluster")) return "GPU集群";
  if (backend) return "本机";
  return "未记录";
}

function bottleneckReason(segment, backend, raw) {
  if (!segment) return "暂无足够的阶段数据";
  if (segment.key === "matting") {
    const queue = Number(raw.children?.comfyui?.backend_handshake?.capacity?.queue_depth || 0);
    return backend === "GPU集群"
      ? `抠图阶段占时最高；统计包含集群排队${queue > 0 ? `（握手时队列 ${queue}）` : ""}`
      : "本机抠图阶段占时最高；统计包含本机任务等待";
  }
  if (segment.key === "postprocess") return "Cherry HTML 后处理占时最高，可重点检查批次大小与浏览器启动成本";
  if (segment.key === "delivery") return "ZIP 打包或飞书上传占时最高，通常与产物体积和网络上传有关";
  if (segment.key === "handoff") return "阶段衔接或角色确认等待占时最高，不是 GPU 计算变慢";
  if (segment.key === "prepare") return "附件接收、落盘或视频抽帧占时最高";
  return "该任务缺少更细的阶段时间点，当前只能定位到任务级耗时";
}

function validRange(start, end) {
  return Number.isFinite(start) && Number.isFinite(end) && end >= start;
}

function clampTime(value, min, max) {
  if (!Number.isFinite(value)) return min;
  return Math.max(min, Math.min(max, value));
}

function minTime(values) {
  const filtered = values.filter(Number.isFinite);
  return filtered.length ? Math.min(...filtered) : null;
}

function maxTime(values) {
  const filtered = values.filter(Number.isFinite);
  return filtered.length ? Math.max(...filtered) : null;
}

function average(values) {
  const filtered = values.filter(Number.isFinite);
  return filtered.length ? filtered.reduce((sum, value) => sum + value, 0) / filtered.length : null;
}

function percentile(values, ratio) {
  const filtered = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!filtered.length) return null;
  const index = (filtered.length - 1) * ratio;
  const lower = Math.floor(index);
  const upper = Math.ceil(index);
  if (lower === upper) return filtered[lower];
  return filtered[lower] + (filtered[upper] - filtered[lower]) * (index - lower);
}
