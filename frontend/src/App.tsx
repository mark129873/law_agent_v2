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
import { SubtaskViewer, type SubtaskItem } from './components/SubtaskViewer'
import { createSession, getSessionDetail, stopTurn } from './api/client'
import { useSessionStream } from './hooks/useSessionStream'
import type { SessionDetail, SessionItem } from './types'

export default function App() {
  // ---------- 会话级状态 ----------
  const [sessions, setSessions] = useState<SessionItem[]>([])
  const [currentId, setCurrentId] = useState<string | null>(null)
  const [detail, setDetail] = useState<SessionDetail | null>(null)
  // 右栏 subtask 面板：点击卡片打开（持有同一 work item 引用，流式更新可见）
  const [activeSubtask, setActiveSubtask] = useState<SubtaskItem | null>(null)

  // SSE 流式状态（只在当前会话上生效）；liveTurn 即实时轮次
  const {
    turn: liveTurn,
    isStreaming,
    error: streamError,
    send,
    regenerate,
    clearTurn,
    pendingApproval,
  } = useSessionStream(currentId)

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

  /** 错误重试 = 重新生成最后一轮（保留用户消息重跑） */
  const handleRetry = useCallback(() => {
    void regenerate(() => {
      void refreshAfterTurn()
    })
  }, [regenerate, refreshAfterTurn])

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
        livePendingApproval={pendingApproval}
        onSend={handleSend}
        onStop={handleStop}
        onNewSession={handleNewSession}
        onRegenerate={handleRetry}
        onApprovalResolved={() => void refreshAfterTurn()}
        onOpenSubtask={setActiveSubtask}
      />

      {/* 右栏：subtask 查看面板（点击卡片才出现，实时跟随输出增长） */}
      {activeSubtask && (
        <SubtaskViewer subtask={activeSubtask} onClose={() => setActiveSubtask(null)} />
      )}
    </div>
  )
}
