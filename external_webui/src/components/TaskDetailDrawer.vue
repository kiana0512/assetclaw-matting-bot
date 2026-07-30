<script setup>
import { onBeforeUnmount, onMounted } from "vue";
import { formatDurationMs, formatTimestamp } from "../domain/task-performance.js";

const props = defineProps({
  task: { type: Object, required: true },
  performance: { type: Object, required: true },
});

const emit = defineEmits(["close"]);

function closeOnEscape(event) {
  if (event.key === "Escape") emit("close");
}

onMounted(() => window.addEventListener("keydown", closeOnEscape));
onBeforeUnmount(() => window.removeEventListener("keydown", closeOnEscape));

function statusClass(status) {
  const value = String(status || "").toLowerCase();
  if (["done", "done_with_errors"].includes(value)) return "ok";
  if (["failed", "blocked"].includes(value)) return "bad";
  if (["running", "queued", "waiting_character"].includes(value)) return "live";
  return "muted";
}
</script>

<template>
  <div class="detail-backdrop" @click.self="emit('close')">
    <section class="performance-detail" role="dialog" aria-modal="true" aria-labelledby="performance-detail-title">
      <header>
        <div class="detail-identity">
          <span>{{ task.label }} · {{ task.id }}</span>
          <h2 id="performance-detail-title">{{ performance.name }}</h2>
        </div>
        <div class="detail-head-actions">
          <span :class="['detail-status', statusClass(performance.status)]">{{ performance.status }}</span>
          <button class="ghost" @click="emit('close')">关闭</button>
        </div>
      </header>

      <div class="detail-body">
        <section class="detail-summary">
          <article><span>端到端耗时</span><b>{{ formatDurationMs(performance.totalMs) }}</b><small>{{ performance.active ? "仍在持续计时" : performance.delivered ? "结束于飞书回执" : "结束于任务最后状态" }}</small></article>
          <article><span>最慢步骤</span><b>{{ performance.bottleneck?.label || "暂无拆分" }}</b><small>{{ performance.bottleneck ? `${Math.round(performance.bottleneck.share * 100)}% · ${formatDurationMs(performance.bottleneck.durationMs)}` : "旧任务缺少阶段时间点" }}</small></article>
          <article><span>抠图吞吐</span><b>{{ performance.throughput ? `${performance.throughput.toFixed(1)} ${performance.throughputUnit}` : "暂无数据" }}</b><small>{{ performance.frames ? `${performance.frames} 个处理单元 · ${performance.backend}` : performance.backend }}</small></article>
          <article><span>实测覆盖率</span><b>{{ Math.round(performance.measuredCoverage * 100) }}%</b><small>推导区间会明确标识，不伪造时间点</small></article>
        </section>

        <section class="bottleneck-callout">
          <span>速度关键 key</span>
          <b>{{ performance.bottleneckReason }}</b>
        </section>

        <section class="timeline-section">
          <div class="section-title"><div><h3>关键路径时间线</h3><p>{{ formatTimestamp(performance.startMs) }} → {{ performance.active ? "现在" : formatTimestamp(performance.endMs) }}</p></div><span>{{ performance.segments.length }} 个阶段</span></div>
          <div v-if="!performance.segments.length" class="timeline-empty">该任务没有可用的阶段时间点。</div>
          <article v-for="(segment, index) in performance.segments" :key="segment.key + index" class="timeline-step">
            <div class="step-index"><i></i><span>{{ index + 1 }}</span></div>
            <div class="step-copy">
              <div><b>{{ segment.label }}</b><em :class="segment.confidence">{{ segment.confidence === "measured" ? "实测" : "推导" }}</em></div>
              <small>{{ formatTimestamp(segment.startMs) }} → {{ formatTimestamp(segment.endMs) }}</small>
              <p>{{ segment.note }}</p>
            </div>
            <div class="step-duration"><b>{{ formatDurationMs(segment.durationMs) }}</b><span>{{ Math.round(segment.share * 100) }}%</span></div>
            <div class="step-bar"><i :style="{ width: `${Math.max(2, segment.share * 100)}%` }"></i></div>
          </article>
        </section>

        <section class="detail-meta">
          <div><span>启动时间</span><b>{{ formatTimestamp(performance.startMs) }}</b></div>
          <div><span>结束 / 当前时间</span><b>{{ formatTimestamp(performance.endMs) }}</b></div>
          <div><span>输入</span><b :title="task.input">{{ task.input || "-" }}</b></div>
          <div><span>输出</span><b :title="task.output">{{ task.output || "-" }}</b></div>
        </section>

        <details class="raw-detail">
          <summary>技术原始数据</summary>
          <pre>{{ JSON.stringify(task.raw, null, 2) }}</pre>
        </details>
      </div>
    </section>
  </div>
</template>

