import axios from 'axios'
import type { AssetKind, AssetStatus } from '@/constants'

/** 开发态经 Vite 代理、桌面态同源，均走 /api 前缀 */
export const api = axios.create({ baseURL: '/api/v1', timeout: 20000 })

export interface Asset {
  kind: AssetKind
  id: string
  name: string
  status: AssetStatus
  tags: string[]
  metadata: Record<string, unknown>
  body: string
  version: number
  created_at: string
  updated_at: string
  rel_path: string
}

export interface Stats {
  by_kind: Record<string, Record<string, number>>
  totals: Record<string, number>
  total: number
}

export interface SystemInfo {
  app: string
  version: string
  data_home: string
  assets_root: string
  index_db: string
  asset_dirs: Record<string, string>
  total: number
}

export interface ListParams {
  kind?: AssetKind
  status?: AssetStatus | ''
  tag?: string
  q?: string
  limit?: number
  offset?: number
}

export const assetsApi = {
  list: (params: ListParams) => api.get<Asset[]>('/assets', { params }).then((r) => r.data),
  get: (kind: AssetKind, id: string) =>
    api.get<Asset>(`/assets/${kind}/${id}`).then((r) => r.data),
  create: (payload: Partial<Asset>) => api.post<Asset>('/assets', payload).then((r) => r.data),
  update: (kind: AssetKind, id: string, patch: Partial<Asset>) =>
    api.patch<Asset>(`/assets/${kind}/${id}`, patch).then((r) => r.data),
  remove: (kind: AssetKind, id: string) =>
    api.delete(`/assets/${kind}/${id}`).then((r) => r.data),
  setStatus: (kind: AssetKind, id: string, status: AssetStatus) =>
    api.post<Asset>(`/assets/${kind}/${id}/status`, { status }).then((r) => r.data),
  skillFiles: (id: string) =>
    api.get<Record<string, string>>(`/skills/${id}/files`).then((r) => r.data),
}

export const systemApi = {
  stats: () => api.get<Stats>('/stats').then((r) => r.data),
  info: () => api.get<SystemInfo>('/system/info').then((r) => r.data),
  reconcile: () => api.post<{ ok: boolean; detail?: string }>('/system/reconcile').then((r) => r.data),
  switchDataHome: (path: string) =>
    api.post<SystemInfo>('/system/data-home', { path }).then((r) => r.data),
}

export interface AgentStatus {
  key: string
  name: string
  vendor: string
  detected: boolean
  roots: string[]
}

export interface ScanJob {
  state: string
  done: boolean
  error: string | null
  agent_keys: string[]
  project_roots: string[]
  progress: Record<string, number>
  counts_by_agent: Record<string, number>
  counts_by_kind: Record<string, number>
  total: number
  indexed: number
  projects_found?: number
  projects_indexed?: number
  usage_backfilled?: number
  started: string
  finished: string
}

export interface DiscoveredRecord {
  id: number
  path: string
  agent: string
  kind: string
  name: string
  size: number
  mtime: number
  summary: string
  tags: string[]
  note: string
  starred: number
  extra: Record<string, unknown>
  status: 'active' | 'missing'
  last_seen: string
  updated_at: string
}

export interface DiscoveredStats {
  total: number
  missing: number
  starred: number
  by_agent: Record<string, number>
  by_kind: Record<string, number>
}

export interface DiscoveredList {
  items: DiscoveredRecord[]
  stats: DiscoveredStats
}

export const scanApi = {
  agents: () => api.get<AgentStatus[]>('/scan/agents').then((r) => r.data),
  run: (payload?: { agents?: string[]; project_roots?: string[] }) =>
    api.post<{ job: ScanJob }>('/scan/run', payload ?? {}).then((r) => r.data),
  status: () => api.get<ScanJob>('/scan/status').then((r) => r.data),
}

