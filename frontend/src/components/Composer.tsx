/**
 * 输入框：多行输入（Enter 发送 / Shift+Enter 换行）；生成中变为停止按钮。
 *
 * 模式切换器（ZCode ComposerModeControls 同形态）：
 * - plan 是独立勾选项（叠加态），build/edit/yolo 是单选；
 * - 草稿制：选择只改本地草稿，随下一次发送生效（POST /turn 携带）；
 * - yolo 选中时按钮呈警示色；plan 启用时按钮旁出现可移除的 plan chip。
 *
 * 视觉（design-taste 重设计）：一体化浮动输入卡 + 圆形发送钮。
 */

import {
  ArrowUp,
  CaretDown,
  Hand,
  Notepad,
  ShieldWarning,
  ShieldCheck,
  Square,
  X,
} from '@phosphor-icons/react'
import { useEffect, useRef, useState } from 'react'

import type { ExecutionDraft, PermissionState } from '../api/client'

export interface ComposerProps {
  disabled?: boolean // 未选会话时禁用
  streaming: boolean // 生成中：显示停止按钮
  /** 当前执行状态（服务端权威值，草稿初值来源） */
  permission: PermissionState
  onSend: (text: string, execution: ExecutionDraft) => void
  onStop: () => void
}

/** 模式显示元数据（ZCode 同款文案） */
const MODE_META: Record<string, { label: string; icon: typeof Hand; hint: string }> = {
  build: { label: '变更前确认', icon: Hand, hint: '有副作用的操作会先征求同意' },
  edit: { label: '自动编辑', icon: ShieldCheck, hint: '文件编辑自动执行，命令仍需确认' },
  yolo: { label: '完全访问', icon: ShieldWarning, hint: '所有操作直接执行，不再确认' },
}

