/**
 * 审批弹窗：高危操作执行前请求用户批准/拒绝（ZCode 选项模型复刻）。
 *
 * 动态选项由后端构造（allowOnce / fullAccess / allowAlways / deny），
 * 键盘 1/2/3 直选；deny 可附反馈（≤4096 字符，拼进喂回模型的理由）；
 * allowAlways 显示将保存的规则内容（精确或前缀，高危根命令强制精确）。
 *
 * 数据源有两个（共用同一组件）：
 * - 实时流中的 approval_request 事件（useSessionStream.pendingApproval）；
 * - 刷新页面后从回放恢复的 detail.pending_approval（与连接无关，
 *   POST /approval 凭 request_id 唤醒后端等待中的 turn）。
 */

import { Warning } from '@phosphor-icons/react'
import { useEffect, useState } from 'react'

import { submitApproval } from '../api/client'
import { MarkdownWithCopy } from './MessageItem'
import type { SessionDetail } from '../types'

const isPlanApproval = (approval: PendingApproval) =>
  approval.tool === 'exit_plan_mode' &&
  typeof (approval.input as { plan?: string } | null)?.plan === 'string'

export interface ApprovalOption {
  option_id: string
  label: string
  content?: string // allowAlways：将保存的规则内容（精确或 `cmd:*` 前缀）
}

export interface PendingApproval {
  request_id: string
  tool: string
  input: unknown
  reason: string
  options: ApprovalOption[]
  full_access: boolean
}

export interface ApprovalModalProps {
  sessionId: string
  approval: PendingApproval
  /** 用户做出决定后回调（父层刷新详情，让留痕卡片翻状态） */
  onResolved: () => void
}

export function ApprovalModal({ sessionId, approval, onResolved }: ApprovalModalProps) {
  const [submitting, setSubmitting] = useState(false)
  const [feedback, setFeedback] = useState('')

  const decide = async (optionId: string) => {
    if (submitting) return
    setSubmitting(true)
    try {
      // 仅 deny 携带反馈（allow 类不传，ZCode 同语义）
      const feedbackForDeny =
        optionId === 'deny' ? feedback.trim().slice(0, 4096) || undefined : undefined
      await submitApproval(sessionId, approval.request_id, optionId, feedbackForDeny)
      onResolved()
    } finally {
      setSubmitting(false)
    }
  }

  // 键盘 1/2/3 直选选项（ZCode PermissionDialog 同交互）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (submitting) return
      const idx = Number(e.key) - 1
      if (Number.isInteger(idx) && idx >= 0 && idx < approval.options.length) {
        void decide(approval.options[idx].option_id)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  return (
    /* 遮罩层：等待期间无法做其他操作（产品决策：永久等待，可点停止） */
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-900/40 backdrop-blur-[2px]">
      <div className="animate-enter w-[30rem] rounded-2xl border border-zinc-200 bg-white p-5 shadow-xl">
        <h2 className="flex items-center gap-2 text-[15px] font-semibold text-zinc-900">
          <Warning size={16} weight="fill" className="text-amber-500" />
          需要你的批准
        </h2>
        <p className="mt-1 text-[13px] leading-relaxed text-zinc-500">
          助手请求执行
          <span className="font-mono font-medium text-zinc-800"> {approval.tool} </span>
          （{approval.reason}）
        </p>

        {/* 计划审批：渲染 markdown 计划；其余显示参数原文 */}
        {isPlanApproval(approval) ? (
          <div className="mt-3 max-h-64 overflow-auto rounded-lg border border-zinc-200 p-3">
            <MarkdownWithCopy text={String((approval.input as { plan?: string })?.plan ?? '')} />
          </div>
        ) : (
          <pre className="mt-3 max-h-32 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-zinc-50 p-2.5 text-[12px] text-zinc-600">
            {JSON.stringify(approval.input ?? {}, null, 2)}
          </pre>
        )}

        {/* 动态选项列表（role=listbox，序号即快捷键） */}
        <div role="listbox" className="mt-3 space-y-1.5">
          {approval.options.map((option, index) => (
            <button
              key={option.option_id}
              type="button"
              role="option"
              aria-selected={false}
              disabled={submitting}
              onClick={() => void decide(option.option_id)}
              data-permission-option-kind={option.option_id}
              className={`flex w-full items-center gap-2.5 rounded-lg border px-3 py-2 text-left text-sm transition disabled:opacity-50 ${
                option.option_id === 'fullAccess'
                  ? 'border-amber-300 bg-amber-50 text-amber-900 hover:border-amber-400'
                  : option.option_id === 'deny'
                    ? 'border-zinc-200 text-zinc-700 hover:bg-zinc-50'
                    : 'border-zinc-200 bg-white text-zinc-800 hover:bg-zinc-50'
              }`}
            >
              <span className="font-num w-4 shrink-0 text-[11px] text-zinc-400">{index + 1}.</span>
              <span className="min-w-0 flex-1">
                {option.label}
                {option.option_id === 'fullAccess' && (
                  <span className="ml-1.5 text-[11px] text-amber-700">
                    授予后不再确认，直到切换模式
                  </span>
                )}
                {option.content && (
                  <span className="font-num ml-1.5 text-[11px] text-zinc-400">
                    规则：{option.content}
                  </span>
                )}
              </span>
            </button>
          ))}
        </div>

        {/* deny 反馈输入（可选，拼进喂回模型的理由） */}
        <textarea
          value={feedback}
          onChange={(e) => setFeedback(e.target.value)}
          maxLength={4096}
          rows={1}
          placeholder="拒绝理由（可选，将反馈给模型）"
          className="mt-3 min-h-[36px] w-full resize-none rounded-lg border border-zinc-200 px-2.5 py-2 text-[12px] text-zinc-700 placeholder:text-zinc-400 focus:border-zinc-400 focus:outline-none"
        />
      </div>
    </div>
  )
}

/** 从回放详情中取未决审批（刷新恢复用） */
export function pendingFromDetail(detail: SessionDetail | null): PendingApproval | null {
  const p = detail?.pending_approval
  if (!p || p.status !== 'requested') return null
  return {
    request_id: p.request_id,
    tool: p.tool,
    input: p.input,
    reason: p.reason,
    options: p.options ?? [],
    full_access: p.full_access ?? false,
  }
}
