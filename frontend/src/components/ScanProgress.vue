<script setup lang="ts">
import { computed } from 'vue'
import type { AgentStatus, ScanJob } from '@/api/client'
import { AGENT_COLORS } from '@/constants'

const props = defineProps<{
  running: boolean
  job: ScanJob | null
  agents?: AgentStatus[]
  countsFallback?: Record<string, number>
  metrics?: { label: string; value: string | number }[]
}>()

const state = computed(() => (props.running ? 'running' : props.job?.error ? 'error' : 'done'))

const agentLines = computed(() => {
  const src =
    props.job?.counts_by_agent && Object.keys(props.job.counts_by_agent).length
      ? props.job.counts_by_agent
      : (props.countsFallback ?? {})
  return Object.entries(src)
    .map(([key, count]) => {
      const idx = props.agents?.findIndex((a) => a.key === key) ?? -1
      return {
        key,
        count,
        name: props.agents?.[idx]?.name ?? key,
        color: idx >= 0 ? AGENT_COLORS[idx % AGENT_COLORS.length] : 'var(--nx-accent)',
      }
    })
    .filter((a) => a.count > 0)
    .sort((a, b) => b.count - a.count)
    .slice(0, 14)
})
</script>

<template>
  <div class="sp">
    <div class="sp-head">
      <div class="sp-status">
        <span v-if="state === 'running'" class="sp-spinner" />
        <el-icon v-else-if="state === 'error'" :size="17" color="var(--nx-danger)">
          <CircleCloseFilled />
        </el-icon>
        <el-icon v-else :size="17" color="var(--nx-success)">
          <CircleCheckFilled />
        </el-icon>
        <b>{{ running ? '正在盘点本机资产…' : job?.error ? '扫描出错' : '扫描完成' }}</b>
        <span v-if="job?.finished && state === 'done'" class="sp-time">{{ job.finished }}</span>
      </div>
      <div v-if="metrics?.length" class="sp-metrics">
        <span v-for="m in metrics" :key="m.label" class="sp-metric">
          {{ m.label }} <em>{{ m.value }}</em>
        </span>
      </div>
    </div>

    <div class="sp-bar" :class="state">
      <span v-if="state === 'running'" class="sp-sweep" />
    </div>

    <div v-if="agentLines.length" class="sp-agents">
      <span v-for="a in agentLines" :key="a.key" class="sp-agent">
        <span class="sp-dot" :style="{ background: a.color }" />
        {{ a.name }}
        <em>{{ a.count }}</em>
      </span>
    </div>

    <div v-if="job?.error" class="sp-error">{{ job.error }}</div>
  </div>
</template>

<style scoped>
.sp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.sp-status {
  display: flex;
  align-items: center;
  gap: 9px;
  font-size: 14px;
  min-width: 0;
}
.sp-status b {
  font-weight: 650;
  letter-spacing: -0.01em;
}
.sp-time {
  font-size: 12px;
  color: var(--nx-text-faint);
  font-variant-numeric: tabular-nums;
}
.sp-metrics {
  display: flex;
  gap: 16px;
  font-size: 12px;
  color: var(--nx-text-dim);
  font-variant-numeric: tabular-nums;
}
.sp-metrics em {
  font-style: normal;
  font-size: 13px;
  font-weight: 650;
  color: var(--nx-text);
}

/* 扫描旋转指示器 */
.sp-spinner {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  border: 2px solid var(--nx-border);
  border-top-color: var(--nx-accent);
  animation: sp-spin 0.8s linear infinite;
  flex: none;
}
@keyframes sp-spin {
  to {
    transform: rotate(360deg);
  }
}

/* 进度条：扫描时流光扫过，完成后静态渐变 */
.sp-bar {
  position: relative;
  height: 6px;
  margin: 16px 0 14px;
  border-radius: 999px;
  background: var(--nx-bg-soft);
  overflow: hidden;
}
.sp-bar.done {
  background: linear-gradient(90deg, rgba(52, 211, 153, 0.35), rgba(34, 211, 238, 0.22));
}
.sp-bar.error {
  background: rgba(248, 113, 113, 0.16);
}
.sp-sweep {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 45%;
  border-radius: inherit;
  background: linear-gradient(
    90deg,
    transparent,
    var(--nx-accent) 45%,
    var(--nx-accent-2) 70%,
    transparent
  );
  box-shadow: 0 0 14px rgba(79, 140, 255, 0.55);
  transform: translateX(-100%);
  animation: sp-sweep 1.4s var(--nx-ease) infinite;
}
@keyframes sp-sweep {
  to {
    transform: translateX(320%);
  }
}

/* 实时 Agent 计数胶囊 */
.sp-agents {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.sp-agent {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: 999px;
  background: var(--nx-bg-soft);
  border: 1px solid var(--nx-border-soft);
  font-size: 11px;
  color: var(--nx-text-dim);
  white-space: nowrap;
}
.sp-agent em {
  font-style: normal;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
  color: var(--nx-text);
}
.sp-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex: none;
  box-shadow: 0 0 6px currentColor;
}
.sp-error {
  margin-top: 10px;
  font-size: 12px;
  color: var(--nx-danger);
}
</style>