<style scoped>
.detail-backdrop { position: fixed; inset: 0; z-index: 30; display: grid; place-items: center; padding: 20px; background: rgba(2, 3, 12, .72); backdrop-filter: blur(6px); }
.performance-detail { width: min(1080px, 100%); max-height: min(860px, 94vh); overflow: auto; border: 1px solid var(--line-strong); border-radius: 9px; background: var(--surface); box-shadow: 0 26px 80px rgba(0,0,0,.48); }
.performance-detail > header { position: sticky; top: 0; z-index: 2; display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 16px 20px; border-bottom: 1px solid var(--line); background: color-mix(in srgb, var(--surface) 94%, transparent); backdrop-filter: blur(14px); }
.detail-identity { min-width: 0; display: grid; gap: 3px; }
.detail-identity span { color: color-mix(in srgb, var(--teal) 65%, var(--text)); font-family: Consolas, "Cascadia Mono", monospace; font-size: 12px; }
.detail-identity h2 { margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 21px; }
.detail-head-actions { display: flex; align-items: center; gap: 10px; }
.detail-status { padding: 6px 10px; border: 1px solid var(--line); border-radius: 99px; color: var(--muted); font-size: 11px; font-weight: 700; }
.detail-status.ok { color: #8ce9a5; border-color: rgba(140,233,165,.35); }
.detail-status.live { color: color-mix(in srgb, var(--teal) 70%, var(--text)); border-color: color-mix(in srgb, var(--teal) 38%, var(--line)); }
.detail-status.bad { color: #ffc0c5; border-color: rgba(255,120,130,.4); }
.detail-body { display: grid; gap: 16px; padding: 18px 20px 22px; }
.detail-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 9px; }
.detail-summary article { min-width: 0; min-height: 94px; display: grid; align-content: center; gap: 6px; padding: 13px 14px; border: 1px solid var(--line); border-radius: 7px; background: color-mix(in srgb, var(--surface-2) 86%, var(--surface)); }
.detail-summary span,
.detail-summary small { color: var(--muted); font-size: 12px; }
.detail-summary b { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 17px; }
.bottleneck-callout { display: grid; grid-template-columns: 110px minmax(0, 1fr); gap: 12px; align-items: center; padding: 13px 15px; border: 1px solid color-mix(in srgb, #f04da9 34%, var(--line)); border-radius: 7px; background: linear-gradient(100deg, color-mix(in srgb, #8b43eb 10%, var(--surface-2)), color-mix(in srgb, #f04da9 6%, var(--surface-2))); }
.bottleneck-callout span { color: #f08dcc; font-size: 12px; font-weight: 700; }
.bottleneck-callout b { font-size: 13px; }
.timeline-section { padding: 16px; border: 1px solid var(--line); border-radius: 7px; background: color-mix(in srgb, var(--surface-2) 76%, var(--surface)); }
.section-title { display: flex; align-items: center; justify-content: space-between; gap: 15px; margin-bottom: 8px; }
.section-title h3 { margin: 0; font-size: 15px; }
.section-title p,
.section-title > span { margin: 4px 0 0; color: var(--muted); font-size: 12px; }
.timeline-step { display: grid; grid-template-columns: 34px minmax(0, 1fr) 105px; grid-template-rows: auto 6px; gap: 8px 12px; padding: 11px 0; border-top: 1px solid var(--line); }
.step-index { position: relative; grid-row: 1 / 3; display: grid; place-items: start center; }
.step-index > i { position: absolute; top: 27px; bottom: -19px; width: 1px; background: var(--line); }
.timeline-step:last-child .step-index > i { display: none; }
.step-index span { width: 25px; height: 25px; display: grid; place-items: center; border-radius: 7px; color: color-mix(in srgb, var(--teal) 72%, var(--text)); background: color-mix(in srgb, var(--teal) 13%, var(--surface-2)); font-size: 11px; font-weight: 700; }
.step-copy { min-width: 0; display: grid; gap: 3px; }
.step-copy > div { display: flex; align-items: center; gap: 8px; }
.step-copy em { padding: 2px 6px; border: 1px solid var(--line); border-radius: 99px; color: var(--muted); font-size: 10px; font-style: normal; }
.step-copy em.measured { color: #8ce9a5; border-color: rgba(140,233,165,.3); }
.step-copy small,
.step-copy p { margin: 0; color: var(--muted); font-size: 11px; }
.step-duration { display: grid; justify-items: end; align-content: start; gap: 3px; }
.step-duration b { font-size: 15px; }
.step-duration span { color: color-mix(in srgb, var(--blue) 68%, var(--text)); font-size: 12px; }
.step-bar { grid-column: 2 / 4; height: 5px; overflow: hidden; border-radius: 99px; background: color-mix(in srgb, var(--muted) 15%, transparent); }
.step-bar i { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, #8b43eb, #f04da9); }
.timeline-empty { min-height: 80px; display: grid; place-items: center; color: var(--muted); }
.detail-meta { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.detail-meta > div { min-width: 0; display: grid; grid-template-columns: 118px minmax(0, 1fr); gap: 10px; padding: 10px 12px; border-bottom: 1px solid var(--line); }
.detail-meta span { color: var(--muted); }
.detail-meta b { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: Consolas, "Cascadia Mono", monospace; font-size: 12px; }
.raw-detail { border: 1px solid var(--line); border-radius: 7px; overflow: hidden; }
.raw-detail summary { padding: 12px 14px; color: var(--muted); cursor: pointer; font-weight: 700; }
.raw-detail pre { max-height: 360px; margin: 0; overflow: auto; border-top: 1px solid var(--line); border-radius: 0; font-size: 11px; }
@media (max-width: 820px) {
  .detail-backdrop { padding: 0; }
  .performance-detail { width: 100%; max-height: 100vh; min-height: 100vh; border-radius: 0; }
  .detail-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .detail-meta { grid-template-columns: 1fr; }
  .performance-detail > header { align-items: flex-start; }
}
@media (max-width: 520px) {
  .detail-summary { grid-template-columns: 1fr; }
  .bottleneck-callout { grid-template-columns: 1fr; }
  .timeline-step { grid-template-columns: 30px minmax(0, 1fr); }
  .step-duration { grid-column: 2; justify-items: start; grid-auto-flow: column; justify-content: start; gap: 8px; }
  .step-bar { grid-column: 2; }
}
</style>
