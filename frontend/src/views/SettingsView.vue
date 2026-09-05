<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { errorMessage, systemApi } from '@/api/client'
import type { SystemInfo } from '@/api/client'

const info = ref<SystemInfo | null>(null)
const newPath = ref('')
const busy = ref(false)

async function load() {
  try {
    info.value = await systemApi.info()
    newPath.value = info.value.data_home
  } catch (e) {
    ElMessage.error(errorMessage(e))
  }
}

async function reconcile() {
  busy.value = true
  try {
    const res = await systemApi.reconcile()
    ElMessage.success(res.detail ?? '已重建索引')
    await load()
  } catch (e) {
    ElMessage.error(errorMessage(e))
  } finally {
    busy.value = false
  }
}

async function switchHome() {
  if (!newPath.value.trim()) return
  busy.value = true
  try {
    info.value = await systemApi.switchDataHome(newPath.value.trim())
    ElMessage.success('数据目录已切换')
  } catch (e) {
    ElMessage.error(errorMessage(e))
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div v-loading="!info" class="nx-page">
    <div class="nx-card" style="margin-bottom: 16px">
      <div class="nx-card-head"><span class="nx-card-title">系统信息</span></div>
      <el-descriptions :column="1" border>
        <el-descriptions-item v-if="info" label="应用">{{ info.app }} v{{ info.version }}</el-descriptions-item>
        <el-descriptions-item v-if="info" label="数据目录">
          <span class="nx-mono">{{ info.data_home }}</span>
        </el-descriptions-item>
        <el-descriptions-item v-if="info" label="资产根目录">
          <span class="nx-mono">{{ info.assets_root }}</span>
        </el-descriptions-item>
        <el-descriptions-item v-if="info" label="索引库">
          <span class="nx-mono">{{ info.index_db }}</span>
        </el-descriptions-item>
        <el-descriptions-item v-if="info" label="资产总数">{{ info.total }}</el-descriptions-item>
      </el-descriptions>
    </div>

    <div class="nx-card" style="margin-bottom: 16px">
      <div class="nx-card-head"><span class="nx-card-title">切换数据目录</span></div>
      <div class="nx-dim" style="margin: 0 0 12px; font-size: 12px; line-height: 1.7">
        切换后立即重建索引；目录不存在时会自动创建四类资产子目录。
      </div>
      <div style="display: flex; gap: 10px">
        <el-input v-model="newPath" placeholder="例如 C:/Users/you/.nexus-agent" class="nx-mono" />
        <el-button type="primary" :loading="busy" @click="switchHome">切换</el-button>
      </div>
    </div>

    <div class="nx-card">
      <div class="nx-card-head"><span class="nx-card-title">索引维护</span></div>
      <div class="nx-dim" style="margin: 0 0 12px; font-size: 12px; line-height: 1.7">
        资产文件可用任意编辑器直接修改；修改后点此按钮，索引会按文件内容重建。
      </div>
      <el-button :loading="busy" @click="reconcile">按文件重建索引</el-button>
    </div>
  </div>
</template>
