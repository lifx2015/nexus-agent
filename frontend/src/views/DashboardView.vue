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
  AgentStatus,
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
import { GROWTH_LEVELS, AGENT_COLORS, KIND_LIST, KIND_META, SCAN_KIND_META } from '@/constants'
import TrendChart from '@/components/TrendChart.vue'
import { fmtCompact, fmtPct } from '@/utils/format'
import { growthOf } from '@/utils/level'

const router = useRouter()
const stats = ref<Stats>({ by_kind: {}, totals: {}, total: 0 })
const recent = ref<Asset[]>([])
const loading = ref(false)

const agentsDetected = ref(0)
const agentsList = ref<AgentStatus[]>([])
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

function discoveredCount(kind: string) {
  return discovered.value?.by_kind?.[kind] ?? 0
}

/** 卡片主数值 = 自建 + 收录（收录即扫描发现的本机资产） */
function kindTotal(kind: string) {
  return (stats.value.totals[kind] ?? 0) + discoveredCount(kind)
}

const topScanKinds = computed(() => {
  const by = discovered.value?.by_kind ?? {}
  return Object.entries(by)
    .filter(([k]) => k !== 'other')
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4)
})

/** 左列「智能体分布」：已检测到的 Agent，按发现资产数排序 */
const agentChips = computed(() => {
  const byAgent = discovered.value?.by_agent ?? {}
  return agentsList.value
    .filter((a) => a.detected)
    .map((a, i) => ({
      key: a.key,
      name: a.name,
      vendor: a.vendor,
      count: byAgent[a.key] ?? 0,
      color: AGENT_COLORS[i % AGENT_COLORS.length],
    }))
    .sort((x, y) => y.count - x.count)
})

// ---- 扫描状态 ---------------------------------------------------------------
// 注意：扫描任务状态存于后端内存，应用重启后为 idle；是否「扫描过」须结合已入库的发现资产判断
const scanning = ref(false)
let scanTimer: number | null = null

const scanStateLabel = computed(() => {
  const st = scanJob.value?.state
  if (scanning.value || st === 'running') return '扫描中…'
  if (st === 'error') return '上次扫描出错'
  if (scanJob.value?.finished || scanJob.value?.state === 'done') return '扫描正常'
  if ((discovered.value?.total ?? 0) > 0) return '已扫描'
  return '尚未扫描'
})

const scanStateClass = computed(() => {
  if (scanning.value || scanJob.value?.state === 'running') return 'nx-pill--scanning'
  if (scanJob.value?.state === 'error') return 'nx-pill--danger'
  if (scanJob.value?.finished || scanJob.value?.state === 'done') return 'nx-pill--success'
  if ((discovered.value?.total ?? 0) > 0) return 'nx-pill--success'
  return 'nx-pill--muted'
})

/** 首次使用（从未扫描过且没有任何发现资产）：只展示扫描引导 */
const heroMode = computed(() => !loading.value && (discovered.value?.total ?? 0) === 0)

const maxProjectTokens = computed(() =>
  topProjects.value.reduce((m, p) => Math.max(m, p.tokens), 0),
)

const tokens30 = computed(() => usage.value?.real_total_tokens ?? 0)
const growth = computed(() => growthOf(tokens30.value))

const hubSnapshotTime = computed(() => (hub.value.created_at ?? '').slice(5, 16))