export const discoveredApi = {
  list: (params: Record<string, unknown>) =>
    api.get<DiscoveredList>('/discovered', { params }).then((r) => r.data),
  stats: () => api.get<DiscoveredStats>('/discovered/stats').then((r) => r.data),
  files: (id: number) =>
    api
      .get<{ name: string; path: string; files: Record<string, string> }>(
        `/discovered/${id}/files`,
      )
      .then((r) => r.data),
  patch: (id: number, payload: Record<string, unknown>) =>
    api.patch<DiscoveredRecord>(`/discovered/${id}`, payload).then((r) => r.data),
  validate: () => api.post<{ active: number; missing: number }>('/discovered/validate').then((r) => r.data),
  open: (id: number, mode: 'file' | 'dir') =>
    api.post<{ ok: boolean; opened: string }>('/discovered/open', { id, mode }).then((r) => r.data),
}

// ---- SkillHub 技能仓库 ----------------------------------------------------
export type HubState =
  | 'update-available'
  | 'up-to-date'
  | 'ahead'
  | 'unknown-local'
  | 'not-found'

export interface HubStatus {
  online: boolean
  total: number
  error?: string
}

export interface HubCategory {
  key: string
  name: string
  nameEn: string
}

export interface HubSkillBrief {
  slug: string
  name: string
  description: string
  description_zh: string
  version: string
  category: string
  iconUrl: string
  downloads: number
  stars: number
  source: string
  namespace: { handle: string; canonicalName: string } | null
}

export interface HubListResult {
  skills: HubSkillBrief[]
  total: number
}

export interface HubVersion {
  version: string
  changelog: string
  createdAt: number
}

export interface HubCompareItem {
  record_id: number
  name: string
  agent: string
  path: string
  skill_dir: string
  local_version: string | null
  state: HubState
  slug: string
  namespace: string
  hub_name: string
  hub_version: string
  changelog: string
  downloads: number
  publisher: string
  verified: boolean
  hub_url: string
}

export interface HubCompareSummary {
  total: number
  matched: number
  updatable: number
  up_to_date: number
  ahead: number
  unknown_local: number
  not_found: number
}

export interface HubCompareResult {
  items: HubCompareItem[]
  summary: HubCompareSummary
  elapsed: number
  created_at?: string | null
}

export interface HubUpdateResult {
  ok: boolean
  record_id: number
  slug: string
  namespace: string
  skill_dir: string
  from_version: string | null
  to_version: string
  backup_dir: string
  files: number
  file_count_expected: number
  md5: string
  md5_verified: boolean | null
}

export const skillhubApi = {
  status: () => api.get<HubStatus>('/skillhub/status').then((r) => r.data),
  categories: () => api.get<{ items: HubCategory[] }>('/skillhub/categories').then((r) => r.data.items),
  skills: (params: { q?: string; category?: string; page?: number; page_size?: number; sort_by?: string }) =>
    api.get<HubListResult>('/skillhub/skills', { params }).then((r) => r.data),
  compare: (ids?: number[]) =>
    api.post<HubCompareResult>('/skillhub/compare', { ids: ids ?? null }).then((r) => r.data),
  compareCached: () =>
    api.get<HubCompareResult>('/skillhub/compare').then((r) => r.data),
  versions: (slug: string, namespace = '') =>
    api
      .get<{ slug: string; namespace: string; versions: HubVersion[] }>('/skillhub/versions', {
        params: { slug, namespace },
      })
      .then((r) => r.data.versions),
  update: (record_id: number, version = '', backup = true) =>
    api.post<HubUpdateResult>('/skillhub/update', { record_id, version, backup }).then((r) => r.data),
}

export function errorMessage(e: unknown): string {
  if (axios.isAxiosError(e)) {
    const detail = (e.response?.data as { detail?: string } | undefined)?.detail
    return detail || e.message
  }
  return String(e)
}

// ---- 流量统计（token usage）-------------------------------------------
export interface UsageSummary {
  requests: number
  sessions: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_creation_tokens: number
  reasoning_tokens: number
  real_total_tokens: number
  cache_hit_rate: number
}

export interface UsageBucket {
  bucket: string
  requests: number
  tokens: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_creation_tokens: number
}

export interface UsageTrends {
  granularity: 'hour' | 'day'
  buckets: UsageBucket[]
}

export interface UsageStatRow {
  key: string
  requests: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_creation_tokens: number
  real_total_tokens: number
}

