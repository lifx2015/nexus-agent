<script setup lang="ts">
/**
 * 手写 SVG 堆叠柱状趋势图（零图表库依赖，离线优先）。
 * metric=stacked：input/output/缓存读/缓存写 四段堆叠；metric=requests：单柱请求数。
 */
import { computed } from 'vue'
import type { UsageBucket } from '@/api/client'
import { fmtCompact } from '@/utils/format'

type Metric = 'stacked' | 'requests'

const props = defineProps<{
  buckets: UsageBucket[]
  metric?: Metric
  granularity?: 'hour' | 'day'
}>()

const W = 900
const H = 210
const PAD = { top: 10, right: 8, bottom: 26, left: 52 }
const innerW = W - PAD.left - PAD.right
const innerH = H - PAD.top - PAD.bottom

const SERIES: { key: keyof UsageBucket; name: string; color: string }[] = [
  { key: 'input_tokens', name: '输入', color: '#4f8cff' },
  { key: 'output_tokens', name: '输出', color: '#22d3ee' },
  { key: 'cache_read_tokens', name: '缓存读', color: '#a78bfa' },
  { key: 'cache_creation_tokens', name: '缓存写', color: '#f59e0b' },
]

const metric = computed<Metric>(() => props.metric || 'stacked')
const n = computed(() => props.buckets.length || 1)

/** 纵坐标上界 = 单桶最大值（非总和！）向上取整到 1/2/5 × 10^k，Y 轴 4 等分 */
const maxValue = computed(() => {
  let raw = 0
  for (const b of props.buckets) {
    raw = Math.max(raw, metric.value === 'requests' ? b.requests : b.tokens)
  }
  raw = Math.max(raw, 1)
  const exp = Math.floor(Math.log10(raw))
  const base = Math.pow(10, exp)
  const m = raw / base
  const nice = m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10
  return nice * base
})

const slot = computed(() => innerW / n.value)
const barW = computed(() => Math.max(2, Math.min(30, slot.value * 0.62)))

function barHeight(v: number): number {
  return (v / maxValue.value) * innerH
}

function yOf(v: number): number {
  return PAD.top + innerH - barHeight(v)
}

/** 每个桶的堆叠段（自下而上） */
const bars = computed(() =>
  props.buckets.map((b, i) => {
    const x = PAD.left + i * slot.value + (slot.value - barW.value) / 2
    let acc = 0
    const segs =
      metric.value === 'requests'
        ? [{ color: '#4f8cff', name: '请求', value: b.requests }]
        : SERIES.map((s) => ({ color: s.color, name: s.name, value: Number(b[s.key]) }))
    const rects = segs
      .filter((s) => s.value > 0)
      .map((s) => {
        const y0 = yOf(acc + s.value)
        const y1 = yOf(acc)
        acc += s.value
        return { x, y: y0, w: barW.value, h: Math.max(1, y1 - y0), color: s.color, tip: `${b.bucket} · ${s.name} ${fmtCompact(s.value)}` }
      })
    return { rects, label: b.bucket, total: metric.value === 'requests' ? b.requests : b.tokens }
  }),
)

const yTicks = computed(() =>
  [0, 1, 2, 3, 4].map((i) => {
    const v = (maxValue.value / 4) * i
    return { y: yOf(v), label: fmtCompact(v) }
  }),
)

/** X 轴抽稀：最多 6 个标签，hour 粒度只留时分 */
const xLabels = computed(() => {
  const total = bars.value.length
  if (!total) return [] as { x: number; text: string; anchor: string }[]
  const step = Math.max(1, Math.ceil(total / 6))
  const out: { x: number; text: string; anchor: string }[] = []
  bars.value.forEach((bar, i) => {
    if (i % step !== 0 && i !== total - 1) return
    let text = bar.label
    if (props.granularity === 'hour') text = text.split(' ')[1] ?? text
    const cx = PAD.left + i * slot.value + slot.value / 2
    out.push({
      x: Math.min(W - PAD.right - 14, Math.max(PAD.left + 10, cx)),
      text,
      anchor: i === 0 ? 'start' : i === total - 1 ? 'end' : 'middle',
    })
  })
  return out
})
</script>

<template>
  <div class="tc">
    <svg :viewBox="`0 0 ${W} ${H}`" class="tc-svg" role="img" aria-label="用量趋势图">
      <!-- 网格与 Y 轴 -->
      <g v-for="t in yTicks" :key="t.y">
        <line :x1="PAD.left" :x2="W - PAD.right" :y1="t.y" :y2="t.y" class="tc-grid" />
        <text :x="PAD.left - 8" :y="t.y + 4" text-anchor="end" class="tc-ylab">{{ t.label }}</text>
      </g>
      <!-- 柱 -->
      <g v-for="(bar, i) in bars" :key="i">
        <rect
          v-for="(r, j) in bar.rects"
          :key="j"
          :x="r.x" :y="r.y" :width="r.w" :height="r.h"
          :fill="r.color" rx="1.5"
        >
          <title>{{ r.tip }}</title>
        </rect>
      </g>
      <!-- X 轴标签 -->
      <text
        v-for="(l, i) in xLabels" :key="i"
        :x="l.x" :y="H - 8" :text-anchor="l.anchor" class="tc-xlab"
      >{{ l.text }}</text>
      <line :x1="PAD.left" :x2="W - PAD.right" :y1="H - PAD.bottom" :y2="H - PAD.bottom" class="tc-axis" />
    </svg>
    <div class="tc-legend">
      <template v-if="metric === 'stacked'">
        <span v-for="s in SERIES" :key="s.key" class="tc-legend-item">
          <i :style="{ background: s.color }" />{{ s.name }}
        </span>
      </template>
      <span v-else class="tc-legend-item"><i style="background: #4f8cff" />请求数</span>
    </div>
  </div>
</template>

<style scoped>
.tc { width: 100%; }
.tc-svg { width: 100%; height: auto; display: block; }
.tc-grid { stroke: var(--el-border-color-lighter, #e2e8f0); stroke-width: 1; stroke-dasharray: 3 3; }
.tc-axis { stroke: var(--el-border-color, #cbd5e1); stroke-width: 1; }
.tc-ylab, .tc-xlab { font-size: 10px; fill: var(--el-text-color-secondary, #94a3b8); font-family: ui-monospace, Consolas, monospace; }
.tc-legend { display: flex; gap: 14px; justify-content: center; margin-top: 6px; }
.tc-legend-item { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; color: var(--el-text-color-secondary, #64748b); }
.tc-legend-item i { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
</style>
