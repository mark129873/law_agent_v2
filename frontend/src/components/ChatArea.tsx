/**
 * 对话区：头部（标题+token 徽标）+ 消息流（回放 turns + 实时 turn）+ 输入框。
 *
 * 渲染规则：
 * - detail.turns 是落盘事实（历史/刷新回看），liveTurn 是正在进行的轮次；
 * - turn 完成后由 App 重新拉详情并清掉 liveTurn（耗时切换为权威值）；
 * - 自动滚动：内容增长时贴住底部。
 */

import { useEffect, useRef } from 'react'

import { ApprovalModal, pendingFromDetail } from './ApprovalModal'
import { TokenBadge } from './TokenBadge'
import { TurnGroup } from './TurnGroup'
import { Composer } from './Composer'
import type { PendingApproval } from './ApprovalModal'
import type { SessionDetail, TurnData } from '../types'

export interface ChatAreaProps {
  detail: SessionDetail | null
  liveTurn: TurnData | null
  isStreaming: boolean
  currentId: string | null
  streamError: string | null
  /** 实时流中的未决审批（useSessionStream） */
  livePendingApproval: { request_id: string; tool: string; input: unknown; reason: string } | null
  onSend: (text: string) => void
  onStop: () => void
  onNewSession: () => void
  /** 错误卡片"重试"→ 重新生成（保留用户消息重跑） */
  onRegenerate: () => void
  /** 审批决定提交后：父层刷新详情（留痕卡片翻状态） */
  onApprovalResolved: () => void
  onOpenSubtask?: (s: { id: string; goal: string }) => void
}

export function ChatArea(props: ChatAreaProps) {
  const {
    detail, liveTurn, isStreaming, currentId, streamError, livePendingApproval,
    onSend, onStop, onNewSession, onRegenerate, onApprovalResolved, onOpenSubtask,
  } = props
  const bottomRef = useRef<HTMLDivElement>(null)

  const turns = detail?.turns ?? []
  const hasContent = turns.length > 0 || liveTurn != null

  // 弹窗数据源：实时流优先；否则用回放恢复的未决审批（刷新场景）
  const modalApproval: PendingApproval | null =
    livePendingApproval ?? pendingFromDetail(detail)

  // 自动滚动：内容变化时贴底
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns.length, liveTurn, liveTurn?.final_text, liveTurn?.work_items.length])

  // ---------- 空状态：未选会话 ----------
  if (!currentId) {
    return (
      <main className="flex min-w-0 flex-1 flex-col items-center justify-center gap-3 bg-zinc-50">
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-800">个人助手</h1>
        <p className="max-w-[28rem] text-center text-sm leading-relaxed text-zinc-500">
          能读文件、跑命令、管任务、派子助手的本地工作伙伴。左侧新建一个会话，开始对话。
        </p>
        <button
          type="button"
          onClick={onNewSession}
          className="rounded-xl bg-zinc-900 px-5 py-2.5 text-sm font-medium text-white transition hover:bg-zinc-700 active:translate-y-px"
        >
          新建会话
        </button>
      </main>
    )
  }

  return (
    <main className="flex min-w-0 flex-1 flex-col bg-zinc-50">
      {/* 头部：标题 + token 用量 */}
      <header className="flex items-center gap-3 border-b border-zinc-200 bg-white px-5 py-3">
        <h1 className="min-w-0 flex-1 truncate text-sm font-medium text-zinc-800">
          {detail?.session.title || '新会话'}
          {liveTurn?.state === 'running' && (
            <span className="ml-2 text-xs font-normal text-sky-600">生成中…</span>
          )}
        </h1>
        <TokenBadge tokens={detail?.session.tokens_used} />
      </header>

      {/* 消息流 */}
      <div className="flex-1 overflow-y-auto px-6 py-5">
        {hasContent ? (
          <div className="mx-auto max-w-3xl space-y-6">
            {turns.map((t) => (
              <TurnGroup
                key={t.turn_id}
                turn={t}
                onOpenSubtask={onOpenSubtask}
                onRetry={onRegenerate}
              />
            ))}
            {/* 正在进行的轮次（或刚完成还未刷新详情） */}
            {liveTurn && (
              <TurnGroup turn={liveTurn} onOpenSubtask={onOpenSubtask} onRetry={onRegenerate} />
            )}
          </div>
        ) : (
          // draft 会话的空对话态
          <div className="flex h-full items-center justify-center">
            <p className="text-sm text-zinc-400">发送第一条消息，开始这段对话</p>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* 连接错误提示（turn 仍在后端继续，可刷新回看） */}
      {streamError && (
        <div className="border-t border-amber-200 bg-amber-50 px-5 py-2 text-[12px] text-amber-700">
          {streamError}
        </div>
      )}

      {/* 输入框 */}
      <Composer disabled={!currentId} streaming={isStreaming} onSend={onSend} onStop={onStop} />

      {/* 审批弹窗：实时请求或刷新恢复的未决审批 */}
      {modalApproval && currentId && (
        <ApprovalModal
          sessionId={currentId}
          approval={modalApproval}
          onResolved={onApprovalResolved}
        />
      )}
    </main>
  )
}