export function Composer({ disabled, streaming, permission, onSend, onStop }: ComposerProps) {
  const [text, setText] = useState('')
  const areaRef = useRef<HTMLTextAreaElement>(null)
  // 草稿制：模式/计划选择只改本地草稿，随发送携带
  const [draftMode, setDraftMode] = useState(permission.mode)
  const [draftPlan, setDraftPlan] = useState(permission.plan_enabled)
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  // 服务端权威值变化（如完全访问授权）时同步草稿
  useEffect(() => {
    setDraftMode(permission.mode)
    setDraftPlan(permission.plan_enabled)
  }, [permission.mode, permission.plan_enabled])

  // 点击菜单外关闭
  useEffect(() => {
    if (!menuOpen) return
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [menuOpen])

  const trySend = () => {
    const trimmed = text.trim()
    if (!trimmed || disabled || streaming) return
    onSend(trimmed, { mode: draftMode, plan_enabled: draftPlan })
    setText('')
    areaRef.current?.focus()
  }

  const canSend = !disabled && !streaming && text.trim().length > 0
  const meta = MODE_META[draftMode] ?? MODE_META.build
  const ModeIcon = meta.icon

  return (
    <div className="bg-zinc-50 px-4 pb-4 pt-1">
      <div
        className={`rounded-2xl border bg-white px-3 py-2.5 transition ${
          disabled
            ? 'border-zinc-200 opacity-70'
            : 'border-zinc-200 focus-within:border-zinc-400 focus-within:shadow-[0_1px_6px_rgb(0_0_0/0.05)]'
        }`}
      >
        {/* 模式切换器 + plan chip（草稿态，随发送生效） */}
        <div className="mb-1.5 flex items-center gap-2">
          <div className="relative" ref={menuRef}>
            <button
              type="button"
              disabled={disabled}
              onClick={() => setMenuOpen((o) => !o)}
              className={`flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-[11px] font-medium transition hover:bg-zinc-100 disabled:opacity-50 ${
                draftMode === 'yolo' ? 'text-amber-700' : 'text-zinc-600'
              }`}
              title={meta.hint}
            >
              <ModeIcon size={13} weight="regular" />
              {meta.label}
              <CaretDown size={10} />
            </button>
            {menuOpen && (
              <div className="absolute bottom-full left-0 z-20 mb-1.5 w-64 rounded-xl border border-zinc-200 bg-white p-1.5 shadow-lg">
                {/* plan：独立勾选项（叠加态） */}
                <button
                  type="button"
                  onClick={() => setDraftPlan((p) => !p)}
                  className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] text-zinc-700 transition hover:bg-zinc-50"
                >
                  <Notepad size={14} className="text-zinc-500" />
                  <span className="flex-1">
                    计划模式
                    <span className="block text-[11px] text-zinc-400">
                      先只读探索，提交计划获批后再实现
                    </span>
                  </span>
                  {draftPlan && (
                    <span className="font-num text-[11px] text-sky-600">✓</span>
                  )}
                </button>
                <div className="my-1 h-px bg-zinc-100" />
                {/* build/edit/yolo：单选 */}
                {(Object.keys(MODE_META) as Array<keyof typeof MODE_META>).map((m) => {
                  const item = MODE_META[m]
                  const Icon = item.icon
                  const active = draftMode === m
                  return (
                    <button
                      key={m}
                      type="button"
                      onClick={() => {
                        setDraftMode(m)
                        setMenuOpen(false)
                      }}
                      className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] transition hover:bg-zinc-50 ${
                        active ? 'bg-zinc-100 text-zinc-900' : 'text-zinc-700'
                      } ${m === 'yolo' ? 'text-amber-700' : ''}`}
                    >
                      <Icon size={14} className={m === 'yolo' ? 'text-amber-600' : 'text-zinc-500'} />
                      <span className="flex-1">
                        {item.label}
                        <span className="block text-[11px] text-zinc-400">{item.hint}</span>
                      </span>
                      {active && <span className="font-num text-[11px] text-sky-600">✓</span>}
                    </button>
                  )
                })}
              </div>
            )}
          </div>
          {/* plan chip：可移除 */}
          {draftPlan && (
            <button
              type="button"
              onClick={() => setDraftPlan(false)}
              className="flex items-center gap-1 rounded-full bg-sky-50 px-2 py-0.5 text-[11px] text-sky-700 transition hover:bg-sky-100"
              title="点击取消计划模式"
            >
              <Notepad size={11} />
              计划模式
              <X size={10} weight="bold" />
            </button>
          )}
        </div>

        <div className="flex items-end gap-2">
          <textarea
            ref={areaRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              // Enter 发送；Shift+Enter 换行（产品决策）
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                trySend()
              }
            }}
            rows={2}
            disabled={disabled}
            placeholder={
              disabled ? '新建或选择一个会话开始对话' : draftPlan ? '计划模式：描述要探索/规划的目标' : '输入消息，Enter 发送，Shift+Enter 换行'
            }
            className="max-h-40 min-h-[44px] flex-1 resize-none bg-transparent px-1 py-1 text-[15px] leading-relaxed text-zinc-800 placeholder:text-zinc-400 focus:outline-none disabled:cursor-not-allowed"
          />
          {streaming ? (
            <button
              type="button"
              onClick={onStop}
              title="停止生成"
              aria-label="停止生成"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-zinc-300 text-zinc-600 transition hover:border-red-300 hover:bg-red-50 hover:text-red-600 active:scale-[0.96] motion-reduce:active:scale-100"
            >
              <Square size={11} weight="fill" />
            </button>
          ) : (
            <button
              type="button"
              onClick={trySend}
              disabled={!canSend}
              title="发送"
              aria-label="发送"
              className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition active:scale-[0.96] motion-reduce:active:scale-100 ${
                canSend
                  ? 'bg-zinc-900 text-white hover:bg-zinc-700'
                  : 'cursor-not-allowed bg-zinc-100 text-zinc-400'
              }`}
            >
              <ArrowUp size={16} weight="bold" />
            </button>
          )}
        </div>
      </div>
      <p className="mt-1.5 text-center text-[11px] text-zinc-300">
        Enter 发送 · Shift+Enter 换行
      </p>
    </div>
  )
}
