/**
 * 审批由后端提供选项，前端只展示并提交决定。
 * 弹窗保留焦点，数字快捷键仅在未输入文字时有效，避免反馈中的数字误触授权。
 */
import { ArrowRight, ShieldCheck, Warning } from '@phosphor-icons/react'
import { useEffect, useRef, useState } from 'react'
import { submitApproval } from '../api/client'

export interface ApprovalOption { option_id: string; label: string; content?: string }
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
  onResolved: () => void
}

export function ApprovalModal({ sessionId, approval, onResolved }: ApprovalModalProps) {
  const [submitting, setSubmitting] = useState(false)
  const [feedback, setFeedback] = useState('')
  const [error, setError] = useState<string | null>(null)
  const dialogRef = useRef<HTMLDivElement>(null)
  // ref 在点击时立即上锁，连续数字键或双击也不会重复提交。
  const submittingRef = useRef(false)
  const decide = async (optionId: string) => {
    if (submittingRef.current) return
    submittingRef.current = true
    setSubmitting(true)
    setError(null)
    try {
      await submitApproval(sessionId, approval.request_id, optionId, optionId === 'deny' ? feedback.trim().slice(0, 4096) || undefined : undefined)
      onResolved()
    } catch (e) { setError(e instanceof Error ? e.message : '提交失败，请重试') }
    finally { submittingRef.current = false; setSubmitting(false) }
  }
  // 首次聚焦弹窗本身，不默认聚焦授权按钮；关闭后返回原先输入位置。
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    dialogRef.current?.focus()
    return () => { if (previous?.isConnected) previous.focus() }
  }, [approval.request_id])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const dialog = dialogRef.current
      if (!dialog) return
      if (e.key === 'Tab') {
        const controls = Array.from(dialog.querySelectorAll<HTMLElement>('button:not(:disabled), textarea:not(:disabled)'))
        const first = controls[0]
        const last = controls[controls.length - 1]
        if (!first) { e.preventDefault(); dialog.focus(); return }
        if (e.shiftKey && (document.activeElement === first || document.activeElement === dialog)) { e.preventDefault(); last.focus() }
        else if (!e.shiftKey && (document.activeElement === last || document.activeElement === dialog)) { e.preventDefault(); first.focus() }
        return
      }
      const target = e.target as HTMLElement
      if (submittingRef.current || e.repeat || e.isComposing || e.ctrlKey || e.metaKey || e.altKey || target.closest('input, textarea, [contenteditable="true"]')) return
      const index = Number(e.key) - 1
      if (Number.isInteger(index) && index >= 0 && index < approval.options.length) {
        e.preventDefault()
        void decide(approval.options[index].option_id)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-900/30 p-4 backdrop-blur-[3px]">
      <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="approval-title" aria-describedby="approval-description" tabIndex={-1} className="approval-dialog animate-enter max-h-[90dvh] w-full max-w-[520px] overflow-y-auto rounded-[20px] border border-zinc-200 bg-white p-6 shadow-[0_24px_80px_#30261826]">
        <div className="mb-5 flex items-center gap-3">
          <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-amber-50 text-amber-700"><ShieldCheck size={25} weight="duotone" /></span>
          <div><h2 id="approval-title" className="text-[18px] font-semibold text-zinc-900">需要你的批准</h2><p className="mt-1 text-[12px] text-zinc-600">查看操作后，选择本次执行方式</p></div>
        </div>
        <p id="approval-description" className="text-[13px] leading-6 text-zinc-600">助手请求执行 <span className="font-mono font-medium text-zinc-800">{approval.tool}</span>：{approval.reason}</p>
        <pre className="mt-4 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-xl border border-zinc-200/70 bg-zinc-50 p-4 text-[12px] leading-6 text-zinc-700">{JSON.stringify(approval.input ?? {}, null, 2)}</pre>
        <div role="group" aria-label="审批选项" className="mt-5 space-y-2">
          {approval.options.map((option, index) => <button key={option.option_id} type="button" disabled={submitting} onClick={() => void decide(option.option_id)} data-permission-option-kind={option.option_id} className={`flex w-full items-center gap-3 rounded-xl border px-3.5 py-3 text-left transition disabled:opacity-50 ${option.option_id === 'fullAccess' ? 'border-amber-200 bg-amber-50/70 text-amber-900 hover:border-amber-400' : option.option_id === 'allowOnce' ? 'border-accent-200 bg-accent-50/50 text-accent-800 hover:border-accent-600' : 'border-zinc-200 text-zinc-700 hover:bg-zinc-50'}`}>
            <span className="font-num flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-current/15 text-[11px]">{index + 1}</span>
            <span className="min-w-0 flex-1 text-[13px] font-medium">{option.label}
              {option.option_id === 'fullAccess' && <span className="mt-1 flex items-start gap-1.5 text-[11px] font-normal leading-5 text-amber-800"><Warning size={13} className="mt-0.5 shrink-0" />允许的操作直接执行，直到切换模式</span>}
              {option.content && <span className="mt-1 block break-all font-mono text-[11px] font-normal leading-5 text-zinc-600">规则：{option.content}</span>}
            </span><ArrowRight size={15} className="shrink-0" />
          </button>)}
        </div>
        <label className="mt-5 block text-[12px] font-medium text-zinc-600" htmlFor="approval-feedback">拒绝理由 <span className="font-normal">（可选）</span></label>
        <textarea id="approval-feedback" value={feedback} onChange={(e) => setFeedback(e.target.value)} maxLength={4096} rows={2} placeholder="说明需要调整的地方，将反馈给模型" className="mt-2 w-full resize-none rounded-xl border border-zinc-300 p-3 text-[13px] leading-6 text-zinc-700 placeholder:text-zinc-500 focus:border-accent-600" />
        {error && <p role="alert" className="mt-3 text-[13px] text-red-700">{error}</p>}
        <p role="status" className="mt-3 text-[11px] text-zinc-500">{submitting ? '正在提交决定…' : '未在输入文字时，可按数字键选择对应选项'}</p>
      </div>
    </div>
  )
}
