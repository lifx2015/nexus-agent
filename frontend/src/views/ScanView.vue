<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { discoveredApi, errorMessage, scanApi } from '@/api/client'
import type { AgentStatus, DiscoveredRecord, DiscoveredStats, ScanJob } from '@/api/client'
import ScanProgress from '@/components/ScanProgress.vue'
import { AGENT_COLORS, SCAN_KIND_META } from '@/constants'

const agents = ref<AgentStatus[]>([])
const rows = ref<DiscoveredRecord[]>([])
const stats = ref<DiscoveredStats>({ total: 0, missing: 0, starred: 0, by_agent: {}, by_kind: {} })
const loading = ref(false)
const running = ref(false)
const job = ref<ScanJob | null>(null)

const filterAgent = ref('')
const filterKind = ref('')
const filterQ = ref('')
const onlyStar = ref(false)

const drawer = ref(false)
const editing = ref<DiscoveredRecord | null>(null)
const form = ref({ starred: false, note: '', tags: [] as string[], kind: '' })

let pollTimer: ReturnType<typeof setInterval> | null = null

function kindOf(kind: string) {
  return SCAN_KIND_META[kind] ?? SCAN_KIND_META.other
}
function agentColor(key: string, idx: number) {
  void idx
  const i = agents.value.findIndex((a) => a.key === key)
  return AGENT_COLORS[(i + AGENT_COLORS.length) % AGENT_COLORS.length]
}
function fmtSize(b: number) {
  if (b >= 1048576) return (b / 1048576).toFixed(1) + ' MB'
  if (b >= 1024) return (b / 1024).toFixed(1) + ' KB'
  return b + ' B'
}
function fmtTime(t: number | string) {
  if (typeof t === 'string') return (t ?? '').replace('T', ' ').slice(0, 19)
  if (!t) return '—'
  const d = new Date(t * 1000)
  return d.toLocaleString('zh-CN', { hour12: false }).slice(0, 17)
}

async function loadDiscovered() {
  loading.value = true
  try {
    const res = await discoveredApi.list({
      agent: filterAgent.value || undefined,
      kind: filterKind.value || undefined,
      q: filterQ.value || undefined,
      starred: onlyStar.value || undefined,
      limit: 500,
    })
    rows.value = res.items
    stats.value = res.stats
  } catch (e) {
    ElMessage.error(errorMessage(e))
  } finally {
    loading.value = false
  }
}

async function loadAgents() {
  try {
    agents.value = await scanApi.agents()
  } catch (e) {
    ElMessage.error(errorMessage(e))
  }
}

async function startScan() {
  if (running.value) return
  try {
    await scanApi.run({})
    running.value = true
    ElMessage.success('扫描已启动')
    poll()
  } catch (e) {
    ElMessage.error(errorMessage(e))
  }
}

function poll() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(async () => {
    try {
      job.value = await scanApi.status()
      running.value = !job.value?.done
      if (job.value?.done) {
        if (pollTimer) clearInterval(pollTimer)
        await loadDiscovered()
      }
    } catch (e) {
      ElMessage.error(errorMessage(e))
      if (pollTimer) clearInterval(pollTimer)
      running.value = false
    }
  }, 1200)
}

async function validatePaths() {
  try {
    const res = await discoveredApi.validate()
    ElMessage.success(`校验完成：${res.active} 有效，${res.missing} 已丢失`)
    await loadDiscovered()
  } catch (e) {
    ElMessage.error(errorMessage(e))
  }
}

async function toggleStar(rec: DiscoveredRecord) {
  const next = !rec.starred
  try {
    await discoveredApi.patch(rec.id, { starred: next })
    // 乐观更新：只改本地行与统计，避免全量刷新
    rec.starred = next ? 1 : 0
    stats.value.starred += next ? 1 : -1
  } catch (e) {
    ElMessage.error(errorMessage(e))
  }
}

