<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { errorMessage, usageApi } from '@/api/client'
import type {
  UsageAdapterInfo, UsageBucket, UsageRecordRow, UsageStatRow, UsageSummary,
} from '@/api/client'
import { fmtCompact, fmtInt, fmtPct, fmtTs } from '@/utils/format'
import TrendChart from '@/components/TrendChart.vue'

const DAYS_OPTIONS = [
    { label: '今天', value: 1 },
    { label: '过去 24 小时', value: -1 },
    { label: '近 7 天', value: 7 },
    { label: '近 30 天', value: 30 },
    { label: '全部', value: 0 },
]

const days = ref(1)
const agent = ref('')
const model = ref('')
const facets = ref<{ agents: string[]; models: string[] }>({ agents: [], models: [] })
const adapters = ref<UsageAdapterInfo[]>([])
const unsupported = ref<{ key: string; name: string }[]>([])
const summary = ref<UsageSummary | null>(null)
const buckets = ref<UsageBucket[]>([])
const granularity = ref<'hour' | 'day'>('day')
const metric = ref<'stacked' | 'requests'>('stacked')

// 三张表的服务端分页状态
const byAgent = ref<UsageStatRow[]>([])
const agentPage = ref(1)
const agentSize = ref(8)
const agentTotal = ref(0)
const byModel = ref<UsageStatRow[]>([])
const modelPage = ref(1)
const modelSize = ref(8)
const modelTotal = ref(0)
const records = ref<UsageRecordRow[]>([])
const recPage = ref(1)
const recSize = ref(20)
const recTotal = ref(0)

const loading = ref(false)
const syncing = ref(false)
const lastSync = ref('')
let timer: number | null = null

const hasData = computed(() => (summary.value?.requests ?? 0) > 0)

function fmtTokens(n: number | undefined): string {
  return fmtCompact(n ?? 0)
}

async function loadAgentStats() {
  const r = await usageApi.stats('agent', {
    days: days.value, limit: agentSize.value, offset: (agentPage.value - 1) * agentSize.value,
  })
  byAgent.value = r.rows
  agentTotal.value = r.total
}

async function loadModelStats() {
  const r = await usageApi.stats('model', {
    days: days.value, limit: modelSize.value, offset: (modelPage.value - 1) * modelSize.value,
  })
  byModel.value = r.rows
  modelTotal.value = r.total
}

async function loadRecords() {
  const q = {
    days: days.value,
    agent: agent.value || undefined,
    model: model.value || undefined,
    limit: recSize.value,
    offset: (recPage.value - 1) * recSize.value,
  }
  const r = await usageApi.records(q)
  records.value = r.items
  recTotal.value = r.total
}

async function loadAll(resetPages = true) {
  loading.value = true
  try {
    const q = {
      days: days.value,
      agent: agent.value || undefined,
      model: model.value || undefined,
    }
    if (resetPages) {
      agentPage.value = 1
      modelPage.value = 1
      recPage.value = 1
    }
    const [s, t, fc, ad] = await Promise.all([
      usageApi.summary(q),
      usageApi.trends(q),
      usageApi.facets(),
      usageApi.adapters(),
    ])
    summary.value = s
    buckets.value = t.buckets
    granularity.value = t.granularity
    facets.value = fc
    adapters.value = ad.adapters
    unsupported.value = ad.unsupported
    await Promise.all([loadAgentStats(), loadModelStats(), loadRecords()])
  } catch (e) {
    ElMessage.error(errorMessage(e))
  } finally {
    loading.value = false
  }
}

async function pollSync() {
  try {
    const job = await usageApi.syncStatus()
    if (job.done) {
      syncing.value = false
      lastSync.value = job.finished
      const t = job.total || {}
      const errs = (t.errors || []).length
      if (job.error || errs) {
        ElMessage.warning(`同步完成但有错误：${job.error || job.total?.errors?.[0] || `${errs} 项`}`)
      } else {
        ElMessage.success(
          `同步完成：扫描 ${fmtInt(t.files_scanned ?? 0)} 个来源，新增 ${fmtInt(t.imported ?? 0)} 条用量`,
        )
      }
      await loadAll()
      return
    }
    window.setTimeout(pollSync, 1000)
  } catch {
    syncing.value = false
  }
}

async function triggerSync() {
  if (syncing.value) return
  syncing.value = true
  try {
    await usageApi.sync()
    pollSync()
  } catch (e) {
    syncing.value = false
    ElMessage.error(errorMessage(e))
  }
}

