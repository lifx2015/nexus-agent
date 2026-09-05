<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { errorMessage, projectsApi, scanApi } from '@/api/client'
import type { AgentStatus, ProjectOpener, ProjectRecord, ProjectStats, ScanJob } from '@/api/client'
import ScanProgress from '@/components/ScanProgress.vue'
import { AGENT_COLORS } from '@/constants'
import { fmtCompact, fmtInt, fmtTs } from '@/utils/format'

const agents = ref<AgentStatus[]>([])
const openers = ref<ProjectOpener[]>([])
const rows = ref<ProjectRecord[]>([])
const stats = ref<ProjectStats>({
  total: 0, missing: 0, excluded: 0, starred: 0, sessions: 0,
  by_agent: {}, attributed_requests: 0, attributed_tokens: 0,
})
const loading = ref(false)
const running = ref(false)
const job = ref<ScanJob | null>(null)

const filterAgent = ref('')
const filterStatus = ref('')
const filterQ = ref('')
const onlyStar = ref(false)
const agentsOpen = ref(false) // 智能体筛选默认折叠，把空间留给项目列表

const detectedCount = computed(() => agents.value.filter((a) => a.detected).length)

const drawer = ref(false)
const editing = ref<ProjectRecord | null>(null)
const form = ref({ starred: false, note: '', tags: [] as string[], name: '' })

let pollTimer: ReturnType<typeof setInterval> | null = null

function agentColor(key: string) {
  const i = agents.value.findIndex((a) => a.key === key)
  return AGENT_COLORS[(i + AGENT_COLORS.length) % AGENT_COLORS.length]
}

const statusTag = (s: string) =>
  s === 'active' ? 'success' : s === 'missing' ? 'danger' : 'info'
const statusLabel = (s: string) =>
  s === 'active' ? '有效' : s === 'missing' ? '丢失' : '已排除'
const sourceLabel = (s: string) =>
  s === 'log' ? '日志回溯' : s === 'manual' ? '手动登记' : '标记文件'

