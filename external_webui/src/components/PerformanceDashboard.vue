<script setup>
import { computed, ref } from "vue";
import { formatDurationMs, formatTimestamp } from "../domain/task-performance.js";

const props = defineProps({
  records: { type: Array, default: () => [] },
  summary: { type: Object, required: true },
});

const emit = defineEmits(["open"]);
const filter = ref("all");

const filters = [
  { key: "all", label: "全部" },
  { key: "video", label: "视频" },
  { key: "image", label: "图片 / ZIP" },
  { key: "running", label: "进行中" },
];

const visibleRecords = computed(() => props.records.filter((record) => {
  if (filter.value === "video") return record.category === "video";
  if (filter.value === "image") return record.category === "image";
  if (filter.value === "running") return record.active;
  return true;
}));

const queueDepth = computed(() => Math.max(0, ...props.records.map((record) => Number(
  record.source?.raw?.children?.comfyui?.backend_handshake?.capacity?.queue_depth || 0
))));

const lowCoverage = computed(() => props.records.filter((record) => record.totalMs > 0 && record.measuredCoverage < 0.75).length);
const activeCount = computed(() => props.records.filter((record) => record.active).length);

const insights = computed(() => {
  const result = [];
  if (props.summary.dominant) {
    result.push({
      tone: "hot",
      title: `当前关键瓶颈：${props.summary.dominant.label}`,
      text: `在 ${props.summary.dominant.sampleCount} 个可比较样本中累计占比 ${Math.round(props.summary.dominant.share * 100)}%，中位耗时 ${formatDurationMs(props.summary.dominant.p50Ms)}。`,
    });
  }
  if (queueDepth.value > 0) {
    result.push({
      tone: "warn",
      title: `GPU 集群握手时最大排队 ${queueDepth.value}`,
      text: "当前接口没有把远端排队和纯 GPU 计算拆开，因此面板将二者合并显示，避免误判计算速度。",
    });
  }
  if (activeCount.value > 0) {
    result.push({
      tone: "live",
      title: `${activeCount.value} 个任务正在计时`,
      text: "进行中任务的总耗时与当前阶段会随 12 秒刷新周期持续更新。",
    });
  }
  if (lowCoverage.value > 0) {
    result.push({
      tone: "muted",
      title: `${lowCoverage.value} 个旧任务缺少完整阶段时间点`,
      text: "这些任务仍显示真实端到端耗时，但不会伪造逐阶段耗时；详情中会标注“推导”。",
    });
  }
  if (!result.length) {
    result.push({ tone: "muted", title: "等待更多完成样本", text: "有任务完成并获得飞书回执后，会自动生成阶段基线与瓶颈结论。" });
  }
  return result;
});

function recordMeta(record) {
  if (record.throughput) return `${record.frames} 帧 · ${record.throughput.toFixed(1)} 帧/分`;
  if (record.frames) return `${record.frames} 个处理单元`;
  return record.backend || "暂无吞吐数据";
}
</script>

