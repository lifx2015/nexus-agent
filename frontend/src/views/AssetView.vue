<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { assetsApi, discoveredApi, errorMessage } from '@/api/client'
import type { Asset, DiscoveredRecord } from '@/api/client'
import SkillPreview from '@/components/SkillPreview.vue'
import { AGENT_COLORS, KIND_META, STATUS_LIST, STATUS_META } from '@/constants'
import type { AssetKind, AssetStatus } from '@/constants'

const route = useRoute()
// kind 可由外部 prop 指定（嵌入「技能」中心页）；独立路由 /assets/:kind 时取路由参数
const props = defineProps<{ kind?: AssetKind }>()
const kind = computed(() => props.kind ?? (route.params.kind as AssetKind))
const meta = computed(() => KIND_META[kind.value])

const ownRows = ref<Asset[]>([])
const trackRows = ref<DiscoveredRecord[]>([])
const loading = ref(false)
const q = ref('')
const status = ref<AssetStatus | ''>('')
const tag = ref('')
const source = ref<'' | 'own' | 'track'>('')

type Row =
  | ({ src: 'own' } & Asset)
  | ({ src: 'track'; starred: boolean } & DiscoveredRecord)

const displayRows = computed<Row[]>(() =>
  mergedRows.value.filter((r) => !source.value || r.src === source.value),
)

const mergedRows = computed<Row[]>(() => {
  const own: Row[] = ownRows.value.map((a) => ({ src: 'own' as const, ...a }))
  // 收录条目：标签筛选在本地补齐（后端按 q 检索）
  const tagFilter = tag.value
  const track: Row[] = trackRows.value
    .filter((d) => !tagFilter || d.tags?.includes(tagFilter))
    .map((d) => ({ src: 'track' as const, starred: !!d.starred, ...d }))
  // 自建在前，收录按 星标 > 时间 排后
  track.sort((a, b) => Number(b.starred) - Number(a.starred) || (b.mtime ?? 0) - (a.mtime ?? 0))
  return [...own, ...track]
})

const sourceStats = computed(() => ({
  own: ownRows.value.length,
  track: trackRows.value.length,
  all: ownRows.value.length + trackRows.value.length,
}))

const allTags = computed(() =>
  Array.from(new Set([...ownRows.value.flatMap((r) => r.tags ?? []), ...trackRows.value.flatMap((r) => r.tags ?? [])])),
)

async function load() {
  loading.value = true
  try {
    const [ownRes, trackRes] = await Promise.all([
      assetsApi.list({
        kind: kind.value,
        q: q.value || undefined,
        status: status.value || undefined,
        tag: tag.value || undefined,
      }),
      discoveredApi.list({ kind: kind.value, q: q.value || undefined, limit: 500 }),
    ])
    ownRows.value = ownRes
    trackRows.value = trackRes.items
  } catch (e) {
    ElMessage.error(errorMessage(e))
  } finally {
    loading.value = false
  }
}

watch(() => route.params.kind, load)
onMounted(load)

// ---- 自建资产：新建 / 编辑 ---------------------------------------------
const drawer = ref(false)
const saving = ref(false)
const editing = ref<Asset | null>(null)
const form = ref({
  name: '',
  body: '',
  status: 'enabled' as AssetStatus,
  tags: [] as string[],
  metadataText: '{}',
})

function openCreate() {
  editing.value = null
  form.value = { name: '', body: '', status: 'enabled', tags: [], metadataText: '{}' }
  drawer.value = true
}

async function openEdit(row: Asset) {
  try {
    const full = await assetsApi.get(row.kind, row.id)
    editing.value = full
    form.value = {
      name: full.name,
      body: full.body,
      status: full.status,
      tags: [...(full.tags ?? [])],
      metadataText: JSON.stringify(full.metadata ?? {}, null, 2),
    }
    drawer.value = true
  } catch (e) {
    ElMessage.error(errorMessage(e))
  }
}

