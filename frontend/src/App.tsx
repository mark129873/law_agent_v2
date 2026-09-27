/**
 * 应用根组件：三栏布局（左侧会话列表 / 中间对话流 / 右侧 subtask 查看面板）。
 *
 * 状态职责划分：
 * - 本组件持有"当前会话"级状态（会话列表、详情、live turn），向下分发；
 * - Sidebar 负责列表展示与新建/删除；
 * - ChatArea 负责消息渲染与输入（Composer 内嵌其中底部）；
 * - SubtaskViewer 只在点击 subtask 卡片时出现（产品决策：点击查看，非常驻）。
 */

import { useCallback, useState } from 'react'

import { ChatArea } from './components/ChatArea'
import { Sidebar } from './components/Sidebar'
import type { SessionDetail, SessionItem, TurnData } from './types'

export default function App() {
  // ---------- 会话级状态 ----------
  const [sessions, setSessions] = useState<SessionItem[]>([])
  const [currentId, setCurrentId] = useState<string | null>(null)
  const [detail, setDetail] = useState<SessionDetail | null>(null)
  const [liveTurn, setLiveTurn] = useState<TurnData | null>(null)
  const [isStreaming, setIsStreaming] = useState(false)
  // 右栏 subtask 状态（FE-5 完成卡片联动，这里先支持关闭）
  const [activeSubtask, setActiveSubtask] = useState<{ id: string; goal: string } | null>(null)

  /** 打开会话：拉取全量回放（resume 的读取路径） */
  const openSession = useCallback(async (id: string) => {
    setCurrentId(id)
    setLiveTurn(null)
    const { getSessionDetail } = await import('./api/client')
    setDetail(await getSessionDetail(id))
  }, [])

  return (
    <div className="flex h-full">
      {/* 左栏：会话列表 */}
      <Sidebar
        sessions={sessions}
        currentId={currentId}
        onSelect={openSession}
        onSessionsChange={setSessions}
        onOpened={(d) => setDetail(d)}
        onLiveTurn={(t) => setLiveTurn(t)}
        onStreaming={(s) => setIsStreaming(s)}
      />

      {/* 中栏：对话流 */}
      <ChatArea
        detail={detail}
        liveTurn={liveTurn}
        isStreaming={isStreaming}
        currentId={currentId}
      />

      {/* 右栏：subtask 查看面板（点开才出现，FE-5 实现） */}
      {activeSubtask && (
        <aside className="w-96 shrink-0 border-l border-zinc-200 bg-white p-4">
          <div className="flex items-center justify-between">
            <p className="text-sm text-zinc-500">subtask 面板（FE-5 实现）</p>
            <button
              type="button"
              onClick={() => setActiveSubtask(null)}
              className="text-xs text-zinc-400 hover:text-zinc-600"
            >
              关闭
            </button>
          </div>
          <p className="mt-2 text-sm text-zinc-700">{activeSubtask.goal}</p>
        </aside>
      )}
    </div>
  )
}
