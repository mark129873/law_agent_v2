/**
 * 审批弹窗：高危操作执行前请求用户批准/拒绝。
 *
 * 数据源有两个（共用同一组件）：
 * - 实时流中的 approval_request 事件（useSessionStream.pendingApproval）；
 * - 刷新页面后从回放恢复的 detail.pending_approval（与连接无关，
 *   POST /approval 凭 request_id 唤醒后端等待中的 turn）。
 */

import { useState } from 'react'

import { submitApproval } from '../api/client'
import type { SessionDetail } from '../types'

export interface PendingApproval {
  request_id: string
  tool: string
  input: unknown
  reason: string
}

export interface ApprovalModalProps {
  sessionId: string
  approval: PendingApproval
  /** 用户做出决定后回调（父层刷新详情，让留痕卡片翻状态） */
  onResolved: () => void
}

export function ApprovalModal({ sessionId, approval, onResolved }: ApprovalModalProps) {
  const [submitting, setSubmitting] = useState(false)

  const decide = async (approved: boolean) => {
    if (submitting) return
    setSubmitting(true)
    try {
      await submitApproval(sessionId, approval.request_id, approved)
      onResolved()
    } finally {
      setSubmitting(false)
    }
  }

  return (
    /* 遮罩层：等待期间无法做其他操作（产品决策：永久等待，可点停止） */
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-900/40">
      <div className="w-[28rem] rounded-2xl border border-zinc-200 bg-white p-5 shadow-xl">
        <h2 className="text-[15px] font-semibold text-zinc-900">需要你的批准</h2>
        <p className="mt-1 text-[13px] leading-relaxed text-zinc-500">
          助手请求执行一个高危操作，请确认是否允许。
        </p>

        {/* 工具名与风险原因 */}
        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5">
          <p className="text-[13px] text-amber-900">
            工具：<span className="font-mono font-medium">{approval.tool}</span>
          </p>
          <p className="mt-0.5 text-[13px] text-amber-800">原因：{approval.reason}</p>
        </div>

        {/* 参数原文 */}
        <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-zinc-50 p-2.5 text-[12px] text-zinc-600">
          {JSON.stringify(approval.input ?? {}, null, 2)}
        </pre>

        {/* 决定按钮 */}
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            disabled={submitting}
            onClick={() => decide(false)}
            className="rounded-lg border border-zinc-300 px-4 py-2 text-sm text-zinc-700 transition hover:bg-zinc-50 disabled:opacity-50"
          >
            拒绝
          </button>
          <button
            type="button"
            disabled={submitting}
            onClick={() => decide(true)}
            className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white transition hover:bg-zinc-700 disabled:opacity-50"
          >
            批准
          </button>
        </div>
      </div>
    </div>
  )
}

/** 从回放详情中取未决审批（刷新恢复用） */
export function pendingFromDetail(detail: SessionDetail | null): PendingApproval | null {
  const p = detail?.pending_approval
  if (!p || p.status !== 'requested') return null
  return { request_id: p.request_id, tool: p.tool, input: p.input, reason: p.reason }
}
