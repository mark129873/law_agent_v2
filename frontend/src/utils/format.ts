/**
 * 展示格式化工具（耗时/token 时间），mono 字体场景专用。
 */

/** 耗时格式（产品确认的 codex 阶梯）：<60s "45s"；<1h "3m 05s"；≥1h "1h 00m 00s" */
export function fmtDuration(ms: number | null | undefined): string {
  if (ms == null) return ''
  const s = Math.max(0, Math.round(ms / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  const rm = s % 60
  if (s < 3600) return `${m}m ${String(rm).padStart(2, '0')}s`
  const h = Math.floor(s / 3600)
  const rs = s % 3600
  return `${h}h ${String(Math.floor(rs / 60)).padStart(2, '0')}m ${String(rs % 60).padStart(2, '0')}s`
}

/** token 数显示：千位以上缩写（1.2k / 34.5k） */
export function fmtTokens(n: number | null | undefined): string {
  if (n == null || n <= 0) return ''
  if (n < 1000) return String(n)
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`
  return `${(n / 1_000_000).toFixed(1)}M`
}
