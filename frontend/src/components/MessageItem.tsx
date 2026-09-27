/**
 * 消息渲染：Markdown + 代码高亮 + 悬停复制。
 * 用于：工作块外的最终回复、中间过程文本、用户气泡（纯文本变体）。
 */

import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'

import type { TodoItem, WorkItem } from '../types'

// 状态词映射（产品文档 §3.3）
export const TOOL_STATUS_WORD: Record<string, string> = {
  pending: '等待中',
  running: '执行中',
  completed: '已执行',
  failed: '执行失败',
  denied: '已拒绝',
  stopped: '已停止',
}

/** 工具输入摘要：一行内的参数预览 */
export function inputSummary(input: unknown): string {
  if (input == null) return ''
  if (typeof input === 'string') return input
  try {
    return JSON.stringify(input, ensureAsciiNone)
  } catch {
    return String(input)
  }
}

function ensureAsciiNone(_k: string, v: unknown) {
  return v
}

/** 最终回复 / 中间文本：Markdown 渲染 + 悬停复制按钮 */
export function MarkdownWithCopy({ text, streaming = false }: { text: string; streaming?: boolean }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    } catch {
      /* 剪贴板不可用时静默 */
    }
  }

  return (
    <div className="group relative">
      {/* 正文：markdown 渲染；生成中尾部加光标 */}
      <div className={`text-[15px] leading-relaxed ${streaming ? 'stream-cursor' : ''}`}>
        <ReactMarkdown rehypePlugins={[rehypeHighlight]}>{text}</ReactMarkdown>
      </div>
      {/* 复制按钮：悬停出现 */}
      <button
        type="button"
        onClick={handleCopy}
        className="absolute -top-1 right-0 hidden rounded border border-zinc-200 bg-white px-1.5 py-0.5 text-[11px] text-zinc-500 transition hover:text-zinc-800 group-hover:block"
        aria-label="复制"
      >
        {copied ? '已复制' : '复制'}
      </button>
    </div>
  )
}

/** 用户消息气泡：右对齐浅灰底纯文本 */
export function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-zinc-100 px-4 py-2.5 text-[15px] leading-relaxed text-zinc-800">
        {text}
      </div>
    </div>
  )
}

/** 任务板卡片：[ ] 待办 / [>] 进行中 / [x] 已完成（实时覆盖刷新） */
export function TodoCard({ items }: { items: TodoItem[] }) {
  const mark = { pending: '[ ]', in_progress: '[>]', completed: '[x]' } as const
  const color = {
    pending: 'text-zinc-400',
    in_progress: 'text-sky-600',
    completed: 'text-emerald-600',
  } as const
  return (
    <div className="rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2">
      <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-zinc-400">任务板</p>
      <ul className="space-y-1">
        {items.map((it, i) => (
          <li key={i} className="font-num flex gap-2 text-[13px]">
            <span className={color[it.status]}>{mark[it.status]}</span>
            <span className={it.status === 'completed' ? 'text-zinc-400 line-through' : 'text-zinc-700'}>
              {it.content}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** 错误卡片：红色内联 + 重试按钮（onRetry 由上层接 /regenerate，FE-4 接线） */
export function ErrorCard({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2">
      <p className="text-[13px] text-red-700">出错了：{message}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-1.5 rounded border border-red-300 px-2 py-0.5 text-xs text-red-700 transition hover:bg-red-100"
        >
          重试
        </button>
      )}
    </div>
  )
}

/** 工具调用卡片：二级折叠（默认收起），头部=名称+状态词，展开=参数与输出 */
export function ToolCallCard({
  item,
}: {
  item: Extract<WorkItem, { kind: 'tool_call' }>
}) {
  const [open, setOpen] = useState(false)
  const running = item.status === 'running' || item.status === 'pending'
  const statusWord = TOOL_STATUS_WORD[item.status] ?? item.status

  return (
    <div className="rounded-lg border border-zinc-200 bg-white">
      {/* 头部：名称 + 输入摘要 + 状态词；点击展开/收起 */}
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
        aria-expanded={open}
      >
        <span className={`font-mono text-[13px] font-medium ${running ? 'text-sky-600' : 'text-zinc-700'}`}>
          {item.name}
        </span>
        <span className="min-w-0 flex-1 truncate text-[12px] text-zinc-400">
          {inputSummary(item.input)}
        </span>
        <span
          className={`shrink-0 text-[11px] ${
            item.status === 'failed'
              ? 'text-red-600'
              : item.status === 'denied'
                ? 'text-amber-600'
                : running
                  ? 'text-sky-600'
                  : 'text-emerald-600'
          }`}
        >
          {statusWord}
        </span>
        <span className={`text-zinc-300 transition ${open ? 'rotate-90' : ''}`}>›</span>
      </button>
      {/* 展开区：参数与输出 */}
      {open && (
        <div className="border-t border-zinc-100 px-3 py-2">
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-zinc-50 p-2 text-[12px] text-zinc-600">
            {JSON.stringify(item.input ?? {}, null, 2)}
          </pre>
          {item.output && (
            <pre className="mt-1.5 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-zinc-900 p-2 text-[12px] text-zinc-100">
              {item.output}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}