export interface UsageRecordRow {
  request_id: string
  agent: string
  model: string
  session_id: string | null
  ts: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_creation_tokens: number
  reasoning_tokens: number
  source_path: string | null
}

export interface UsageSourceStats {
  name: string
  agent: string
  files_scanned: number
  imported: number
  skipped: number
  errors: string[]
}

export interface UsageSyncJob {
  state: string
  done: boolean
  started: string
  finished: string
  sources: Record<string, UsageSourceStats>
  total: { files_scanned?: number; imported?: number; skipped?: number; errors?: string[] }
  error: string | null
}

export interface UsageAdapterInfo {
  source: string
  agent: string
  name: string
  detected: boolean
  roots: string[]
  records: number
}

export interface UsageAdaptersInfo {
  adapters: UsageAdapterInfo[]
  unsupported: { key: string; name: string }[]
}

export interface UsageQuery {
  days?: number
  agent?: string
  model?: string
  start_ts?: number
  end_ts?: number
}

// ---- 项目管理（AI 开发/维护过的项目）-------------------------------------
export interface ProjectRecord {
  id: number
  path: string
  name: string
  agents: string[]
  source: 'log' | 'marker' | 'manual'
  status: 'active' | 'missing' | 'excluded'
  sessions: number
  last_activity_ts: number
  first_seen: string
  last_seen: string
  note: string
  starred: number
  tags: string[]
  extra: Record<string, unknown>
  requests: number
  tokens: number
}

export interface ProjectStats {
  total: number
  missing: number
  excluded: number
  starred: number
  sessions: number
  by_agent: Record<string, number>
  attributed_requests: number
  attributed_tokens: number
}

export interface ProjectList {
  items: ProjectRecord[]
  stats: ProjectStats
}

export interface ProjectOpener {
  key: string
  label: string
  kind: 'system' | 'editor' | 'agent'
  bin: string
  available: boolean
}

export const projectsApi = {
  list: (params: Record<string, unknown>) =>
    api.get<ProjectList>('/projects', { params }).then((r) => r.data),
  add: (path: string) => api.post<ProjectRecord>('/projects', { path }).then((r) => r.data),
  patch: (id: number, payload: Record<string, unknown>) =>
    api.patch<ProjectRecord>(`/projects/${id}`, payload).then((r) => r.data),
  exclude: (id: number) => api.post<ProjectRecord>(`/projects/${id}/exclude`).then((r) => r.data),
  restore: (id: number) => api.post<ProjectRecord>(`/projects/${id}/restore`).then((r) => r.data),
  validate: () =>
    api.post<{ active: number; missing: number; excluded: number }>('/projects/validate').then((r) => r.data),
  openers: () => api.get<ProjectOpener[]>('/projects/openers').then((r) => r.data),
  open: (id: number, app = 'explorer') =>
    api.post<{ ok: boolean; opened: string; app: string }>('/projects/open', { id, app }).then((r) => r.data),
}

export const usageApi = {
  adapters: () => api.get<UsageAdaptersInfo>('/usage/adapters').then((r) => r.data),
  sync: () => api.post<{ job: UsageSyncJob }>('/usage/sync').then((r) => r.data),
  syncStatus: () => api.get<UsageSyncJob>('/usage/sync/status').then((r) => r.data),
  reset: () => api.post<{ deleted: number }>('/usage/reset').then((r) => r.data),
  summary: (params: UsageQuery) =>
    api.get<UsageSummary>('/usage/summary', { params }).then((r) => r.data),
  trends: (params: UsageQuery) =>
    api.get<UsageTrends>('/usage/trends', { params }).then((r) => r.data),
  stats: (dim: 'agent' | 'model', params: UsageQuery & { limit?: number; offset?: number }) =>
    api
      .get<{ rows: UsageStatRow[]; total: number }>('/usage/stats', { params: { ...params, dim } })
      .then((r) => r.data),
  records: (params: UsageQuery & { limit?: number; offset?: number }) =>
    api.get<{ items: UsageRecordRow[]; total: number }>('/usage/records', { params }).then((r) => r.data),
  facets: () =>
    api.get<{ agents: string[]; models: string[] }>('/usage/facets').then((r) => r.data),
}
