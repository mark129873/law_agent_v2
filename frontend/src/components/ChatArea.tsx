/**
 * 对话区：消息流 + 输入框。
 * FE-1 为布局占位，FE-3/FE-4 实现完整功能（props 已定型）。
 */

import type { SessionDetail, TurnData } from '../types'

export interface ChatAreaProps {
  detail: SessionDetail | null
  liveTurn: TurnData | null
  isStreaming: boolean
  currentId: string | null
}

export function ChatArea(_props: ChatAreaProps) {
  return (
    <main className="flex min-w-0 flex-1 flex-col">
      <p className="p-4 text-sm text-zinc-400">对话流（FE-3 实现）</p>
    </main>
  )
}
