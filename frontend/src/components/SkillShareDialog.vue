<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { errorMessage, shareApi } from '@/api/client'
import type { ShareDeployResult, ShareStatus } from '@/api/client'

const props = defineProps<{
  /** own = 自建资产；track = 扫描发现项 */
  src: 'own' | 'track'
  id: number | string
  skillName: string
}>()

const visible = defineModel<boolean>({ default: false })

const loading = ref(false)
const status = ref<ShareStatus | null>(null)
const overwrite = ref(false)
const selected = ref<string[]>([])
const deploying = ref(false)
const busyTarget = ref('')

const targets = computed(() => status.value?.targets ?? [])
const installableCount = computed(
  () => targets.value.filter((t) => !t.installed && !t.is_source).length,
)

async function load() {
  loading.value = true
  try {
    status.value = await shareApi.status(props.src, props.id)
    selected.value = []
  } catch (e) {
    ElMessage.error(errorMessage(e))
    visible.value = false
  } finally {
    loading.value = false
  }
}

watch(visible, (v) => {
  if (v) load()
})

function toggleSelect(key: string, installed: boolean, isSource: boolean) {
  if (installed || isSource) return
  const i = selected.value.indexOf(key)
  if (i >= 0) selected.value.splice(i, 1)
  else selected.value.push(key)
}

async function deploySelected() {
  if (!selected.value.length || deploying.value) return
  deploying.value = true
  try {
    const results = await shareApi.deploy(props.src, props.id, selected.value, overwrite.value)
    report(results)
    await load()
  } catch (e) {
    ElMessage.error(errorMessage(e))
  } finally {
    deploying.value = false
  }
}

async function deployOne(key: string) {
  if (busyTarget.value) return
  busyTarget.value = key
  try {
    const results = await shareApi.deploy(props.src, props.id, [key], true)
    report(results)
    await load()
  } catch (e) {
    ElMessage.error(errorMessage(e))
  } finally {
    busyTarget.value = ''
  }
}

async function removeOne(key: string) {
  if (busyTarget.value) return
  busyTarget.value = key
  try {
    const r = await shareApi.remove(props.src, props.id, key)
    ElMessage.success(`已从目标卸载（备份：${r.backup_dir}）`)
    await load()
  } catch (e) {
    ElMessage.error(errorMessage(e))
  } finally {
    busyTarget.value = ''
  }
}

function report(results: ShareDeployResult[]) {
  const ok = results.filter((r) => r.ok && !r.skipped)
  const fail = results.filter((r) => !r.ok)
  if (ok.length) ElMessage.success(`已安装到 ${ok.length} 个智能体${ok.some((r) => r.backup_dir) ? '（同名旧版已备份）' : ''}`)
  for (const f of fail) ElMessage.error(f.error || '安装失败')
}
</script>

<template>
  <el-dialog v-model="visible" title="跨智能体复用" width="640px">
    <div v-loading="loading">
      <div style="margin-bottom: 4px; font-weight: 600">{{ skillName }}</div>
      <div class="nx-mono nx-dim" style="font-size: 12px; margin-bottom: 12px; word-break: break-all">
        {{ status?.source_dir }}
      </div>

      <div class="nx-toolbar" style="margin-bottom: 10px">
        <span class="nx-dim" style="font-size: 12px">
          勾选要安装的智能体（复制技能包到其 skills 目录）
        </span>
        <span class="nx-spacer" />
        <el-checkbox v-model="overwrite">目标同名时覆盖（先备份）</el-checkbox>
      </div>

      <div class="share-targets">
        <div
          v-for="t in targets"
          :key="t.key"
          class="share-target"
          :class="{
            'share-target--disabled': t.is_source || (!t.exists && !t.installed),
            'share-target--selected': selected.includes(t.key),
          }"
          @click="toggleSelect(t.key, t.installed, t.is_source)"
        >
          <el-checkbox
            :model-value="selected.includes(t.key)"
            :disabled="t.installed || t.is_source"
            @click.stop
            @change="toggleSelect(t.key, t.installed, t.is_source)"
          />
          <div class="share-target-main">
            <div class="share-target-name">
              {{ t.name }}
              <el-tag v-if="t.is_source" size="small" effect="plain" type="info">来源</el-tag>
              <el-tag v-else-if="t.installed" size="small" effect="plain" type="success">已安装</el-tag>
              <el-tag v-else-if="!t.exists" size="small" effect="plain" type="warning">未检测到该智能体</el-tag>
            </div>
            <div class="nx-mono nx-dim share-target-dir">{{ t.dir }}</div>
          </div>
          <div class="share-target-actions" @click.stop>
            <el-button
              v-if="t.installed && !t.is_source"
              link
              type="danger"
              size="small"
              :loading="busyTarget === t.key"
              @click="removeOne(t.key)"
            >
              卸载
            </el-button>
            <el-button
              v-else-if="t.installed"
              link
              size="small"
              disabled
            >
              来源
            </el-button>
            <el-button
              v-else
              link
              type="primary"
              size="small"
              :loading="busyTarget === t.key"
              @click="deployOne(t.key)"
            >
              安装
            </el-button>
          </div>
        </div>
      </div>
    </div>
    <template #footer>
      <el-button @click="visible = false">关闭</el-button>
      <el-button
        type="primary"
        :disabled="!selected.length"
        :loading="deploying"
        @click="deploySelected"
      >
        安装到选中项{{ selected.length ? `（${selected.length}）` : '' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.share-targets {
  max-height: 380px;
  overflow: auto;
  border: 1px solid var(--nx-border-soft, #e5e7eb);
  border-radius: 10px;
}
.share-target {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  cursor: pointer;
  border-bottom: 1px solid var(--nx-border-soft, #f0f0f0);
}
.share-target:last-child {
  border-bottom: none;
}
.share-target--disabled {
  opacity: 0.55;
  cursor: default;
}
.share-target--selected {
  background: var(--nx-accent-soft, #eef4ff);
}
.share-target-main {
  min-width: 0;
  flex: 1;
}
.share-target-name {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 13px;
}
.share-target-dir {
  font-size: 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.share-target-actions {
  flex-shrink: 0;
}
</style>
