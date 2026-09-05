<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { errorMessage, skillhubApi } from '@/api/client'
import type {
  HubCategory,
  HubCompareItem,
  HubCompareResult,
  HubListResult,
  HubSkillBrief,
  HubStatus,
  HubVersion,
} from '@/api/client'
import AssetView from '@/views/AssetView.vue'

type TabName = 'mine' | 'sync' | 'browse'
const TAB_NAMES: TabName[] = ['mine', 'sync', 'browse']

const route = useRoute()
const router = useRouter()
const tab = ref<TabName>(TAB_NAMES.includes(route.query.tab as TabName) ? (route.query.tab as TabName) : 'mine')

// tab 状态同步到 query，刷新/回退后保持
watch(tab, (t) => {
  router.replace({ query: { ...route.query, tab: t === 'mine' ? undefined : t } })
})

// ---- 连接状态 -------------------------------------------------------------
const status = ref<HubStatus | null>(null)

async function loadStatus() {
  try {
    status.value = await skillhubApi.status()
  } catch {
    status.value = { online: false, total: 0 }
  }
}

// ---- 本机对比 --------------------------------------------------------------
const comparing = ref(false)
const result = ref<HubCompareResult | null>(null)
const filterState = ref('')
const filterQ = ref('')
const lastCompareAt = ref<string | null>(null)

const STATE_META: Record<string, { label: string; type: 'warning' | 'success' | 'info' | 'primary' }> = {
  'update-available': { label: '可升级', type: 'warning' },
  'up-to-date': { label: '已最新', type: 'success' },
  ahead: { label: '本地领先', type: 'info' },
  'unknown-local': { label: '无版本号', type: 'primary' },
  'not-found': { label: '未收录', type: 'info' },
}

const filteredItems = computed(() => {
  let items = result.value?.items ?? []
  if (filterState.value) items = items.filter((i) => i.state === filterState.value)
  const q = filterQ.value.trim().toLowerCase()
  if (q) {
    items = items.filter(
      (i) => i.name.toLowerCase().includes(q) || (i.slug || '').toLowerCase().includes(q),
    )
  }
  return items
})

/** 页面加载时读取持久化快照（后端不访问网络） */
async function loadCachedCompare() {
  try {
    const snap = await skillhubApi.compareCached()
    if (snap.created_at && snap.items.length) {
      result.value = snap
      lastCompareAt.value = snap.created_at
    }
  } catch {
    /* 快照读取失败不阻断，可手动对比 */
  }
}

async function runCompare() {
  if (comparing.value) return
  comparing.value = true
  try {
    result.value = await skillhubApi.compare()
    lastCompareAt.value = result.value?.created_at ?? null
    if ((result.value?.summary.total ?? 0) === 0) {
      ElMessage.info('本机尚未扫描到技能：请先到「扫描中心」执行扫描')
    }
  } catch (e) {
    ElMessage.error(errorMessage(e))
  } finally {
    comparing.value = false
  }
}

// ---- 更新 ------------------------------------------------------------------
const updateDialog = ref(false)
const updateTarget = ref<HubCompareItem | null>(null)
const updateVersion = ref('')
const updateBackup = ref(true)
const updating = ref(false)

function askUpdate(item: HubCompareItem) {
  updateTarget.value = item
  updateVersion.value = item.hub_version || ''
  updateBackup.value = true
  updateDialog.value = true
}

async function doUpdate() {
  if (!updateTarget.value || updating.value) return
  updating.value = true
  try {
    const res = await skillhubApi.update(
      updateTarget.value.record_id,
      updateVersion.value,
      updateBackup.value,
    )
    updateDialog.value = false
    ElMessage.success(
      `已更新 ${res.slug}：${res.from_version ?? '无版本'} → ${res.to_version}` +
        (res.md5_verified === true ? '（MD5 校验通过）' : ''),
    )
    if (res.backup_dir) ElMessage.info({ message: `旧版本已备份：${res.backup_dir}`, duration: 6000 })
    // 本地刷新对应行
    const it = result.value?.items.find((x) => x.record_id === res.record_id)
    if (it) {
      it.local_version = res.to_version
      it.hub_version = res.to_version
      it.state = 'up-to-date'
    }
  } catch (e) {
    ElMessage.error(errorMessage(e))
  } finally {
    updating.value = false
  }
}

// ---- 版本历史抽屉 -------------------------------------------------------------
const verDrawer = ref(false)
const verLoading = ref(false)
const versions = ref<HubVersion[]>([])
const verTitle = ref('')