function parseMetadata(): Record<string, unknown> {
  const text = form.value.metadataText.trim()
  if (!text) return {}
  try {
    const parsed = JSON.parse(text)
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      throw new Error('元数据需为 JSON 对象')
    }
    return parsed as Record<string, unknown>
  } catch (e) {
    throw new Error(`元数据 JSON 无效: ${(e as Error).message}`)
  }
}

async function save() {
  if (!form.value.name.trim()) {
    ElMessage.warning('请填写名称')
    return
  }
  let metadata: Record<string, unknown>
  try {
    metadata = parseMetadata()
  } catch (e) {
    ElMessage.error((e as Error).message)
    return
  }

  saving.value = true
  try {
    const payload = {
      name: form.value.name.trim(),
      body: form.value.body,
      status: form.value.status,
      tags: form.value.tags,
      metadata,
    }
    if (editing.value) {
      await assetsApi.update(editing.value.kind, editing.value.id, payload)
      ElMessage.success('已保存')
    } else {
      await assetsApi.create({ kind: kind.value, ...payload })
      ElMessage.success('已创建')
    }
    drawer.value = false
    await load()
  } catch (e) {
    ElMessage.error(errorMessage(e))
  } finally {
    saving.value = false
  }
}

async function toggleStatus(row: Asset) {
  const next: AssetStatus = row.status === 'enabled' ? 'disabled' : 'enabled'
  try {
    await assetsApi.setStatus(row.kind, row.id, next)
    // 乐观更新：只改本地行，避免全量刷新
    const src = ownRows.value.find((a) => a.id === row.id)
    if (src) src.status = next
  } catch (e) {
    ElMessage.error(errorMessage(e))
  }
}