async function open(rec: DiscoveredRecord, mode: 'file' | 'dir') {
  try {
    const res = await discoveredApi.open(rec.id, mode)
    ElMessage.success(`已打开：${res.opened}`)
  } catch (e) {
    ElMessage.error(errorMessage(e))
  }
}

function annotate(rec: DiscoveredRecord) {
  editing.value = rec
  form.value = { starred: !!rec.starred, note: rec.note || '', tags: [...(rec.tags || [])], kind: rec.kind }
  drawer.value = true
}

async function saveAnnotate() {
  if (!editing.value) return
  try {
    await discoveredApi.patch(editing.value.id, {
      starred: form.value.starred,
      note: form.value.note,
      tags: form.value.tags,
      kind: form.value.kind || undefined,
    })
    drawer.value = false
    await loadDiscovered()
    ElMessage.success('已保存')
  } catch (e) {
    ElMessage.error(errorMessage(e))
  }
}

async function removeRecord(rec: DiscoveredRecord) {
  try {
    await ElMessageBox.confirm(
      `从跟踪列表移除「${rec.name}」？仅移除记录，不会删除原文件。`,
      '移除记录',
      { type: 'warning', confirmButtonText: '移除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await discoveredApi.patch(rec.id, { starred: false, note: 'removed' })
    await loadDiscovered()
  } catch (e) {
    ElMessage.error(errorMessage(e))
  }
}

function pickAgent(key: string) {
  filterAgent.value = filterAgent.value === key ? '' : key
  loadDiscovered()
}

watch([filterAgent, filterKind, onlyStar], loadDiscovered)

onMounted(async () => {
  await Promise.all([loadAgents(), loadDiscovered()])
  // 若上次扫描尚未完成则继续轮询
  try {
    const st = await scanApi.status()
    if (!st.done) poll()
  } catch {
    /* ignore */
  }
})
onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<template>
  <div v-loading="loading" class="nx-page">
    <!-- 顶部操作 -->
    <div class="nx-page-head">
      <div style="min-width: 0">
        <div class="nx-page-title">本机 AI 智能体资产盘点</div>
        <div class="nx-page-desc">只记录路径做跟踪管理 · 绝不搬运或修改你的 Agent 数据</div>
      </div>
      <span class="nx-spacer" />
      <el-button :loading="running" type="primary" @click="startScan">
        <el-icon style="margin-right: 4px"><Refresh /></el-icon>{{ running ? '扫描中…' : '扫描本机' }}
      </el-button>
      <el-button @click="validatePaths">校验路径</el-button>
    </div>

    <!-- 统计 -->
    <div class="nx-stat-grid">
      <div class="nx-stat" style="--stat-color: #4f8cff">
        <div class="nx-stat-icon"><el-icon :size="19"><DataLine /></el-icon></div>
        <div class="nx-stat-body">
          <div class="nx-stat-label">已发现资产</div>
          <div class="nx-stat-value">{{ stats.total }}</div>
          <div class="nx-stat-sub">按 Agent 分布 · 点击下方卡片筛选</div>
        </div>
      </div>
      <div class="nx-stat" style="--stat-color: #22d3ee">
        <div class="nx-stat-icon"><el-icon :size="19"><MagicStick /></el-icon></div>
        <div class="nx-stat-body">
          <div class="nx-stat-label">技能 Skills</div>
          <div class="nx-stat-value">{{ stats.by_kind.skill ?? 0 }}</div>
          <div class="nx-stat-sub">本机全部 Agent 技能包</div>
        </div>
      </div>
      <div class="nx-stat" style="--stat-color: #f59e0b">
        <div class="nx-stat-icon"><el-icon :size="19"><Notebook /></el-icon></div>
        <div class="nx-stat-body">
          <div class="nx-stat-label">规范 + 记忆</div>
          <div class="nx-stat-value">{{ (stats.by_kind.rule ?? 0) + (stats.by_kind.memory ?? 0) }}</div>
          <div class="nx-stat-sub">规则 / 指令 / 长期记忆</div>
        </div>
      </div>
      <div class="nx-stat" style="--stat-color: #fbbf24">
        <div class="nx-stat-icon"><el-icon :size="19"><StarFilled /></el-icon></div>
        <div class="nx-stat-body">
          <div class="nx-stat-label">星标关注</div>
          <div class="nx-stat-value">{{ stats.starred }}</div>
          <div class="nx-stat-sub">丢失 {{ stats.missing }} 项 · 标记留意</div>
        </div>
      </div>
    </div>

    <!-- Agent 网格 -->
    <div class="nx-card" style="margin-bottom: 16px">
      <div class="nx-card-head">
        <span class="nx-card-title">受支持的智能体（{{ agents.length }}）</span>
        <span class="nx-spacer" />
        <span class="nx-dim" style="font-size: 12px">高亮 = 本机检测到数据</span>
      </div>
      <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px">
        <div
          v-for="(a, i) in agents"
          :key="a.key"
          class="agent-chip"
          :class="{ detected: a.detected, active: filterAgent === a.key }"
          :style="{ '--chip-color': a.detected ? AGENT_COLORS[i % AGENT_COLORS.length] : undefined }"
          tabindex="0"
          @click="pickAgent(a.key)"
          @keydown.enter="pickAgent(a.key)"
        >
          <div class="agent-chip-name">
            <span v-if="a.detected" class="dot" />
            {{ a.name }}
          </div>
          <div class="agent-chip-sub">
            {{ stats.by_agent[a.key] ?? 0 }} 项 · {{ a.vendor || '—' }}
          </div>
        </div>
      </div>
    </div>

    <!-- 扫描进度 -->
    <div v-if="running || (job && job.done)" class="nx-card" style="margin-bottom: 16px">
      <ScanProgress
        :running="running"
        :job="job"
        :agents="agents"
        :counts-fallback="stats.by_agent"
        :metrics="job ? [
          { label: '已发现', value: `${job.total} 项` },
          { label: '入库', value: `${job.indexed} 条` },
        ] : []"
      />
    </div>

    <!-- 筛选 -->
    <div class="nx-toolbar">
      <el-input v-model="filterQ" placeholder="搜索名称 / 摘要 / 路径" clearable style="width: 240px" @keyup.enter="loadDiscovered" @clear="loadDiscovered">
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-select v-model="filterKind" placeholder="类型" clearable style="width: 110px">
        <el-option v-for="(v, k) in SCAN_KIND_META" :key="k" :label="v.label" :value="k" />
      </el-select>
      <el-button :type="onlyStar ? 'warning' : 'default'" @click="onlyStar = !onlyStar">
        <el-icon style="margin-right: 3px"><StarFilled v-if="onlyStar" /><Star v-else /></el-icon>星标
      </el-button>
      <el-button text @click="loadDiscovered">刷新</el-button>
      <span class="nx-spacer" />
      <span class="nx-dim" style="font-size: 12px">{{ rows.length }} 条展示</span>
    </div>

    <!-- 发现列表 -->
    <div class="nx-card">
      <el-table v-if="rows.length" :data="rows" size="small" max-height="560" style="width: 100%">
        <el-table-column width="46" align="center">
          <template #default="{ row }">
            <el-icon
              :size="16"
              :color="row.starred ? 'var(--nx-star)' : 'var(--nx-text-faint)'"
              style="cursor: pointer; transition: transform 0.15s var(--nx-ease)"
              @click="toggleStar(row)"
            >
              <StarFilled v-if="row.starred" /><Star v-else />
            </el-icon>
          </template>
        </el-table-column>
        <el-table-column label="智能体" width="140">
          <template #default="{ row }">
            <div style="display: flex; align-items: center; gap: 5px">
              <span class="dot" :style="{ background: agentColor(row.agent) }" />
              <span>{{ row.agent }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="70" align="center">
          <template #default="{ row }">
            <el-tag size="small" :color="kindOf(row.kind).color" effect="dark">{{ kindOf(row.kind).label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="名称 / 摘要" min-width="240">
          <template #default="{ row }">
            <div style="font-weight: 600; line-height: 1.3">{{ row.name }}</div>
            <div v-if="row.summary" class="nx-dim" style="font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
              {{ row.summary }}
            </div>
          </template>
        </el-table-column>
        <el-table-column label="路径" min-width="200">
          <template #default="{ row }">
            <div class="nx-mono nx-dim" style="font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap" :title="row.path">
              {{ row.path }}
            </div>
          </template>
        </el-table-column>
        <el-table-column label="体积" width="80" align="right">
          <template #default="{ row }"><span class="nx-mono nx-dim">{{ fmtSize(row.size) }}</span></template>
        </el-table-column>
        <el-table-column label="状态" width="76" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'active' ? 'success' : 'danger'" effect="plain">
              {{ row.status === 'active' ? '有效' : '丢失' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" align="right">
          <template #default="{ row }">
            <el-button link size="small" @click="open(row, 'dir')">目录</el-button>
            <el-button link size="small" @click="open(row, 'file')">打开</el-button>
            <el-button link type="primary" size="small" @click="annotate(row)">标注</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div v-else class="nx-empty">
        尚未盘点任何资产。<br />点击「扫描本机」，将对 {{ agents.length }} 家智能体的数据目录做只读盘点。
      </div>
    </div>

    <!-- 标注抽屉 -->
    <el-drawer v-model="drawer" title="跟踪标注" size="440px">
      <div v-if="editing" style="margin-bottom: 14px">
        <div style="font-weight: 600; font-size: 15px">{{ editing.name }}</div>
        <div class="nx-mono nx-dim" style="font-size: 12px; word-break: break-all">{{ editing.path }}</div>
      </div>
      <el-form v-if="editing" label-position="top">
        <el-form-item label="归类">
          <el-select v-model="form.kind" style="width: 100%">
            <el-option v-for="(v, k) in SCAN_KIND_META" :key="k" :label="v.label" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item label="标签">
          <el-select v-model="form.tags" multiple filterable allow-create default-first-option style="width: 100%" placeholder="回车创建标签" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.note" type="textarea" :rows="4" placeholder="为什么关注它 / 待办" />
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="form.starred">星标关注</el-checkbox>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="drawer = false">取消</el-button>
        <el-button type="primary" @click="saveAnnotate">保存</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.agent-chip {
  border: 1px solid var(--nx-border-soft);
  border-radius: 10px;
  padding: 10px 12px;
  background: var(--nx-bg-soft);
  cursor: pointer;
  transition: border-color 0.18s var(--nx-ease), background 0.18s var(--nx-ease),
    transform 0.18s var(--nx-ease);
}
.agent-chip:hover {
  border-color: var(--nx-outline);
  background: var(--nx-bg-hover);
  transform: translateY(-1px);
}
.agent-chip:focus-visible {
  outline: 2px solid var(--nx-accent);
  outline-offset: 2px;
}
.agent-chip.detected {
  border-color: color-mix(in srgb, var(--chip-color, var(--nx-accent)) 38%, var(--nx-border-soft));
}
.agent-chip.active {
  border-color: var(--nx-accent);
  background: var(--nx-accent-soft);
}
.agent-chip-name {
  font-weight: 600;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.agent-chip-sub {
  margin-top: 4px;
  font-size: 11px;
  color: var(--nx-text-faint);
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
  flex: none;
  background: var(--chip-color, var(--nx-accent));
  box-shadow: 0 0 6px color-mix(in srgb, var(--chip-color, var(--nx-accent)) 70%, transparent);
}
</style>
