import assert from "node:assert/strict";
import test from "node:test";

import {
  buildTaskPerformance,
  formatDurationMs,
  parseTimestamp,
  summarizePerformance,
} from "../src/domain/task-performance.js";

function videoTask(overrides = {}) {
  return {
    module: "DIRECT_VIDEO",
    category: "video",
    label: "视频直发",
    id: "VID_SAMPLE",
    name: "sample.mp4",
    status: "DONE",
    raw: {
      status: "DONE",
      stage: "done",
      created_at: "2026-07-28T09:29:23Z",
      updated_at: "2026-07-28T10:31:42Z",
      videos: [{ frame_count: 97 }],
      children: {
        comfyui: {
          status: "DONE",
          backend: "gpu_control",
          backend_handshake: { checked_at: "2026-07-28T09:29:25Z", capacity: { queue_depth: 9 } },
          updated_at: "2026-07-28T10:23:00Z",
        },
        cherry_runs: {
          CHERRY_SAMPLE: {
            options: {
              html_attempts: [
                { started_at: "2026-07-28T10:23:07Z", finished_at: "2026-07-28T10:29:45Z", input_dir: "sample" },
              ],
            },
          },
        },
      },
      delivery: { delivered_at: "2026-07-28T10:31:42Z" },
      ...overrides,
    },
  };
}

test("parses Feishu epoch milliseconds and ISO timestamps", () => {
  assert.equal(parseTimestamp("1785234702000"), 1785234702000);
  assert.equal(parseTimestamp("1785234702"), 1785234702000);
  assert.equal(parseTimestamp("2026-07-28T10:31:42Z"), Date.parse("2026-07-28T10:31:42Z"));
});

test("builds a non-overlapping direct video critical path", () => {
  const result = buildTaskPerformance(videoTask(), Date.parse("2026-07-28T11:00:00Z"));
  assert.equal(result.totalMs, 62 * 60 * 1000 + 19 * 1000);
  assert.equal(result.delivered, true);
  assert.equal(result.backend, "GPU集群");
  assert.deepEqual(result.segments.map((item) => item.key), ["prepare", "matting", "handoff", "postprocess", "delivery"]);
  assert.equal(result.segments[0].durationMs, 2000);
  assert.equal(result.segments[1].durationMs, 53 * 60 * 1000 + 35 * 1000);
  assert.equal(result.segments[2].durationMs, 7000);
  assert.equal(result.segments[3].durationMs, 6 * 60 * 1000 + 38 * 1000);
  assert.equal(result.segments[4].durationMs, 117000);
  assert.equal(result.segments.reduce((sum, item) => sum + item.durationMs, 0), result.totalMs);
  assert.equal(result.bottleneck.key, "matting");
  assert.match(result.bottleneckReason, /队列 9/);
  assert.ok(result.throughput > 1.7 && result.throughput < 1.9);
});

test("keeps active matting elapsed time live", () => {
  const task = videoTask({
    status: "RUNNING",
    stage: "matting",
    updated_at: "2026-07-28T09:40:00Z",
    delivery: {},
    children: {
      comfyui: {
        status: "RUNNING",
        backend: "gpu_control",
        backend_handshake: { checked_at: "2026-07-28T09:29:25Z" },
        updated_at: "2026-07-28T09:40:00Z",
      },
    },
  });
  task.status = "RUNNING";
  const now = Date.parse("2026-07-28T09:59:23Z");
  const result = buildTaskPerformance(task, now);
  assert.equal(result.active, true);
  assert.equal(result.totalMs, 30 * 60 * 1000);
  assert.equal(result.segments.at(-1).endMs, now);
  assert.equal(result.bottleneck.key, "matting");
});

test("marks a terminal task without a delivery receipt as not delivered", () => {
  const task = videoTask({ delivery: {}, drive_file: {}, updated_at: "2026-07-28T10:31:42Z" });
  const result = buildTaskPerformance(task);
  assert.equal(result.delivered, false);
  assert.equal(result.endMs, Date.parse("2026-07-28T10:31:42Z"));
});

test("summarizes successful records without counting failed totals", () => {
  const good = buildTaskPerformance(videoTask());
  const failedTask = videoTask({ status: "FAILED", delivery: {}, updated_at: "2026-07-28T09:39:23Z" });
  failedTask.status = "FAILED";
  const failed = buildTaskPerformance(failedTask);
  const summary = summarizePerformance([good, failed]);
  assert.equal(summary.sampleCount, 2);
  assert.equal(summary.completedCount, 2);
  assert.equal(summary.successfulCount, 1);
  assert.equal(summary.p50Ms, good.totalMs);
  assert.equal(summary.dominant.key, "matting");
});

test("formats missing, short and hour durations clearly", () => {
  assert.equal(formatDurationMs(null), "暂无数据");
  assert.equal(formatDurationMs(42_000), "42秒");
  assert.equal(formatDurationMs(125_000), "2分5秒");
  assert.equal(formatDurationMs(3_660_000), "1小时1分");
});
