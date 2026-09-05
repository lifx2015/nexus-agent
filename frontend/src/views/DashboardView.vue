<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  assetsApi,
  discoveredApi,
  errorMessage,
  projectsApi,
  scanApi,
  skillhubApi,
  systemApi,
  usageApi,
} from '@/api/client'
import type {
  Asset,
  DiscoveredStats,
  HubCompareSummary,
  ProjectRecord,
  ProjectStats,
  ScanJob,
  Stats,
  UsageBucket,
  UsageSummary,
} from '@/api/client'
import { KIND_LIST, KIND_META, SCAN_KIND_META } from '@/constants'
import TrendChart from '@/components/TrendChart.vue'
import { fmtCompact, fmtPct } from '@/utils/format'

const router = useRouter()
const stats = ref<Stats>({ by_kind: {}, totals: {}, total: 0 })
const recent = ref<Asset[]>([])
const loading = ref(false)

const agentsDetected = ref(0)
const scanJob = ref<ScanJob | null>(null)
const discovered = ref<DiscoveredStats | null>(null)
const usage = ref<UsageSummary | null>(null)
const usageToday = ref<UsageSummary | null>(null)
const usage24h = ref<UsageSummary | null>(null)
const usageBuckets = ref<UsageBucket[]>([])
const usageGranularity = ref<'hour' | 'day'>('day')
const busyAction = ref('')

const projectStats = ref<ProjectStats | null>(null)
const topProjects = ref<ProjectRecord[]>([])
const hub = ref<{ summary: HubCompareSummary | null; created_at: string | null }>({
  summary: null,
  created_at: null,
})

function enabledCount(kind: string) {
  return stats.value.by_kind[kind]?.enabled ?? 0
}

const topScanKinds = computed(() => {
  const by = discovered.value?.by_kind ?? {}
  return Object.entries(by)
    .filter(([k]) => k !== 'other')
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4)
})

const scanStateLabel = computed(() => {
  const st = scanJob.value?.state
  if (st === 'running') return '扫描中'
  if (st === 'error') return '上次扫描出错'
  if (scanJob.value?.finished) return '扫描正常'
  return '尚未扫描'
})

const maxProjectTokens = computed(() =>
  topProjects.value.reduce((m, p) => Math.max(m, p.tokens), 0),
)

const hubSnapshotTime = computed(() => (hub.value.created_at ?? '').slice(5, 16))

async function load() {
  loading.value = true
  const now = Math.floor(Date.now() / 1000)
  const midnight = new Date()
  midnight.setHours(0, 0, 0, 0)
  const [s, r, ag, sj, ds, us, ut, pj, hb, utd, u24] = await Promise.allSettled([
    systemApi.stats(),
    assetsApi.list({ limit: 5 }),
    scanApi.agents(),
    scanApi.status(),
    discoveredApi.stats(),
    usageApi.summary({ days: 30 }),
    usageApi.trends({ days: 7 }),
    projectsApi.list({ status: 'active', limit: 500 }),
    skillhubApi.compareCached(),
    usageApi.summary({ start_ts: Math.floor(midnight.getTime() / 1000), end_ts: now }),
    usageApi.summary({ start_ts: now - 86400, end_ts: now }),
  ])
  if (s.status === 'fulfilled') stats.value = s.value
  if (r.status === 'fulfilled') recent.value = r.value
  if (ag.status === 'fulfilled')
    agentsDetected.value = ag.value.filter((a) => a.detected).length
  if (sj.status === 'fulfilled') scanJob.value = sj.value
  if (ds.status === 'fulfilled') discovered.value = ds.value
  if (us.status === 'fulfilled') usage.value = us.value
  if (ut.status === 'fulfilled') {
    usageBuckets.value = ut.value.buckets
    usageGranularity.value = ut.value.granularity
  }
  if (pj.status === 'fulfilled') {
    projectStats.value = pj.value.stats
    topProjects.value = [...pj.value.items]
      .sort((a, b) => b.tokens - a.tokens)
      .slice(0, 5)
  }
  if (hb.status === 'fulfilled')
    hub.value = { summary: hb.value.summary, created_at: hb.value.created_at }
  if (utd.status === 'fulfilled') usageToday.value = utd.value
  if (u24.status === 'fulfilled') usage24h.value = u24.value
  const failed = [s, r, ag, sj, ds, us, ut, pj, hb, utd, u24].find((x) => x.status === 'rejected')
  if (failed) ElMessage.error(errorMessage((failed as PromiseRejectedResult).reason))
  loading.value = false
}