async function remove(row: Asset) {
  try {
    await ElMessageBox.confirm(
      `确定删除「${row.name}」？对应文件将从数据目录移除。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await assetsApi.remove(row.kind, row.id)
    ElMessage.success('已删除')
    await load()
  } catch (e) {
    ElMessage.error(errorMessage(e))
  }
}

// ---- 收录条目：星标 / 打开原位置 ---------------------------------------
function agentColor(key: string) {
  return AGENT_COLORS[Math.abs(hashStr(key)) % AGENT_COLORS.length]
}
function hashStr(s: string) {
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0
  return h
}

async function toggleTrackStar(row: Row & { src: 'track' }) {
  const next = !row.starred
  try {
    await discoveredApi.patch(row.id, { starred: next })
    // 乐观更新：只改本地行，避免全量刷新
    const src = trackRows.value.find((d) => d.id === row.id)
    if (src) src.starred = next ? 1 : 0
  } catch (e) {
    ElMessage.error(errorMessage(e))
  }
}

async function openTracked(rec: DiscoveredRecord, mode: 'file' | 'dir') {
  try {
    const res = await discoveredApi.open(rec.id, mode)
    ElMessage.success(`已打开：${res.opened}`)
  } catch (e) {
    ElMessage.error(errorMessage(e))
  }
}

function fmtSize(b: number) {
  if (b >= 1048576) return (b / 1048576).toFixed(1) + ' MB'
  if (b >= 1024) return (b / 1024).toFixed(1) + ' KB'
  return b + ' B'
}

function avatarBg(row: Row) {
  if (row.src === 'own') return 'linear-gradient(135deg, #4f8cff, #22d3ee)'
  const c = agentColor(row.agent)
  return `linear-gradient(135deg, ${c}, ${c}99)`
}

function skillDesc(row: Row): string {
  if (row.src === 'track') {
    const d = (row.extra?.description as string | undefined) || row.summary || ''
    return d || '（SKILL.md 未提供 description，点「打开」查看原文）'
  }
  return row.rel_path
}

function shortTime(t: string) {
  return (t ?? '').replace('T', ' ').slice(0, 19)
}

// ---- 本地预览（技能 Markdown / 工具记忆规范正文 / JSON 文本） ----------
const previewVisible = ref(false)
const preview = ref<{
  source: 'own' | 'track'
  id: string | number
  name: string
  kind: AssetKind
  agent?: string
  path?: string
}>({
  source: 'own',
  id: '',
  name: '',
  kind: 'skill',
})

function openPreview(row: Row) {
  preview.value = {
    source: row.src,
    id: row.id,
    name: row.name,
    kind: kind.value,
    agent: row.src === 'track' ? row.agent : undefined,
    path: row.src === 'track' ? row.path : row.rel_path,
  }
  previewVisible.value = true
}
</script>

<template>
  <div v-loading="loading" class="nx-page">
    <div class="nx-toolbar" style="flex-wrap: wrap">
      <el-input v-model="q" placeholder="搜索名称 / 正文 / 摘要 / 路径" clearable style="width: 250px" @keyup.enter="load" @clear="load">
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <!-- 来源标签筛选 -->
      <el-radio-group v-model="source">
        <el-radio-button value="">全部 {{ sourceStats.all }}</el-radio-button>
        <el-radio-button value="own">自建 {{ sourceStats.own }}</el-radio-button>
        <el-radio-button value="track">收录 {{ sourceStats.track }}</el-radio-button>
      </el-radio-group>
      <el-select v-model="status" placeholder="状态" clearable style="width: 110px" @change="load">
        <el-option v-for="s in STATUS_LIST" :key="s" :label="STATUS_META[s].label" :value="s" />
      </el-select>
      <el-select v-model="tag" placeholder="标签" clearable filterable style="width: 140px" @change="load">
        <el-option v-for="t in allTags" :key="t" :label="t" :value="t" />
      </el-select>
      <el-button @click="load">刷新</el-button>
      <span class="nx-spacer" />
      <el-button type="primary" @click="openCreate">
        <el-icon style="margin-right: 4px"><Plus /></el-icon>新建{{ meta.label }}
      </el-button>
    </div>

    <div class="nx-card">
      <!-- 技能页：卡片流（概要 + 图标 + 附件统计） -->
      <template v-if="displayRows.length && kind === 'skill'">
        <div class="skill-list">
          <div
            v-for="row in displayRows"
            :key="(row.src === 'own' ? 'own-' : 'tr-') + row.id"
            class="skill-card"
          >
            <div class="skill-avatar" :style="{ background: avatarBg(row) }">
              {{ (row.name || '?').trim().charAt(0).toUpperCase() }}
            </div>

            <div class="skill-main">
              <div class="skill-title">
                <span class="skill-name" title="点击预览" @click="openPreview(row)">{{ row.name }}</span>
                <span v-if="row.src === 'track'" class="skill-agent">
                  <span class="dot" :style="{ background: agentColor(row.agent) }" />
                  {{ row.agent }}
                </span>
                <span v-else class="skill-agent skill-agent--own">自建</span>
                <el-icon
                  v-if="row.src === 'track' && row.starred"
                  :size="14" color="var(--nx-star)"
                ><StarFilled /></el-icon>
              </div>
              <div class="skill-desc">{{ skillDesc(row) }}</div>
              <div class="skill-meta">
                <template v-if="row.src === 'track'">
                  <span v-if="row.extra?.files">{{ row.extra.files }} 个文件</span>
                  <span class="skill-meta-sep">·</span>
                  <span>{{ fmtSize(row.size) }}</span>
                  <span class="skill-meta-sep">·</span>
                </template>
                <span>更新 {{ shortTime(row.updated_at) }}</span>
                <template v-if="row.tags?.length">
                  <span class="skill-meta-sep">·</span>
                  <span v-for="t in row.tags" :key="t" class="skill-tag">{{ t }}</span>
                </template>
              </div>
            </div>

            <div class="skill-side">
              <button
                v-if="row.src === 'own'"
                class="nx-pill"
                :class="row.status === 'enabled' ? 'nx-pill--success' : row.status === 'archived' ? 'nx-pill--warning' : 'nx-pill--muted'"
                @click="toggleStatus(row)"
              >{{ STATUS_META[row.status].label }}</button>
              <span
                v-else class="nx-pill"
                :class="row.status === 'active' ? 'nx-pill--success' : 'nx-pill--danger'"
              >{{ row.status === 'active' ? '有效' : '丢失' }}</span>

              <div class="skill-actions">
                <template v-if="row.src === 'own'">
                  <button class="nx-link nx-link--primary" @click="openPreview(row)">预览</button>
                  <button class="nx-link nx-link--primary" @click="openEdit(row)">编辑</button>
                  <button class="nx-link nx-link--danger" @click="remove(row)">删除</button>
                </template>
                <template v-else>
                  <button class="nx-link nx-link--primary" @click="openPreview(row)">预览</button>
                  <button class="nx-link" @click="toggleTrackStar(row)">
                    {{ row.starred ? '去星' : '星标' }}
                  </button>
                  <button class="nx-link" @click="openTracked(row, 'dir')">目录</button>
                </template>
              </div>
            </div>
          </div>
        </div>
      </template>

      <el-table v-else-if="displayRows.length"
        :data="displayRows"
        size="small" max-height="620" style="width: 100%">
        <!-- 来源标签：自建 / 智能体收录 -->
        <el-table-column label="来源" width="120">
          <template #default="{ row }">
            <el-tag v-if="row.src === 'own'" type="primary" effect="dark" size="small">自建</el-tag>
            <template v-else>
              <el-tag :color="agentColor(row.agent)" effect="dark" size="small" style="border: none">
                <span class="dot" style="background: rgba(255,255,255,.85); margin-right: 5px" />
                {{ row.agent }}
              </el-tag>
            </template>
          </template>
        </el-table-column>

        <el-table-column label="名称 / 摘要" min-width="260">
          <template #default="{ row }">
            <div style="font-weight: 600; line-height: 1.35">
              {{ row.name }}
              <el-icon
                v-if="row.src === 'track' && row.starred"
                :size="13" color="var(--nx-star)" style="vertical-align: -2px"
              ><StarFilled /></el-icon>
            </div>
            <div
              class="nx-dim nx-mono"
              style="font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap"
              :title="row.src === 'own' ? row.rel_path : row.path"
            >
              {{ row.src === 'own' ? row.rel_path : row.path }}
            </div>
          </template>
        </el-table-column>

        <el-table-column label="状态" width="92" align="center">
          <template #default="{ row }">
            <!-- 自建：点击启停；收录：有效/丢失 -->
            <el-tag
              v-if="row.src === 'own'"
              :type="STATUS_META[row.status].type"
              size="small"
              style="cursor: pointer"
              @click="toggleStatus(row)"
            >
              {{ STATUS_META[row.status].label }}
            </el-tag>
            <el-tag v-else size="small" :type="row.status === 'active' ? 'success' : 'danger'" effect="plain">
              {{ row.status === 'active' ? '有效' : '丢失' }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="标签" min-width="150">
          <template #default="{ row }">
            <el-tag v-for="t in row.tags" :key="t" size="small" effect="plain" style="margin-right: 4px">{{ t }}</el-tag>
            <span v-if="!row.tags?.length" class="nx-dim">—</span>
          </template>
        </el-table-column>

        <el-table-column label="体积" width="82" align="right">
          <template #default="{ row }">
            <span class="nx-mono nx-dim">{{ row.src === 'track' ? fmtSize(row.size) : '—' }}</span>
          </template>
        </el-table-column>

        <el-table-column label="更新" width="165">
          <template #default="{ row }">
            <span class="nx-mono nx-dim" style="font-size: 11px">
              {{ shortTime(row.updated_at) }}
            </span>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="180" align="right" fixed="right">
          <template #default="{ row }">
            <template v-if="row.src === 'own'">
              <el-button link type="primary" size="small" @click="openPreview(row)">预览</el-button>
              <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
              <el-button link type="danger" size="small" @click="remove(row)">删除</el-button>
            </template>
            <template v-else>
              <el-button link type="primary" size="small" @click="openPreview(row)">预览</el-button>
              <el-button link size="small" @click="toggleTrackStar(row)">
                {{ row.starred ? '去星' : '星标' }}
              </el-button>
              <el-button link size="small" @click="openTracked(row, 'dir')">目录</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>

      <div v-else class="nx-empty">
        暂无{{ meta.label }} —— {{ meta.desc }}<br />
        <span class="nx-dim" style="font-size: 12px">
          可在此新建，或到「扫描中心」扫描本机，发现的{{ meta.label }}会自动收录进来（只记录路径，不复制）
        </span>
      </div>
    </div>

    <el-drawer
      v-model="drawer"
      :title="editing ? `编辑${meta.label} · ${editing.name}` : `新建${meta.label}`"
      size="52%"
    >
      <el-form label-position="top">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="唯一名称，将作为文件名" />
        </el-form-item>
        <el-form-item label="状态">
          <el-radio-group v-model="form.status">
            <el-radio-button v-for="s in STATUS_LIST" :key="s" :value="s">
              {{ STATUS_META[s].label }}
            </el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="标签">
          <el-select v-model="form.tags" multiple filterable allow-create default-first-option style="width: 100%" placeholder="回车创建标签" />
        </el-form-item>
        <el-form-item label="元数据（JSON）">
          <el-input v-model="form.metadataText" type="textarea" :rows="5" class="nx-mono" />
        </el-form-item>
        <el-form-item label="正文（Markdown）">
          <el-input v-model="form.body" type="textarea" :rows="12" placeholder="支持 Markdown，AI 可直接读取" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="drawer = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-drawer>

    <SkillPreview v-model="previewVisible" v-bind="preview" />
  </div>
</template>

<style scoped>
.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  display: inline-block;
  flex: none;
}

/* 技能卡片流 */
.skill-list {
  display: flex;
  flex-direction: column;
}
.skill-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 12px;
  margin: 0 -12px;
  border-bottom: 1px solid var(--nx-border-soft);
  transition: background 0.18s var(--nx-ease);
  /* 屏外行跳过渲染/布局，长列表滚动与首屏显著提速 */
  content-visibility: auto;
  contain-intrinsic-size: auto 96px;
}
.skill-card:last-child {
  border-bottom: none;
}
.skill-card:hover {
  background: var(--nx-bg-hover);
}
.skill-avatar {
  width: 42px;
  height: 42px;
  border-radius: 11px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 19px;
  font-weight: 700;
  color: #fff;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.25);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.25), 0 2px 8px rgba(0, 0, 0, 0.25);
}
.skill-main {
  flex: 1;
  min-width: 0;
}
.skill-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.skill-name {
  font-size: 14px;
  font-weight: 700;
  letter-spacing: -0.01em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
  transition: color 0.15s var(--nx-ease);
}
.skill-name:hover {
  color: var(--nx-accent);
}
.skill-agent {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex: none;
  height: 20px;
  padding: 0 8px;
  border-radius: 999px;
  font-size: 11px;
  color: var(--nx-text-dim);
  background: var(--nx-bg-soft);
  border: 1px solid var(--nx-border-soft);
  white-space: nowrap;
}
.skill-agent .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex: none;
}
.skill-agent--own {
  color: var(--nx-accent);
  background: var(--nx-accent-soft);
  border-color: transparent;
}
.skill-desc {
  margin-top: 4px;
  font-size: 12px;
  color: var(--nx-text-dim);
  line-height: 1.55;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-all;
}
.skill-meta {
  margin-top: 6px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px 6px;
  font-size: 11px;
  color: var(--nx-text-faint);
  font-variant-numeric: tabular-nums;
}
.skill-meta-sep {
  opacity: 0.5;
}
.skill-tag {
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--nx-bg-soft);
  border: 1px solid var(--nx-border-soft);
  color: var(--nx-text-dim);
  white-space: nowrap;
}
.skill-side {
  flex: none;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
}
.skill-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  opacity: 0.55;
  transition: opacity 0.18s var(--nx-ease);
}
.skill-card:hover .skill-actions,
.skill-card:focus-within .skill-actions {
  opacity: 1;
}
</style>