<template>
  <div class="performance-dashboard">
    <section class="performance-hero">
      <div>
        <p class="section-kicker">PERFORMANCE</p>
        <h2>任务耗时分析</h2>
        <p>从飞书任务创建到 ZIP / 文件回传，按真实时间点拆解关键路径。</p>
      </div>
      <div class="coverage-note">
        <b>{{ summary.successfulCount }}</b>
        <span>成功样本</span>
        <small>最近加载 {{ summary.sampleCount }} 个父任务</small>
      </div>
    </section>

    <section class="performance-kpis">
      <article>
        <span>端到端中位数</span>
        <b>{{ formatDurationMs(summary.p50Ms) }}</b>
        <small>比平均值更不受异常任务影响</small>
      </article>
      <article>
        <span>P90 完成耗时</span>
        <b>{{ formatDurationMs(summary.p90Ms) }}</b>
        <small>约 90% 的成功任务不超过此值</small>
      </article>
      <article>
        <span>平均抠图吞吐</span>
        <b>{{ summary.averageThroughput ? `${summary.averageThroughput.toFixed(1)} 帧/分` : "暂无数据" }}</b>
        <small>抠图耗时包含本机 / 集群排队</small>
      </article>
      <article class="primary-kpi">
        <span>累计最慢步骤</span>
        <b>{{ summary.dominant?.label || "等待样本" }}</b>
        <small v-if="summary.dominant">占已分析阶段 {{ Math.round(summary.dominant.share * 100) }}%</small>
        <small v-else>完成任务后自动定位</small>
      </article>
    </section>

    <section class="analysis-columns">
      <article class="analysis-panel stage-panel">
        <header>
          <div><h3>阶段基线</h3><p>成功任务的中位耗时与累计占比</p></div>
          <span>{{ summary.stages.length }} 个阶段</span>
        </header>
        <div v-if="!summary.stages.length" class="analysis-empty">暂无可比较的完成任务。</div>
        <div v-for="stage in summary.stages" :key="stage.key" class="stage-benchmark">
          <div class="stage-benchmark-copy">
            <b>{{ stage.label }}</b>
            <span>中位 {{ formatDurationMs(stage.p50Ms) }} · 平均 {{ formatDurationMs(stage.averageMs) }}</span>
          </div>
          <div class="stage-share"><i :style="{ width: `${Math.max(2, stage.share * 100)}%` }"></i></div>
          <strong>{{ Math.round(stage.share * 100) }}%</strong>
          <small>{{ stage.measuredCount }}/{{ stage.sampleCount }} 实测</small>
        </div>
      </article>

      <article class="analysis-panel insight-panel">
        <header><div><h3>速度诊断</h3><p>按现有时间数据自动寻找关键 key</p></div></header>
        <div v-for="item in insights" :key="item.title" :class="['insight', item.tone]">
          <i></i>
          <div><b>{{ item.title }}</b><p>{{ item.text }}</p></div>
        </div>
      </article>
    </section>

    <section class="analysis-panel record-panel">
      <header class="record-head">
        <div><h3>逐任务关键路径</h3><p>点击任务查看完整阶段时间线与数据可信度</p></div>
        <div class="performance-filters">
          <button v-for="item in filters" :key="item.key" :class="{ active: filter === item.key }" @click="filter = item.key">{{ item.label }}</button>
        </div>
      </header>
      <div class="performance-table-head">
        <span>任务</span><span>端到端</span><span>最慢步骤</span><span>处理速度</span><span>状态</span>
      </div>
      <div v-if="!visibleRecords.length" class="analysis-empty">当前筛选没有任务。</div>
      <button v-for="record in visibleRecords" :key="record.key" class="performance-row" @click="emit('open', record.source)">
        <span class="record-name"><b :title="record.name">{{ record.name }}</b><small>{{ record.label }} · {{ formatTimestamp(record.startMs) }}</small></span>
        <span class="record-total"><b>{{ formatDurationMs(record.totalMs) }}</b><small>{{ record.active ? "持续计时" : record.delivered ? "飞书已回传" : "按最后状态计算" }}</small></span>
        <span class="record-bottleneck"><b>{{ record.bottleneck?.label || "暂无拆分" }}</b><small>{{ record.bottleneck ? `${Math.round(record.bottleneck.share * 100)}% · ${formatDurationMs(record.bottleneck.durationMs)}` : "缺少阶段时间点" }}</small></span>
        <span><b>{{ recordMeta(record) }}</b><small>{{ record.backend }}</small></span>
        <em :class="record.status.toLowerCase()">{{ record.status }}</em>
      </button>
    </section>
  </div>
</template>

