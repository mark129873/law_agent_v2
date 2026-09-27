/**
 * 输入框：多行输入（Enter 发送 / Shift+Enter 换行）；生成中变为停止按钮。
 */

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

  return (
    <div className="border-t border-zinc-200 bg-white p-3">
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
          placeholder={disabled ? '新建或选择一个会话开始对话' : '输入消息，Enter 发送，Shift+Enter 换行'}
          className="min-h-[52px] flex-1 resize-none rounded-xl border border-zinc-200 px-3.5 py-2.5 text-[15px] text-zinc-800 placeholder:text-zinc-400 focus:border-zinc-400 focus:outline-none disabled:bg-zinc-50"
        />
        {streaming ? (
          // 停止按钮（生成中）
          <button
            type="button"
            onClick={onStop}
            className="h-[52px] shrink-0 rounded-xl border border-zinc-300 px-4 text-sm font-medium text-zinc-700 transition hover:bg-zinc-50 active:translate-y-px"
          >
            停止
          </button>
        ) : (
          <button
            type="button"
            onClick={trySend}
            disabled={disabled || !text.trim()}
            className="h-[52px] shrink-0 rounded-xl bg-zinc-900 px-5 text-sm font-medium text-white transition hover:bg-zinc-700 active:translate-y-px disabled:opacity-40"
          >
            发送
          </button>
        )}
      </div>
    </div>
  )
}