async function runScan() {
  busyAction.value = 'scan'
  try {
    await scanApi.run()
    ElMessage.success('已触发扫描，稍后刷新查看结果')
    setTimeout(() => load(), 3000)
  } catch (e) {
    ElMessage.error(errorMessage(e))
  } finally {
    busyAction.value = ''
  }
}

async function syncUsage() {
  busyAction.value = 'usage'
  try {
    await usageApi.sync()
    ElMessage.success('已触发流量同步，稍后刷新查看结果')
    setTimeout(() => load(), 4000)
  } catch (e) {
    ElMessage.error(errorMessage(e))
  } finally {
    busyAction.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div v-loading="loading" class="nx-page">
    <!-- 四类资产统计 -->
    <div class="nx-stat-grid">
      <div
        v-for="k in KIND_LIST"
        :key="k"
        class="nx-stat"
        tabindex="0"
        :style="{ '--stat-color': KIND_META[k].color } as Record<string, string>"
        @click="k === 'skill' ? router.push('/skills') : router.push(`/assets/${k}`)"
        @keydown.enter="k === 'skill' ? router.push('/skills') : router.push(`/assets/${k}`)"
      >
        <div class="nx-stat-icon">
          <el-icon :size="19"><component :is="KIND_META[k].icon" /></el-icon>
        </div>
        <div class="nx-stat-body">
          <div class="nx-stat-label">{{ KIND_META[k].label }}</div>
          <div class="nx-stat-value">{{ stats.totals[k] ?? 0 }}</div>
          <div class="nx-stat-sub">{{ enabledCount(k) }} 条启用 · {{ KIND_META[k].desc }}</div>
        </div>
      </div>
    </div>

    <div class="nx-dash-cols">
      <!-- 主列：最近更新 + 项目用量 -->
      <div class="nx-dash-main">
        <div class="nx-card">
          <div class="nx-card-head">
            <span class="nx-card-title">最近更新</span>
            <span class="nx-spacer" />
            <el-button size="small" text @click="load">刷新</el-button>
          </div>

          <el-table v-if="recent.length" :data="recent" size="small" style="width: 100%">
            <el-table-column label="类型" width="72">
              <template #default="{ row }">
                <el-tag size="small" :color="KIND_META[row.kind].color" effect="dark">
                  {{ KIND_META[row.kind].label }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="name" label="名称" min-width="170" show-overflow-tooltip />
            <el-table-column label="标签" min-width="130">
              <template #default="{ row }">
                <el-tag
                  v-for="t in row.tags.slice(0, 3)"
                  :key="t"
                  size="small"
                  effect="plain"
                  style="margin-right: 4px"
                >
                  {{ t }}
                </el-tag>
                <span v-if="!row.tags?.length" class="nx-dim">—</span>
              </template>
            </el-table-column>
            <el-table-column prop="updated_at" label="更新时间" width="160" class-name="nx-mono" />
            <el-table-column width="60">
              <template #default="{ row }">
                <button
                  class="nx-link nx-link--primary"
                  @click="row.kind === 'skill' ? router.push('/skills') : router.push(`/assets/${row.kind}`)"
                >
                  查看
                </button>
              </template>
            </el-table-column>
          </el-table>

          <div v-else class="nx-empty" style="padding: 32px 0">
            暂无资产。去「工具 / 记忆 / 规范 / 技能」创建第一条，或直接把 .md 文件放进数据目录。
          </div>
        </div>

        <!-- 项目用量（AI 项目管理） -->
        <div class="nx-card nx-card--grow">
          <div class="nx-card-head">
            <el-icon color="#34d399"><Folder /></el-icon>
            <span class="nx-card-title">项目用量 TOP</span>
            <span class="nx-dim" style="font-size: 12px">
              共 {{ projectStats?.total ?? 0 }} 个项目 ·
              归因 {{ fmtCompact(projectStats?.attributed_tokens ?? 0) }} tokens
            </span>
            <span class="nx-spacer" />
            <el-button size="small" text type="primary" @click="router.push('/projects')">
              进入管理
            </el-button>
          </div>

          <div v-if="topProjects.length" class="nx-proj-list">
            <div
              v-for="p in topProjects"
              :key="p.id"
              class="nx-proj-row"
              :title="p.path"
              @click="router.push('/projects')"
            >
              <span class="nx-proj-name">{{ p.name }}</span>
              <span class="nx-proj-meta">{{ p.agents.length }} Agent · {{ p.sessions }} 会话</span>
              <span class="nx-proj-bar">
                <i :style="{ width: maxProjectTokens ? `${Math.max(3, (p.tokens / maxProjectTokens) * 100)}%` : '0%' }" />
              </span>
              <span class="nx-proj-tokens">{{ fmtCompact(p.tokens) }}</span>
            </div>
          </div>
          <div v-else class="nx-empty" style="padding: 24px 0">
            暂未发现项目。在「扫描中心」扫描后，会从各 Agent 会话日志回溯出你 AI 开发过的项目。
          </div>
        </div>
      </div>

      <!-- 侧列：扫描中心 + 流量统计 + 技能仓库 -->
      <div class="nx-dash-side">
        <div class="nx-card">
          <div class="nx-card-head">
            <el-icon color="#22d3ee"><Aim /></el-icon>
            <span class="nx-card-title">扫描中心</span>
            <span class="nx-spacer" />
            <span class="nx-pill" :class="scanStateLabel === '扫描正常' ? 'nx-pill--success' : 'nx-pill--muted'">
              {{ scanStateLabel }}
            </span>
          </div>
          <div class="nx-side-metric">
            <span class="nx-side-metric-value">{{ discovered?.total ?? 0 }}</span>
            <span class="nx-side-metric-label">条发现资产 · {{ agentsDetected }} 个本机 Agent</span>
          </div>
          <div v-if="topScanKinds.length" class="nx-side-kinds">
            <span
              v-for="[k, n] in topScanKinds"
              :key="k"
              class="nx-side-kind"
              :style="{ color: SCAN_KIND_META[k]?.color }"
            >
              <i :style="{ background: SCAN_KIND_META[k]?.color }" />
              {{ SCAN_KIND_META[k]?.label ?? k }} {{ n }}
            </span>
          </div>
          <div class="nx-side-actions">
            <el-button size="small" :loading="busyAction === 'scan'" @click="runScan">立即扫描</el-button>
            <el-button size="small" text type="primary" @click="router.push('/scan')">进入扫描中心</el-button>
          </div>
        </div>

        <div class="nx-card">
          <div class="nx-card-head">
            <el-icon color="#4f8cff"><TrendCharts /></el-icon>
            <span class="nx-card-title">流量统计</span>
            <span class="nx-spacer" />
            <span class="nx-dim" style="font-size: 12px">近 30 天</span>
          </div>
          <div class="nx-side-metric">
            <span class="nx-side-metric-value">{{ fmtCompact(usage?.real_total_tokens ?? 0) }}</span>
            <span class="nx-side-metric-label">
              tokens · {{ fmtCompact(usage?.requests ?? 0) }} 次请求
            </span>
          </div>
          <div class="nx-side-kv">
            <span class="nx-dim">会话</span><span>{{ fmtCompact(usage?.sessions ?? 0) }}</span>
            <span class="nx-dim">缓存命中</span>
            <span :style="{ color: (usage?.cache_hit_rate ?? 0) >= 0.5 ? 'var(--nx-success)' : undefined }">
              {{ fmtPct(usage?.cache_hit_rate ?? 0) }}
            </span>
          </div>
          <div class="nx-mini-grid">
            <div class="nx-mini">
              <div class="nx-mini-label">今日 0 点至今</div>
              <div class="nx-mini-value">{{ fmtCompact(usageToday?.real_total_tokens ?? 0) }}</div>
              <div class="nx-mini-sub">{{ fmtCompact(usageToday?.requests ?? 0) }} 次请求</div>
            </div>
            <div class="nx-mini">
              <div class="nx-mini-label">过去 24 小时</div>
              <div class="nx-mini-value">{{ fmtCompact(usage24h?.real_total_tokens ?? 0) }}</div>
              <div class="nx-mini-sub">{{ fmtCompact(usage24h?.requests ?? 0) }} 次请求</div>
            </div>
          </div>
          <TrendChart
            v-if="usageBuckets.length"
            :buckets="usageBuckets"
            :granularity="usageGranularity"
            metric="requests"
            style="margin-top: 6px"
          />
          <div class="nx-side-actions">
            <el-button size="small" :loading="busyAction === 'usage'" @click="syncUsage">同步用量</el-button>
            <el-button size="small" text type="primary" @click="router.push('/usage')">查看明细</el-button>
          </div>
        </div>

        <!-- SkillHub 快照（不访问网络） -->
        <div class="nx-card">
          <div class="nx-card-head">
            <el-icon color="#a78bfa"><MagicStick /></el-icon>
            <span class="nx-card-title">技能仓库</span>
            <span class="nx-spacer" />
            <span
              class="nx-pill"
              :class="(hub.summary?.updatable ?? 0) > 0 ? 'nx-pill--warning' : 'nx-pill--success'"
            >
              {{ (hub.summary?.updatable ?? 0) > 0 ? `${hub.summary?.updatable} 个可更新` : '本机技能无待更新' }}
            </span>
          </div>
          <div class="nx-side-kv" style="margin-top: 2px">
            <span class="nx-dim">已对比技能</span><span>{{ hub.summary?.total ?? 0 }}</span>
            <span class="nx-dim">仓库匹配</span>
            <span>{{ hub.summary?.matched ?? 0 }}</span>
          </div>
          <div class="nx-side-actions">
            <span class="nx-dim" style="font-size: 11px; margin-right: auto">
              {{ hub.created_at ? `快照 ${hubSnapshotTime}` : '尚无对比快照' }}
            </span>
            <el-button size="small" text type="primary" @click="router.push('/skills')">进入技能中心</el-button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.nx-dash-cols {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 16px;
}

.nx-dash-main {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.nx-card--grow {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.nx-card--grow > :last-child {
  flex: 1;
  min-height: 0;
}

.nx-dash-side {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.nx-side-metric {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}
.nx-side-metric-value {
  font-size: 26px;
  font-weight: 700;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}
.nx-side-metric-label {
  font-size: 12px;
  color: var(--nx-text-faint);
}

.nx-side-kinds {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  margin-top: 10px;
  font-size: 12px;
}
.nx-side-kind {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-variant-numeric: tabular-nums;
}
.nx-side-kind i {
  width: 7px;
  height: 7px;
  border-radius: 2px;
  display: inline-block;
}

.nx-side-kv {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 2px 14px;
  margin-top: 10px;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.nx-side-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--nx-border-soft);
}

/* 今日 / 24h 迷你统计 */
.nx-mini-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-top: 12px;
}
.nx-mini {
  background: var(--nx-bg-soft);
  border: 1px solid var(--nx-border-soft);
  border-radius: var(--nx-r-sm);
  padding: 10px 12px;
  min-width: 0;
}
.nx-mini-label {
  font-size: 11px;
  color: var(--nx-text-faint);
}
.nx-mini-value {
  margin-top: 3px;
  font-size: 18px;
  font-weight: 700;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}
.nx-mini-sub {
  margin-top: 2px;
  font-size: 11px;
  color: var(--nx-text-faint);
  font-variant-numeric: tabular-nums;
}

/* 项目用量行 */
.nx-proj-list {
  display: flex;
  flex-direction: column;
}
.nx-proj-row {
  display: grid;
  grid-template-columns: minmax(90px, 1.2fr) auto minmax(80px, 1fr) 64px;
  align-items: center;
  gap: 12px;
  padding: 7px 6px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s var(--nx-ease);
}
.nx-proj-row:hover {
  background: var(--nx-bg-soft);
}
.nx-proj-name {
  font-size: 13px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.nx-proj-meta {
  font-size: 11px;
  color: var(--nx-text-faint);
  white-space: nowrap;
}
.nx-proj-bar {
  height: 6px;
  border-radius: 999px;
  background: var(--nx-bg-hover);
  overflow: hidden;
}
.nx-proj-bar i {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, var(--nx-accent), var(--nx-accent-2));
  transition: width 0.4s var(--nx-ease);
}
.nx-proj-tokens {
  text-align: right;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--nx-text-dim);
}

@media (max-width: 1180px) {
  .nx-dash-cols {
    grid-template-columns: 1fr;
  }
}
</style>