async function showVersions(item: { slug: string; namespace: string; hub_name?: string; name?: string }) {
  verTitle.value = item.hub_name || item.name || item.slug
  verDrawer.value = true
  verLoading.value = true
  versions.value = []
  try {
    versions.value = await skillhubApi.versions(item.slug, item.namespace || '')
  } catch (e) {
    ElMessage.error(errorMessage(e))
    verDrawer.value = false
  } finally {
    verLoading.value = false
  }
}

function fmtDate(ms: number) {
  if (!ms) return '—'
  return new Date(ms).toLocaleDateString('zh-CN', { hour12: false })
}

// ---- 浏览仓库 ---------------------------------------------------------------
const categories = ref<HubCategory[]>([])
const browseQ = ref('')
const browseCat = ref('')
const browsePage = ref(1)
const pageSize = 20
const browseData = ref<HubListResult | null>(null)
const browseLoading = ref(false)
const browseDetail = ref<HubSkillBrief | null>(null)

async function loadCategories() {
  try {
    categories.value = (await skillhubApi.categories()) ?? []
  } catch {
    /* 分类加载失败不阻断浏览 */
  }
}

async function loadBrowse() {
  browseLoading.value = true
  try {
    browseData.value = await skillhubApi.skills({
      q: browseQ.value || undefined,
      category: browseCat.value || undefined,
      page: browsePage.value,
      page_size: pageSize,
    })
  } catch (e) {
    ElMessage.error(errorMessage(e))
  } finally {
    browseLoading.value = false
  }
}

function browseSearch() {
  browsePage.value = 1
  loadBrowse()
}

/** 浏览详情与本机对比结果的交叉引用 */
function localMatchOf(skill: HubSkillBrief): HubCompareItem | undefined {
  return result.value?.items.find(
    (i) => i.slug && (i.slug === skill.slug || i.skill_dir.endsWith('/' + skill.slug)),
  )
}

function fmtNum(n: number) {
  if (n >= 10000) return (n / 10000).toFixed(1) + ' 万'
  return String(n ?? 0)
}

onMounted(() => {
  loadStatus()
  loadCategories()
  loadCachedCompare()
})
</script>

