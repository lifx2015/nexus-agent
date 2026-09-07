export type AssetKind = 'tool' | 'memory' | 'rule' | 'skill'
export type AssetStatus = 'enabled' | 'disabled' | 'archived'

export const KIND_META: Record<
  AssetKind,
  { label: string; icon: string; color: string; desc: string }
> = {
  tool: { label: '工具', icon: 'Setting', color: '#4f8cff', desc: '可调用的工具、函数与脚本定义' },
  memory: { label: '记忆', icon: 'Collection', color: '#a78bfa', desc: '长期事实、偏好与上下文' },
  rule: { label: '规范', icon: 'Document', color: '#f59e0b', desc: '行为约束、编码规范与准则' },
  skill: { label: '技能', icon: 'MagicStick', color: '#22d3ee', desc: '可复用能力包（SKILL.md）' },
}

export const STATUS_META: Record<
  AssetStatus,
  { label: string; type: 'success' | 'info' | 'warning' }
> = {
  enabled: { label: '启用', type: 'success' },
  disabled: { label: '停用', type: 'info' },
  archived: { label: '归档', type: 'warning' },
}

export const KIND_LIST = Object.keys(KIND_META) as AssetKind[]
export const STATUS_LIST = Object.keys(STATUS_META) as AssetStatus[]

/** 扫描发现的资产类型（含会话/配置等） */
export const SCAN_KIND_META: Record<string, { label: string; color: string }> = {
  rule: { label: '规范', color: '#f59e0b' },
  memory: { label: '记忆', color: '#a78bfa' },
  skill: { label: '技能', color: '#22d3ee' },
  tool: { label: '工具', color: '#4f8cff' },
  session: { label: '会话', color: '#64748b' },
  config: { label: '配置', color: '#94a3b8' },
  other: { label: '其他', color: '#475569' },
}

export const AGENT_COLORS = ['#4f8cff', '#22d3ee', '#a78bfa', '#f59e0b', '#f472b6', '#34d399', '#f87171', '#818cf8']

/** 用户成长等级：按近 30 天 tokens 计算，0 → 10B 分 10 级，10B+ 满级 */
export interface GrowthLevel {
  level: number
  name: string
  threshold: number
  color: string
}

export const GROWTH_LEVELS: GrowthLevel[] = [
  { level: 1, name: '启程', threshold: 0, color: '#8a97ad' },
  { level: 2, name: '探索', threshold: 50_000_000, color: '#4f8cff' },
  { level: 3, name: '实践', threshold: 150_000_000, color: '#38bdf8' },
  { level: 4, name: '熟练', threshold: 400_000_000, color: '#22d3ee' },
  { level: 5, name: '精通', threshold: 800_000_000, color: '#2dd4bf' },
  { level: 6, name: '专家', threshold: 1_500_000_000, color: '#34d399' },
  { level: 7, name: '大师', threshold: 3_000_000_000, color: '#a3e635' },
  { level: 8, name: '宗师', threshold: 5_000_000_000, color: '#fbbf24' },
  { level: 9, name: '传奇', threshold: 8_000_000_000, color: '#f472b6' },
  { level: 10, name: '王者', threshold: 10_000_000_000, color: '#f59e0b' },
]
