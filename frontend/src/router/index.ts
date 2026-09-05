import { createRouter, createWebHashHistory } from 'vue-router'

const router = createRouter({
  // 桌面壳为静态托管，hash 模式可避免刷新 404
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/dashboard' },
    {
      path: '/dashboard',
      name: 'dashboard',
      component: () => import('@/views/DashboardView.vue'),
    },
    {
      path: '/skills',
      name: 'skills',
      component: () => import('@/views/SkillsView.vue'),
    },
    // 技能已在「技能」中心页（tab 组织），旧入口重定向兼容
    { path: '/assets/skill', redirect: '/skills' },
    { path: '/skillhub', redirect: '/skills' },
    {
      path: '/assets/:kind',
      name: 'assets',
      component: () => import('@/views/AssetView.vue'),
    },
    {
      path: '/scan',
      name: 'scan',
      component: () => import('@/views/ScanView.vue'),
    },
    {
      path: '/projects',
      name: 'projects',
      component: () => import('@/views/ProjectsView.vue'),
    },
    {
      path: '/usage',
      name: 'usage',
      component: () => import('@/views/UsageView.vue'),
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('@/views/SettingsView.vue'),
    },
  ],
})

export default router
