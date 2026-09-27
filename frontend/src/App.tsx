/**
 * 应用根组件：三栏布局（左侧会话列表 / 中间对话流 / 右侧 subtask 查看面板）。
 *
 * 状态职责划分：
 * - 本组件持有"当前会话"级状态（会话列表、详情、live turn），向下分发；
 * - useSessionStream 负责 SSE 事件 → 实时 turn；
 * - turn 收口（onFinished）后重新拉详情，以落盘事实替换实时轮次。
 */

import { useCallback, useState } from 'react'

import { ChatArea } from './components/ChatArea'
import { Sidebar } from './components/Sidebar'
import { createSession, getSessionDetail, stopTurn } from './api/client'
import { useSessionStream } from './hooks/useSessionStream'
import type { SessionDetail, SessionItem } from './types'

export default function App() {
  // ---------- 会话级状态 ----------
  const [sessions, setSessions] = useState<SessionItem[]>([])
  const [currentId, setCurrentId] = useState<string | null>(null)
  const [detail, setDetail] = useState<SessionDetail | null>(null)
  // 右栏 subtask 状态（FE-5 完成卡片联动）
  const [activeSubtask, setActiveSubtask] = useState<{ id: string; goal: string } | null>(null)

  // SSE 流式状态（只在当前会话上生效）；liveTurn 即实时轮次
  const { turn: liveTurn, isStreaming, error: streamError, send, clearTurn } = useSessionStream(currentId)

  /** 打开历史会话：拉取全量回放（resume 的读取路径） */
  const openSession = useCallback(async (id: string) => {
    setCurrentId(id)
    clearTurn()
    setDetail(await getSessionDetail(id))
  }, [clearTurn])

  /** draft 会话：进入空对话态，首条消息发出后才落库 */
  const handleDraftCreated = useCallback(
    (id: string) => {
      setCurrentId(id)
      setDetail(null)
      clearTurn()
    },
    [clearTurn],
  )

  /** 新建会话（空状态页的按钮也走这里） */
  const handleNewSession = useCallback(async () => {
    const { id } = await createSession()
    handleDraftCreated(id)
  }, [handleDraftCreated])

  /** turn 收口后：重新拉详情（耗时等切换为落盘权威值）并清掉实时轮次 */
  const refreshAfterTurn = useCallback(async () => {
    if (!currentId) return
    try {
      setDetail(await getSessionDetail(currentId))
    } catch {
      /* draft 会话首条消息失败时详情仍不存在，忽略 */
    }
    clearTurn()
  }, [currentId, clearTurn])

  /** 发送消息：流结束后刷新详情与列表 */
  const handleSend = useCallback(
    (text: string) => {
      void send(text, () => {
        void refreshAfterTurn()
      })
    },
    [send, refreshAfterTurn],
  )

  /** 停止当前生成（后端在下一个检查点优雅收口） */
  const handleStop = useCallback(() => {
    if (!currentId) return
    void stopTurn(currentId)
  }, [currentId])

  return (
    <div className="flex h-full">
      {/* 左栏：会话列表 */}
      <Sidebar
        sessions={sessions}
        currentId={currentId}
        onSelect={openSession}
        onSessionsChange={setSessions}
        onDraftCreated={handleDraftCreated}
      />

      {/* 中栏：对话流 */}
      <ChatArea
        detail={detail}
        liveTurn={liveTurn}
        isStreaming={isStreaming}
        currentId={currentId}
        streamError={streamError}
        onSend={handleSend}
        onStop={handleStop}
        onNewSession={handleNewSession}
        onOpenSubtask={setActiveSubtask}
      />

      {/* 右栏：subtask 查看面板（点开才出现，FE-5 实现完整联动） */}
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