async function resetAll() {
  try {
    await ElMessageBox.confirm(
      '将清空已导入的用量明细与增量游标（只动本软件数据库，不碰任何智能体的会话文件）；下次同步会全量重建。',
      '重建用量数据',
      { type: 'warning', confirmButtonText: '清空并重建', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const { deleted } = await usageApi.reset()
    ElMessage.success(`已清空 ${fmtInt(deleted)} 条明细，点击「同步」重建`)
    await loadAll()
  } catch (e) {
    ElMessage.error(errorMessage(e))
  }
}

onMounted(() => {
  loadAll()
  // 与后端自动增量同步（默认 120s）错峰的轻量轮询
  timer = window.setInterval(loadAll, 30000)
})
onUnmounted(() => {
  if (timer !== null) window.clearInterval(timer)
})
</script>

<template>
  <div v-loading="loading" class="nx-page">
    <div class="usage-desc">
      解析本机智能体的会话日志（只读，不修改任何原文件），统计每次模型调用的 token 消耗
    </div>

    <div class="nx-toolbar" style="margin-bottom: 14px">
      <el-radio-group v-model="days" size="small" @change="() => loadAll()">
        <el-radio-button v-for="o in DAYS_OPTIONS" :key="o.value" :value="o.value">
          {{ o.label }}
        </el-radio-button>
      </el-radio-group>
      <el-select v-model="agent" size="small" clearable placeholder="全部智能体" style="width: 150px" @change="() => loadAll()">
        <el-option v-for="a in facets.agents" :key="a" :label="a" :value="a" />
      </el-select>
      <el-select v-model="model" size="small" clearable filterable placeholder="全部模型" style="width: 210px" @change="() => loadAll()">
        <el-option v-for="m in facets.models" :label="m" :value="m" />
      </el-select>
      <span class="nx-spacer" />
      <span v-if="lastSync" class="nx-dim" style="font-size: 12px">上次同步 {{ lastSync }}</span>
      <el-button size="small" type="primary" :loading="syncing" @click="triggerSync">
        <el-icon><Refresh /></el-icon>&nbsp;同步
      </el-button>
      <el-button size="small" text @click="resetAll">重建</el-button>
    </div>

    <!-- 数据来源状态 -->
    <div class="usage-sources">
      <el-tooltip v-for="a in adapters" :key="a.source" :content="a.roots.join('\n') || '未发现数据目录'">
        <span class="nx-pill" :class="a.detected ? 'nx-pill--success' : 'nx-pill--muted'">
          {{ a.name }} · {{ a.detected ? '已检测' : '未发现' }}
          <template v-if="a.records"> · {{ fmtTokens(a.records) }} 条</template>
        </span>
      </el-tooltip>
      <el-tooltip v-if="unsupported.length" :content="unsupported.map((u) => u.name).join(' / ')">
        <span class="nx-pill nx-pill--muted">{{ unsupported.length }} 家暂不支持（会话无 usage 字段）</span>
      </el-tooltip>
    </div>

    <!-- 空态 -->
    <div v-if="!hasData && !loading" class="nx-card">
      <div class="nx-empty">
        暂无用量数据。若本机使用过 Claude Code / Codex / Gemini CLI / OpenCode，点上方「同步」导入；
        之后每 2 分钟自动增量同步。
      </div>
    </div>

    <template v-else>
      <!-- 汇总卡 -->
      <div class="nx-stat-grid" v-if="summary">
        <div class="nx-stat">
          <div class="nx-stat-body">
            <div class="nx-stat-label">真实处理 Token</div>
            <div class="nx-stat-value">{{ fmtTokens(summary.real_total_tokens) }}</div>
            <div class="nx-stat-sub">输入+输出+缓存建+缓存读</div>
          </div>
        </div>
        <div class="nx-stat">
          <div class="nx-stat-body">
            <div class="nx-stat-label">输入</div>
            <div class="nx-stat-value">{{ fmtTokens(summary.input_tokens) }}</div>
            <div class="nx-stat-sub">不含缓存的新鲜输入</div>
          </div>
        </div>
        <div class="nx-stat">
          <div class="nx-stat-body">
            <div class="nx-stat-label">输出</div>
            <div class="nx-stat-value">{{ fmtTokens(summary.output_tokens) }}</div>
            <div class="nx-stat-sub">含思考 {{ fmtTokens(summary.reasoning_tokens) }}</div>
          </div>
        </div>
        <div class="nx-stat">
          <div class="nx-stat-body">
            <div class="nx-stat-label">缓存读</div>
            <div class="nx-stat-value">{{ fmtTokens(summary.cache_read_tokens) }}</div>
            <div class="nx-stat-sub">命中率 {{ fmtPct(summary.cache_hit_rate) }}</div>
          </div>
        </div>
        <div class="nx-stat">
          <div class="nx-stat-body">
            <div class="nx-stat-label">缓存写</div>
            <div class="nx-stat-value">{{ fmtTokens(summary.cache_creation_tokens) }}</div>
            <div class="nx-stat-sub">prompt cache 创建</div>
          </div>
        </div>
        <div class="nx-stat">
          <div class="nx-stat-body">
            <div class="nx-stat-label">请求 / 会话</div>
            <div class="nx-stat-value">{{ fmtInt(summary.requests) }} / {{ fmtInt(summary.sessions) }}</div>
            <div class="nx-stat-sub">模型调用次数 / 会话数</div>
          </div>
        </div>
      </div>

      <!-- 趋势 -->
      <div class="nx-card" style="margin-top: 14px">
        <div class="nx-card-head">
          <span class="nx-card-title">用量趋势</span>
          <span class="nx-spacer" />
          <el-radio-group v-model="metric" size="small">
            <el-radio-button value="stacked">Token 构成</el-radio-button>
            <el-radio-button value="requests">请求数</el-radio-button>
          </el-radio-group>
        </div>
        <TrendChart :buckets="buckets" :metric="metric" :granularity="granularity" />
      </div>

      <!-- 分组统计 -->
      <div class="usage-two-col">
        <div class="nx-card">
          <div class="nx-card-head"><span class="nx-card-title">按智能体</span></div>
          <el-table :data="byAgent" size="small" style="width: 100%">
            <el-table-column prop="key" label="智能体" min-width="120" />
            <el-table-column label="请求" width="70" align="right">
              <template #default="{ row }">{{ fmtInt(row.requests) }}</template>
            </el-table-column>
            <el-table-column label="Token" width="90" align="right">
              <template #default="{ row }">{{ fmtTokens(row.real_total_tokens) }}</template>
            </el-table-column>
            <el-table-column label="输入/输出" min-width="120" align="right">
              <template #default="{ row }">{{ fmtTokens(row.input_tokens) }} / {{ fmtTokens(row.output_tokens) }}</template>
            </el-table-column>
          </el-table>
          <el-pagination
            v-if="agentTotal > agentSize"
            v-model:current-page="agentPage"
            class="usage-pager"
            layout="total, prev, pager, next"
            :total="agentTotal"
            :page-size="agentSize"
            size="small"
            background
            @current-change="loadAgentStats"
          />
        </div>
        <div class="nx-card">
          <div class="nx-card-head"><span class="nx-card-title">按模型</span></div>
          <el-table :data="byModel" size="small" style="width: 100%">
            <el-table-column prop="key" label="模型" min-width="150" class-name="nx-mono" />
            <el-table-column label="请求" width="70" align="right">
              <template #default="{ row }">{{ fmtInt(row.requests) }}</template>
            </el-table-column>
            <el-table-column label="Token" width="90" align="right">
              <template #default="{ row }">{{ fmtTokens(row.real_total_tokens) }}</template>
            </el-table-column>
            <el-table-column label="输入/输出" min-width="120" align="right">
              <template #default="{ row }">{{ fmtTokens(row.input_tokens) }} / {{ fmtTokens(row.output_tokens) }}</template>
            </el-table-column>
          </el-table>
          <el-pagination
            v-if="modelTotal > modelSize"
            v-model:current-page="modelPage"
            class="usage-pager"
            layout="total, prev, pager, next"
            :total="modelTotal"
            :page-size="modelSize"
            size="small"
            background
            @current-change="loadModelStats"
          />
        </div>
      </div>

      <!-- 明细 -->
      <div class="nx-card" style="margin-top: 14px">
        <div class="nx-card-head">
          <span class="nx-card-title">最近调用</span>
          <span class="nx-spacer" />
          <el-pagination
            v-model:current-page="recPage"
            v-model:page-size="recSize"
            layout="total, sizes, prev, pager, next"
            :total="recTotal"
            :page-sizes="[10, 20, 50, 100]"
            size="small"
            background
            @current-change="loadRecords"
            @size-change="() => { recPage = 1; loadRecords() }"
          />
        </div>
        <el-table :data="records" size="small" style="width: 100%">
          <el-table-column label="时间" width="150" class-name="nx-mono">
            <template #default="{ row }">{{ fmtTs(row.ts) }}</template>
          </el-table-column>
          <el-table-column prop="agent" label="智能体" width="110" />
          <el-table-column prop="model" label="模型" min-width="170" class-name="nx-mono" />
          <el-table-column label="输入" width="80" align="right">
            <template #default="{ row }">{{ fmtTokens(row.input_tokens) }}</template>
          </el-table-column>
          <el-table-column label="输出" width="80" align="right">
            <template #default="{ row }">{{ fmtTokens(row.output_tokens) }}</template>
          </el-table-column>
          <el-table-column label="缓存读" width="80" align="right">
            <template #default="{ row }">{{ fmtTokens(row.cache_read_tokens) }}</template>
          </el-table-column>
          <el-table-column label="缓存写" width="80" align="right">
            <template #default="{ row }">{{ fmtTokens(row.cache_creation_tokens) }}</template>
          </el-table-column>
        </el-table>
      </div>
    </template>
  </div>
</template>

<style scoped>
.usage-desc {
  font-size: 12px;
  color: var(--el-text-color-secondary, #94a3b8);
  margin-bottom: 12px;
}
/* 紧凑统计卡：小窗口自适应列数 + 缩小字号（仅本页覆盖，不影响概览页） */
.nx-stat-grid {
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}
.nx-stat-value {
  font-size: 20px;
}
.nx-stat-label {
  font-size: 12px;
}
.nx-stat-sub {
  font-size: 11px;
}
.usage-sources {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
.usage-two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
  margin-top: 14px;
}
.usage-pager {
  margin-top: 10px;
  justify-content: flex-end;
}
@media (max-width: 980px) {
  .usage-two-col { grid-template-columns: 1fr; }
}
</style>
