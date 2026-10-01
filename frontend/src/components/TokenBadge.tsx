/**
 * token 用量徽标：会话头部显示上下文占用进度（mono 数字）。
 *
 * 口径（用量聚合，docs/ARCHITECTURE.md §3.7）：
 * used = 最近一轮"最后一步 input"（≈当前上下文占用），
 * window = 上下文窗口大小——与后端 compact 阈值同一来源。
 */

import { fmtTokens } from '../utils/format'

export function TokenBadge({ used, window }: { used: number | null | undefined; window: number | null | undefined }) {
  if (!window || !used) return null // 无数据（旧数据/新会话）不显示
  const percent = Math.min(100, Math.round((used / window) * 100))
  return (
    <span
      className="font-num rounded-full border border-zinc-200 bg-zinc-50 px-2 py-0.5 text-[11px] text-zinc-500"
      title={`上下文占用：${fmtTokens(used)} / ${fmtTokens(window)} tokens（${percent}%）`}
    >
      {fmtTokens(used)} / {fmtTokens(window)} · {percent}%
    </span>
  )
}