<style scoped>
.performance-dashboard { display: grid; gap: 16px; }
.performance-hero,
.analysis-panel { border: 1px solid var(--line); border-radius: 7px; background: var(--surface); box-shadow: var(--shadow); }
.performance-hero { min-height: 118px; display: flex; align-items: center; justify-content: space-between; gap: 24px; padding: 22px 24px; background: linear-gradient(105deg, color-mix(in srgb, var(--teal) 12%, var(--surface)), var(--surface) 50%, color-mix(in srgb, var(--blue) 5%, var(--surface))); }
.section-kicker { margin: 0 0 3px; color: color-mix(in srgb, var(--teal) 68%, var(--blue)); font-size: 12px; font-weight: 700; letter-spacing: .12em; }
.performance-hero h2 { margin: 0; font-size: 26px; }
.performance-hero p:not(.section-kicker) { margin: 7px 0 0; color: var(--muted); }
.coverage-note { min-width: 170px; display: grid; grid-template-columns: auto 1fr; align-items: baseline; gap: 2px 9px; padding-left: 22px; border-left: 1px solid var(--line); }
.coverage-note b { color: color-mix(in srgb, var(--teal) 65%, var(--text)); font-size: 30px; }
.coverage-note span { font-weight: 700; }
.coverage-note small { grid-column: 1 / -1; color: var(--muted); }
.performance-kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.performance-kpis article { min-height: 104px; display: grid; align-content: center; gap: 7px; padding: 15px 16px; border: 1px solid var(--line); border-radius: 7px; background: color-mix(in srgb, var(--surface-2) 86%, var(--surface)); }
.performance-kpis span,
.performance-kpis small { color: var(--muted); }
.performance-kpis b { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 21px; }
.performance-kpis .primary-kpi { border-color: color-mix(in srgb, var(--teal) 44%, var(--line)); background: linear-gradient(135deg, color-mix(in srgb, var(--teal) 12%, var(--surface-2)), color-mix(in srgb, var(--blue) 5%, var(--surface-2))); }
.analysis-columns { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(330px, .75fr); gap: 16px; }
.analysis-panel { padding: 18px; }
.analysis-panel > header { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
.analysis-panel h3 { margin: 0; font-size: 16px; }
.analysis-panel header p { margin: 4px 0 0; color: var(--muted); font-size: 12px; }
.analysis-panel header > span { color: var(--muted); }
.stage-benchmark { display: grid; grid-template-columns: minmax(230px, 1fr) minmax(120px, .8fr) 44px 68px; align-items: center; gap: 12px; min-height: 54px; padding: 8px 0; border-top: 1px solid var(--line); }
.stage-benchmark-copy { min-width: 0; display: grid; gap: 3px; }
.stage-benchmark-copy span,
.stage-benchmark small { color: var(--muted); font-size: 12px; }
.stage-share { height: 7px; overflow: hidden; border-radius: 99px; background: color-mix(in srgb, var(--muted) 16%, transparent); }
.stage-share i { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, #8b43eb, #f04da9); }
.stage-benchmark strong { color: color-mix(in srgb, var(--blue) 68%, var(--text)); text-align: right; }
.stage-benchmark small { text-align: right; }
.insight { display: grid; grid-template-columns: 9px minmax(0, 1fr); gap: 11px; padding: 13px 0; border-top: 1px solid var(--line); }
.insight > i { width: 8px; height: 8px; margin-top: 5px; border-radius: 50%; background: var(--muted); }
.insight.hot > i { background: #f04da9; box-shadow: 0 0 12px color-mix(in srgb, #f04da9 60%, transparent); }
.insight.warn > i { background: var(--amber); }
.insight.live > i { background: var(--teal); }
.insight b { font-size: 14px; }
.insight p { margin: 5px 0 0; color: var(--muted); font-size: 12px; line-height: 1.6; }
.record-head { margin-bottom: 10px !important; }
.performance-filters { display: flex; gap: 6px; }
.performance-filters button { min-height: 32px; padding: 0 11px; font-size: 12px; }
.performance-filters button.active { border-color: color-mix(in srgb, var(--teal) 50%, var(--line)); color: var(--text); background: color-mix(in srgb, var(--teal) 14%, var(--surface-2)); }
.performance-table-head,
.performance-row { display: grid; grid-template-columns: minmax(260px, 1.3fr) 120px minmax(190px, 1fr) 170px 94px; gap: 14px; align-items: center; }
.performance-table-head { padding: 9px 12px; color: var(--faint); font-size: 11px; font-weight: 700; letter-spacing: .04em; }
.performance-row { width: 100%; min-height: 68px; padding: 10px 12px; border: 0; border-top: 1px solid var(--line); border-radius: 0; text-align: left; background: transparent; }
.performance-row:hover { background: color-mix(in srgb, var(--teal) 7%, transparent); }
.performance-row > span { min-width: 0; display: grid; gap: 3px; }
.performance-row b,
.performance-row small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.performance-row small { color: var(--muted); font-size: 12px; font-weight: 400; }
.record-name b { font-size: 14px; }
.record-total b { color: color-mix(in srgb, var(--blue) 68%, var(--text)); font-size: 16px; }
.performance-row em { justify-self: start; padding: 5px 9px; border: 1px solid var(--line); border-radius: 99px; color: var(--muted); font-size: 11px; font-style: normal; font-weight: 700; }
.performance-row em.done,
.performance-row em.done_with_errors { color: #8ce9a5; border-color: rgba(140, 233, 165, .35); }
.performance-row em.running,
.performance-row em.queued,
.performance-row em.waiting_character { color: color-mix(in srgb, var(--teal) 70%, var(--text)); border-color: color-mix(in srgb, var(--teal) 38%, var(--line)); }
.performance-row em.failed,
.performance-row em.blocked { color: #ffc0c5; border-color: rgba(255, 120, 130, .4); }
.analysis-empty { min-height: 90px; display: grid; place-items: center; color: var(--muted); border-top: 1px solid var(--line); }
@media (max-width: 1180px) {
  .performance-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .analysis-columns { grid-template-columns: 1fr; }
  .performance-table-head { display: none; }
  .performance-row { grid-template-columns: minmax(240px, 1fr) 110px minmax(180px, 1fr) 90px; }
  .performance-row > span:nth-of-type(4) { display: none; }
}
@media (max-width: 760px) {
  .performance-hero { display: grid; }
  .coverage-note { padding: 12px 0 0; border-left: 0; border-top: 1px solid var(--line); }
  .performance-kpis { grid-template-columns: 1fr; }
  .stage-benchmark { grid-template-columns: 1fr 58px; }
  .stage-share { grid-column: 1; }
  .stage-benchmark small { grid-column: 2; grid-row: 2; }
  .record-head { display: grid !important; }
  .performance-filters { overflow-x: auto; }
  .performance-row { grid-template-columns: 1fr auto; }
  .performance-row > span:nth-of-type(3),
  .performance-row > span:nth-of-type(4) { display: none; }
}
</style>