<template>
  <div class="nx-page">
    <!-- 页头（顶栏已显示「技能」标题，此处只留描述 + 仓库状态，压缩纵向空间） -->
    <div class="nx-page-head skills-head">
      <div class="nx-page-desc" style="min-width: 0">
        管理本机全部技能（自建 + 收录）· 关联 SkillHub.cn 仓库 · 版本对比 · 一键更新（旧版本自动备份，可手动还原）
      </div>
      <span class="nx-spacer" />
      <span v-if="status" class="nx-pill" :class="status.online ? 'nx-pill--success' : 'nx-pill--danger'">
        {{ status.online ? `仓库在线 · ${fmtNum(status.total)} 个技能` : '仓库不可达' }}
      </span>
    </div>

    <el-alert
      v-if="status && !status.online"
      type="warning"
      :closable="false"
      show-icon
      style="margin-bottom: 16px"
      :title="'无法连接 SkillHub（api.skillhub.cn）——对比与浏览功能暂不可用，技能管理不受影响'"
    />

    <el-tabs v-model="tab" class="skills-tabs">
      <!-- ============ Tab 1：我的技能 ============ -->
      <el-tab-pane label="我的技能" name="mine">
        <AssetView kind="skill" />
      </el-tab-pane>

      <!-- ============ Tab 2：对比更新 ============ -->
      <el-tab-pane label="对比更新" name="sync">
        <div class="nx-toolbar" style="margin-bottom: 14px">
          <el-button type="primary" :loading="comparing" @click="runCompare">
            <el-icon style="margin-right: 4px"><Refresh /></el-icon>
            {{ comparing ? '对比中…' : result ? '重新对比' : '对比本机技能' }}
          </el-button>
          <template v-if="result">
            <el-select v-model="filterState" placeholder="全部状态" clearable style="width: 130px">
              <el-option v-for="(m, k) in STATE_META" :key="k" :label="m.label" :value="k" />
            </el-select>
            <el-input
              v-model="filterQ"
              placeholder="搜索名称 / slug"
              clearable
              style="width: 200px"
            />
            <span class="nx-dim" style="font-size: 12px">
              <template v-if="lastCompareAt">对比于 {{ lastCompareAt }} · </template>耗时
              {{ result.elapsed }}s · 展示 {{ filteredItems.length }}/{{ result.summary.total }} 项
            </span>
          </template>
        </div>

        <!-- 汇总卡片 -->
        <div v-if="result" class="nx-stat-grid" style="margin-bottom: 16px">
          <div class="nx-stat" style="--stat-color: #4f8cff">
            <div class="nx-stat-icon"><el-icon :size="19"><Connection /></el-icon></div>
            <div class="nx-stat-body">
              <div class="nx-stat-label">已关联仓库</div>
              <div class="nx-stat-value">{{ result.summary.matched }}</div>
              <div class="nx-stat-sub">共 {{ result.summary.total }} 个本机技能</div>
            </div>
          </div>
          <div class="nx-stat" style="--stat-color: #f59e0b">
            <div class="nx-stat-icon"><el-icon :size="19"><UploadFilled /></el-icon></div>
            <div class="nx-stat-body">
              <div class="nx-stat-label">可升级</div>
              <div class="nx-stat-value">{{ result.summary.updatable }}</div>
              <div class="nx-stat-sub">仓库有更新版本</div>
            </div>
          </div>
          <div class="nx-stat" style="--stat-color: #34d399">
            <div class="nx-stat-icon"><el-icon :size="19"><CircleCheck /></el-icon></div>
            <div class="nx-stat-body">
              <div class="nx-stat-label">已最新 / 领先</div>
              <div class="nx-stat-value">{{ result.summary.up_to_date + result.summary.ahead }}</div>
              <div class="nx-stat-sub">无需更新</div>
            </div>
          </div>
          <div class="nx-stat" style="--stat-color: #94a3b8">
            <div class="nx-stat-icon"><el-icon :size="19"><QuestionFilled /></el-icon></div>
            <div class="nx-stat-body">
              <div class="nx-stat-label">未收录 / 无版本号</div>
              <div class="nx-stat-value">{{ result.summary.not_found + result.summary.unknown_local }}</div>
              <div class="nx-stat-sub">自定义技能或缺失版本信息</div>
            </div>
          </div>
        </div>

        <!-- 对比结果表 -->
        <div class="nx-card">
          <el-table
            v-if="result && filteredItems.length"
            :data="filteredItems"
            size="small"
            max-height="540"
            style="width: 100%"
          >
            <el-table-column label="技能" min-width="220">
              <template #default="{ row }">
                <div style="font-weight: 600; line-height: 1.3; display: flex; align-items: center; gap: 6px">
                  {{ row.name }}
                  <el-tag v-if="row.verified" size="small" type="success" effect="plain">认证</el-tag>
                </div>
                <div class="nx-dim" style="font-size: 12px">
                  {{ row.agent }}<template v-if="row.hub_name"> · {{ row.hub_name }}</template>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="本地版本" width="100" align="center">
              <template #default="{ row }">
                <span class="nx-mono" :class="{ 'nx-dim': !row.local_version }">
                  {{ row.local_version || '—' }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="仓库版本" width="100" align="center">
              <template #default="{ row }">
                <span class="nx-mono" :class="{ 'nx-dim': !row.hub_version }">
                  {{ row.hub_version || '—' }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="130" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="STATE_META[row.state]?.type ?? 'info'" effect="light">
                  {{ STATE_META[row.state]?.label ?? row.state }}
                </el-tag>
                <div
                  v-if="row.state === 'update-available'"
                  class="nx-mono nx-dim"
                  style="font-size: 11px; margin-top: 2px"
                >
                  {{ row.local_version }} → {{ row.hub_version }}
                </div>
              </template>
            </el-table-column>
            <el-table-column label="下载量" width="90" align="right">
              <template #default="{ row }">
                <span class="nx-mono nx-dim">{{ row.downloads ? fmtNum(row.downloads) : '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="200" align="right" fixed="right">
              <template #default="{ row }">
                <el-button
                  v-if="row.state === 'update-available' || row.state === 'unknown-local'"
                  link
                  type="primary"
                  size="small"
                  @click="askUpdate(row)"
                >
                  {{ row.state === 'unknown-local' ? '安装最新' : '更新' }}
                </el-button>
                <el-button
                  v-if="row.slug"
                  link
                  size="small"
                  @click="showVersions({ slug: row.slug, namespace: row.namespace, hub_name: row.hub_name })"
                >
                  版本历史
                </el-button>
                <el-link
                  v-if="row.hub_url"
                  :href="row.hub_url"
                  target="_blank"
                  type="primary"
                  style="font-size: 12px; margin-left: 8px"
                >
                  仓库页
                </el-link>
              </template>
            </el-table-column>
          </el-table>
          <div v-else-if="result" class="nx-empty">没有符合筛选条件的技能。</div>
          <div v-else class="nx-empty">
            尚未对比。<br />点击「对比本机技能」，将本机发现的技能包与 SkillHub 仓库逐一比对版本。
          </div>
        </div>
      </el-tab-pane>

      <!-- ============ Tab 3：浏览仓库 ============ -->
      <el-tab-pane label="浏览仓库" name="browse">
        <div class="nx-toolbar" style="margin-bottom: 14px">
          <el-input
            v-model="browseQ"
            placeholder="搜索仓库技能（回车搜索）"
            clearable
            style="width: 260px"
            @keyup.enter="browseSearch"
            @clear="browseSearch"
          >
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
          <el-select v-model="browseCat" placeholder="全部分类" clearable style="width: 160px" @change="browseSearch">
            <el-option v-for="c in categories" :key="c.key" :label="c.name" :value="c.key" />
          </el-select>
          <el-button @click="browseSearch">搜索</el-button>
          <span class="nx-spacer" />
          <el-pagination
            v-if="browseData && browseData.total > pageSize"
            layout="prev, pager, next"
            :total="Math.min(browseData.total, 10000)"
            :page-size="pageSize"
            :current-page="browsePage"
            @current-change="(p: number) => { browsePage = p; loadBrowse() }"
          />
        </div>

        <div class="nx-card" v-loading="browseLoading">
          <el-table v-if="browseData && browseData.skills.length" :data="browseData.skills" size="small" max-height="560">
            <el-table-column label="技能" min-width="260">
              <template #default="{ row }">
                <div style="display: flex; align-items: center; gap: 10px; min-width: 0">
                  <el-avatar v-if="row.iconUrl" :src="row.iconUrl" :size="28" shape="square" />
                  <div style="min-width: 0">
                    <div style="font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis">
                      {{ row.namespace?.canonicalName || row.name }}
                    </div>
                    <div
                      class="nx-dim"
                      style="font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis"
                    >
                      {{ row.description_zh || row.description }}
                    </div>
                  </div>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="版本" width="90" align="center">
              <template #default="{ row }"><span class="nx-mono">{{ row.version || '—' }}</span></template>
            </el-table-column>
            <el-table-column label="下载" width="90" align="right">
              <template #default="{ row }"><span class="nx-mono nx-dim">{{ fmtNum(row.downloads) }}</span></template>
            </el-table-column>
            <el-table-column label="来源" width="100" align="center">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :type="row.source === 'enterprise' ? 'success' : 'info'">
                  {{ row.source === 'enterprise' ? '企业' : row.source === 'clawhub' ? '同步' : '社区' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="140" align="right">
              <template #default="{ row }">
                <el-button link size="small" type="primary" @click="browseDetail = row">详情</el-button>
                <el-button
                  link
                  size="small"
                  @click="showVersions({ slug: row.slug, namespace: row.namespace?.handle || '', hub_name: row.name })"
                >
                  历史
                </el-button>
              </template>
            </el-table-column>
          </el-table>
          <div v-else class="nx-empty">输入关键词搜索 SkillHub 仓库（{{ fmtNum(status?.total ?? 0) }} 个技能）。</div>
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- 更新确认对话框 -->
    <el-dialog v-model="updateDialog" title="更新技能" width="580px">
      <template v-if="updateTarget">
        <div style="margin-bottom: 12px">
          <div style="font-weight: 600; font-size: 15px">{{ updateTarget.hub_name || updateTarget.name }}</div>
          <div class="nx-mono nx-dim" style="font-size: 12px">
            {{ updateTarget.slug }} · {{ updateTarget.skill_dir }}
          </div>
        </div>
        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 12px">
          <span class="nx-mono nx-dim">v{{ updateTarget.local_version || '无版本' }}</span>
          <el-icon><Right /></el-icon>
          <el-input v-model="updateVersion" style="width: 130px" class="nx-mono" />
        </div>
        <div v-if="updateTarget.changelog" class="hub-changelog">{{ updateTarget.changelog }}</div>
        <el-checkbox v-model="updateBackup" style="margin-top: 12px">
          更新前备份旧版本（数据目录 skillhub/backups，每技能保留 3 份）
        </el-checkbox>
        <el-alert
          type="warning"
          :closable="false"
          show-icon
          style="margin-top: 10px"
          title="更新会覆盖技能目录内容，智能体可能需要重启后生效。"
        />
      </template>
      <template #footer>
        <el-button @click="updateDialog = false">取消</el-button>
        <el-button type="primary" :loading="updating" @click="doUpdate">
          {{ updating ? '更新中…' : '确认更新' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 版本历史抽屉 -->
    <el-drawer v-model="verDrawer" :title="`版本历史 · ${verTitle}`" size="480px">
      <div v-loading="verLoading">
        <el-timeline v-if="versions.length" style="padding-left: 4px">
          <el-timeline-item
            v-for="v in versions"
            :key="v.version"
            :timestamp="fmtDate(v.createdAt)"
            placement="top"
          >
            <div class="nx-mono" style="font-weight: 600">v{{ v.version }}</div>
            <div
              v-if="v.changelog"
              class="nx-dim"
              style="font-size: 12px; white-space: pre-wrap; margin-top: 4px; word-break: break-word"
            >
              {{ v.changelog }}
            </div>
          </el-timeline-item>
        </el-timeline>
        <div v-else-if="!verLoading" class="nx-empty">无版本记录。</div>
      </div>
    </el-drawer>

    <!-- 仓库技能详情对话框 -->
    <el-dialog v-model="browseDetail" width="560px" :title="browseDetail?.name">
      <template v-if="browseDetail">
        <div style="display: flex; gap: 14px; align-items: flex-start; margin-bottom: 14px">
          <el-avatar v-if="browseDetail.iconUrl" :src="browseDetail.iconUrl" :size="56" shape="square" />
          <div style="min-width: 0">
            <div class="nx-mono nx-dim" style="font-size: 12px">{{ browseDetail.namespace?.canonicalName || browseDetail.slug }}</div>
            <div style="margin-top: 4px">
              <el-tag size="small" class="nx-mono" effect="plain">v{{ browseDetail.version || '—' }}</el-tag>
              <span class="nx-dim" style="font-size: 12px; margin-left: 10px">
                下载 {{ fmtNum(browseDetail.downloads) }} · 星 {{ browseDetail.stars ?? 0 }}
              </span>
            </div>
          </div>
        </div>
        <p style="margin: 0 0 14px; line-height: 1.7; font-size: 13px">
          {{ browseDetail.description_zh || browseDetail.description || '（无描述）' }}
        </p>
        <div v-if="localMatchOf(browseDetail)" class="hub-local">
          <el-icon><Monitor /></el-icon>
          本机已安装
          <span class="nx-mono">v{{ localMatchOf(browseDetail)!.local_version || '?' }}</span>
          <template v-if="localMatchOf(browseDetail)!.state === 'update-available'">
            <el-button link type="primary" size="small" @click="askUpdate(localMatchOf(browseDetail)!)">
              更新到 v{{ localMatchOf(browseDetail)!.hub_version }}
            </el-button>
          </template>
          <el-tag v-else size="small" :type="STATE_META[localMatchOf(browseDetail)!.state]?.type ?? 'info'">
            {{ STATE_META[localMatchOf(browseDetail)!.state]?.label }}
          </el-tag>
        </div>
      </template>
      <template #footer>
        <el-link
          v-if="browseDetail"
          :href="`https://skillhub.cn/#/skills/${browseDetail.namespace?.handle ? browseDetail.namespace.handle + '/' : ''}${browseDetail.slug}`"
          target="_blank"
          type="primary"
        >
          在 SkillHub 打开
        </el-link>
        <el-button style="margin-left: 14px" @click="browseDetail = null">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* 压缩页头与 tab 的纵向间距，让列表内容上移 */
.skills-head {
  margin-bottom: 8px;
}
.skills-tabs :deep(.el-tabs__header) {
  margin-bottom: 10px;
}
.skills-tabs :deep(.el-tabs__item) {
  height: 36px;
}
/* 统计卡片字号整体缩小一档 */
.skills-tabs :deep(.nx-stat-label) {
  font-size: 12px;
}
.skills-tabs :deep(.nx-stat-value) {
  font-size: 24px;
}
.skills-tabs :deep(.nx-stat-sub) {
  margin-top: 4px;
  font-size: 11px;
}
.skills-tabs :deep(.nx-stat-icon) {
  width: 36px;
  height: 36px;
  border-radius: 10px;
}
/* 嵌入的资产页不再叠加页面进入动画（tab 内已有自己的节奏） */
.skills-tabs :deep(.nx-page > *) {
  animation: none;
}
.hub-changelog {
  background: var(--nx-bg-soft, #f6f8fa);
  border: 1px solid var(--nx-border-soft, #e5e7eb);
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 180px;
  overflow: auto;
}
.hub-local {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--nx-accent-soft, #eef4ff);
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 13px;
}
</style>
