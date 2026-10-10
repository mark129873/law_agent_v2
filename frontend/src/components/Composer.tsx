/**
 * 输入卡：先写目标，再选择执行方式。权限选择只改变本地草稿，随发送生效。
 * Enter 发送、Shift+Enter 换行；中文输入法正在组字时，Enter 不能误发。
 */
import { ArrowUp, CaretDown, Check, Hand, ShieldCheck, ShieldWarning, Square } from '@phosphor-icons/react'
import { useEffect, useRef, useState } from 'react'
import type { ExecutionDraft, PermissionState } from '../api/client'

export interface ComposerProps {
  disabled?: boolean
  streaming: boolean
  permission: PermissionState
  onSend: (text: string, execution: ExecutionDraft) => void
  onStop: () => void
}
const MODE_META: Record<string, { label: string; icon: typeof Hand; hint: string }> = {
  build: { label: '变更前确认', icon: Hand, hint: '有副作用的操作会先征求同意' },
  edit: { label: '自动编辑', icon: ShieldCheck, hint: '文件编辑自动执行，命令仍需确认' },
  yolo: { label: '完全访问', icon: ShieldWarning, hint: '允许的操作直接执行，禁止规则仍生效' },
}

export function Composer({ disabled, streaming, permission, onSend, onStop }: ComposerProps) {
  const [text, setText] = useState('')
  const [draftMode, setDraftMode] = useState(permission.mode)
  const [previousMode, setPreviousMode] = useState(permission.mode)
  const [menuOpen, setMenuOpen] = useState(false)
  const areaRef = useRef<HTMLTextAreaElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const modeButtonRef = useRef<HTMLButtonElement>(null)

  // 仅服务端模式变化时重置草稿；渲染提交前同步，避免 effect 触发额外更新。
  // 正文单独保存，审批回显不会清空用户正在输入的内容。
  if (previousMode !== permission.mode) {
    setPreviousMode(permission.mode)
    setDraftMode(permission.mode)
  }
  useEffect(() => {
    if (!menuOpen) return
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [menuOpen])
  // 高度随正文增长，到 200px 后内部滚动，保证发送按钮一直留在视野中。
  useEffect(() => {
    const area = areaRef.current
    if (!area) return
    area.style.height = 'auto'
    area.style.height = `${Math.min(200, Math.max(70, area.scrollHeight))}px`
  }, [text])

  const trySend = () => {
    const trimmed = text.trim()
    if (!trimmed || disabled || streaming) return
    onSend(trimmed, { mode: draftMode })
    setText('')
    areaRef.current?.focus()
  }
  const meta = MODE_META[draftMode] ?? MODE_META.build
  const ModeIcon = meta.icon
  const canSend = !disabled && !streaming && text.trim().length > 0

  return (
    <div className="composer-shell">
      <div className={`composer-card ${disabled ? 'opacity-60' : ''}`}>
        <textarea ref={areaRef} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); trySend() }
        }} rows={2} disabled={disabled} aria-label="消息" placeholder={disabled ? '新建或选择会话开始对话' : '描述你的目标，或继续这段对话…'} className="composer-input block" />
        <div className="mt-3 flex items-center justify-between gap-3">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <div ref={menuRef} className="relative" onKeyDown={(e) => {
              if (e.key === 'Escape') { setMenuOpen(false); modeButtonRef.current?.focus() }
            }}>
              <button ref={modeButtonRef} type="button" disabled={disabled} onClick={() => setMenuOpen(!menuOpen)} aria-expanded={menuOpen} aria-controls="execution-menu" title={meta.hint} className={`flex h-8 items-center gap-1.5 rounded-lg bg-zinc-100/80 px-2.5 text-[12px] font-medium transition hover:bg-zinc-200/70 ${draftMode === 'yolo' ? 'text-amber-800' : 'text-zinc-600'}`}>
                <ModeIcon size={15} />{meta.label}<CaretDown size={12} />
              </button>
              {menuOpen && <div id="execution-menu" role="group" aria-label="执行模式" className="floating-menu absolute bottom-full left-0 z-20 mb-3 w-72 max-w-[calc(100vw-48px)] rounded-xl bg-white p-2">
                <p className="px-3 pb-2 pt-1 text-[11px] font-medium text-zinc-500">随下一条消息生效</p>
                {Object.entries(MODE_META).map(([mode, item]) => {
                  const Icon = item.icon
                  return <button key={mode} type="button" aria-pressed={draftMode === mode} onClick={() => { setDraftMode(mode); setMenuOpen(false); modeButtonRef.current?.focus() }} className={`flex w-full items-center gap-3 rounded-lg p-3 text-left hover:bg-zinc-50 ${draftMode === mode ? 'bg-accent-50' : ''}`}>
                    <Icon size={18} className={`shrink-0 ${mode === 'yolo' ? 'text-amber-700' : 'text-zinc-600'}`} />
                    <span className={`flex-1 text-[13px] font-medium ${mode === 'yolo' ? 'text-amber-800' : 'text-zinc-800'}`}>{item.label}<span className="mt-1 block text-[11px] font-normal leading-5 text-zinc-600">{item.hint}</span></span>
                    {draftMode === mode && <Check size={16} weight="bold" className="text-accent-700" />}
                  </button>
                })}
              </div>}
            </div>
          </div>
          {streaming ? (
            <button type="button" onClick={onStop} title="停止生成" aria-label="停止生成" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-zinc-300 text-zinc-700 transition hover:border-red-300 hover:bg-red-50 hover:text-red-700"><Square size={14} weight="fill" /></button>
          ) : (
            <button type="button" onClick={trySend} disabled={!canSend} title="发送" aria-label="发送" className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl transition ${canSend ? 'bg-accent-300 text-zinc-900 hover:bg-accent-400' : 'bg-zinc-100 text-zinc-500'}`}><ArrowUp size={19} weight="bold" /></button>
          )}
        </div>
      </div>
      <p className="mt-3 text-center text-[11px] text-zinc-500">Enter 发送 · Shift+Enter 换行</p>
    </div>
  )
}
