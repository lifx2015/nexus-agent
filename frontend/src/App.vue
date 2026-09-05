<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { KIND_LIST, KIND_META } from '@/constants'

const route = useRoute()

const title = computed(() => {
  const p = route.path
  if (p.startsWith('/dashboard')) return '概览'
  if (p.startsWith('/scan')) return '扫描中心'
  if (p.startsWith('/skills')) return '技能'
  if (p.startsWith('/projects')) return 'AI 项目管理'
  if (p.startsWith('/usage')) return '流量统计'
  if (p.startsWith('/settings')) return '设置'
  if (p.startsWith('/assets/')) {
    const k = route.params.kind as keyof typeof KIND_META
    return KIND_META[k]?.label ?? '资产'
  }
  return 'Nexus Agent'
})

const activeMenu = computed(() => {
  const p = route.path
  if (p.startsWith('/assets/')) return p
  if (p.startsWith('/scan')) return '/scan'
  if (p.startsWith('/skills')) return '/skills'
  if (p.startsWith('/projects')) return '/projects'
  if (p.startsWith('/usage')) return '/usage'
  if (p.startsWith('/settings')) return '/settings'
  return '/dashboard'
})
</script>

<template>
  <el-container class="nx-shell">
    <el-aside width="216px" class="nx-aside">
      <div class="nx-brand">
        <span class="nx-brand-mark">AI</span>
        <span>AI 项目管理</span>
      </div>

      <div class="nx-menu">
        <el-menu :default-active="activeMenu" router background-color="transparent">
          <div class="nx-menu-label">工作台</div>
          <el-menu-item index="/dashboard">
            <el-icon><DataAnalysis /></el-icon>
            <span>概览</span>
          </el-menu-item>

          <div class="nx-menu-label">资产</div>
          <el-menu-item v-for="k in KIND_LIST.filter((k) => k !== 'skill')" :key="k" :index="`/assets/${k}`">
            <el-icon><component :is="KIND_META[k].icon" /></el-icon>
            <span>{{ KIND_META[k].label }}</span>
          </el-menu-item>
          <el-menu-item index="/skills">
            <el-icon><MagicStick /></el-icon>
            <span>技能</span>
          </el-menu-item>
          <el-menu-item index="/projects">
            <el-icon><Folder /></el-icon>
            <span>AI 项目管理</span>
          </el-menu-item>

          <div class="nx-menu-label">发现</div>
          <el-menu-item index="/scan">
            <el-icon><Aim /></el-icon>
            <span>扫描中心</span>
          </el-menu-item>

          <div class="nx-menu-label">数据统计</div>
          <el-menu-item index="/usage">
            <el-icon><TrendCharts /></el-icon>
            <span>流量统计</span>
          </el-menu-item>

          <div class="nx-menu-label">系统</div>
          <el-menu-item index="/settings">
            <el-icon><Tools /></el-icon>
            <span>设置</span>
          </el-menu-item>
        </el-menu>
      </div>

      <div class="nx-aside-footer">
        <span class="nx-dot nx-dot--green" />
        本地数据 · 离线运行
      </div>
    </el-aside>

    <el-container>
      <el-header class="nx-header">
        <div class="nx-header-title">{{ title }}</div>
        <div class="nx-header-chip">
          <span class="nx-dot nx-dot--green" />
          数据不出本机
        </div>
      </el-header>
      <el-main class="nx-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>
