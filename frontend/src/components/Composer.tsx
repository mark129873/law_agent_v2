/**
 * 输入框：多行输入（Enter 发送 / Shift+Enter 换行）；生成中变为停止按钮。
 *
 * 视觉（design-taste 重设计）：一体化浮动输入卡——外层圆角容器承载边框与
 * focus-within ring，textarea 无边框融入其中；发送为圆形近黑按钮（ArrowUp），
 * 与聊天类工具的现代形态一致。圆角体系见 docs/ARCHITECTURE.md §7。
 */

import { ArrowUp, Square } from '@phosphor-icons/react'
import { useRef, useState } from 'react'

export interface ComposerProps {
  disabled?: boolean // 未选会话时禁用
  streaming: boolean // 生成中：显示停止按钮
  onSend: (text: string) => void
  onStop: () => void
}

export function Composer({ disabled, streaming, onSend, onStop }: ComposerProps) {
  const [text, setText] = useState('')
  const areaRef = useRef<HTMLTextAreaElement>(null)

  const trySend = () => {
    const trimmed = text.trim()
    if (!trimmed || disabled || streaming) return
    onSend(trimmed)
    setText('')
    areaRef.current?.focus()
  }

  const canSend = !disabled && !streaming && text.trim().length > 0

  return (
    <div className="bg-zinc-50 px-4 pb-4 pt-1">
      <div
        className={`flex items-end gap-2 rounded-2xl border bg-white px-3 py-2.5 transition ${
          disabled
            ? 'border-zinc-200 opacity-70'
            : 'border-zinc-200 focus-within:border-zinc-400 focus-within:shadow-[0_1px_6px_rgb(0_0_0/0.05)]'
        }`}
      >
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
          placeholder={disabled ? '新建或选择一个会话开始对话' : '输入消息，Enter 发送，Shift+Enter 换行'}
          className="max-h-40 min-h-[44px] flex-1 resize-none bg-transparent px-1 py-1 text-[15px] leading-relaxed text-zinc-800 placeholder:text-zinc-400 focus:outline-none disabled:cursor-not-allowed"
        />
        {streaming ? (
          // 停止按钮（生成中）：方形图标钮，红点提示可中断
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
      {/* 输入提示：弱化到几乎不可见，占位但不成噪 */}
      <p className="mt-1.5 text-center text-[11px] text-zinc-300">
        Enter 发送 · Shift+Enter 换行
      </p>
    </div>
  )
}
