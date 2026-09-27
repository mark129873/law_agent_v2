/**
 * token 用量徽标：会话头部显示累计用量（mono 数字）。
 */

import { fmtTokens } from '../utils/format'

export function TokenBadge({ tokens }: { tokens: number | null | undefined }) {
  const shown = fmtTokens(tokens)
  if (!shown) return null
  return (
    <span
      className="font-num rounded-full border border-zinc-200 bg-zinc-50 px-2 py-0.5 text-[11px] text-zinc-500"
      title="会话累计输出 token"
    >
      {shown} tok
    </span>
  )
}
