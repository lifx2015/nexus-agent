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
