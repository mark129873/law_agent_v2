/**
 * 对话区：头部（标题+token 徽标）+ 消息流（回放 turns + 实时 turn）+ 输入框。
 *
 * 渲染规则：
 * - detail.turns 是落盘事实（历史/刷新回看），liveTurn 是正在进行的轮次；
 * - turn 完成后由 App 重新拉详情并清掉 liveTurn（耗时切换为权威值）；
 * - 自动滚动：内容增长时贴住底部。
 */

import { useEffect, useRef } from 'react'
import { ChatTeardropText, SidebarSimple } from '@phosphor-icons/react'

import { ApprovalModal } from './ApprovalModal'
import { pendingFromDetail } from '../utils/approval'
import { TokenBadge } from './TokenBadge'
import { TurnGroup } from './TurnGroup'
import { Composer } from './Composer'
import { useShell } from '../ShellContext'
import type { LivePendingApproval } from '../hooks/useSessionStream'
import type { ExecutionDraft, PermissionState } from '../api/client'
import type { PendingApproval } from './ApprovalModal'
import type { SubtaskItem } from './SubtaskViewer'
import type { SessionDetail, TurnData } from '../types'

export interface ChatAreaProps {
  detail: SessionDetail | null
  liveTurn: TurnData | null
  isStreaming: boolean
  currentId: string | null
  streamError: string | null
  /** 当前执行状态（模式）：模式切换器初值与回显 */
  permission: PermissionState
  /** 实时流中的未决审批（useSessionStream） */
  livePendingApproval: LivePendingApproval | null
  onSend: (text: string, execution: ExecutionDraft) => void
  onStop: () => void
  /** 错误卡片"重试"→ 重新生成（保留用户消息重跑） */
  onRegenerate: () => void
  /** 审批决定提交后：父层刷新详情（留痕卡片翻状态） */
  onApprovalResolved: () => void
  onOpenSubtask?: (s: SubtaskItem) => void
}

export function ChatArea(props: ChatAreaProps) {
  const {
    detail, liveTurn, isStreaming, currentId, streamError, livePendingApproval, permission,
    onSend, onStop, onRegenerate, onApprovalResolved, onOpenSubtask,
  } = props
  const { sidebarOpen, toggleSidebar } = useShell()
  const bottomRef = useRef<HTMLDivElement>(null)

  const turns = detail?.turns ?? []
  const hasContent = turns.length > 0 || liveTurn != null

  // 弹窗数据源：实时流优先；否则用回放恢复的未决审批（刷新场景）
  const modalApproval: PendingApproval | null =
    livePendingApproval ?? pendingFromDetail(detail)

  // 自动滚动：内容变化时贴底
  useEffect(() => {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    bottomRef.current?.scrollIntoView({ behavior: reduced ? 'instant' : 'smooth', block: 'end' })
  }, [turns.length, liveTurn, liveTurn?.final_text, liveTurn?.work_items.length])

  // ---------- 会话视图（未选会话的欢迎页已由 App.tsx 的 `/` 路由承担） ----------
  return (
    <main className="flex min-w-0 flex-1 flex-col bg-white">
      {/* 头部：侧栏开关 + 标题 + token 用量 */}
      <header className="workspace-header">
        <button
          type="button"
          onClick={toggleSidebar}
          className="icon-button"
          aria-label={sidebarOpen ? '收起侧栏' : '展开侧栏'}
          aria-pressed={sidebarOpen}
          title={sidebarOpen ? '收起侧栏' : '展开侧栏'}
        >
          <SidebarSimple size={20} weight="regular" />
        </button>
        <h1 className="min-w-0 flex-1 truncate text-[14px] font-semibold text-zinc-800">
          {detail?.session.title || '新会话'}
          {liveTurn?.state === 'running' && (
            <span className="ml-2 text-xs font-normal text-accent-700">生成中…</span>
          )}
        </h1>
        <TokenBadge used={detail?.session.context_used} window={detail?.session.context_window} />
      </header>

      {/* 消息流 */}
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-8 sm:px-8">
        {hasContent ? (
          <div className="mx-auto max-w-[800px] space-y-10">
            {turns.map((t) => (
              <TurnGroup
                key={t.turn_id}
                turn={t}
                onOpenSubtask={onOpenSubtask}
                onRegenerate={onRegenerate}
              />
            ))}
            {/* 正在进行的轮次（或刚完成还未刷新详情） */}
            {liveTurn && (
              <TurnGroup turn={liveTurn} onOpenSubtask={onOpenSubtask} onRegenerate={onRegenerate} />
            )}
          </div>
        ) : (
          // draft 会话的空对话态
          <div className="flex h-full flex-col items-center justify-center px-4 text-center">
            <span className="mb-5 flex h-14 w-14 items-center justify-center rounded-2xl bg-accent-50 text-accent-700"><ChatTeardropText size={26} weight="duotone" /></span>
            <h2 className="text-[22px] font-semibold tracking-tight text-zinc-900">这次，想完成什么？</h2>
            <p className="mt-3 text-[14px] leading-7 text-zinc-600">发送第一条消息，开始这段对话。<br />可以描述目标，也可以从一个具体问题开始。</p>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* 连接错误提示（turn 仍在后端继续，可刷新回看） */}
      {streamError && (
        <div role="alert" className="mx-5 mb-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-[13px] text-amber-800">
          {streamError}
        </div>
      )}

      {/* 输入框 */}
      <Composer
        disabled={!currentId}
        streaming={isStreaming}
        permission={permission}
        onSend={onSend}
        onStop={onStop}
      />

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