async function loadProjects() {
  loading.value = true
  try {
    const res = await projectsApi.list({
      agent: filterAgent.value || undefined,
      status: filterStatus.value || undefined,
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
    ElMessage.success('扫描已启动（将同步盘点 AI 项目）')
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
        await loadProjects()
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
    const res = await projectsApi.validate()
    ElMessage.success(`校验完成：${res.active} 有效，${res.missing} 丢失，${res.excluded} 已排除`)
    await loadProjects()
  } catch (e) {
    ElMessage.error(errorMessage(e))
  }
}

async function addProject() {
  let value: string
  try {
    const res = await ElMessageBox.prompt(
      '输入要跟踪的项目目录绝对路径（只登记路径，不读取内容）',
      '手动登记项目',
      { confirmButtonText: '登记', cancelButtonText: '取消', inputPlaceholder: '如 E:\\workspace\\my-project' },
    )
    value = (res.value || '').trim()
  } catch {
    return
  }
  if (!value) return
  try {
    await projectsApi.add(value)
    ElMessage.success('已登记')
    await loadProjects()
  } catch (e) {
    ElMessage.error(errorMessage(e))
  }
}

async function toggleStar(rec: ProjectRecord) {
  const next = !rec.starred
  try {
    await projectsApi.patch(rec.id, { starred: next })
    rec.starred = next ? 1 : 0
    stats.value.starred += next ? 1 : -1
  } catch (e) {
    ElMessage.error(errorMessage(e))
  }
}

async function openDir(rec: ProjectRecord, app = 'explorer') {
  try {
    const res = await projectsApi.open(rec.id, app)
    ElMessage.success(`已在 ${res.app} 中打开：${res.opened}`)
  } catch (e) {
    ElMessage.error(errorMessage(e))
  }
}

async function loadOpeners() {
  try {
    openers.value = await projectsApi.openers()
  } catch {
    /* 打开方式加载失败不阻塞列表 */
  }
}

const availableOpeners = computed(() => openers.value.filter((o) => o.available && o.key !== 'explorer'))
const unavailableOpeners = computed(() => openers.value.filter((o) => !o.available && o.key !== 'explorer'))

function openWith(rec: ProjectRecord, cmd: string | number | object) {
  openDir(rec, String(cmd))
}

function annotate(rec: ProjectRecord) {
  editing.value = rec
  form.value = { starred: !!rec.starred, note: rec.note || '', tags: [...(rec.tags || [])], name: rec.name }
  drawer.value = true
}

async function saveAnnotate() {
  if (!editing.value) return
  try {
    await projectsApi.patch(editing.value.id, {
      starred: form.value.starred,
      note: form.value.note,
      tags: form.value.tags,
      name: form.value.name || undefined,
    })
    drawer.value = false
    await loadProjects()
    ElMessage.success('已保存')
  } catch (e) {
    ElMessage.error(errorMessage(e))
  }
}

async function toggleExclude(rec: ProjectRecord) {
  const excluding = rec.status !== 'excluded'
  try {
    if (excluding) {
      await ElMessageBox.confirm(
        `排除「${rec.name}」？排除后扫描不再将其列入，可随时恢复。仅改记录，不动原目录。`,
        '排除项目',
        { type: 'warning', confirmButtonText: '排除', cancelButtonText: '取消' },
      )
      await projectsApi.exclude(rec.id)
    } else {
      await projectsApi.restore(rec.id)
    }
    await loadProjects()
    ElMessage.success(excluding ? '已排除' : '已恢复')
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(errorMessage(e))
  }
}

function pickAgent(key: string) {
  filterAgent.value = filterAgent.value === key ? '' : key
  loadProjects()
}

watch([filterStatus, onlyStar], loadProjects)

onMounted(async () => {
  await Promise.all([loadAgents(), loadProjects(), loadOpeners()])
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
    <!-- 顶部操作（页头标题已在顶栏展示，此处只留描述） -->
    <div class="nx-page-head">
      <div style="min-width: 0">
        <div class="nx-page-desc">从本机智能体会话日志回溯 AI 实际开发/维护过的项目 · 只跟踪路径，不搬数据</div>
      </div>
      <span class="nx-spacer" />
      <el-button :loading="running" type="primary" @click="startScan">
        <el-icon style="margin-right: 4px"><Refresh /></el-icon>{{ running ? '扫描中…' : '扫描本机' }}
      </el-button>
      <el-button @click="addProject"><el-icon style="margin-right: 4px"><Plus /></el-icon>登记项目</el-button>
      <el-button @click="validatePaths">校验路径</el-button>
    </div>

    <!-- 统计（紧凑版，给列表留空间） -->
    <div class="nx-stat-grid stat-compact">
      <div class="nx-stat" style="--stat-color: #4f8cff">
        <div class="nx-stat-icon"><el-icon :size="16"><Folder /></el-icon></div>
        <div class="nx-stat-body">
          <div class="nx-stat-label">跟踪项目</div>
          <div class="nx-stat-value">{{ stats.total }}</div>
          <div class="nx-stat-sub">丢失 {{ stats.missing }} · 排除 {{ stats.excluded }}</div>
        </div>
      </div>
      <div class="nx-stat" style="--stat-color: #22d3ee">
        <div class="nx-stat-icon"><el-icon :size="16"><ChatDotRound /></el-icon></div>
        <div class="nx-stat-body">
          <div class="nx-stat-label">关联会话</div>
          <div class="nx-stat-value">{{ fmtInt(stats.sessions) }}</div>
          <div class="nx-stat-sub">会话 → 项目 映射</div>
        </div>
      </div>
      <div class="nx-stat" style="--stat-color: #a78bfa">
        <div class="nx-stat-icon"><el-icon :size="16"><TrendCharts /></el-icon></div>
        <div class="nx-stat-body">
          <div class="nx-stat-label">已归因用量</div>
          <div class="nx-stat-value">{{ fmtCompact(stats.attributed_tokens) }}</div>
          <div class="nx-stat-sub">{{ fmtInt(stats.attributed_requests) }} 次调用</div>
        </div>
      </div>
      <div class="nx-stat" style="--stat-color: #fbbf24">
        <div class="nx-stat-icon"><el-icon :size="16"><StarFilled /></el-icon></div>
        <div class="nx-stat-body">
          <div class="nx-stat-label">星标关注</div>
          <div class="nx-stat-value">{{ stats.starred }}</div>
          <div class="nx-stat-sub">token 用量见列表</div>
        </div>
      </div>
    </div>

    <!-- 按智能体筛选（可折叠，默认收起） -->
    <div class="nx-card agent-filter" style="margin-bottom: 12px">
      <div
        class="agent-filter-head"
        role="button"
        tabindex="0"
        @click="agentsOpen = !agentsOpen"
        @keydown.enter="agentsOpen = !agentsOpen"
      >
        <span class="agent-filter-title">按智能体筛选</span>
        <span class="nx-dim" style="font-size: 12px">{{ detectedCount }} 家在本机有数据 · 共 {{ stats.total }} 个项目</span>
        <el-tag
          v-if="filterAgent && !agentsOpen"
          size="small"
          closable
          type="info"
          @close.stop="pickAgent(filterAgent)"
        >
          {{ filterAgent }}
        </el-tag>
        <span class="nx-spacer" />
        <el-icon class="chev" :class="{ open: agentsOpen }"><ArrowDown /></el-icon>
      </div>
      <div v-show="agentsOpen" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; margin-top: 12px">
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
          <div class="agent-chip-sub">{{ stats.by_agent[a.key] ?? 0 }} 个项目 · {{ a.vendor || '—' }}</div>
        </div>
      </div>
    </div>

    <!-- 扫描进度 -->
    <div v-if="running || (job && job.done)" class="nx-card scan-status" style="margin-bottom: 12px">
      <ScanProgress
        :running="running"
        :job="job"
        :agents="agents"
        :counts-fallback="stats.by_agent"
        :metrics="job ? [
          { label: '资产', value: `${job.total} 项` },
          { label: '项目', value: `${job.projects_found ?? 0} 个` },
          { label: '用量归因', value: `${job.usage_backfilled ?? 0} 条` },
        ] : []"
      />
    </div>

    <!-- 筛选 -->
    <div class="nx-toolbar">
      <el-input v-model="filterQ" placeholder="搜索名称 / 路径 / 备注" clearable style="width: 240px" @keyup.enter="loadProjects" @clear="loadProjects">
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-select v-model="filterStatus" placeholder="状态" clearable style="width: 110px">
        <el-option label="有效" value="active" />
        <el-option label="丢失" value="missing" />
        <el-option label="已排除" value="excluded" />
      </el-select>
      <el-button :type="onlyStar ? 'warning' : 'default'" @click="onlyStar = !onlyStar">
        <el-icon style="margin-right: 3px"><StarFilled v-if="onlyStar" /><Star v-else /></el-icon>星标
      </el-button>
      <el-button text @click="loadProjects">刷新</el-button>
      <span class="nx-spacer" />
      <span class="nx-dim" style="font-size: 12px">{{ rows.length }} 个项目</span>
    </div>

    <!-- 项目列表 -->
    <div class="nx-card">
      <el-table v-if="rows.length" :data="rows" size="small" max-height="640" style="width: 100%">
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
        <el-table-column label="项目" min-width="250">
          <template #default="{ row }">
            <div style="font-weight: 600; line-height: 1.3">{{ row.name }}</div>
            <div class="nx-mono nx-dim" style="font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap" :title="row.path">
              {{ row.path }}
            </div>
            <div v-if="(row.tags && row.tags.length) || row.note" style="margin-top: 4px; display: flex; align-items: center; gap: 4px; flex-wrap: wrap">
              <el-tag v-for="t in row.tags" :key="t" size="small" effect="plain">{{ t }}</el-tag>
              <el-tooltip v-if="row.note" :content="row.note" placement="top">
                <span class="note-hint">
                  <el-icon :size="12"><ChatLineSquare /></el-icon>{{ row.note }}
                </span>
              </el-tooltip>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="智能体" min-width="150">
          <template #default="{ row }">
            <div style="display: flex; align-items: center; gap: 5px; flex-wrap: wrap">
              <span v-for="a in row.agents" :key="a" class="nx-pill" style="font-size: 11px">
                <span class="dot" :style="{ background: agentColor(a) }" />&nbsp;{{ a }}
              </span>
              <span v-if="!row.agents.length" class="nx-dim" style="font-size: 11px">—</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="88" align="center">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" type="info">{{ sourceLabel(row.source) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="Tokens / 会话 / 调用" width="170" align="right">
          <template #default="{ row }">
            <el-tooltip
              content="Tokens = 已归因 token 总量；会话 = 关联会话数；调用 = 模型调用次数"
              placement="top"
            >
              <span class="nx-mono usage-cell">
                <b>{{ fmtCompact(row.tokens) }}</b>
                <i class="sep">/</i>{{ row.sessions }}
                <i class="sep">/</i>{{ fmtInt(row.requests) }}
              </span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="76" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="statusTag(row.status)" effect="plain">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最近活动" width="150">
          <template #default="{ row }">
            <span class="nx-mono nx-dim" style="font-size: 12px">{{ fmtTs(row.last_activity_ts) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" align="right">
          <template #default="{ row }">
            <el-dropdown size="small" trigger="click" @command="(cmd) => openWith(row, cmd)">
              <el-button link type="primary" size="small">
                打开<el-icon style="margin-left: 2px"><ArrowDown /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="explorer">资源管理器</el-dropdown-item>
                  <el-dropdown-item
                    v-for="o in availableOpeners"
                    :key="o.key"
                    :command="o.key"
                    divided
                  >
                    {{ o.label }}<span class="nx-dim" style="font-size: 11px">{{ o.kind === 'agent' ? ' · 终端' : '' }}</span>
                  </el-dropdown-item>
                  <el-dropdown-item v-if="unavailableOpeners.length" disabled divided>
                    <span class="nx-dim" style="font-size: 11px">本机未检测到：{{ unavailableOpeners.map((o) => o.label).join(' / ') }}</span>
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
            <el-button link type="primary" size="small" @click="annotate(row)">标注</el-button>
            <el-button link :type="row.status === 'excluded' ? 'success' : 'danger'" size="small" @click="toggleExclude(row)">
              {{ row.status === 'excluded' ? '恢复' : '排除' }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div v-else class="nx-empty">
        尚未跟踪任何项目。<br />点击「扫描本机」，将从各智能体的会话日志回溯 AI 实际工作过的项目（只读）。
      </div>
    </div>

    <!-- 标注抽屉 -->
    <el-drawer v-model="drawer" title="项目标注" size="440px">
      <div v-if="editing" style="margin-bottom: 14px">
        <div style="font-weight: 600; font-size: 15px">{{ editing.name }}</div>
        <div class="nx-mono nx-dim" style="font-size: 12px; word-break: break-all">{{ editing.path }}</div>
      </div>
      <el-form v-if="editing" label-position="top">
        <el-form-item label="显示名称">
          <el-input v-model="form.name" placeholder="留空则使用目录名" />
        </el-form-item>
        <el-form-item label="标签">
          <el-select v-model="form.tags" multiple filterable allow-create default-first-option style="width: 100%" placeholder="回车创建标签" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.note" type="textarea" :rows="4" placeholder="项目背景 / 待办 / 为什么关注" />
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
/* ---- 紧凑布局：压缩页头/统计/筛选的纵向占用，把空间留给项目列表 ---- */
.nx-page-head {
  margin-bottom: 12px;
}
.nx-page-title {
  font-size: 16px;
}
.nx-page-desc {
  font-size: 12px;
  margin-top: 2px;
}

/* 统计卡紧凑版（覆盖全局 nx-stat 的大内边距与大数字） */
.stat-compact.nx-stat-grid {
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}
.stat-compact .nx-stat {
  padding: 10px 14px;
  gap: 10px;
  align-items: center;
}
.stat-compact .nx-stat-icon {
  width: 32px;
  height: 32px;
  border-radius: 9px;
}
.stat-compact .nx-stat-label {
  font-size: 12px;
  line-height: 1.2;
}
.stat-compact .nx-stat-value {
  margin-top: 1px;
  font-size: 19px;
}
.stat-compact .nx-stat-sub {
  margin-top: 2px;
  font-size: 11px;
}

/* 智能体筛选：单行可折叠头 */
.agent-filter {
  padding: 10px 16px;
}
.agent-filter-head {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  user-select: none;
}
.agent-filter-head:focus-visible {
  outline: 2px solid var(--nx-accent);
  outline-offset: 2px;
}
.agent-filter-title {
  font-weight: 600;
  font-size: 13px;
}
.chev {
  transition: transform 0.18s var(--nx-ease);
  color: var(--nx-text-dim);
}
.chev.open {
  transform: rotate(180deg);
}

/* 扫描状态条更薄 */
.scan-status {
  padding: 10px 16px;
}

.nx-toolbar {
  margin-bottom: 12px;
}

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

/* 列内备注提示：单行截断 + tooltip 全文 */
.note-hint {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  max-width: 260px;
  font-size: 11px;
  color: var(--nx-text-faint);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: default;
}

/* 合并列：Tokens / 会话 / 调用 */
.usage-cell {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.usage-cell b {
  font-weight: 700;
}
.usage-cell .sep {
  color: var(--nx-text-faint);
  font-style: normal;
  margin: 0 5px;
}
</style>