async function load() {
  loading.value = true
  const now = Math.floor(Date.now() / 1000)
  const midnight = new Date()
  midnight.setHours(0, 0, 0, 0)
  const [s, r, ag, sj, ds, us, ut, pj, hb, utd, u24] = await Promise.allSettled([
    systemApi.stats(),
    assetsApi.list({ limit: 8 }),
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
  if (ag.status === 'fulfilled') {
    agentsList.value = ag.value
    agentsDetected.value = ag.value.filter((a) => a.detected).length
  }
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
      .slice(0, 7)
  }
  if (hb.status === 'fulfilled')
    hub.value = { summary: hb.value.summary, created_at: hb.value.created_at }
  if (utd.status === 'fulfilled') usageToday.value = utd.value
  if (u24.status === 'fulfilled') usage24h.value = u24.value
  const failed = [s, r, ag, sj, ds, us, ut, pj, hb, utd, u24].find((x) => x.status === 'rejected')
  if (failed) ElMessage.error(errorMessage((failed as PromiseRejectedResult).reason))
  loading.value = false
}

async function pollScan() {
  if (scanTimer) window.clearTimeout(scanTimer)
  scanTimer = window.setTimeout(async () => {
    scanTimer = null
    try {
      const job = await scanApi.status()
      scanJob.value = job
      if (job.state === 'running') {
        await pollScan()
      } else {
        scanning.value = false
        ElMessage.success('扫描完成')
        await load()
      }
    } catch {
      scanning.value = false
    }
  }, 1200)
}

async function runScan() {
  if (scanning.value) return
  busyAction.value = 'scan'
  try {
    await scanApi.run()
    scanning.value = true
    pollScan()
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

onMounted(() => {
  load().then(() => {
    // 进入页面时若已有扫描任务在跑（比如从扫描中心发起后切回来），接管进度轮询
    if (scanJob.value?.state === 'running') {
      scanning.value = true
      pollScan()
    }
  })
})
</script>

<template>
  <div v-loading="loading" class="nx-page">
    <!-- 首次使用引导：尚未扫描过时只展示扫描入口，扫完自动进入完整概览 -->
    <div v-if="heroMode" class="hero-card">
      <div class="hero-icon">
        <el-icon :size="30"><Aim /></el-icon>
      </div>
      <div class="hero-title">欢迎使用 Nexus Agent</div>
      <div class="hero-desc">
        先扫描一次本机：检测已安装的 AI 编程智能体，收录它们的技能、规范、记忆与配置资产，
        并回溯 AI 项目与用量统计。全部数据只在本机处理，不会上传。
      </div>
      <el-button
        type="primary"
        size="large"
        :loading="scanning || busyAction === 'scan'"
        class="hero-btn"
        @click="runScan"
      >
        <el-icon v-if="!(scanning || busyAction === 'scan')" style="margin-right: 6px"><Refresh /></el-icon>
        {{ scanning ? '正在扫描本机…' : '立即扫描本机' }}
      </el-button>
      <div class="hero-hint">
        <template v-if="scanning">正在检测本机 Agent 与资产，完成后本页自动刷新…</template>
        <template v-else-if="agentsDetected">已检测到 {{ agentsDetected }} 个本机 Agent，随时可以开始</template>
        <template v-else>扫描通常只需几秒钟</template>
      </div>
    </div>

    <template v-else>
      <!-- 四类资产统计（自建 + 扫描收录） -->
      <div class="nx-stat-grid">
        <div
          v-for="k in KIND_LIST"
          :key="k"
          class="nx-stat"
          tabindex="0"
          :style="{ '--stat-color': KIND_META[k].color } as Record<string, string>"
          :title="KIND_META[k].desc"
          @click="k === 'skill' ? router.push('/skills') : router.push(`/assets/${k}`)"
          @keydown.enter="k === 'skill' ? router.push('/skills') : router.push(`/assets/${k}`)"
        >
          <div class="nx-stat-icon">
            <el-icon :size="19"><component :is="KIND_META[k].icon" /></el-icon>
          </div>
          <div class="nx-stat-body">
            <div class="nx-stat-label">{{ KIND_META[k].label }}</div>
            <div class="nx-stat-value">{{ kindTotal(k) }}</div>
            <div class="nx-stat-sub">
              自建 {{ stats.totals[k] ?? 0 }} · 收录 {{ discoveredCount(k) }}
            </div>
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

        <!-- 智能体分布（本机检测到的 Agent 及资产数） -->
        <div class="nx-card">
          <div class="nx-card-head">
            <el-icon color="#a78bfa"><Cpu /></el-icon>
            <span class="nx-card-title">智能体分布</span>
            <span class="nx-dim" style="font-size: 12px">
              检测到 {{ agentsDetected }} 家 · 发现资产 {{ discovered?.total ?? 0 }} 条
            </span>
            <span class="nx-spacer" />
            <el-button size="small" text type="primary" @click="router.push('/scan')">
              进入扫描中心
            </el-button>
          </div>
          <div v-if="agentChips.length" class="agent-grid">
            <button
              v-for="a in agentChips"
              :key="a.key"
              class="agent-cell"
              :title="`${a.name} · ${a.vendor || '未知厂商'}`"
              @click="router.push('/scan')"
            >
              <span class="agent-dot" :style="{ background: a.color }" />
              <span class="agent-name">{{ a.name }}</span>
              <span class="agent-count">{{ a.count }}</span>
            </button>
          </div>
          <div v-else class="nx-empty" style="padding: 24px 0">
            尚未检测到本机 Agent，去「扫描中心」执行一次扫描。
          </div>
        </div>
      </div>

      <!-- 侧列：成长等级 + 扫描中心 + 流量统计 + 技能仓库 -->
      <div class="nx-dash-side">
        <div class="nx-card">
          <div class="nx-card-head">
            <el-icon color="#fbbf24"><Medal /></el-icon>
            <span class="nx-card-title">成长等级</span>
            <span class="nx-spacer" />
            <el-popover placement="bottom-end" :width="280" trigger="click">
              <template #reference>
                <button class="nx-link nx-link--primary">等级说明</button>
              </template>
              <div class="nx-lv-guide">
                <div
                  v-for="g in GROWTH_LEVELS"
                  :key="g.level"
                  class="nx-lv-guide-row"
                  :class="{ on: g.level <= growth.current.level }"
                >
                  <span class="nx-lv-guide-dot" :style="{ background: g.color }" />
                  <span class="nx-lv-guide-name">Lv.{{ g.level }} {{ g.name }}</span>
                  <span class="nx-lv-guide-th">
                    {{ g.threshold === 0 ? '0' : fmtCompact(g.threshold) }}+ tokens
                  </span>
                  <el-icon v-if="g.level <= growth.current.level" color="var(--nx-success)">
                    <Check />
                  </el-icon>
                </div>
                <div class="nx-lv-guide-note">
                  按近 30 天真实处理 tokens 计算（输入+输出+缓存），15B+ 为满级王者。
                </div>
              </div>
            </el-popover>
          </div>
          <div class="nx-lv-row">
            <div
              class="nx-lv-badge"
              :style="{ '--lv-color': growth.current.color, '--lv-progress': growth.progressPct }"
            >
              <div class="nx-lv-badge-inner">Lv.{{ growth.current.level }}</div>
            </div>
            <div class="nx-lv-info">
              <div class="nx-lv-name" :style="{ color: growth.current.color }">
                {{ growth.current.name }}
              </div>
              <div class="nx-lv-tokens">近 30 天 {{ fmtCompact(tokens30) }} tokens</div>
              <div class="nx-lv-next">
                {{
                  growth.next
                    ? `距「Lv.${growth.next.level} ${growth.next.name}」还需 ${fmtCompact(growth.next.threshold - tokens30)}`
                    : '已达最高等级 · 王者'
                }}
              </div>
            </div>
          </div>
        </div>

        <div class="nx-card">
          <div class="nx-card-head">
            <el-icon color="#22d3ee"><Aim /></el-icon>
            <span class="nx-card-title">扫描中心</span>
            <span class="nx-spacer" />
            <span class="nx-pill" :class="scanStateClass">
              <i v-if="scanning || scanJob?.state === 'running'" class="scan-spinner" />
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
            <el-button
              size="small"
              :type="scanning ? 'primary' : 'default'"
              :loading="scanning || busyAction === 'scan'"
              class="scan-btn"
              @click="runScan"
            >
              {{ scanning ? '扫描中…' : '立即扫描' }}
            </el-button>
            <el-button size="small" text type="primary" @click="router.push('/scan')">进入扫描中心</el-button>
            <span v-if="scanning" class="scan-live">正在检测本机 Agent 与资产…</span>
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
    </template>
  </div>
</template>

<style scoped>
/* ---- 首次使用引导 ---- */
.hero-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  padding: 72px 32px 64px;
  border: 1px solid var(--nx-border-soft);
  border-radius: var(--nx-r-lg, 16px);
  background:
    radial-gradient(600px 200px at 50% -60px, var(--nx-accent-soft, rgba(79, 140, 255, 0.12)), transparent),
    var(--nx-bg-card, var(--nx-bg-soft));
}
.hero-icon {
  width: 64px;
  height: 64px;
  border-radius: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--nx-accent, #4f8cff);
  background: var(--nx-accent-soft, rgba(79, 140, 255, 0.14));
  margin-bottom: 18px;
}
.hero-title {
  font-size: 20px;
  font-weight: 700;
  letter-spacing: -0.01em;
}
.hero-desc {
  margin-top: 10px;
  max-width: 520px;
  font-size: 13px;
  line-height: 1.8;
  color: var(--nx-text-dim);
}
.hero-btn {
  margin-top: 24px;
  min-width: 200px;
  height: 42px;
  font-size: 14px;
}
.hero-hint {
  margin-top: 14px;
  font-size: 12px;
  color: var(--nx-text-faint);
}

/* ---- 扫描中的高亮状态 ---- */
.nx-pill--scanning {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--nx-accent, #4f8cff);
  background: var(--nx-accent-soft, rgba(79, 140, 255, 0.14));
  border: 1px solid var(--nx-accent, #4f8cff);
  font-weight: 600;
  animation: scan-pulse 1.4s ease-in-out infinite;
}
@keyframes scan-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.55; }
}
.scan-spinner {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  border: 2px solid currentColor;
  border-top-color: transparent;
  animation: scan-spin 0.8s linear infinite;
}
@keyframes scan-spin {
  to { transform: rotate(360deg); }
}
.scan-btn {
  min-width: 104px;
  font-weight: 600;
}
.scan-live {
  font-size: 12px;
  font-weight: 600;
  color: var(--nx-accent, #4f8cff);
  animation: scan-pulse 1.4s ease-in-out infinite;
  margin-left: auto;
}

.nx-dash-cols {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 392px;
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

/* 两列底部对齐：较矮一侧由最后一张卡片撑满剩余高度 */
.nx-dash-side > .nx-card:last-child {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.nx-dash-side > .nx-card:last-child .nx-side-kv {
  margin-bottom: 14px;
}
.nx-dash-side > .nx-card:last-child .nx-side-actions {
  margin-top: auto;
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

/* 成长等级徽标 */
.nx-lv-row {
  display: flex;
  align-items: center;
  gap: 16px;
}
.nx-lv-badge {
  width: 68px;
  height: 68px;
  border-radius: 50%;
  flex: none;
  display: grid;
  place-items: center;
  background: conic-gradient(
    var(--lv-color) calc(var(--lv-progress) * 1%),
    var(--nx-bg-hover) 0
  );
  box-shadow: 0 2px 14px color-mix(in srgb, var(--lv-color) 30%, transparent);
}
.nx-lv-badge-inner {
  width: 54px;
  height: 54px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: var(--nx-bg-elevated);
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--lv-color);
  font-variant-numeric: tabular-nums;
}
.nx-lv-info {
  min-width: 0;
}
.nx-lv-name {
  font-size: 17px;
  font-weight: 700;
  letter-spacing: -0.01em;
}
.nx-lv-tokens {
  margin-top: 3px;
  font-size: 12px;
  color: var(--nx-text-dim);
  font-variant-numeric: tabular-nums;
}
.nx-lv-next {
  margin-top: 3px;
  font-size: 11px;
  color: var(--nx-text-faint);
}

/* 等级说明弹层 */
.nx-lv-guide {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.nx-lv-guide-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  border-radius: 6px;
  font-size: 12px;
  color: var(--nx-text-faint);
}
.nx-lv-guide-row.on {
  color: var(--nx-text);
  background: var(--nx-bg-soft);
}
.nx-lv-guide-dot {
  width: 8px;
  height: 8px;
  border-radius: 3px;
  flex: none;
}
.nx-lv-guide-name {
  flex: 1;
}
.nx-lv-guide-th {
  font-variant-numeric: tabular-nums;
  color: var(--nx-text-faint);
}
.nx-lv-guide-note {
  margin-top: 8px;
  padding: 8px 6px 2px;
  border-top: 1px solid var(--nx-border-soft);
  font-size: 11px;
  line-height: 1.6;
  color: var(--nx-text-faint);
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

/* 智能体分布网格 */
.agent-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 10px;
}
.agent-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
  border: 1px solid var(--nx-border-soft);
  border-radius: var(--nx-r-sm);
  background: var(--nx-bg-soft);
  cursor: pointer;
  text-align: left;
  transition: border-color 0.15s var(--nx-ease), background 0.15s var(--nx-ease),
    transform 0.15s var(--nx-ease);
}
.agent-cell:hover {
  border-color: color-mix(in srgb, var(--nx-accent) 45%, var(--nx-border-soft));
  background: var(--nx-bg-hover);
  transform: translateY(-1px);
}
.agent-dot {
  width: 8px;
  height: 8px;
  border-radius: 3px;
  flex: none;
}
.agent-name {
  flex: 1;
  min-width: 0;
  font-size: 12.5px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.agent-count {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--nx-text-dim);
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
