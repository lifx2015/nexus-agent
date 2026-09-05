/** 数字与时间格式化工具（流量统计页共用） */

/** 紧凑数字：1234 → 1.2k，3456789 → 3.5M */
export function fmtCompact(n: number): string {
  if (!Number.isFinite(n)) return '0'
  const abs = Math.abs(n)
  if (abs >= 1e9) return trimZero((n / 1e9).toFixed(2)) + 'B'
  if (abs >= 1e6) return trimZero((n / 1e6).toFixed(2)) + 'M'
  if (abs >= 1e3) return trimZero((n / 1e3).toFixed(1)) + 'k'
  return String(Math.round(n))
}

function trimZero(s: string): string {
  return s.replace(/\.0+$/, '').replace(/(\.\d*[1-9])0+$/, '$1')
}

/** 千分位整数 */
export function fmtInt(n: number): string {
  return Math.round(n).toLocaleString('en-US')
}

/** unix 秒 → 本地日期时间 */
export function fmtTs(ts: number): string {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  const pad = (x: number) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/** 比率 0~1 → 百分比字符串 */
export function fmtPct(ratio: number): string {
  return `${(ratio * 100).toFixed(1)}%`
}
