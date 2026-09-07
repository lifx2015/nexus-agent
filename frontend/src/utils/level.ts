import { GROWTH_LEVELS } from '@/constants'
import type { GrowthLevel } from '@/constants'

export interface GrowthState {
  current: GrowthLevel
  next: GrowthLevel | null
  /** 距下一级的进度 0~100 */
  progressPct: number
}

export function growthOf(tokens: number): GrowthState {
  const t = Math.max(0, tokens)
  let current = GROWTH_LEVELS[0]
  for (const g of GROWTH_LEVELS) {
    if (t >= g.threshold) current = g
  }
  const next = GROWTH_LEVELS.find((g) => g.threshold > t) ?? null
  const progressPct = next
    ? Math.min(100, ((t - current.threshold) / (next.threshold - current.threshold)) * 100)
    : 100
  return { current, next, progressPct }
}
