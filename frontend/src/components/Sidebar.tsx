/**
 * 侧栏：会话列表 + 新建/删除 + 呼吸点。
 * FE-1 为布局占位，FE-2 实现完整功能（props 已定型）。
 */

import type { SessionDetail, SessionItem, TurnData } from '../types'

export interface SidebarProps {
  sessions: SessionItem[]
  currentId: string | null
  onSelect: (id: string) => void
  onSessionsChange: (items: SessionItem[]) => void
  onOpened: (detail: SessionDetail) => void
  onLiveTurn: (turn: TurnData | null) => void
  onStreaming: (streaming: boolean) => void
}

export function Sidebar(_props: SidebarProps) {
  return (
    <aside className="w-64 shrink-0 border-r border-zinc-200 bg-white">
      <p className="p-4 text-sm text-zinc-400">会话列表（FE-2 实现）</p>
    </aside>
  )
}
